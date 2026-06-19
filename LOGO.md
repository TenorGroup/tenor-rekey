# tenor/rekey - app icon rule (DRAFT, pending founder approval)

Locks how the app icon is built so any future session regenerates an identical
mark, and so other Tenor sub-brand apps follow the same construction. The icon
is the **stacked sub-brand lockup** (brand grammar `12-tenor-os-stacked`): the
master symbol on top, the focal word below - never the bare `t/`.

Generator: `app/tools/gen_icon.swift` (renders with the real Geist Sans Medium
via CoreText, then `iconutil` / asset-catalog). Re-run: `swift app/tools/gen_icon.swift`.

## Construction (per square tile of side S)

1. **Background**: ink `#000000`, full-bleed rounded square, corner radius
   `0.21875 * S` (the iOS/macOS squircle ratio, = 224 on a 1024 tile).
2. **Symbol `t/`** (top): Geist Sans Medium (500), letter-spacing `-0.04em`,
   colour paper `#FAFAF7`; the slash `/` at **30% opacity** (brand syntax
   hierarchy), `t` at 100%. Symbol font size `= 0.40 * S`.
3. **Focal word** (below), here `rekey`: same font / tracking / paper, **100%
   opacity**. Sized so its glyph width is `~0.60 * S` (auto-fit, capped at
   `0.24 * S` font), so a longer focal stays inside the tile.
4. **Layout**: symbol over focal, gap `~0.045 * S`, the whole `t/ + focal`
   block optically centred on its glyph-ink bounds (not the line box).
5. **Sizes**: 16 / 32 / 128 / 256 / 512 px at @1x and @2x, into
   `app/Assets.xcassets/AppIcon.appiconset`.
6. **Stamp**: this rule tracks `@tenor/brand` (symbol `02-symbol-t`, app-icon
   `03-app-icon-1024`, stacked lockup `12-*-stacked`). Bump when the brand does.

## Rules

- Always the stacked lockup (symbol + focal), never bare `t/`, for a sub-brand app.
- Never substitute the font: Geist Sans Medium only, rendered from the brand TTF.
- Slash always 30%; `t` and focal always 100%; paper on ink only (no other colours,
  no signal blue in the icon).
- To make another Tenor sub-brand app icon, change only the `focal` constant in
  `gen_icon.swift`; everything else is fixed.

> Promote to `@tenor/brand` guidelines (sub-brand app-icon rule) once the founder
> approves this mark.
