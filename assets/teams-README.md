# Teams registry — logos, colors, rosters

`teams.json` is the one place that describes every team, every player, and the league. The exporter reads it to put logos on the score bug and player photos on the final-score screen. The browser marking tool will use it for a dropdown team picker (next feature).

## Where it goes

Drop this inside your repo alongside the asset images it references. Any of these layouts works — the exporter auto-detects the first one it finds:

```
repo/
├── assets/
│   ├── teams.json
│   ├── league.png
│   ├── teams/
│   │   ├── celtics.png
│   │   ├── lakers.png
│   │   ├── warriors.png
│   │   ├── heat.png
│   │   ├── bucks.png
│   │   └── suns.png
│   └── players/
│       ├── jacob.jpg
│       ├── daniel.jpg
│       ├── joseph.jpg
│       └── nathan.jpg
├── exporter/
│   ├── export.py
│   ├── gui.py
│   └── ...
└── index.html
```

All paths *inside* `teams.json` are relative to the `teams.json` file itself. So `"logo": "teams/celtics.png"` looks for `assets/teams/celtics.png` when teams.json lives at `assets/teams.json`.

## Schema

```jsonc
{
  "league": {
    "name": "Your League",
    "logo": "league.png"          // relative to teams.json
  },
  "teams": [
    {
      "id": "celtics",            // stable identifier (never changes)
      "name": "Celtics",          // display name
      "color1": "#007A33",        // primary color
      "color2": "#BA9653",        // secondary (for gradient)
      "logo": "teams/celtics.png",
      "players": ["Jacob", "Daniel"]   // player names
    }
  ],
  "players": [
    { "name": "Jacob", "photo": "players/jacob.jpg" }
  ]
}
```

**How player matching works:** Team `players` contains names as strings. The exporter looks them up in the top-level `players` list by exact case-insensitive name match. If a name in a team roster has no matching entry in `players`, the final screen shows a colored initial-circle fallback instead of a photo — nothing breaks.

**How team matching works:** When the exporter reads a `markings.json` (from the browser tool), it looks up each team in the registry by `id` first, then by case-insensitive `name` match. If matched, the registry fills in `logo` and `players` on that team. Colors in `markings.json` win over the registry (so if you manually customize colors for a specific game in the browser tool, that sticks).

## Asset recommendations

- **League logo:** transparent PNG, ~500×500. It shows at ~10% of video height, so 500px is plenty.
- **Team logos:** transparent PNG, ~300×300. They appear in both the score bug (small) and the final card (large), so start big.
- **Player photos:** square JPG or PNG, 400×400+ works well. They're center-cropped to a circle automatically — portrait photos work as long as the face is near the center.
- **Transparency matters for logos.** If your team logos have white backgrounds, they'll show up as white squares on the gradient. Either use PNGs with transparent backgrounds, or pre-process with "remove background."

## Missing files? No problem

Every asset is optional. If a logo file doesn't exist at its referenced path, that logo just isn't drawn — the rest of the render still works. Same for player photos.

## CLI override

To use a specific teams.json (e.g. for testing), pass `--teams path/to/other.json` to `export.py`, or set the "Team presets" field in the GUI. Leaving it blank uses auto-detection.
