/* ═══════════════════════════════════════════════
   ClawsCummer v2.9 — Chat-First Frontend
   Chat panel on top, collapsible terminal below.
   Thinking UI + clean output parsing.
   ═══════════════════════════════════════════════ */

let term = null;
let fitAddon = null;
let isTerminalActive = false;
let isLaunching = false;
let terminalVisible = false;

// ── Chat state ────────────────────────────────────────────────────────────────
let currentAssistantMsg = null;  // DOM element for the streaming response content
let currentThinkingEl = null;    // DOM element for the "Thinking" collapsible
let currentToolsEl = null;       // DOM element for tool use list
let currentResponseWrap = null;  // Wrapper for the entire assistant turn
let assistantBuffer = '';
let lastOutputTime = 0;
let settleTimer = null;
let isStreaming = false;
let cliReady = false;
let suppressUntilReady = true;
let lastCleanContent = '';
let discardUntilPrompt = false;  // Discard stale output after user sends new msg
let discardDeadline = 0;         // Timeout for discard phase
let autoPromptPending = false;   // True when an auto-prompt will be injected after CLI ready
let awaitingUserInput = false;   // True after turn completes — ignore stale terminal output
let currentPromptText = '';      // The prompt we sent — used to match response
let lastProcessedLen = 0;        // #10 fix: incremental processing
const MAX_CHAT_MESSAGES = 500;   // #10 fix: DOM cap

// ── Debug logging ────────────────────────────────────────────────────────────
let debugLines = [];
function dbg(msg) {
  debugLines.push(`[${Date.now()}] ${msg}`);
  if (debugLines.length > 500) debugLines.shift();
}
// Called from Python to dump JS debug state
window.getDebugLog = function() { return debugLines.join('\n'); };

// ── Called from Python via evaluate_js ───────────────────────────────────────
window.termWrite = function(data) {
  if (term) term.write(data);
  if (isTerminalActive) feedChat(data);
};

// Called from Python when auto-prompt is about to be sent
window.autoPromptSent = function() {
  autoPromptPending = true;
};

window.handleStatus = function(msg) {
  if (msg.event === 'switching') {
    setStatus('switching');
    showBanner(msg.text || 'Switching CLI...');
    addSystemMessage(msg.text || 'Switching...');
  } else if (msg.event === 'switched') {
    setStatus('running');
    hideBanner();
    loadAccounts();
    addSystemMessage(msg.text || 'Switched!');
  } else if (msg.event === 'session_ended') {
    setStatus('idle');
    finalizeAssistantTurn();
    addSystemMessage('Session ended.');
    showLaunchScreen();
    loadSessions();
  } else if (msg.event === 'planning') {
    setStatus('switching');
    showBanner('Planning with Gemini...');
  } else if (msg.event === 'rate_limit_ask') {
    setStatus('switching');
    showRateLimitBanner(msg.text || 'Rate limit detected.');
  } else if (msg.event === 'permission_request') {
    // Show permission request as a chat message with Allow button
    const permEl = document.createElement('div');
    permEl.className = 'chat-msg chat-msg-permission';
    permEl.innerHTML = `
      <div class="perm-text">${escapeHtml(msg.text || 'Permission requested')}</div>
      <div class="perm-actions">
        <button class="perm-btn perm-allow" onclick="approvePermission(this)">Allow</button>
        <button class="perm-btn perm-deny" onclick="denyPermission(this)">Deny</button>
      </div>
    `;
    document.getElementById('chat-messages').appendChild(permEl);
    scrollChatToBottom();
  } else if (msg.event === 'auth_required') {
    setStatus('idle');
    // Stop thinking indicator and polling
    stopResponsePolling();
    finalizeAssistantTurn();
    addSystemMessage(msg.text || 'Authentication required.');
    // Auto-open terminal so user can complete auth
    if (!terminalVisible) toggleTerminal();
    showBanner('Authentication required — complete login in the terminal below');
  }
};

// ── Initialization ───────────────────────────────────────────────────────────
window.addEventListener('pywebviewready', async () => {
  await loadAccounts();
  await loadSessions();
  setupEventListeners();
});

async function loadAccounts() {
  try {
    const data = JSON.parse(await pywebview.api.get_accounts());
    const selector = document.getElementById('account-selector');
    selector.innerHTML = '';
    data.accounts.forEach(acc => {
      const opt = document.createElement('option');
      opt.value = acc.id;
      const cliIcon = {claude: 'C', gemini: 'G', codex: 'X'}[acc.cli_type] || '?';
      opt.textContent = `${cliIcon} · ${acc.label}`;
      opt.dataset.cliType = acc.cli_type;
      if (acc.id === data.active_id) opt.selected = true;
      selector.appendChild(opt);
    });
    updateCliBadge();
    const mode = await pywebview.api.get_workflow_mode();
    document.getElementById('mode-selector').value = mode;
  } catch (e) {
    console.error('Failed to load accounts:', e);
  }
}

