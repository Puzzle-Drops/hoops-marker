# CLAUDE.md

> Context document for AI assistants working on this project. Read this first.

## Project: Hoops Highlight Marker

A two-part toolchain for marking basketball video and producing highlight reels with an animated score bug overlay.

**Live site:** `https://<username>.github.io/<reponame>/` (GitHub Pages, `main` branch, served from repo root)

**Status:** Part 1 (browser marking tool) shipped with team presets, pre-game/final overlays, and clickable mark buttons. Part 2 (Python exporter with matching pre-game + final screens, league + team logos, aspect-ratio player portraits) shipped.

---

## Vision

The user wants a fast, keyboard-driven workflow to:

1. Watch a basketball video (local file **or** YouTube)
2. Tap hotkeys at each scoring moment to log baskets with team + point value
3. Configure team names and colors/gradients for a broadcast-style score bug
4. Export the markings as JSON
5. Separately, feed the video + JSON into a Python exporter that cuts a highlight reel: optional **pre-game intro** (league logo + both team cards with logos, names, player portraits, and a "VS"), then each highlight clip showing pre-roll seconds before a basket, the basket itself with the score bug animating from the old total to the new total, and post-roll seconds after, then a matching **final-score** screen (same visual language, scores in place of "VS"). Team logos, league logo, and player portraits are sourced from `assets/teams.json` — aspect ratio preserved for every image.

The marking tool lives in the browser (this repo). The exporter lives as a Python script run locally on the user's machine.

---

## Architecture

### Why split it?

- **Marking phase** needs responsive UI, tight hotkey capture, YouTube embed support → browser is ideal, zero-install.
- **Export phase** needs real video encoding, frame-accurate overlays, concatenation → ffmpeg/Python is ideal, browser can't do this well.
- The JSON is the contract between the two halves.

### Data flow

```
[Local video OR YouTube URL]
           │
           ▼
 ┌─────────────────────┐
 │  Marking tool       │  ← this repo, GitHub Pages
 │  (index.html)       │
 └─────────┬───────────┘
           │ exports
           ▼
    markings.json
           │
           ▼
 ┌─────────────────────┐
 │  Python exporter    │  ← separate local tool (not yet built)
 │  (uses ffmpeg)      │  ← requires local video file on disk
 └─────────┬───────────┘
           │
           ▼
   highlights.mp4
```

### YouTube caveat

Marking against a YouTube URL works fine in the browser via the IFrame Player API. **But** the exporter needs the actual video file on disk — YouTube's ToS prohibits the site from downloading, so users grab it themselves with `yt-dlp` separately. The JSON stores the YouTube video ID so the exporter can verify it's being run against the right source.

---

## Deployment (GitHub Pages)

- Branch: `main`
- Source: repo root (no build step)
- Entry point: `index.html`
- Served at: `https://<username>.github.io/<reponame>/`

**Why hosted vs. local file://?** Opening `index.html` directly via `file://` breaks the YouTube IFrame API — browsers treat `file://` as an isolated security origin and block the cross-origin postMessage the YT player uses. Local videos still work over `file://`, but YouTube requires `http://` or `https://`. GitHub Pages solves this for free. For local dev, run `python -m http.server 8000` or `npx serve` and visit `http://localhost:8000`.

---

## Tech stack

- **Single static HTML file.** No build, no bundler, no framework, no dependencies.
- Vanilla JS, CSS variables for theming.
- YouTube IFrame Player API (loaded dynamically from `youtube.com/iframe_api` when a YouTube URL is entered).
- HTML5 `<video>` for local files.

Why no framework: keeps the app trivial to host, trivial to hack on, and zero npm overhead. The state is small enough that a single `state` object + manual render functions are cleaner than React for this.

---

## File structure

```
/
├── index.html       # the entire app (HTML + CSS + JS inline)
├── CLAUDE.md        # this file
└── README.md        # user-facing readme
```

Future:
```
/exporter/
├── export.py        # Python exporter (planned)
├── requirements.txt
└── README.md
```

---

## Features shipped (marking tool)

- Load local video via file picker
- Load YouTube video by URL (handles `youtube.com/watch?v=`, `youtu.be/`, `shorts/`, `embed/`, or bare 11-char ID)
- Play/pause via button or `Space`
- Scrubber with clickable seek; marks rendered on the scrubber in team colors
- Hotkey-driven mark creation (see table below)
- Marker list in sidebar, sorted by timestamp, click to jump, click again to select
- Contextual arrow-key behavior: skip video if nothing selected, nudge selected marker if one is
- Team config: editable names, two color pickers per team, gradient toggle, live preview
- Live score bug overlay rendered on top of video, updates as playhead crosses marks
- Count-up animation + pulse when score increments (broadcast style)
- Score bug positioning: top-left, top-right, bottom-left, bottom-right
- Final score preview: `🏆 Final` button toggles a big centered final-score card
- Settings: pre-roll, post-roll, final-score duration, skip amount, nudge step
- JSON export with team config, settings, and all marks
- JSON import, with automatic prompt to reload the YouTube video if source matches
- Toast notifications with team-colored dots for visual confirmation of every action
- Undo (`Ctrl+Z` / `Cmd+Z`) removes the most recently added mark

