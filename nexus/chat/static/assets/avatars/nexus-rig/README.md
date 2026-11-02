# NEXUS puppet asset contract — 005D.57C

Status: graphics BLOCKED; no production layers are present yet.

Design source: existing `nexus.png` is authoritative. Do not redesign.
Source raster: 1254 x 1254 px, RGB, no alpha.

## Required production canvas
Every layer must share one identical transparent RGBA canvas so registration is deterministic.
- Native/reference canvas: 1254 x 1254 px (1x)
- Preferred authored high-resolution canvas: 2508 x 2508 px (2x), created from genuine high-resolution/layer artwork, NOT by upscaling the 1254 source.
- 4K must not be claimed unless genuine source artwork supports it.

## Required transparent assets
- `body.png` — torso/hood/shoulders only; pivot normalized (0.50, 0.82), depth 10.
- `head.png` — robot head/base shell, excluding independently moving eye/visor and mouth elements; pivot (0.50, 0.56), depth 20.
- `eyes.png` — luminous visor/eye content intended for gaze movement; pivot (0.50, 0.43), depth 30.
- `lids.png` — blink/occlusion layer where visually applicable; same eye registration, depth 31.
- `mouth-neutral.png`, `mouth-AI.png`, `mouth-E.png`, `mouth-O.png`, `mouth-U.png`, `mouth-MBP.png`, `mouth-FV.png` — registered mouth/visor speech shapes; pivot (0.50, 0.62), depth 32.
- `holo.png` — optional transparent holographic highlights/effects, depth 40.

All files must be transparent RGBA PNGs with the same canvas dimensions and alignment. Empty/fabricated layers, rectangular crops with opaque background, CSS-distorted copies, and AI/upscaled derivatives of the flat source are not acceptable as completed production layers.

## Acceptance before wiring into avatar_rigs.js
1. RGBA with meaningful transparent pixels.
2. Exact common canvas dimensions.
3. Visual registration against authoritative NEXUS portrait.
4. Layer contains the semantic part claimed, not an opaque crop.
5. Independent transform does not expose obvious holes/duplicated features in neutral composition.
6. Neutral composite is visually checked in a browser before declaring the rig complete.
