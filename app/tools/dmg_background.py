#!/usr/bin/env python3
"""Render the install-window background for the tenor/rekey .dmg.

A Dieter-Rams-instrument-panel install window: true-black canvas, the
tenor/rekey wordmark (opacity hierarchy 50/30/100), and one signal-blue arrow
from the app to the Applications folder. The app icon + Applications alias are
placed on top by the dmg layout at the slots this art leaves for them.

Output: app/dist/.dmgbg/background.png (1x) + background@2x.png (Retina).
"""
import os
import pathlib
from PIL import Image, ImageDraw, ImageFont

HERE = pathlib.Path(__file__).resolve().parent
FONTS = HERE.parent / "Resources" / "Fonts"
OUT = HERE.parent / "dist" / ".dmgbg"
OUT.mkdir(parents=True, exist_ok=True)

W, H = 660, 420                 # dmg window content size (points)
APP_SLOT = (175, 205)          # must match the AppleScript icon positions
APPS_SLOT = (485, 205)
CANVAS = (11, 12, 14)          # #0b0c0e brand canvas
WHITE = (245, 246, 248)
BLUE = (0, 102, 255)           # #0066FF signal blue - operational cursor only


def font(name, size):
    return ImageFont.truetype(str(FONTS / name), size)


def render(scale):
    img = Image.new("RGB", (W * scale, H * scale), CANVAS)
    d = ImageDraw.Draw(img, "RGBA")
    s = scale

    # hairline frame inset (instrument-panel discipline)
    d.rectangle([12 * s, 12 * s, (W - 12) * s, (H - 12) * s],
                outline=(255, 255, 255, 18), width=max(1, s))

    # wordmark: tenor / rekey  (50% / 30% / 100%)
    wm = font("Geist-Medium.ttf", 30 * s)
    parts = [("tenor", 128), ("/", 77), ("rekey", 255)]
    widths = [d.textlength(t, font=wm) for t, _ in parts]
    x = (W * s - sum(widths)) / 2
    y = 60 * s
    for (t, a), w in zip(parts, widths):
        d.text((x, y), t, font=wm, fill=(WHITE[0], WHITE[1], WHITE[2], a))
        x += w

    # arrow from the app slot to the Applications slot (between the two icons)
    midy = APP_SLOT[1] * s
    x0 = (APP_SLOT[0] + 70) * s
    x1 = (APPS_SLOT[0] - 70) * s
    d.line([(x0, midy), (x1, midy)], fill=(*BLUE, 235), width=max(2, 2 * s))
    head = 9 * s
    d.polygon([(x1, midy), (x1 - head, midy - head * 0.7),
               (x1 - head, midy + head * 0.7)], fill=(*BLUE, 235))

    # hint under the arrow
    hint = font("Geist-Regular.ttf", 11 * s)
    msg = "drag to the Applications folder to install"
    mw = d.textlength(msg, font=hint)
    d.text(((W * s - mw) / 2, (H - 70) * s), msg, font=hint,
           fill=(255, 255, 255, 120))

    # faint labels under each slot are drawn by Finder (icon names); leave room.
    return img


def main():
    render(1).save(OUT / "background.png")
    render(2).save(OUT / "background@2x.png")
    # a combined tiff so the dmg keeps the Retina variant
    base = Image.open(OUT / "background.png")
    hi = Image.open(OUT / "background@2x.png")
    base.save(OUT / "background.tiff", save_all=True, append_images=[hi],
              compression="tiff_deflate", dpi=(72, 72))
    print("wrote", OUT / "background.png", "+ @2x + tiff")


if __name__ == "__main__":
    main()
