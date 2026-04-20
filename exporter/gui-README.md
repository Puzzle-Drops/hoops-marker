# 🏀 Hoops Highlight Exporter — GUI

Point-and-click version of the exporter. Fill in the form, hit Start, walk away.

## First-time setup

You need these three things installed on your computer. All one-time:

**1. Python 3.9 or newer** — https://www.python.org/downloads/
- On Windows, during install **check the box that says "Add Python to PATH"** or nothing else will work.

**2. ffmpeg** — the video engine.
- **macOS:** `brew install ffmpeg` (install Homebrew first if you don't have it)
- **Windows:** download from https://www.gyan.dev/ffmpeg/builds/ (pick "release essentials"), unzip, and add the `bin\` folder to your PATH
- **Linux:** `sudo apt install ffmpeg`

**3. The Python packages.** Open a terminal/command prompt in the `exporter/` folder and run:
```
pip install -r requirements.txt
```

That's it. You only do this once.

## Launching the GUI

Files you should have in the same folder:
- `gui.py` — the app
- `export.py` — the processing engine
- `requirements.txt` — deps
- Platform launcher (one of these):
  - **Windows:** `Hoops Exporter.bat` — **double-click this**
  - **macOS:** `hoops-exporter.command` — first run: right-click → **Open** to get past Gatekeeper, then double-click thereafter
  - **Linux:** `hoops-exporter.command` — `chmod +x hoops-exporter.command`, then double-click

If the launcher doesn't work on your system, you can always run it from a terminal:
```
python gui.py
```

### macOS gatekeeper note

The first time you double-click `hoops-exporter.command`, macOS will refuse because it's unsigned. Workaround, one time only:
1. Right-click the file → **Open**
2. Click **Open** in the dialog that warns you
3. After that, normal double-click works

You may need to `chmod +x hoops-exporter.command` in a terminal first.

## Using the app

1. **Pick your video source** — radio button for Local file or YouTube URL.
2. **Pick your markings JSON** — the file you exported from the browser marking tool. When you select it, the app auto-fills the pre-roll / post-roll / final-screen values from the JSON. It will also auto-fill the YouTube URL if your JSON was marked against YouTube.
3. **Pick an output file** — defaults to `<project>/vods/highlights/<json-name>.mp4`. Picking a markings JSON auto-updates the output filename to match the JSON stem (e.g. `season1-game1-lakers-bucks.json` → `season1-game1-lakers-bucks.mp4`). `vods/` is gitignored so renders never end up in the repo.
4. **Tweak settings if you want:**
   - **Pre-roll / Post-roll** — seconds before/after each scoring moment to include
   - **Pre-game screen** — how long the intro "VS" card stays on screen (set 0 to skip it)
   - **Final screen** — how long the big final-score card stays on screen (set 0 to skip)
   - **Score bug scale** — 1.0 for 1080p source, try 1.3–1.5 for 4K, 0.7–0.8 for 720p
   - **FPS** — usually 30. Drop to 24 for a more cinematic feel if your source supports it.
   - **Quality (CRF)** — lower means better but bigger. 18 ≈ visually lossless, 20–23 is a good balance, 28 gets small fast.
   - **Preset** — trade render speed vs. file size. `ultrafast` for a quick preview, `medium` default, `slow` for best compression.
   - **Keep downloaded YT file** — by default the app deletes the YouTube download after export. Tick this to keep it.
   - **Team presets (auto-detected)** — a status line shows whether `teams.json` was found. Drop the file at `assets/teams.json` next to the exporter or next to your markings JSON and it gets picked up automatically. See [`assets/teams-README.md`](../assets/teams-README.md) for the schema.
5. **Hit Start Export.** Progress shows up in the log panel at the bottom. Go grab a coffee — rendering isn't instant.
6. When it finishes, a dialog asks if you want to reveal the file in Finder/Explorer.

## What if something goes wrong?

Everything prints to the log panel. Common issues:

- **"Could not load the exporter"** on startup → `export.py` isn't next to `gui.py`, or you haven't run `pip install -r requirements.txt`.
- **"ffmpeg not found"** during render → ffmpeg isn't on your PATH. Open a terminal and verify `ffmpeg -version` works before launching the GUI.
- **`yt-dlp` errors on YouTube download** → `pip install -U yt-dlp` to grab the latest version. YouTube changes things and yt-dlp is always playing catch-up.
- **Font looks ugly in the final video** → drop a TTF file named `Inter-Bold.ttf` into the exporter folder; the script will pick it up automatically.
- **Rendering is slow** → it is. Per-frame score-bug compositing is the price for matching the browser preview exactly. Use preset `ultrafast` + CRF 23 for faster drafts.

## Relationship to the CLI

The GUI is a friendlier wrapper around `export.py`. If you ever want the command-line version for scripting or batch jobs, `export.py` still works standalone — see the exporter README. The GUI uses the exact same processing code.