---

## Hotkey reference

| Key | Action |
|---|---|
| `Space` | Play / pause |
| `←` / `→` | Skip ±5s (default) if no marker selected; nudge selected marker by ±0.1s (default) if one is |
| `E` | Team 1 three-pointer (+3) |
| `D` | Team 1 two-pointer (+2) |
| `R` | Team 2 three-pointer (+3) |
| `F` | Team 2 two-pointer (+2) |
| `T` | Plain marker (no team, 0 points) — for noting cool plays, assists, etc. |
| `Ctrl`/`Cmd` + `Z` | Undo last mark |
| `Esc` | Deselect current marker |
| `Del` / `Backspace` | Delete selected marker |

Skip amount and nudge step are adjustable in the Clip Settings panel.

Hotkeys are globally captured via `keydown` listener on `document`, but are ignored when focus is inside an `<input>`, `<textarea>`, or contentEditable element — so team-name typing doesn't accidentally fire them.

---

## JSON schema

Exported from the tool, consumed by the Python exporter:

```json
{
  "version": 1,
  "source": {
    "type": "youtube",
    "videoId": "abc123XYZ_0"
  },
  "teams": {
    "1": { "name": "Hawks",   "color1": "#E03A3E", "color2": "#8B0000", "gradient": true },
    "2": { "name": "Celtics", "color1": "#007A33", "color2": "#004D20", "gradient": true }
  },
  "config": {
    "preRoll": 4,
    "postRoll": 1,
    "finalDuration": 3,
    "skipAmount": 5,
    "nudgeStep": 0.1,
    "bugPosition": "top-left"
  },
  "marks": [
    { "t": 47.3,  "team": 2, "points": 3 },
    { "t": 62.1,  "team": 1, "points": 2 },
    { "t": 89.0,  "team": 0, "points": 0, "note": "great pass" }
  ],
  "createdAt": "2026-04-20T15:32:00.000Z"
}
```

Notes:
- `source.type` is `"local"` or `"youtube"`. For local, `videoId` is omitted.
- `team` is `1`, `2`, or `0` (plain mark, no team).
- `team: 0` marks always have `points: 0` and do not affect the score bug. They're for flagging moments the user wants to highlight without a basket.
- `t` is seconds from the start of the source video, to 3 decimal places.
- `marks` in the exported JSON are already sorted chronologically.
- `bugPosition` is one of `"top-left" | "top-right" | "bottom-left" | "bottom-right"`.

---

## Design decisions (decisions log)

**Hybrid architecture (browser + Python).** Considered a pure-browser export using `MediaRecorder` + Canvas — rejected because browser video encoding is slow, quality-limited, and chokes on long videos. Python + ffmpeg is the right tool for the output side.

**Contextual arrow keys instead of shift-modifiers.** User preferred "no selection = skip, selection = nudge" over "plain arrows skip, shift+arrows nudge." Feels more natural, fewer modifiers to remember, and there's no case where you want to skip while a marker is selected (because you're actively editing it).

**Count-up animation, not flash.** User specifically wanted broadcast-style roll-up (0 → 1 → 2 → 3 over ~400ms) with a subtle scale-pulse on the team row. Implemented via JS step interpolation, not CSS keyframes, so the digits hit the exact target value.

**Single HTML file, no build.** Makes GitHub Pages deployment trivial, makes the tool hackable without tooling, and the codebase is small enough it doesn't need modularization.

**Gradient toggle per team.** Broadcast score bugs almost always use gradients, but some users want flat colors. Default to gradient on, with a checkbox to disable — when off, `color2` is ignored.

**Four position options, not two.** User initially said "top left or bottom left." Added top-right and bottom-right because the UI cost was zero and different videos have different dead zones.

**Mark-only key (`T`).** Not for points — for tagging interesting non-scoring moments (nice assist, dunk that got fouled, a call to review later). These are included in the JSON but don't move the score bug.

**Undo is last-in, not time-ordered.** `Ctrl+Z` removes the most recently *added* mark, not the latest in time. This matches intuition — if you mis-tapped a key, you want to undo that specific tap regardless of where in the timeline it landed.

---

## Known gotchas

- **`file://` breaks YouTube.** Covered above. Always serve over HTTP for YouTube; local files are fine either way.
- **YouTube duration is sometimes 0 on initial load** until the user starts playback. The tool polls `getDuration()` every 100ms and updates the scrubber/total-time display when a real value comes back.
- **YouTube IFrame doesn't have `timeupdate`**, so we poll `getCurrentTime()` at 10Hz for the same reason. Local videos use the native `timeupdate` event.
- **Mobile browsers are not yet tested.** The layout has a responsive breakpoint at 900px but the hotkey-heavy workflow assumes a physical keyboard.
- **Color pickers on some browsers** don't fire `input` events until the picker closes. Accepted — live preview updates on close, which is fine.
- **Single undo, no redo.** Intentional for scope. If this becomes annoying, add a redo stack.

