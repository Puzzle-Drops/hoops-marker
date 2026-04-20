# Hoops Highlight Exporter

The second half of the toolchain. Takes a `markings.json` exported from the [browser marking tool](../) and a source video, produces a highlight reel with an animated broadcast-style score bug, a **pre-game intro**, the highlight clips, and a **final-score** screen. Pre-game and final screens can show the league logo, team logos, and aspect-ratio-preserving player portraits when a `teams.json` registry is present.

## Install

```bash
# 1. Install ffmpeg (required) — pick one:
#    macOS:    brew install ffmpeg
#    Windows:  https://www.gyan.dev/ffmpeg/builds/  (add bin/ to PATH)
#    Linux:    sudo apt install ffmpeg

# 2. Install Python dependencies
pip install -r requirements.txt
```

Python 3.9+ recommended.

## Usage

### Local video file (simplest path)

```bash
python export.py --video game.mp4 --marks markings.json
```

Writes `highlights.mp4` in the current directory.

### YouTube video (one-command)

```bash
python export.py --youtube https://youtu.be/VIDEO_ID --marks markings.json
```

This uses `yt-dlp` to download the video first, then runs the export. The downloaded file is deleted afterward (pass `--keep-download` to keep it). Downloading is your call — the tool just wraps `yt-dlp`, which you could run yourself.

If you'd rather keep the steps separate:

```bash
yt-dlp -f "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best" -o game.mp4 https://youtu.be/VIDEO_ID
python export.py --video game.mp4 --marks markings.json
```

### All flags

| Flag | Default | Description |
|---|---|---|
| `--video PATH` | — | Local video file (mutually exclusive with `--youtube`) |
| `--youtube URL` | — | YouTube URL; downloads via yt-dlp (mutually exclusive with `--video`) |
| `--marks PATH` | — | **Required.** Path to `markings.json` from the browser tool |
| `--out PATH` | `highlights.mp4` | Output file |
| `--fps N` | `30` | Output frame rate |
| `--bug-scale F` | `1.5` | Score bug size multiplier. `1.0` for a smaller bug, `0.8` for 720p |
| `--preset P` | `medium` | ffmpeg x264 preset. `ultrafast`/`fast` = quicker but bigger, `slow` = smaller but slower |
| `--crf N` | `18` | ffmpeg CRF. 0 = lossless, 18 = visually lossless (default), 23 = ffmpeg default, 28 = smaller |
| `--keep-download` | off | Keep the downloaded YouTube file instead of deleting after export |

## How it works

1. Reads the JSON; sorts marks by timestamp; computes the running score across the game.
2. Optionally prepends a **pre-game intro** for `preGameDuration` seconds (default 3s) with league logo, team logos + names, player portraits, and a big "VS".
3. For each mark, cuts `[t − preRoll, t + postRoll]` from the source video.
4. Overlays a score bug onto every frame of that clip:
   - Before the basket moment: shows the *previous* score.
   - For `0.4s` starting at the basket: counts up from previous to new score.
   - For `0.5s` after the basket: the scoring row briefly brightens (pulse).
   - After that: shows the *new* score, static, until the clip ends.
   - For "mark-only" hits (`T` key, `team: 0`): no score change, bug stays static.
5. Concatenates all clips in chronological order.
6. Appends a **final-score** card for `finalDuration` seconds (default 3s) — same visual language as the intro with scores in place of VS.
7. Renders `highlights.mp4` via ffmpeg (libx264 + AAC, yuv420p for broad compatibility).

Pre-roll, post-roll, pre-game duration, final-score duration, team colors, and bug position all come from the JSON. Set any duration to `0` to skip that segment.

## Team registry (`teams.json`)

The exporter auto-detects `assets/teams.json` near the marks file or the exporter script. See [`assets/teams-README.md`](../assets/teams-README.md) for the full schema. When found, team logos/colors and player portraits get populated automatically — no per-game tweaking needed. When missing, the exporter falls back to whatever team info is in the JSON from the browser tool.

## Troubleshooting

**`ffmpeg` not found.** moviepy doesn't install ffmpeg for you. Install it via your OS package manager and make sure `ffmpeg -version` works in a terminal.

**Fonts look generic / wrong.** The script tries several common bold sans-serif fonts (DejaVu Sans Bold, Arial Bold, Segoe UI Bold, Helvetica). On minimal Linux or odd setups, it falls back to PIL's default bitmap font, which looks rough at large sizes. Fix: drop a font file named `Inter-Bold.ttf` (or any TTF) next to `export.py` — the script will find it.

**Rendering is slow.** Expected. Per-frame PIL compositing is the trade-off for getting the score bug to match the browser preview exactly. To speed things up:
- `--preset ultrafast` for a quick preview pass
- Lower `--fps` (e.g. 24 for cinematic, 25 for PAL) if source allows
- Shorter `preRoll` / `postRoll` in the JSON before exporting
- Fewer marks

**Timestamps are off by a few frames.** If you marked against a YouTube video and are exporting against a downloaded copy, they should match. If you're exporting against a re-encode (different duration), the offsets will drift. Re-mark against the exact file you're exporting.

**Output has black bars / wrong size.** moviepy preserves the source resolution. If you need a specific size, re-encode separately or add `.resize()` in the script.

**Audio is cut oddly at clip boundaries.** Each clip cut is a hard cut on both video and audio. If this sounds abrupt, we can add a short audio crossfade — open an issue or adjust `concatenate_videoclips` to use `method='chain'` with audio padding.

**`yt-dlp` fails on a video.** YouTube occasionally blocks downloads. Update yt-dlp (`pip install -U yt-dlp`) — it's in a cat-and-mouse game and the latest version usually works.

## What it doesn't do (yet)

- Player names / assists / game clock overlay — only team score bug and marks
- Music or crossfades between clips
- Chapter markers in the output
- Re-scoring (if you change a mark's points after export, re-run)
- Side-by-side comparison clips

Send feature requests / feedback.
