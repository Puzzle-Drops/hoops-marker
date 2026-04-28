#!/usr/bin/env python3
"""
🏀 Hoops Highlight Exporter — GUI

Double-click this file to launch the app. On Windows, rename to `gui.pyw`
if you want it to open without a console window.

Requires `export.py` and `requirements.txt` installed in the same folder.
"""

import hashlib
import json
import os
import queue
import re
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
        self.batch_status = tk.StringVar(value="")

        # Source of truth for selected JSON files. The marks_path Entry shows
        # a representation; this list is what _collect_options actually reads.
        self.marks_paths_list: list[str] = []

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
        ttk.Label(jf, text="Pick one — or hold Ctrl/Shift to select multiple for batch export. "
                          "Settings auto-fill from the first JSON.",
                  foreground="#666").grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(jf, textvariable=self.batch_status,
                  foreground="#7ee787").grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 0))

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
        paths = filedialog.askopenfilenames(
            title="Select markings JSON (Ctrl/Shift-click for multiple)",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not paths:
            return
        paths = list(paths)
        self.marks_paths_list = paths

        if len(paths) == 1:
            p = paths[0]
            self.marks_path.set(p)
            self.batch_status.set("")
            self._autoload_from_json(p)
            # Match output filename to the JSON stem, inside <project>/vods/highlights/
            try:
                self.output_path.set(str(self._default_output_dir() / f"{Path(p).stem}.mp4"))
            except Exception:
                pass
        else:
            names = [Path(p).name for p in paths]
            shown = ", ".join(names[:3])
            if len(names) > 3:
                shown += f", … (+{len(names) - 3} more)"
            self.marks_path.set(f"{len(paths)} files: {shown}")
            self.batch_status.set(
                f"Batch mode: {len(paths)} JSONs selected — each exports as "
                f"<output folder>/<jsonStem>.mp4. Source comes from each JSON."
            )
            # Autoload settings from the first JSON so the entry fields make sense.
            self._autoload_from_json(paths[0])
            # Output becomes a folder in batch mode.
            try:
                self.output_path.set(str(self._default_output_dir()))
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
    def _extract_youtube_id(url: str) -> str | None:
        """Pull an 11-char YouTube ID out of common URL shapes, or return None.
        Mirrors the browser tool: handles youtu.be/, watch?v=, shorts/, embed/, or a bare ID."""
        s = (url or "").strip()
        if not s:
            return None
        # Bare 11-char ID
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
            return s
        m = re.search(r"(?:youtu\.be/|v=|/shorts/|/embed/)([A-Za-z0-9_-]{11})", s)
        return m.group(1) if m else None

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
        # ---- Resolve which JSON files to process ----
        paths = list(self.marks_paths_list)
        if not paths:
            # No multi-select; fall back to whatever's in the entry (legacy / hand-typed).
            single = self.marks_path.get().strip()
            if single and os.path.exists(single):
                paths = [single]
        if not paths:
            messagebox.showerror("Missing JSON", "Pick a markings JSON file.")
            return None
        for p in paths:
            if not os.path.exists(p):
                messagebox.showerror("Missing JSON", f"JSON not found:\n{p}")
                return None

        is_batch = len(paths) > 1

        # ---- Output ----
        out_value = self.output_path.get().strip()
        if not out_value:
            messagebox.showerror("Missing output", "Pick an output path.")
            return None

        if is_batch:
            # Treat as a directory; if it points at a .mp4, take the parent.
            output_dir = (os.path.dirname(out_value)
                          if out_value.lower().endswith(".mp4") else out_value)
            output_dir = output_dir or "."
        else:
            output_dir = None  # single-job uses out_value as a file path verbatim

        # ---- Numeric ----
        try:
            preRoll = float(self.pre_roll.get())
            postRoll = float(self.post_roll.get())
            preGame = float(self.pre_game_duration.get())
            finalDur = float(self.final_duration.get())
            bugScale = float(self.bug_scale.get())
            fps = int(self.fps.get())
            crf = int(self.crf.get())
        except ValueError as e:
            messagebox.showerror("Invalid number", f"Check the numeric fields.\n\n{e}")
            return None

        # ---- Per-job source resolution ----
        # In single-job mode, the GUI's source picker wins (legacy behavior).
        # In batch mode, each JSON's own `source` field wins; the GUI source
        # is only used as a fallback when a JSON has no source info.
        gui_kind = self.source_type.get()
        gui_local = self.local_path.get().strip()
        gui_url = self.youtube_url.get().strip()

        def _from_gui():
            if gui_kind == "local":
                if not gui_local or not os.path.exists(gui_local):
                    return None, "Pick a local video file."
                return ("local", os.path.abspath(gui_local)), None
            url = gui_url
            if not url:
                return None, "Paste a YouTube URL."
            vid = self._extract_youtube_id(url)
            if vid:
                return ("youtube", vid), None
            # Couldn't parse an ID — pass the URL through as the cache key.
            return ("youtube_url", url), None

        jobs = []
        for p in paths:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                messagebox.showerror("Invalid JSON", f"{os.path.basename(p)}:\n{e}")
                return None

            if is_batch:
                src = data.get("source") or {}
                if src.get("type") == "youtube" and src.get("videoId"):
                    source = ("youtube", src["videoId"])
                elif src.get("type") == "local":
                    if not gui_local or not os.path.exists(gui_local):
                        messagebox.showerror(
                            "Missing video",
                            f"'{os.path.basename(p)}' has a local source. "
                            "Pick a local video file in the GUI — it'll be reused "
                            "for any local-source JSONs in the batch.",
                        )
                        return None
                    source = ("local", os.path.abspath(gui_local))
                else:
                    src_tuple, err = _from_gui()
                    if err:
                        messagebox.showerror(
                            "Missing source",
                            f"'{os.path.basename(p)}' has no source info — "
                            f"please fill in a source in the GUI.\n\n{err}",
                        )
                        return None
                    source = src_tuple
            else:
                src_tuple, err = _from_gui()
                if err:
                    messagebox.showerror("Missing source", err)
                    return None
                source = src_tuple

            if is_batch:
                out = os.path.join(output_dir, f"{Path(p).stem}.mp4")
            else:
                out = out_value
            jobs.append({"marks": p, "source": source, "out": out})

        return {
            "jobs": jobs,
            "preRoll": preRoll,
            "postRoll": postRoll,
            "preGameDuration": preGame,
            "finalDuration": finalDur,
            "bugScale": bugScale,
            "fps": fps,
            "crf": crf,
            "preset": self.preset.get().strip() or "medium",
            "keepDownload": bool(self.keep_download.get()),
        }

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
        """Runs in a background thread. Iterates over all selected JSON jobs,
        downloading each unique YouTube source only once and reusing the
        loaded VideoFileClip across jobs that share a source."""

        stdout_orig = sys.stdout
        stderr_orig = sys.stderr
        sys.stdout = StdoutCapture(self.log_queue, "out")
        sys.stderr = StdoutCapture(self.log_queue, "err")

        import tempfile

        # videoId -> downloaded local path. Survives across jobs in this run.
        yt_cache: dict[str, str] = {}
        downloaded_paths: list[str] = []

        # Currently-loaded source clip. Reused across consecutive jobs that
        # share a video file; reloaded only when the path actually changes.
        current_video_path: str | None = None
        current_source = None

        successes: list[str] = []
        failures: list[tuple[str, str]] = []

        try:
            jobs = opts["jobs"]
            n = len(jobs)
            print(f"Starting export of {n} job{'s' if n != 1 else ''}.")

            for i, job in enumerate(jobs):
                job_label = os.path.basename(job["marks"])
                print(f"\n{'=' * 60}")
                print(f"Job {i + 1}/{n}: {job_label}")
                print('=' * 60)

                try:
                    # ---- Resolve / download source ----
                    src_kind, src_key = job["source"]

                    if src_kind == "youtube":
                        if src_key in yt_cache:
                            video_path = yt_cache[src_key]
                            print(f"Reusing already-downloaded YouTube video "
                                  f"({src_key}) → {video_path}")
                        else:
                            url = f"https://youtu.be/{src_key}"
                            out_path = os.path.join(
                                tempfile.gettempdir(),
                                f"hoops_yt_{src_key}.mp4",
                            )
                            video_path = download_youtube(url, out_path=out_path)
                            yt_cache[src_key] = video_path
                            downloaded_paths.append(video_path)
                    elif src_kind == "youtube_url":
                        # We couldn't parse a videoId; key the cache by the URL string.
                        if src_key in yt_cache:
                            video_path = yt_cache[src_key]
                            print(f"Reusing already-downloaded YouTube video → {video_path}")
                        else:
                            slug = hashlib.md5(src_key.encode("utf-8")).hexdigest()[:10]
                            out_path = os.path.join(
                                tempfile.gettempdir(),
                                f"hoops_yt_{slug}.mp4",
                            )
                            video_path = download_youtube(src_key, out_path=out_path)
                            yt_cache[src_key] = video_path
                            downloaded_paths.append(video_path)
                    else:  # "local"
                        video_path = src_key

                    if not os.path.exists(video_path):
                        raise FileNotFoundError(f"Video not found: {video_path}")

                    # ---- Load (or reuse) the source clip ----
                    if current_video_path != video_path:
                        if current_source is not None:
                            try:
                                current_source.close()
                            except Exception:
                                pass
                            current_source = None
                            current_video_path = None
                        print(f"Loading video: {video_path}")
                        current_source = VideoFileClip(video_path)
                        current_video_path = video_path
                        print(f"  Duration: {current_source.duration:.1f}s · "
                              f"Size: {current_source.size[0]}x{current_source.size[1]} · "
                              f"FPS: {current_source.fps:.1f}")
                    else:
                        print(f"Reusing already-loaded source clip "
                              f"(same video as previous job).")

                    # ---- Render this job ----
                    actual_out = self._render_job(job, current_source, opts)
                    successes.append(actual_out)
                    print(f"\n[{i + 1}/{n}] Done → {actual_out}")

                except Exception as e:
                    tb = traceback.format_exc()
                    print(tb, file=sys.stderr)
                    failures.append((job_label, f"{type(e).__name__}: {e}"))

            # ---- Cleanup loaded source ----
            if current_source is not None:
                try:
                    current_source.close()
                except Exception:
                    pass
                current_source = None

            # ---- Cleanup downloaded YouTube files ----
            if not opts["keepDownload"]:
                for p in downloaded_paths:
                    try:
                        if os.path.exists(p):
                            os.remove(p)
                            print(f"Cleaned up downloaded file: {p}")
                    except OSError:
                        pass

            # ---- Final report ----
            ok = len(successes)
            err = len(failures)
            if err == 0:
                msg = (f"Exported {ok}/{n} reel{'s' if n != 1 else ''}." if n > 1
                       else f"Exported {successes[0]}")
                reveal = successes[0] if ok == 1 else (
                    os.path.dirname(successes[0]) if successes else None
                )
                self.root.after(0, self._finish, True, msg, reveal)
            else:
                lines = [f"{ok}/{n} succeeded, {err} failed:"]
                for name, emsg in failures:
                    lines.append(f"  • {name}: {emsg}")
                full = "\n".join(lines)
                print(f"\n{full}")
                reveal = successes[-1] if successes else None
                # Treat partial success as failure dialog so the user sees the list.
                self.root.after(0, self._finish, ok > 0 and err == 0, full, reveal)

        except Exception as e:
            tb = traceback.format_exc()
            print(tb, file=sys.stderr)
            self.root.after(0, self._finish, False, f"{type(e).__name__}: {e}")

        finally:
            sys.stdout = stdout_orig
            sys.stderr = stderr_orig
            try:
                if current_source is not None:
                    current_source.close()
            except Exception:
                pass

    def _render_job(self, job: dict, source, opts: dict) -> str:
        """Render one JSON's highlight reel against an already-loaded source clip.
        Returns the final output path actually written (which may differ from
        opts['out'] if the destination was unwritable and we kept it in temp)."""
        import shutil
        import tempfile
        import uuid

        marks_path = job["marks"]

        with open(marks_path, "r", encoding="utf-8") as f:
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
        config = dict(data.get("config") or {})
        config["preRoll"] = opts["preRoll"]
        config["postRoll"] = opts["postRoll"]
        config["preGameDuration"] = opts["preGameDuration"]
        config["finalDuration"] = opts["finalDuration"]
        config.setdefault("bugPosition", "top-left")

        teams_config_path = auto_find_teams_config(marks_path)
        teams_registry = load_teams_config(teams_config_path) if teams_config_path else None
        if teams_registry:
            print(f"Using teams config: {teams_config_path}")
            enrich_teams_from_registry(teams, teams_registry)
        else:
            print("No teams.json found — exporting with basic team info only.")

        clips: list = []
        try:
            entries = compute_running_scores(marks)
            chunks = build_chunks(entries, config, source.duration)

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
                print(f"  [{i + 1}/{len(chunks)}]  {chunk['start']:.2f}–{chunk['end']:.2f}s  "
                      f"({len(chunk['events'])} event{'s' if len(chunk['events']) > 1 else ''}: "
                      f"{', '.join(labels)})  → {final_new[1]}–{final_new[2]}")
                clip = make_chunk_clip(
                    source, chunk, config, teams, bug_scale=opts["bugScale"]
                )
                if clip is not None:
                    clips.append(clip)

            if opts["finalDuration"] > 0:
                print("  [final] final-score screen")
                totals = final_totals(marks)
                clips.append(make_final_screen(
                    teams, totals, opts["finalDuration"],
                    source.size, opts["bugScale"],
                    teams_registry=teams_registry,
                ))

            # Render to temp first to dodge OneDrive write interception, then move.
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

            actual_out = job["out"]
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

            for stray in (temp_audio,):
                try:
                    if os.path.exists(stray):
                        os.remove(stray)
                except OSError:
                    pass

            return actual_out
        finally:
            for c in clips:
                try:
                    c.close()
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