async function loadSessions() {
  try {
    const sessions = JSON.parse(await pywebview.api.get_conversations());
    const container = document.getElementById('launch-sessions');
    container.innerHTML = '';
    sessions.slice(0, 5).forEach(s => {
      const card = document.createElement('div');
      card.className = 'session-card';
      const cliClass = s.cli_type === 'claude' ? 'session-cli-claude' : 'session-cli-gemini';
      const cliLabel = s.cli_type === 'claude' ? 'C' : 'G';
      const topic = s.topic.length > 80 ? s.topic.slice(0, 80) + '...' : s.topic;
      const age = formatAge(s.last_timestamp);
      card.innerHTML = `
        <div class="session-topic">
          <span class="session-cli ${cliClass}">${cliLabel}</span>
          ${escapeHtml(topic)}
        </div>
        <div class="session-meta">${escapeHtml(s.project_key)} · ${age} · ${s.message_count} msgs</div>
      `;
      card.addEventListener('click', () => resumeSession(s));
      container.appendChild(card);
    });
  } catch (e) {
    console.error('Failed to load sessions:', e);
  }
}

// ── Event Listeners ──────────────────────────────────────────────────────────
function setupEventListeners() {
  document.getElementById('account-selector').addEventListener('change', async (e) => {
    const result = JSON.parse(await pywebview.api.switch_account(e.target.value));
    updateCliBadge();
    if (result.ok && isTerminalActive) {
      // Kill current session and go back to launch screen with new account
      await pywebview.api.kill_session();
      finalizeAssistantTurn();
      stopResponsePolling();
      isTerminalActive = false;
      awaitingUserInput = false;
      addSystemMessage(`Switched to ${result.label} (${result.cli_type})`);
      showLaunchScreen();
      await loadSessions();
    }
  });
  document.getElementById('mode-selector').addEventListener('change', async (e) => {
    await pywebview.api.set_workflow_mode(e.target.value);
  });
  document.getElementById('add-account-btn').addEventListener('click', showModal);
  document.getElementById('toggle-terminal-btn').addEventListener('click', toggleTerminal);
  document.getElementById('terminal-collapse-btn').addEventListener('click', toggleTerminal);

  document.querySelectorAll('.modal-choice').forEach(btn => {
    btn.addEventListener('click', () => {
      modalCliType = btn.dataset.type;
      document.getElementById('modal-step').classList.add('hidden');
      document.getElementById('modal-name').classList.remove('hidden');
      const cliNames = {claude: 'Claude', gemini: 'Gemini', codex: 'Codex'};
      document.getElementById('modal-name-label').textContent =
        `Name this ${cliNames[modalCliType] || modalCliType} account:`;
      document.getElementById('modal-name-input').focus();
    });
  });
  document.getElementById('modal-name-submit').addEventListener('click', modalNameSubmit);
  document.getElementById('modal-name-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') modalNameSubmit();
  });
  // Auth: Login here (opens browser console)
  document.getElementById('modal-auth-login').addEventListener('click', async () => {
    const result = JSON.parse(await pywebview.api.run_auth(modalCliType));
    if (!result.ok) { showModalStatus(result.error || 'Auth failed'); return; }
    document.getElementById('modal-auth-status').textContent = 'Login window opened. Complete auth there, then click Done.';
    document.getElementById('modal-auth-status').classList.remove('hidden');
    document.getElementById('modal-auth-done').classList.remove('hidden');
  });

  // Auth: Import credentials file
  document.getElementById('modal-auth-import').addEventListener('click', async () => {
    const fileResult = JSON.parse(await pywebview.api.pick_creds_file());
    if (!fileResult.ok) { showModalStatus(fileResult.error || 'No file selected'); return; }
    const importResult = JSON.parse(await pywebview.api.import_creds(modalAccountName, modalCliType, fileResult.content));
    if (!importResult.ok) { showModalStatus(importResult.error || 'Import failed'); return; }
    showModalStatus(`Account "${importResult.label}" imported!`);
    await loadAccounts();
    setTimeout(hideModal, 1000);
  });

  // Auth: Done — save account after login
  document.getElementById('modal-auth-done').addEventListener('click', async () => {
    const result = JSON.parse(await pywebview.api.add_account(modalAccountName, modalCliType));
    if (!result.ok) { showModalStatus(result.error || 'Failed to add account'); return; }
    showModalStatus('Account added!');
    await loadAccounts();
    setTimeout(hideModal, 800);
  });
  document.getElementById('modal-close').addEventListener('click', hideModal);

  // Launch screen prompt
  document.getElementById('launch-prompt').addEventListener('keydown', async (e) => {
    if (e.key === 'Enter') {
      const prompt = e.target.value.trim();
      if (prompt && !isLaunching) {
        isLaunching = true;
        e.target.value = '';
        try { await launchNew(prompt); } finally { isLaunching = false; }
      }
    }
  });

  // Chat input
  const chatInput = document.getElementById('chat-input');
  chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendChatMessage();
    }
  });
  chatInput.addEventListener('input', () => {
    chatInput.style.height = 'auto';
    chatInput.style.height = Math.min(chatInput.scrollHeight, 120) + 'px';
  });
  document.getElementById('chat-send-btn').addEventListener('click', sendChatMessage);

  window.addEventListener('resize', () => {
    if (fitAddon && terminalVisible) { fitAddon.fit(); sendResize(); }
  });
}

