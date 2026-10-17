# Forge brand kit

The mark is an "F" in brand orange with an amber spark. Sources are the SVG files; the PNGs are rendered from them.

| Token | Hex | Use |
| --- | --- | --- |
| Orange | `#F25C05` | The mark, accents. Not for text or button fills on white: white on it is 3.3:1. |
| Amber | `#FFB224` | The spark only. |
| Navy | `#0B1220` | Dark backgrounds, app icon, logo text on light backgrounds. |

UI controls use the palette generated from the orange (`frontend/src/app/core/theme.ts`): `primary.700` in light mode (6.1:1 with white text) and `primary.500` with navy text in dark mode (5.6:1).

| File | Use |
| --- | --- |
| `svg/forge-logo-light.svg`, `svg/forge-logo-dark.svg` | Horizontal logo for light and dark backgrounds. |
| `svg/forge-mark.svg`, `svg/forge-mark-mono-*.svg` | Mark alone, in colour or single colour. |
| `svg/forge-icon.svg`, `png/forge-icon-*.png` | App icon (rounded navy square), web manifest. |
| `svg/favicon.svg`, `png/favicon*.{png,ico}`, `png/apple-touch-icon-180.png` | Browser and home-screen icons. |
| `preview.png` | Overview of the kit. |

The web app serves copies from `frontend/public/` (`favicon.*`, `apple-touch-icon.png`, `manifest.webmanifest`, `brand/`).