---

## Roadmap: Python exporter (not yet built)

The next tool to build. Specification:

### Inputs
- Path to a local video file (if `source.type === "youtube"` in the JSON, user must download it themselves with `yt-dlp` first and pass the local path)
- Path to a `markings.json` exported from the marking tool

### Behavior
1. Parse JSON; sort marks by `t`.
2. Compute running score at each scoring mark (carry forward from previous marks).
3. For each mark (scoring or plain):
   - Cut the clip `[t - preRoll, t + postRoll]` from the source video.
   - Render a PNG sequence (or a dynamic ffmpeg overlay) of the score bug. Before `t`, show the *previous* score. Starting at `t`, animate the scoring team's number counting up over ~400ms to the new value, with a scale-pulse matching the browser preview.
   - For `team: 0` marks, no score change; just render the static bug with current score through the whole clip.
   - Burn the score bug into the clip at the configured position.
4. Concatenate all clips in chronological order.
5. Append a final-score screen: big centered `Team1 Score — Team2 Score` card for `config.finalDuration` seconds.
6. Write `highlights.mp4`.

### Suggested implementation
- `moviepy` for clip cutting and concatenation (simpler than raw ffmpeg for this).
- `Pillow` (PIL) to render each score-bug frame as a PNG with the team gradient. Cache frames where the score isn't changing.
- ffmpeg on the PATH for encoding.
- CLI: `python export.py --video game.mp4 --marks markings.json --out highlights.mp4`
- Optional flags: `--resolution 1920x1080`, `--fps 30`, `--bug-scale 1.0`.

### Score bug rendering in Python — strategy
The exporter should match the browser preview visually. Options:
- **(Preferred)** Render the bug with PIL at the configured resolution: rounded rectangles, linear gradients (use `PIL.Image.new` with a gradient paste), text with a good sans-serif fallback (DejaVu Sans, Inter, SF Pro if available). Composite onto each frame of the clip window around `t`.
- **(Backup)** Use ffmpeg `drawtext` + `drawbox` filters. Faster but no gradients, harder to animate count-up. Don't do this unless PIL gets too slow.

### Open questions for when we build the exporter
- Font: bundle a free font (e.g. Inter) in the exporter repo, or require system fonts?
- Count-up easing: linear or ease-out? Browser uses linear; ease-out might feel better in video.
- Team-color luminance check — if the user picks a very light team color, the white score text becomes unreadable. Auto-darken the text? Add outline? Leave it to the user?
- Encoding preset: `libx264 -preset medium -crf 20` is a reasonable default. Expose as a flag?

---

## Style guide

- CSS variables in `:root` for all colors. Don't hardcode hex in component styles.
- Functions are top-level, grouped by concern with comment banners (`// === STATE ===`, etc.).
- `state` is the single source of truth. All render functions derive from it. Don't read the DOM to compute state.
- Toasts for every user action that isn't visually self-evident — reinforces that the keypress was captured.
- Dark theme only. Users are watching video; a light UI would be jarring.

---

## Contributing / testing checklist

When changing the marking tool, verify:
- [ ] Local MP4 loads and plays
- [ ] YouTube URL loads and plays (test over `http://localhost` — `file://` will fail, that's expected)
- [ ] All hotkeys fire on a loaded video
- [ ] Hotkeys do NOT fire while typing in team-name input
- [ ] Arrow keys skip when nothing selected, nudge when a marker is selected
- [ ] Selected marker highlights, ESC deselects
- [ ] Score bug animates count-up and pulses when a scoring mark is crossed
- [ ] Scrubber marks render in correct team colors
- [ ] Click on scrubber seeks to that time
- [ ] Export JSON downloads a valid file matching the schema above
- [ ] Import JSON restores team config, settings, marks, and prompts for YouTube reload if applicable
- [ ] Final score preview shows correct totals
- [ ] Responsive layout doesn't break at narrow widths

---

## Conversation history summary (for future context)

This project originated from a single request describing the full vision: local or YouTube source, keyboard-driven marking with a specific hotkey map (E/D/R/F/T), contextual nudging, pre-roll/post-roll constants, customizable team score bug with gradients, JSON import/export, and a Python-based export step that marries the two. Architecture was proposed as a hybrid (browser tool + Python exporter) and accepted. Design questions resolved in the first exchange:

- Nudging: **contextual** — arrows always nudge the selected marker if one is selected, otherwise skip the video.
- Score animation: **broadcast-style count-up** with a scale-pulse.
- Build order: **marking tool first**, then Python exporter after the user has tested the marking flow with real games.

The marking tool was then built as a single-file HTML app. First attempt to load via `file://` hit the YouTube IFrame origin restriction; decision made to host on GitHub Pages to solve this permanently.

Next step: user tests the marking tool on a real game, exports a JSON, and that JSON becomes the spec for the Python exporter.