// ── Chat Logic ───────────────────────────────────────────────────────────────
function sendChatMessage() {
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text || !isTerminalActive) return;

  // Finalize any in-progress response
  finalizeAssistantTurn();

  addUserMessage(text);

  // Reset state for new response
  assistantBuffer = '';
  lastCleanContent = '';
  lastProcessedLen = 0;
  awaitingUserInput = false;
  stopResponsePolling();
  hideBanner();
  discardUntilPrompt = true;
  discardDeadline = Date.now() + 2000;

  currentPromptText = text;
  pywebview.api.pty_input(text + '\r');

  input.value = '';
  input.style.height = 'auto';
}

function addUserMessage(text) {
  const msgs = document.getElementById('chat-messages');
  const el = document.createElement('div');
  el.className = 'chat-msg chat-msg-user';
  el.textContent = text;
  msgs.appendChild(el);
  trimChatMessages();
  scrollChatToBottom();
}

function addSystemMessage(text) {
  const msgs = document.getElementById('chat-messages');
  const el = document.createElement('div');
  el.className = 'chat-msg chat-msg-system';
  el.textContent = text;
  msgs.appendChild(el);
  trimChatMessages();
  scrollChatToBottom();
}

// ── Assistant Turn (Thinking + Tools + Response) ─────────────────────────────
let thinkingStartTime = 0;
let thinkingTimer = null;

function startAssistantTurn() {
  finalizeAssistantTurn();
  assistantBuffer = '';
  lastCleanContent = '';
  lastProcessedLen = 0;
  isStreaming = true;
  thinkingStartTime = Date.now();

  const msgs = document.getElementById('chat-messages');

  // Wrapper for entire assistant turn
  currentResponseWrap = document.createElement('div');
  currentResponseWrap.className = 'assistant-turn';

  // Thinking indicator (collapsible) — shows verb, elapsed time, effort
  currentThinkingEl = document.createElement('details');
  currentThinkingEl.className = 'thinking-block';
  currentThinkingEl.innerHTML = `
    <summary>
      <span class="thinking-indicator">
        <span class="thinking-dots"><span></span><span></span><span></span></span>
        <span class="thinking-verb">Thinking</span>...
        <span class="thinking-meta">
          <span class="thinking-time">0s</span>
          <span class="thinking-effort"></span>
          <span class="thinking-tools"></span>
        </span>
      </span>
    </summary>
    <div class="thinking-content"></div>`;
  currentResponseWrap.appendChild(currentThinkingEl);

  // Update elapsed time every second
  thinkingTimer = setInterval(() => {
    if (currentThinkingEl) {
      const elapsed = Math.round((Date.now() - thinkingStartTime) / 1000);
      const timeEl = currentThinkingEl.querySelector('.thinking-time');
      if (timeEl) timeEl.textContent = `${elapsed}s`;
    }
  }, 1000);

  // Tool use section (hidden until tools detected)
  currentToolsEl = document.createElement('div');
  currentToolsEl.className = 'tools-block hidden';
  currentResponseWrap.appendChild(currentToolsEl);

  // Response content area
  currentAssistantMsg = document.createElement('div');
  currentAssistantMsg.className = 'chat-msg chat-msg-assistant streaming';
  currentResponseWrap.appendChild(currentAssistantMsg);

  msgs.appendChild(currentResponseWrap);
  trimChatMessages();
  scrollChatToBottom();
}

function updateThinking(thinkingLines, toolLines) {
  if (!currentThinkingEl) return;

  // Update thinking content
  if (thinkingLines.length > 0) {
    const content = currentThinkingEl.querySelector('.thinking-content');
    content.textContent = thinkingLines.join('\n');
  }

  // Update tool use
  if (toolLines.length > 0 && currentToolsEl) {
    currentToolsEl.classList.remove('hidden');
    currentToolsEl.innerHTML = toolLines.map(t =>
      `<div class="tool-item">${escapeHtml(t)}</div>`
    ).join('');
  }
}

function finalizeThinking() {
  if (!currentThinkingEl) return;
  const summary = currentThinkingEl.querySelector('summary');
  if (summary) {
    const elapsed = thinkingStartTime ? Math.round((Date.now() - thinkingStartTime) / 1000) : 0;
    // Always show "Thought for Ns" — even without detailed content
    if (elapsed > 0) {
      summary.innerHTML = `<span class="thinking-indicator thinking-done">Thought for ${elapsed}s</span>`;
    } else {
      currentThinkingEl.remove();
      currentThinkingEl = null;
    }
  }
}

function updateAssistantContent(text) {
  if (!currentAssistantMsg) return;
  if (text.trim()) {
    currentAssistantMsg.innerHTML = formatChatText(text);
    currentAssistantMsg.classList.remove('streaming');
    scrollChatToBottom();
  }
}

