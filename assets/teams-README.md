# Teams registry — logos, colors, rosters

`teams.json` is the one place that describes every team, every player, and the league. Both the exporter and the browser marking tool read it: the exporter uses it to put logos on the score bug, player photos on the pre-game and final screens, and the league logo on both overlays; the browser uses it for the team dropdown picker, the overlay previews, and to keep colors/logos/rosters in sync once you pick a preset.

## Where it goes

Drop this inside your repo alongside the asset images it references. Any of these layouts works — the exporter auto-detects the first one it finds:

```
repo/
├── assets/
│   ├── teams.json
│   ├── league/
│   │   └── LBL.png            # league logo
│   ├── teams/
│   │   ├── Celtics.png
│   │   ├── Lakers.png
│   │   ├── Warriors.png
│   │   ├── Heat.png
│   │   ├── Bucks.png
│   │   └── Suns.png
│   └── players/
│       ├── Jacob.png
│       ├── Daniel.png
│       ├── Joseph.png
│       └── Nathan.png
├── exporter/
│   ├── export.py
│   ├── gui.py
│   └── ...
└── index.html
```

All paths *inside* `teams.json` are relative to the `teams.json` file itself. So `"logo": "teams/Celtics.png"` looks for `assets/teams/Celtics.png` when teams.json lives at `assets/teams.json`. **Filename case must match on case-sensitive filesystems (Linux, most web servers).**

## Schema

```jsonc
{
  "league": {
    "name": "Your League",
    "logo": "league/LBL.png"        // relative to teams.json
  },
  "teams": [
    {
      "id": "celtics",              // stable identifier (never changes)
      "name": "Celtics",            // display name
      "color1": "#007A33",          // primary color
      "color2": "#BA9653",          // secondary (for gradient)
      "logo": "teams/Celtics.png",
      "players": ["Jacob", "Daniel"]   // player names
    }
  ],
  "players": [
    { "name": "Jacob", "photo": "players/Jacob.png" }
  ]
}
```

**How player matching works:** Team `players` contains names as strings. The exporter looks them up in the top-level `players` list by exact case-insensitive name match. If a name has no matching photo entry, the overlay renders a rounded-rectangle tile with the player's initial on the team's secondary color — nothing breaks.

**How team matching works:** When the exporter reads a `markings.json` (from the browser tool), it looks up each team in the registry by `id` first, then by case-insensitive `name` match. If matched, the registry fills in `logo` and `players` on that team. Colors in `markings.json` win over the registry (so if you manually customize colors for a specific game in the browser tool, that sticks).

## Asset recommendations

- **League logo:** transparent PNG, ~500×500 or wider banner. Rendered at ~72px tall in the browser overlay and ~10% of video height in the exporter, with width derived from the image's aspect ratio.
- **Team logos:** transparent PNG, ~300×300 or larger. They appear in the score bug (small), the browser sidebar thumb, and the pre-game / final-score cards (large). Aspect ratio is preserved everywhere — non-square logos render fine.
- **Player photos:** portrait PNGs preserve aspect ratio now. Pictures are shown inside a rounded-rectangle tile and scaled to fit — tall portraits no longer get center-cropped to squares. Minimum ~200×260 recommended; larger is better.
- **Transparency matters for logos.** If your team logos have white backgrounds, they'll show up as white rectangles on the gradient. Either use PNGs with transparent backgrounds, or pre-process with "remove background."

## Missing files? No problem

Every asset is optional. If a logo or photo file doesn't exist at its referenced path, that slot just isn't drawn and the rest of the render/overlay still works.

## Auto-detection

The browser fetches `teams.json` from `./assets/teams.json` (so it works out of the box when you `python -m http.server` from the repo root). The exporter GUI and CLI both auto-detect — no manual path needed unless you want a custom file. Leave the field blank and the GUI will show "auto-detected ✓" with the discovered path.
