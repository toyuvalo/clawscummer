#!/usr/bin/env python3
"""
Generate clawscummer.ico — two teal claws gripping a white ball.
"""
import math, os
from PIL import Image, ImageDraw, ImageFilter

BG    = (13, 17, 23)
TEAL  = (0, 200, 162)
WHITE = (248, 250, 255)


def draw_annular_arc(d, cx, cy, r_out, r_in, deg_start, deg_end, fill, steps=72):
    """Filled ring-sector polygon."""
    outer, inner = [], []
    for i in range(steps + 1):
        a = math.radians(deg_start + (deg_end - deg_start) * i / steps)
        outer.append((cx + r_out * math.cos(a), cy + r_out * math.sin(a)))
        inner.append((cx + r_in  * math.cos(a), cy + r_in  * math.sin(a)))
    d.polygon(outer + list(reversed(inner)), fill=fill)


def draw_claw(d, cx, cy, s, side):
    """
    side = -1  → left claw  (arc through LEFT,  fingers point RIGHT)
    side = +1  → right claw (arc through RIGHT, fingers point LEFT)
    """
    cc_x = cx + side * s * 0.22   # claw body center-x
    cc_y = cy

    r_out = s * 0.26
    r_in  = s * 0.14

    # Arc angles (PIL/screen convention: 0=right, 90=down, clockwise)
    # Left  claw: 90° → 270° through 180° (back on left,  opening faces right) ✓
    # Right claw: -90° → 90° through   0° (back on right, opening faces left)  ✓
    if side == -1:
        a_start, a_end = 90, 270
    else:
        a_start, a_end = -90, 90

    # --- Claw body arc ---
    draw_annular_arc(d, cc_x, cc_y, r_out, r_in, a_start, a_end, TEAL)

    # Rounded end-caps on both tips
    for angle in [a_start, a_end]:
        a   = math.radians(angle)
        mid = (r_out + r_in) / 2
        cap = (r_out - r_in) / 2
        px  = cc_x + mid * math.cos(a)
        py  = cc_y + mid * math.sin(a)
        d.ellipse([px - cap, py - cap, px + cap, py + cap], fill=TEAL)

    # --- Finger tip targets (just touching the ball edge) ---
    ftp_x = cx + side * s * 0.09   # side=-1 → cx-0.09s (left of ball), side=+1 → right

    def tip(angle_deg, r):
        a = math.radians(angle_deg)
        return (cc_x + r * math.cos(a), cc_y + r * math.sin(a))

    # Which angle is "upper" (top) vs "lower" (bottom):
    #   Left  claw: top = a_end=270°,  bottom = a_start=90°
    #   Right claw: top = a_start=-90°, bottom = a_end=90°
    top_ang = a_end   if side == -1 else a_start
    bot_ang = a_start if side == -1 else a_end

    uo = tip(top_ang, r_out);  ui = tip(top_ang, r_in)
    lo = tip(bot_ang, r_out);  li = tip(bot_ang, r_in)

    ftp_upper = (ftp_x, cy - s * 0.04)
    ftp_lower = (ftp_x, cy + s * 0.04)

    d.polygon([uo, ui, ftp_upper], fill=TEAL)
    d.polygon([lo, li, ftp_lower], fill=TEAL)


def make_icon(size=256):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)
    cx  = cy = size / 2
    s   = size

    # Dark circular background
    d.ellipse([1, 1, s - 1, s - 1], fill=(*BG, 255))

    # Claws
    draw_claw(d, cx, cy, s, -1)
    draw_claw(d, cx, cy, s, +1)

    # White ball — drawn last so it sits on top of the claw tips
    rb = s * 0.13
    # soft drop-shadow
    sd = s * 0.016
    d.ellipse([cx-rb+sd, cy-rb+sd, cx+rb+sd, cy+rb+sd], fill=(0, 0, 0, 55))
    # main ball
    d.ellipse([cx - rb, cy - rb, cx + rb, cy + rb], fill=WHITE)
    # specular highlight (upper-left)
    hr = rb * 0.38
    hx = cx - rb * 0.38
    hy = cy - rb * 0.38
    d.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=(255, 255, 255, 200))

    return img


def save_ico(path):
    sizes  = [256, 128, 64, 48, 32, 16]
    base   = make_icon(256)
    frames = []
    for sz in sizes:
        if sz == 256:
            frames.append(base.copy())
        else:
            frames.append(base.resize((sz, sz), Image.LANCZOS))
    frames[0].save(
        path, format="ICO",
        sizes=[(sz, sz) for sz in sizes],
        append_images=frames[1:],
    )
    print(f"  Saved: {path}")

    # Also save a 512x512 PNG thumbnail for GitHub / sharing
    png_path = path.replace(".ico", "_thumb.png")
    make_icon(512).save(png_path, format="PNG")
    print(f"  Saved: {png_path}")


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clawscummer.ico")
    save_ico(out)