function finalizeAssistantTurn() {
  if (!isStreaming) return;

  finalizeThinking();

  if (currentAssistantMsg) {
    currentAssistantMsg.classList.remove('streaming');
    // Don't overwrite content — readAndDisplayResponse already set clean text from conversation file
    // Only remove if completely empty (no content was ever set)
    if (!currentAssistantMsg.innerHTML.trim()) {
      currentAssistantMsg.remove();
    }
  }

  // Remove empty turn wrapper
  if (currentResponseWrap && !currentResponseWrap.children.length) {
    currentResponseWrap.remove();
  }

  currentAssistantMsg = null;
  currentThinkingEl = null;
  currentToolsEl = null;
  currentResponseWrap = null;
  assistantBuffer = '';
  lastCleanContent = '';
  lastProcessedLen = 0;
  isStreaming = false;
  if (settleTimer) { clearTimeout(settleTimer); settleTimer = null; }
  if (thinkingTimer) { clearInterval(thinkingTimer); thinkingTimer = null; }
  stopResponsePolling();
}

// ── Feed & Parse PTY Output ──────────────────────────────────────────────────
// Ink uses 2D cursor positioning — terminal output cannot be parsed for clean text.
// Strategy: use terminal output ONLY for state detection (ready, thinking, turn done),
// then read Claude's conversation file for the actual clean response text.
let suppressStartTime = 0;
let feedCallCount = 0;
// (turnSettleTimer removed — now using conversation file polling)

function feedChat(data) {
  assistantBuffer += data;
  lastOutputTime = Date.now();
  feedCallCount++;

  const detect = stripAnsiForDetection(data);

  // Ignore stale terminal output after a turn completes
  if (awaitingUserInput) return;

  // Phase 1: Wait for CLI ready (❯ prompt)
  if (suppressUntilReady) {
    const allDetect = stripAnsiForDetection(assistantBuffer);
    const hasPrompt = /❯/.test(allDetect) || /\?\s*for\s*shortcuts/.test(allDetect) || /◇/.test(allDetect);
    const timedOut = suppressStartTime > 0 && (Date.now() - suppressStartTime > 15000);

    if (hasPrompt || timedOut) {
      dbg(`SUPPRESS→OFF prompt=${hasPrompt} timeout=${timedOut}`);
      cliReady = true;
      suppressUntilReady = false;
      assistantBuffer = '';
      if (autoPromptPending) {
        discardUntilPrompt = true;
        discardDeadline = Date.now() + 3000;
        autoPromptPending = false;
      }
      updateDebugBar();
      return;
    }
    return;
  }

  // Phase 2: Discard prompt echo until thinking starts
  if (discardUntilPrompt) {
    const timedOut = Date.now() > discardDeadline;
    const hasSpinner = /[✢✶✻✽●◐◑◒◓⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]/.test(detect);
    const hasThinking = /lithering|azzmatazz|eliberat|onking|hinking|rocessing|nitializ|enerating|nalyzing|easoning|nchanting/i.test(detect);

    if (timedOut || hasSpinner || hasThinking) {
      dbg(`DISCARD→STREAMING spin=${hasSpinner} think=${hasThinking}`);
      discardUntilPrompt = false;
      assistantBuffer = '';
      hideBanner();  // Dismiss any auth/status banners — CLI is working
      if (!isStreaming) {
        startAssistantTurn();
        startResponsePolling();
      }
    }
    return;
  }

  // Phase 3: Streaming — show thinking indicator, poll conversation file for response
  if (!isStreaming) {
    startAssistantTurn();
    startResponsePolling();
  } else if (!responsePollTimer) {
    // Polling wasn't started yet (e.g. phase 2 was skipped) — start it now
    startResponsePolling();
  }

  // Update thinking verb
  const thinkMatch = detect.match(/(Slithering|Razzmatazzing|Deliberating|Honking|Enchanting|Thinking|Processing|Working|Initializing|Generating|Analyzing|Searching|Reasoning)/i);
  if (thinkMatch && currentThinkingEl) {
    const verb = currentThinkingEl.querySelector('.thinking-verb');
    if (verb) verb.textContent = thinkMatch[1];
  }

  // Update effort level (Claude: "medium · /effort", Codex effort flags)
  const effortMatch = detect.match(/(low|medium|high)\s*[·.]\s*\/?effort/i) ||
                      detect.match(/effort[:\s]*(low|medium|high)/i);
  if (effortMatch && currentThinkingEl) {
    const effortEl = currentThinkingEl.querySelector('.thinking-effort');
    if (effortEl) effortEl.textContent = `· ${effortMatch[1]} effort`;
  }

  // Update tool activity (Read, Write, Glob, etc.)
  const toolMatch = detect.match(/(Reading|Writing|Editing|Globbing|Grepping|Searching|Running)\s+.{0,30}/i);
  if (toolMatch && currentThinkingEl) {
    const toolsEl = currentThinkingEl.querySelector('.thinking-tools');
    if (toolsEl) toolsEl.textContent = `· ${toolMatch[0].slice(0, 40)}`;
  }

  // Token count (if visible)
  const tokenMatch = detect.match(/(\d+[,.]?\d*)\s*tokens?\s*(used|remaining|in|out)/i);
  if (tokenMatch && currentThinkingEl) {
    const content = currentThinkingEl.querySelector('.thinking-content');
    if (content) content.textContent = `${tokenMatch[1]} tokens ${tokenMatch[2]}`;
  }

  updateDebugBar();
}

