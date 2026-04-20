#!/usr/bin/env python3
"""
🏀 Hoops Highlight Exporter — GUI

Double-click this file to launch the app. On Windows, rename to `gui.pyw`
if you want it to open without a console window.

Requires `export.py` and `requirements.txt` installed in the same folder.
"""

import json
import os
import queue
import sys
import threading
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk


# ---------------------------------------------------------------------------
# Import the processing functions from export.py. Do this lazily with a
# friendly dialog if imports fail (missing deps, missing file, etc.)
# ---------------------------------------------------------------------------

IMPORT_ERROR = None
try:
    HERE = Path(__file__).resolve().parent
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    from export import (  # noqa: E402
        compute_running_scores,
        build_chunks,
        download_youtube,
        final_totals,
        make_final_screen,
        make_pre_game_screen,
        make_chunk_clip,
        make_highlight_clip,
        load_teams_config,
        auto_find_teams_config,
        enrich_teams_from_registry,
    )
    from moviepy.editor import (  # noqa: E402
        VideoFileClip,
        concatenate_videoclips,
    )
except Exception as e:
    IMPORT_ERROR = f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}"


# ---------------------------------------------------------------------------
# Stdout capture so print() calls from export.py show up in the log panel
# ---------------------------------------------------------------------------

class StdoutCapture:
    """Routes writes into a queue, tagging lines ending in \\n vs \\r so the
    UI can either append or overwrite-in-place (for progress bars)."""

    def __init__(self, q: queue.Queue, stream_name: str = "out"):
        self.q = q
        self._pending = ""
        self.stream_name = stream_name
        # Attributes yt-dlp / tqdm / etc probe for. We don't provide a `.buffer`
        # (binary sub-stream) on purpose — that attribute name makes them think
        # we're a real TextIOWrapper and they try to write bytes to it.
        self.encoding = "utf-8"
        self.errors = "replace"

    def write(self, text: str) -> int:
        if not isinstance(text, str):
            try:
                text = text.decode(self.encoding, self.errors)
            except Exception:
                text = str(text)
        self._pending += text
        while True:
            nl = self._pending.find("\n")
            cr = self._pending.find("\r")
            if nl == -1 and cr == -1:
                break
            if nl == -1:
                idx, kind = cr, "r"
            elif cr == -1:
                idx, kind = nl, "n"
            else:
                if cr < nl:
                    idx, kind = cr, "r"
                else:
                    idx, kind = nl, "n"
            line = self._pending[:idx]
            self._pending = self._pending[idx + 1 :]
            self.q.put((kind, line))
        return len(text)

    def flush(self) -> None:
        if self._pending:
            self.q.put(("n", self._pending))
            self._pending = ""

    # Some libraries check these; returning sensible values keeps them happy.
    def isatty(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    def readable(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

PRESETS = [
    "ultrafast", "superfast", "veryfast", "faster", "fast",
    "medium", "slow", "slower", "veryslow",
]


class ExporterApp:

    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("🏀 Hoops Highlight Exporter")
        root.geometry("780x860")
        root.minsize(640, 680)

        # Apply a reasonable theme
        style = ttk.Style()
        if sys.platform.startswith("linux") and "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Accent.TButton", font=("", 11, "bold"), padding=10)
        style.configure("Section.TLabelframe.Label", font=("", 10, "bold"))

        # ---- State vars ----
        self.source_type = tk.StringVar(value="local")
        self.local_path = tk.StringVar()
        self.youtube_url = tk.StringVar()
        self.marks_path = tk.StringVar()
        self.output_path = tk.StringVar(value=str(self._default_output_dir() / "highlights.mp4"))

        self.pre_roll = tk.StringVar(value="4.0")
        self.post_roll = tk.StringVar(value="1.0")
        self.pre_game_duration = tk.StringVar(value="3.0")
        self.final_duration = tk.StringVar(value="3.0")
        self.bug_scale = tk.StringVar(value="1.5")

        self.fps = tk.StringVar(value="30")
        self.crf = tk.StringVar(value="18")
        self.preset = tk.StringVar(value="medium")
        self.keep_download = tk.BooleanVar(value=False)
        self.presets_status = tk.StringVar(value="Team presets: (detecting…)")

        # ---- Runtime state ----
        self.log_queue: queue.Queue = queue.Queue()
        self.worker_thread: threading.Thread | None = None
        self.running = False

        self._build_ui()
        self._toggle_source()
        self._refresh_presets_status()
        self.root.after(60, self._poll_log)

    # ----- UI construction --------------------------------------------

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)

        # --- Header ---
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text="🏀  Hoops Highlight Exporter",
                  font=("", 16, "bold")).pack(side="left")

        # --- Source section ---
        src = ttk.LabelFrame(outer, text="Video source", padding=10, style="Section.TLabelframe")
        src.pack(fill="x", pady=(0, 8))
        src.columnconfigure(1, weight=1)

        ttk.Radiobutton(src, text="Local file", value="local",
                        variable=self.source_type,
                        command=self._toggle_source).grid(row=0, column=0, sticky="w")
        self.local_entry = ttk.Entry(src, textvariable=self.local_path)
        self.local_entry.grid(row=0, column=1, sticky="ew", padx=8)
        self.local_btn = ttk.Button(src, text="Browse…", command=self._browse_local, width=12)
        self.local_btn.grid(row=0, column=2)

        ttk.Radiobutton(src, text="YouTube URL", value="youtube",
                        variable=self.source_type,
                        command=self._toggle_source).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.yt_entry = ttk.Entry(src, textvariable=self.youtube_url)
        self.yt_entry.grid(row=1, column=1, columnspan=2, sticky="ew", padx=8, pady=(6, 0))

        # --- Markings JSON ---
        jf = ttk.LabelFrame(outer, text="Markings JSON", padding=10, style="Section.TLabelframe")
        jf.pack(fill="x", pady=(0, 8))
        jf.columnconfigure(0, weight=1)
        ttk.Entry(jf, textvariable=self.marks_path).grid(row=0, column=0, sticky="ew")
        ttk.Button(jf, text="Browse…", command=self._browse_marks, width=12).grid(row=0, column=1, padx=(8, 0))
        ttk.Label(jf, text="Settings from the JSON will auto-fill the fields below.",
                  foreground="#666").grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # --- Output ---
        of = ttk.LabelFrame(outer, text="Output file", padding=10, style="Section.TLabelframe")
        of.pack(fill="x", pady=(0, 8))
        of.columnconfigure(0, weight=1)
        ttk.Entry(of, textvariable=self.output_path).grid(row=0, column=0, sticky="ew")
        ttk.Button(of, text="Save as…", command=self._browse_output, width=12).grid(row=0, column=1, padx=(8, 0))

        # --- Team presets status (auto-detected) ---
        tf = ttk.Frame(outer)
        tf.pack(fill="x", pady=(0, 8))
        ttk.Label(tf, textvariable=self.presets_status,
                  foreground="#666").pack(anchor="w")

        # --- Settings: two columns ---
        settings = ttk.Frame(outer)
        settings.pack(fill="x", pady=(0, 8))
        settings.columnconfigure(0, weight=1, uniform="col")
        settings.columnconfigure(1, weight=1, uniform="col")

        clip = ttk.LabelFrame(settings, text="Clip settings", padding=10, style="Section.TLabelframe")
        clip.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        clip.columnconfigure(1, weight=1)
        self._settings_row(clip, 0, "Pre-roll (sec)", self.pre_roll)
        self._settings_row(clip, 1, "Post-roll (sec)", self.post_roll)
        self._settings_row(clip, 2, "Pre-game screen (sec)", self.pre_game_duration)
        self._settings_row(clip, 3, "Final screen (sec)", self.final_duration)
        self._settings_row(clip, 4, "Score bug scale", self.bug_scale)

        render = ttk.LabelFrame(settings, text="Render settings", padding=10, style="Section.TLabelframe")
        render.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        render.columnconfigure(1, weight=1)
        self._settings_row(render, 0, "FPS", self.fps)
        self._settings_row(render, 1, "Quality (CRF)", self.crf,
                           hint="lower = better, 18 ≈ lossless, 28 = small")
        ttk.Label(render, text="Preset").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Combobox(render, textvariable=self.preset, values=PRESETS,
                     state="readonly", width=14).grid(row=2, column=1, sticky="w", padx=8, pady=3)
        ttk.Checkbutton(render, text="Keep downloaded YT file",
                        variable=self.keep_download).grid(row=3, column=0, columnspan=2,
                                                          sticky="w", pady=(6, 0))

        # --- Start button ---
        btn_frame = ttk.Frame(outer)
        btn_frame.pack(fill="x", pady=(4, 8))
        self.start_btn = ttk.Button(btn_frame, text="▶  Start Export",
                                    style="Accent.TButton",
                                    command=self._on_start)
        self.start_btn.pack(fill="x")

        # --- Progress bar ---
        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 6))

        # --- Log panel ---
        log_frame = ttk.LabelFrame(outer, text="Log", padding=6, style="Section.TLabelframe")
        log_frame.pack(fill="both", expand=True)

        log_toolbar = ttk.Frame(log_frame)
        log_toolbar.pack(fill="x", pady=(0, 4))
        ttk.Button(log_toolbar, text="📋 Copy log", command=self._copy_log, width=14
                   ).pack(side="left")
        ttk.Button(log_toolbar, text="🗑 Clear", command=self._clear_log, width=10
                   ).pack(side="left", padx=(6, 0))
        ttk.Label(log_toolbar, text="  (you can also select text and Ctrl+C)",
                  foreground="#888").pack(side="left")

        self.log = scrolledtext.ScrolledText(log_frame, height=10, font=("Consolas", 10),
                                             wrap="word",
                                             background="#111418", foreground="#d7dae0",
                                             insertbackground="#d7dae0",
                                             selectbackground="#3b82f6",
                                             selectforeground="#ffffff")
        self.log.pack(fill="both", expand=True)
        self.log.tag_configure("err", foreground="#ff7a7a")
        self.log.tag_configure("ok", foreground="#7ee787")
        self.log.tag_configure("dim", foreground="#888")

        # Make it read-only by blocking key input, but still allow selection + copy.
        def _block_edits(event):
            # Allow copy-related keys
            ctrl = (event.state & 0x4) != 0
            if ctrl and event.keysym.lower() in ("c", "a", "insert"):
                return None
            # Allow navigation keys
            if event.keysym in ("Left", "Right", "Up", "Down", "Home", "End",
                                "Prior", "Next", "Shift_L", "Shift_R",
                                "Control_L", "Control_R"):
                return None
            return "break"
        self.log.bind("<Key>", _block_edits)

        self._log_line("Ready. Fill in a source, a JSON file, and hit Start.", tag="dim")

    def _copy_log(self) -> None:
        try:
            text = self.log.get("1.0", "end-1c")
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()  # keep clipboard after window closes
        except tk.TclError:
            pass

    def _clear_log(self) -> None:
        self.log.delete("1.0", "end")

    def _settings_row(self, parent, row, label, var, hint=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        entry = ttk.Entry(parent, textvariable=var, width=10)
        entry.grid(row=row, column=1, sticky="w", padx=8, pady=3)
        if hint:
            ttk.Label(parent, text=hint, foreground="#888", font=("", 9)
                      ).grid(row=row, column=2, sticky="w")

    # ----- UI callbacks ----------------------------------------------

    def _toggle_source(self) -> None:
        if self.source_type.get() == "local":
            self.local_entry.state(["!disabled"])
            self.local_btn.state(["!disabled"])
            self.yt_entry.state(["disabled"])
        else:
            self.local_entry.state(["disabled"])
            self.local_btn.state(["disabled"])
            self.yt_entry.state(["!disabled"])

    def _browse_local(self) -> None:
        path = filedialog.askopenfilename(
            title="Select video file",
            filetypes=[("Video files", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v"),
                       ("All files", "*.*")],
        )
        if path:
            self.local_path.set(path)

    def _browse_marks(self) -> None:
        path = filedialog.askopenfilename(
            title="Select markings JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        self.marks_path.set(path)
        self._autoload_from_json(path)
        # Match the output filename to the JSON stem, inside <project>/vods/highlights/
        try:
            stem = Path(path).stem
            self.output_path.set(str(self._default_output_dir() / f"{stem}.mp4"))
        except Exception:
            pass
        self._refresh_presets_status()

    def _browse_output(self) -> None:
        default_name = Path(self.output_path.get()).name or "highlights.mp4"
        path = filedialog.asksaveasfilename(
            title="Save highlight reel as…",
            defaultextension=".mp4",
            initialfile=default_name,
            filetypes=[("MP4 video", "*.mp4"), ("All files", "*.*")],
        )
        if path:
            self.output_path.set(path)

    @staticmethod
    def _default_output_dir() -> Path:
        """`<project>/vods/highlights/` — project root is the parent of this
        exporter folder. Falls back to the user's Desktop if the path can't be
        resolved (e.g. the exporter is running from a weird working dir)."""
        try:
            return Path(__file__).resolve().parent.parent / "vods" / "highlights"
        except NameError:
            return Path.home() / "Desktop"

    def _refresh_presets_status(self) -> None:
        """Show the user whether teams.json was auto-detected, and from where."""
        # Prefer looking relative to a marks file if the user has picked one
        marks_hint = self.marks_path.get().strip() or None
        try:
            found = auto_find_teams_config(marks_hint)
        except Exception:
            found = None
        if found:
            # Shorten for display: show ".../assets/teams.json" relative-ish
            try:
                short = os.sep.join(Path(found).parts[-3:])
            except Exception:
                short = found
            self.presets_status.set(f"Team presets: auto-detected · {short}")
        else:
            self.presets_status.set(
                "Team presets: none found (put teams.json in ./assets next to this exporter)"
            )

    def _autoload_from_json(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self._log_line(f"Could not parse JSON: {e}", tag="err")
            return

        cfg = data.get("config", {}) or {}
        if "preRoll" in cfg:         self.pre_roll.set(str(cfg["preRoll"]))
        if "postRoll" in cfg:        self.post_roll.set(str(cfg["postRoll"]))
        if "preGameDuration" in cfg: self.pre_game_duration.set(str(cfg["preGameDuration"]))
        if "finalDuration" in cfg:   self.final_duration.set(str(cfg["finalDuration"]))

        # If it references a YouTube video and the URL field is empty, fill it
        src = data.get("source", {}) or {}
        if src.get("type") == "youtube" and src.get("videoId") and not self.youtube_url.get():
            self.youtube_url.set(f"https://youtu.be/{src['videoId']}")
            self.source_type.set("youtube")
            self._toggle_source()

        teams = data.get("teams", {}) or {}
        t1 = (teams.get("1") or {}).get("name", "Team 1")
        t2 = (teams.get("2") or {}).get("name", "Team 2")
        n_marks = len(data.get("marks", []) or [])
        self._log_line(f"Loaded {n_marks} marks · {t1} vs {t2} · settings applied", tag="ok")

    # ----- Validation + start ----------------------------------------

    def _collect_options(self) -> dict | None:
        opts = {}
        # Source
        if self.source_type.get() == "local":
            p = self.local_path.get().strip()
            if not p or not os.path.exists(p):
                messagebox.showerror("Missing video", "Pick a local video file.")
                return None
            opts["video"] = p
            opts["youtube"] = None
        else:
            url = self.youtube_url.get().strip()
            if not url:
                messagebox.showerror("Missing URL", "Paste a YouTube URL.")
                return None
            opts["video"] = None
            opts["youtube"] = url

        # Marks
        mp = self.marks_path.get().strip()
        if not mp or not os.path.exists(mp):
            messagebox.showerror("Missing JSON", "Pick a markings JSON file.")
            return None
        opts["marks"] = mp

        # Output
        out = self.output_path.get().strip()
        if not out:
            messagebox.showerror("Missing output", "Pick an output filename.")
            return None
        opts["out"] = out

        # Numeric
        try:
            opts["preRoll"] = float(self.pre_roll.get())
            opts["postRoll"] = float(self.post_roll.get())
            opts["preGameDuration"] = float(self.pre_game_duration.get())
            opts["finalDuration"] = float(self.final_duration.get())
            opts["bugScale"] = float(self.bug_scale.get())
            opts["fps"] = int(self.fps.get())
            opts["crf"] = int(self.crf.get())
        except ValueError as e:
            messagebox.showerror("Invalid number", f"Check the numeric fields.\n\n{e}")
            return None

        opts["preset"] = self.preset.get().strip() or "medium"
        opts["keepDownload"] = bool(self.keep_download.get())
        return opts

    def _on_start(self) -> None:
        if self.running:
            return
        opts = self._collect_options()
        if opts is None:
            return

        # Lock UI
        self.running = True
        self.start_btn.state(["disabled"])
        self.start_btn.configure(text="Rendering…")
        self.progress.start(12)

        # Clear log
        self.log.delete("1.0", "end")

        self.worker_thread = threading.Thread(
            target=self._run_export, args=(opts,), daemon=True
        )
        self.worker_thread.start()

    def _finish(self, success: bool, final_msg: str,
                actual_path: str | None = None) -> None:
        self.running = False
        self.start_btn.state(["!disabled"])
        self.start_btn.configure(text="▶  Start Export")
        self.progress.stop()
        if success:
            self._log_line(final_msg, tag="ok")
            try:
                reveal_path = actual_path or self.output_path.get()
                if messagebox.askyesno("Done", f"{final_msg}\n\nReveal the file?"):
                    self._reveal(reveal_path)
            except tk.TclError:
                pass
        else:
            self._log_line(final_msg, tag="err")
            try:
                messagebox.showerror("Export failed", final_msg)
            except tk.TclError:
                pass

    @staticmethod
    def _reveal(path: str) -> None:
        path = os.path.abspath(path)
        try:
            if sys.platform == "darwin":
                os.system(f'open -R "{path}"')
            elif sys.platform.startswith("win"):
                os.system(f'explorer /select,"{path}"')
            else:
                os.system(f'xdg-open "{os.path.dirname(path)}"')
        except Exception:
            pass

    # ----- The actual worker -----------------------------------------

    def _run_export(self, opts: dict) -> None:
        """Runs in a background thread. Mirrors what export.py's main() does
        but takes options from the GUI instead of argparse."""

        stdout_orig = sys.stdout
        stderr_orig = sys.stderr
        sys.stdout = StdoutCapture(self.log_queue, "out")
        sys.stderr = StdoutCapture(self.log_queue, "err")

        downloaded = False
        video_path = None
        source = None
        clips = []

        try:
            # 1. Resolve source
            if opts["youtube"]:
                video_path = download_youtube(opts["youtube"])
                downloaded = True
            else:
                video_path = opts["video"]

            if not os.path.exists(video_path):
                raise FileNotFoundError(f"Video not found: {video_path}")

            # 2. Load JSON
            with open(opts["marks"], "r", encoding="utf-8") as f:
                data = json.load(f)
            marks = sorted(data.get("marks", []), key=lambda m: float(m["t"]))
            if not marks:
                raise ValueError("No marks in JSON — nothing to export.")
            teams = data.get("teams") or {
                "1": {"name": "Team 1", "color1": "#E03A3E",
                      "color2": "#8B0000", "gradient": True},
                "2": {"name": "Team 2", "color1": "#007A33",
                      "color2": "#004D20", "gradient": True},
            }
            # Build config, letting GUI overrides win over JSON
            config = dict(data.get("config") or {})
            config["preRoll"] = opts["preRoll"]
            config["postRoll"] = opts["postRoll"]
            config["preGameDuration"] = opts["preGameDuration"]
            config["finalDuration"] = opts["finalDuration"]
            # bugPosition stays as whatever the JSON said (or default)
            config.setdefault("bugPosition", "top-left")

            # Load teams registry (logos + players) — auto-detected
            teams_config_path = auto_find_teams_config(opts["marks"])
            teams_registry = load_teams_config(teams_config_path) if teams_config_path else None
            if teams_registry:
                print(f"Using teams config: {teams_config_path}")
                enrich_teams_from_registry(teams, teams_registry)
            else:
                print("No teams.json found — exporting with basic team info only.")

            # 3. Load video
            print(f"Loading video: {video_path}")
            source = VideoFileClip(video_path)
            print(f"  Duration: {source.duration:.1f}s · "
                  f"Size: {source.size[0]}x{source.size[1]} · "
                  f"FPS: {source.fps:.1f}")

            # 4. Build clips
            entries = compute_running_scores(marks)
            chunks = build_chunks(entries, config, source.duration)

            # Pre-game intro
            if opts["preGameDuration"] > 0:
                print("  [pregame] pre-game intro screen")
                clips.append(make_pre_game_screen(
                    teams, opts["preGameDuration"],
                    source.size, opts["bugScale"],
                    teams_registry=teams_registry,
                ))

            merged_away = len(entries) - sum(len(c["events"]) == 1 for c in chunks)
            if merged_away:
                print(f"  Merged {merged_away} overlapping window(s) → {len(chunks)} clip(s)")
            for i, chunk in enumerate(chunks):
                labels = []
                for ent in chunk["events"]:
                    mk = ent["mark"]
                    labels.append(f"+{mk['points']} T{mk['team']}"
                                  if mk.get("team") in (1, 2) else "MARK")
                final_new = chunk["events"][-1]["new"]
                print(f"  [{i+1}/{len(chunks)}]  {chunk['start']:.2f}–{chunk['end']:.2f}s  "
                      f"({len(chunk['events'])} event{'s' if len(chunk['events'])>1 else ''}: "
                      f"{', '.join(labels)})  → {final_new[1]}–{final_new[2]}")
                clip = make_chunk_clip(
                    source, chunk, config, teams, bug_scale=opts["bugScale"]
                )
                if clip is not None:
                    clips.append(clip)

            # 5. Final screen
            if opts["finalDuration"] > 0:
                print("  [final] final-score screen")
                totals = final_totals(marks)
                clips.append(make_final_screen(
                    teams, totals, opts["finalDuration"],
                    source.size, opts["bugScale"],
                    teams_registry=teams_registry,
                ))

            # 6. Concatenate + write
            #
            # Rendering directly to the user's chosen output folder can fail
            # when that folder is OneDrive-synced (Windows Documents, Desktop,
            # Pictures on modern setups). OneDrive intercepts the file creation
            # and the temp-audio file moviepy needs ends up Permission Denied.
            # Workaround: render to the system temp dir, then move the finished
            # file to the real destination as a single atomic operation.
            import tempfile, shutil, uuid

            print("\nConcatenating clips…")
            final = concatenate_videoclips(clips, method="compose")

            tmp_dir = tempfile.gettempdir()
            render_id = uuid.uuid4().hex[:8]
            temp_render = os.path.join(tmp_dir, f"hoops_render_{render_id}.mp4")
            temp_audio = os.path.join(tmp_dir, f"hoops_audio_{render_id}.m4a")

            print(f"Rendering to temp: {temp_render}")
            final.write_videofile(
                temp_render,
                fps=opts["fps"],
                codec="libx264",
                audio_codec="aac",
                preset=opts["preset"],
                ffmpeg_params=["-crf", str(opts["crf"]), "-pix_fmt", "yuv420p"],
                threads=max(2, (os.cpu_count() or 4) - 1),
                temp_audiofile=temp_audio,
            )

            # Move to the user's chosen destination
            actual_out = opts["out"]
            try:
                out_dir = os.path.dirname(actual_out)
                if out_dir:
                    os.makedirs(out_dir, exist_ok=True)
                if os.path.exists(actual_out):
                    try:
                        os.remove(actual_out)
                    except OSError:
                        pass
                shutil.move(temp_render, actual_out)
                print(f"Saved: {actual_out}")
            except (OSError, PermissionError) as e:
                print(f"\nCould not move file to {actual_out}: {e}")
                print(f"The render finished, but your chosen folder is blocking the write")
                print(f"(usually this is OneDrive sync). Your video is here instead:")
                print(f"  {temp_render}")
                actual_out = temp_render

            # Cleanup stray temp audio (moviepy usually removes it, but belt+suspenders)
            for stray in (temp_audio,):
                try:
                    if os.path.exists(stray):
                        os.remove(stray)
                except OSError:
                    pass

            # Cleanup
            try:
                source.close()
            except Exception:
                pass
            for c in clips:
                try:
                    c.close()
                except Exception:
                    pass

            if downloaded and not opts["keepDownload"]:
                try:
                    os.remove(video_path)
                    print(f"Cleaned up downloaded file: {video_path}")
                except OSError:
                    pass

            print(f"\nDone → {actual_out}")
            self.root.after(0, self._finish, True,
                            f"Exported {len(entries)} clips to {actual_out}",
                            actual_out)

        except Exception as e:
            tb = traceback.format_exc()
            print(tb, file=sys.stderr)
            self.root.after(0, self._finish, False, f"{type(e).__name__}: {e}")

        finally:
            sys.stdout = stdout_orig
            sys.stderr = stderr_orig
            try:
                if source is not None:
                    source.close()
            except Exception:
                pass

    # ----- Log pump ---------------------------------------------------

    def _poll_log(self) -> None:
        try:
            while True:
                kind, line = self.log_queue.get_nowait()
                self._append_log(line, overwrite_last=(kind == "r"),
                                 tag="err" if "Error" in line or "error" in line else None)
        except queue.Empty:
            pass
        self.root.after(60, self._poll_log)

    def _log_line(self, line: str, tag: str | None = None) -> None:
        self._append_log(line, overwrite_last=False, tag=tag)

    def _append_log(self, line: str, overwrite_last: bool = False,
                    tag: str | None = None) -> None:
        if overwrite_last:
            try:
                last_start = self.log.index("end-2l linestart")
                self.log.delete(last_start, "end-1c")
                if self.log.index("end-1c") != "1.0":
                    self.log.insert("end", "\n")
            except tk.TclError:
                pass
        if tag:
            self.log.insert("end", line + "\n", tag)
        else:
            self.log.insert("end", line + "\n")
        self.log.see("end")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if IMPORT_ERROR:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Missing dependencies or export.py",
            "Couldn't load the exporter.\n\n"
            "Make sure `export.py` is in the same folder as this file, and\n"
            "that you've run:\n\n"
            "    pip install -r requirements.txt\n\n"
            f"Details:\n{IMPORT_ERROR}",
        )
        sys.exit(1)

    root = tk.Tk()
    ExporterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