// Poll conversation file for the response matching our specific prompt
let responsePollTimer = null;

function startResponsePolling() {
  stopResponsePolling();
  dbg(`POLL START for prompt: "${currentPromptText.slice(0, 40)}"`);

  // Poll every 1s — looks for response that follows our specific prompt
  responsePollTimer = setInterval(async () => {
    try {
      const result = JSON.parse(await pywebview.api.get_response_for_prompt(currentPromptText));
      if (result.ok && result.text) {
        dbg(`POLL HIT: response (${result.text.length} chars)`);
        stopResponsePolling();
        displayCleanResponse(result.text);
      }
    } catch (e) {
      dbg(`POLL ERROR: ${e}`);
    }
  }, 1000);
}

function stopResponsePolling() {
  if (responsePollTimer) {
    clearInterval(responsePollTimer);
    responsePollTimer = null;
  }
}

function displayCleanResponse(text) {
  if (currentAssistantMsg) {
    currentAssistantMsg.innerHTML = formatChatText(text);
    currentAssistantMsg.classList.remove('streaming');
    scrollChatToBottom();
  }
  finalizeAssistantTurn();
  assistantBuffer = '';
  awaitingUserInput = true;
}

// Lightweight ANSI strip for detection only
function stripAnsiForDetection(str) {
  return str
    .replace(/\x1b\[[\x20-\x3f]*[\x40-\x7e]/g, '')
    .replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g, '')
    .replace(/\x1b[PX^_][^\x1b]*\x1b\\/g, '')
    .replace(/\x1b[>=<()#][0-9]*/g, '')
    .replace(/\x1b[a-zA-Z]/g, '')
    .replace(/\[[?>=<][0-9;]*[a-zA-Z]/g, '')
    .replace(/\x07/g, '')
    .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, '');
}

// Debug bar (no-op in production)
function updateDebugBar() {}

function parseAndRender() {
  const stripped = stripAnsi(assistantBuffer);
  lastProcessedLen = assistantBuffer.length;

  const { thinking, tools, response } = categorizeOutput(stripped);

  if (thinking.length || tools.length || response.trim()) {
    dbg(`PARSE think:${thinking.length} tools:${tools.length} resp:${response.length} respSnip:${response.slice(0,80).replace(/\n/g,'⏎')}`);
  }

  updateThinking(thinking, tools);

  if (response.trim() && response !== lastCleanContent) {
    lastCleanContent = response;
    updateAssistantContent(response);
  }

}

// ── Output Categorization ────────────────────────────────────────────────────
function categorizeOutput(stripped) {
  const lines = stripped.split('\n');
  const thinking = [];
  const tools = [];
  const response = [];
  let inResponse = false;

  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();
    if (!trimmed) {
      if (inResponse) response.push('');
      continue;
    }

    // ── Thinking / spinner lines ──
    if (isThinkingLine(trimmed)) {
      thinking.push(trimmed);
      continue;
    }

    // ── Tool use lines ──
    if (isToolLine(trimmed)) {
      tools.push(trimmed);
      continue;
    }

    // ── CLI chrome (discard) ──
    if (isChromeLine(trimmed)) continue;

    // ── Real response content ──
    inResponse = true;
    response.push(lines[i]);
  }

  // Trim trailing empty lines from response
  while (response.length > 0 && !response[response.length - 1].trim()) {
    response.pop();
  }
  // Trim leading empty lines
  while (response.length > 0 && !response[0].trim()) {
    response.shift();
  }

  return { thinking, tools, response: response.join('\n') };
}

function isThinkingLine(trimmed) {
  if (/^[✢✶✻✽●·*◐◑◒◓⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]\s*(Razzmatazzing|Thinking|Processing|Working)/i.test(trimmed)) return true;
  if (/^(Razzmatazzing|Thinking|Processing)…?\s*$/i.test(trimmed)) return true;
  if (/^[◐◑◒◓]\s*(medium|high|low)\s*·\s*\/effort/i.test(trimmed)) return true;
  if (/^\s*medium\s*·\s*\/effort/i.test(trimmed)) return true;
  if (/^esc\s*to\s*interrupt/i.test(trimmed)) return true;
  return false;
}

function isToolLine(trimmed) {
  // Tool use — only short status lines, not response sentences
  if (/^(Reading|Read|Writing|Wrote|Editing|Edited|Globbing|Grepping)\s+\d+\s*(file|dir)\w*\s*$/i.test(trimmed) && trimmed.length < 30) return true;
  if (/^\(ctrl\+o\s*to\s*expand\)/i.test(trimmed)) return true;
  if (/^⎿\s*[~\/\\]/.test(trimmed)) return true;
  return false;
}

function isChromeLine(trimmed) {
  // Box drawing
  if (/^[╭╰╮╯│├┤┬┴┼─━═┌└┐┘┃║╔╗╚╝╠╣╦╩╬▐▛▜▝▘]+\s*$/.test(trimmed)) return true;
  if (/^[─━═]{3,}/.test(trimmed)) return true;
  // Welcome banner
  if (/^Welcome\s*back/i.test(trimmed)) return true;
  // Only match the specific welcome banner model line (includes context/token info)
  if (/^(Opus|Sonnet|Haiku)\s+\d+(\.\d+)?\s*\(/.test(trimmed)) return true;
  if (/^Claude\s*(Max|Pro|Free)\s*·/i.test(trimmed)) return true;
  if (/^Tips\s*for\s*getting\s*started/i.test(trimmed)) return true;
  if (/^Run\s*\/init\s*to\s*create/i.test(trimmed)) return true;
  if (/^Recent\s*activity/i.test(trimmed)) return true;
  if (/^No\s*recent\s*activity/i.test(trimmed)) return true;
  // Account lines — only match standalone email or org lines (very short, no other content)
  if (/^\S+@\S+\.\S+('s\s*Organization)?\s*$/.test(trimmed) && trimmed.length < 50) return true;
  if (/^~[\\\/]/.test(trimmed) && trimmed.length < 40) return true;
  // Prompts
  if (/^❯\s*$/.test(trimmed)) return true;
  if (/^❯\s*\[Pasted\s*text/i.test(trimmed)) return true;
  if (/^\?\s*for\s*shortcuts/.test(trimmed)) return true;
  // Pasted text markers
  if (/^\[Pasted\s*text\s*#?\d*/i.test(trimmed)) return true;
  // Token counts
  if (/^\d+\s*tokens?\s*(remaining|used|in|out)/i.test(trimmed)) return true;
  // Pipe/box filler
  if (/^[│║\s]+$/.test(trimmed)) return true;
  // Claude version line
  if (/^claude\s*$/.test(trimmed)) return true;
  // Gemini prompt
  if (/^>\s*$/.test(trimmed)) return true;
  // Claude Code version/header lines
  if (/^ClaudeCode\s*v/i.test(trimmed)) return true;
  if (/^claude\s*(code)?$/i.test(trimmed)) return true;
  // Short garbage: ONLY spinner chars and whitespace, nothing else
  if (/^[✢✶✻✽●·*◐◑◒◓⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏\s]+$/.test(trimmed)) return true;
  return false;
}

// ── ANSI Stripping ───────────────────────────────────────────────────────────
function stripAnsi(str) {
  let s = str
    .replace(/\x1b\[[\x20-\x3f]*[\x40-\x7e]/g, '') // CSI (ECMA-48 compliant: all param/intermediate/final bytes)
    .replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g, '') // OSC
    .replace(/\x1b[PX^_][^\x1b]*\x1b\\/g, '')     // DCS/PM/APC
    .replace(/\x1b[>=<()#][0-9]*/g, '')            // ESC + mode chars
    .replace(/\x1b[a-zA-Z]/g, '')                  // ESC + letter
    .replace(/\[[?>=<][0-9;]*[a-zA-Z]/g, '')       // Orphan CSI remnants (ESC stripped separately)
    .replace(/\x07/g, '')                           // BEL
    .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, ''); // Control chars (incl ESC)

  // Smart CR handling: for each line, keep only text after the last \r (handles spinner overwrites)
  // but preserve text when \r is just a trailing cursor reset
  s = s.split('\n').map(line => {
    if (!line.includes('\r')) return line;
    const parts = line.split('\r');
    for (let i = parts.length - 1; i >= 0; i--) {
      if (parts[i].length > 0) return parts[i];
    }
    return '';
  }).join('\n');

  return s;
}

// ── Text Formatting (escapeHtml runs FIRST — safe against XSS) ──────────────
function formatChatText(text) {
  // Step 1: Escape all HTML entities first (XSS protection)
  let html = escapeHtml(text);

  // Step 2: Apply markdown-like formatting on the escaped text
  // Code blocks
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre>$2</pre>');
  // Inline code
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
  // Bold
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  // Line breaks
  html = html.replace(/\n/g, '<br>');

  return html;
}

function extractResponseText(raw) {
  const stripped = stripAnsi(raw);
  const { response } = categorizeOutput(stripped);
  return response;
}

// ── DOM Management ───────────────────────────────────────────────────────────
function trimChatMessages() {
  const msgs = document.getElementById('chat-messages');
  while (msgs.children.length > MAX_CHAT_MESSAGES) {
    msgs.removeChild(msgs.firstChild);
  }
}

function scrollChatToBottom() {
  const msgs = document.getElementById('chat-messages');
  requestAnimationFrame(() => { msgs.scrollTop = msgs.scrollHeight; });
}

// ── Terminal ─────────────────────────────────────────────────────────────────
function initTerminal() {
  if (term) return;
  term = new Terminal({
    theme: {
      background: '#0c0c11', foreground: '#e2e8f4', cursor: '#818cf8',
      cursorAccent: '#0c0c11', selectionBackground: '#4f46e544',
      black: '#0c0c11', red: '#ef4444', green: '#34d399', yellow: '#f59e0b',
      blue: '#818cf8', magenta: '#c084fc', cyan: '#4ecdc4', white: '#e2e8f4',
      brightBlack: '#52525b', brightRed: '#f87171', brightGreen: '#6ee7b7',
      brightYellow: '#fbbf24', brightBlue: '#a5b4fc', brightMagenta: '#d8b4fe',
      brightCyan: '#7eddd5', brightWhite: '#f8fafc',
    },
    fontFamily: "'Cascadia Code', 'Consolas', 'Courier New', monospace",
    fontSize: 14, lineHeight: 1.2, cursorBlink: true, cursorStyle: 'bar',
    scrollback: 5000, allowProposedApi: true,
  });

  fitAddon = new FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  term.loadAddon(new WebLinksAddon.WebLinksAddon());
  term.open(document.getElementById('terminal-container'));
  fitAddon.fit();

  term.attachCustomKeyEventHandler(ev => {
    if (ev.type !== 'keydown') return true;
    if (ev.ctrlKey && ev.key === 'c' && term.hasSelection()) {
      navigator.clipboard.writeText(term.getSelection());
      return false;
    }
    if (ev.ctrlKey && ev.key === 'v') {
      navigator.clipboard.readText().then(t => { if (t) pywebview.api.pty_input(t); }).catch(() => {});
      return false;
    }
    return true;
  });

  term.onData(data => { pywebview.api.pty_input(data); });
  term.onResize(({ cols, rows }) => { pywebview.api.pty_resize(cols, rows); });
}

function sendResize() {
  if (term) pywebview.api.pty_resize(term.cols, term.rows);
}

function toggleTerminal() {
  const panel = document.getElementById('terminal-panel');
  terminalVisible = !terminalVisible;
  if (terminalVisible) {
    panel.classList.remove('hidden');
    if (fitAddon) setTimeout(() => { fitAddon.fit(); sendResize(); }, 50);
  } else {
    panel.classList.add('hidden');
  }
}

// ── Session Launch ───────────────────────────────────────────────────────────
async function launchNew(prompt) {
  showChatView();
  initTerminal();
  if (term) term.clear();

  cliReady = false;
  suppressUntilReady = true;
  suppressStartTime = Date.now();
  feedCallCount = 0;
  assistantBuffer = '';
  lastCleanContent = '';
  lastProcessedLen = 0;
  discardUntilPrompt = false;
  discardDeadline = 0;
  awaitingUserInput = false;

  addUserMessage(prompt);
  currentPromptText = prompt;

  // Set BEFORE launch so feedChat receives welcome banner + prompt detection
  isTerminalActive = true;
  setStatus('running');
  try {
    const result = JSON.parse(await pywebview.api.launch_session('new', '', '', prompt));
    if (!result.ok) {
      isTerminalActive = false;
      setStatus('idle');
      addSystemMessage('Error: ' + (result.error || 'Launch failed'));
      return;
    }
  } catch (e) {
    isTerminalActive = false;
    setStatus('idle');
    addSystemMessage('Launch error: ' + e);
    return;
  }
  if (terminalVisible && fitAddon) { fitAddon.fit(); sendResize(); }
}

async function resumeSession(session) {
  if (isLaunching) return;
  isLaunching = true;
  try { await _resumeSession(session); } finally { isLaunching = false; }
}

async function _resumeSession(session) {
  const selector = document.getElementById('account-selector');
  const currentOpt = selector.options[selector.selectedIndex];
  if (currentOpt && currentOpt.dataset.cliType !== session.cli_type) {
    for (const opt of selector.options) {
      if (opt.dataset.cliType === session.cli_type) {
        selector.value = opt.value;
        await pywebview.api.switch_account(opt.value);
        updateCliBadge();
        break;
      }
    }
  }

  showChatView();
  initTerminal();
  if (term) term.clear();

  cliReady = false;
  suppressUntilReady = true;
  suppressStartTime = Date.now();
  feedCallCount = 0;
  assistantBuffer = '';
  lastCleanContent = '';
  lastProcessedLen = 0;
  discardUntilPrompt = false;
  awaitingUserInput = false;

  addSystemMessage('Resuming session...');

  // Set BEFORE launch so feedChat receives welcome banner + prompt detection
  isTerminalActive = true;
  setStatus('running');
  try {
    const result = JSON.parse(await pywebview.api.launch_session('resume', session.session_id, session.project_path, ''));
    if (!result.ok) { isTerminalActive = false; setStatus('idle'); addSystemMessage('Error: ' + (result.error || 'Resume failed')); return; }
  } catch (e) { isTerminalActive = false; setStatus('idle'); addSystemMessage('Resume error: ' + e); return; }

  if (terminalVisible && fitAddon) { fitAddon.fit(); sendResize(); }
}

function showChatView() {
  document.getElementById('launch-screen').classList.add('hidden');
  document.getElementById('chat-panel').classList.remove('hidden');
  document.getElementById('chat-messages').innerHTML = '';
  setTimeout(() => document.getElementById('chat-input').focus(), 100);
}

function showLaunchScreen() {
  document.getElementById('chat-panel').classList.add('hidden');
  document.getElementById('terminal-panel').classList.add('hidden');
  document.getElementById('launch-screen').classList.remove('hidden');
  isTerminalActive = false;
  terminalVisible = false;
}

// ── Modal ────────────────────────────────────────────────────────────────────
let modalCliType = '';
let modalAccountName = '';

function showModal() {
  modalCliType = ''; modalAccountName = '';
  document.getElementById('modal-step').classList.remove('hidden');
  document.getElementById('modal-name').classList.add('hidden');
  document.getElementById('modal-auth').classList.add('hidden');
  document.getElementById('modal-status').classList.add('hidden');
  document.getElementById('modal-name-input').value = '';
  document.getElementById('modal-overlay').classList.remove('hidden');
}
function hideModal() { document.getElementById('modal-overlay').classList.add('hidden'); }

async function modalNameSubmit() {
  const name = document.getElementById('modal-name-input').value.trim();
  if (!name) return;
  modalAccountName = name;

  if (modalCliType === 'gemini') {
    // Gemini uses browser OAuth — just add the account
    const result = JSON.parse(await pywebview.api.add_account(name, 'gemini'));
    if (!result.ok) { showModalStatus(result.error || 'Failed'); return; }
    showModalStatus('Gemini account added!');
    await loadAccounts();
    setTimeout(hideModal, 1200);
    return;
  }

  // Claude / Codex — run auth in the embedded terminal
  document.getElementById('modal-name').classList.add('hidden');
  document.getElementById('modal-auth').classList.remove('hidden');

  document.querySelector('#modal-auth p').textContent = `A login window has opened. Complete the auth flow there.`;
  const cmdEl = document.getElementById('modal-auth-cmd');
  if (cmdEl) cmdEl.textContent = modalCliType === 'claude' ? 'claude auth login' : 'codex login';

  // Run auth in a separate console window (OAuth needs a real terminal)
  const result = JSON.parse(await pywebview.api.run_auth(modalCliType));
  if (!result.ok) {
    showModalStatus(result.error || 'Auth failed');
    return;
  }
}
function showModalStatus(text) {
  const el = document.getElementById('modal-status');
  el.textContent = text; el.classList.remove('hidden');
}

// ── Status ───────────────────────────────────────────────────────────────────
function setStatus(state) {
  const dot = document.getElementById('status-dot');
  dot.className = `status-${state}`;
  dot.title = state.charAt(0).toUpperCase() + state.slice(1);
}
function showBanner(text) {
  document.getElementById('status-text').textContent = text;
  document.getElementById('status-banner').classList.remove('hidden');
}
function hideBanner() {
  document.getElementById('status-banner').classList.add('hidden');
  document.getElementById('rate-limit-banner').classList.add('hidden');
}
function showRateLimitBanner(text) {
  document.getElementById('rate-limit-text').textContent = text;
  document.getElementById('rate-limit-banner').classList.remove('hidden');
}
async function onRateLimitSwitch() {
  document.getElementById('rate-limit-banner').classList.add('hidden');
  await pywebview.api.confirm_switch();
}
async function onRateLimitDismiss() {
  document.getElementById('rate-limit-banner').classList.add('hidden');
  await pywebview.api.dismiss_rate_limit();
}

// ── Helpers ──────────────────────────────────────────────────────────────────
// ── Permission handling ───────────────────────────────────────────────────
function approvePermission(btn) {
  pywebview.api.pty_input('y\r');
  btn.closest('.chat-msg-permission').classList.add('perm-resolved');
  btn.closest('.perm-actions').innerHTML = '<span class="perm-approved">Allowed</span>';
}
function denyPermission(btn) {
  pywebview.api.pty_input('n\r');
  btn.closest('.chat-msg-permission').classList.add('perm-resolved');
  btn.closest('.perm-actions').innerHTML = '<span class="perm-denied">Denied</span>';
}

function updateCliBadge() {
  const selector = document.getElementById('account-selector');
  const opt = selector.options[selector.selectedIndex];
  const badge = document.getElementById('cli-indicator');
  if (opt) {
    const cliIcon = {claude: 'C', gemini: 'G', codex: 'X'}[opt.dataset.cliType] || '?';
    badge.textContent = cliIcon;
    badge.className = `badge badge-${opt.dataset.cliType}`;
  }
}
function formatAge(isoStr) {
  try {
    const diff = Date.now() - new Date(isoStr).getTime();
    const days = Math.floor(diff / 86400000);
    const hours = Math.floor(diff / 3600000);
    if (days > 0) return `${days}d ago`;
    if (hours > 0) return `${hours}h ago`;
    return 'just now';
  } catch { return ''; }
}
function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}
