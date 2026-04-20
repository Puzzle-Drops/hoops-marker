#!/usr/bin/env python3
"""
Hoops Highlight Exporter
========================
Takes a markings.json exported from the browser marking tool and a source
video, and produces a highlight reel with an animated broadcast-style
score bug overlay and a final-score screen.

Usage:
    python export.py --video game.mp4 --marks markings.json
    python export.py --youtube https://youtu.be/abc123 --marks markings.json
    python export.py --video game.mp4 --marks markings.json --out reel.mp4

See README.md for more.
"""

import argparse
import functools
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    from moviepy.editor import (
        VideoFileClip,
        ImageClip,
        concatenate_videoclips,
    )
except ImportError:
    print("ERROR: moviepy is not installed.")
    print("Install with:  pip install -r requirements.txt")
    sys.exit(1)


# =============================================================================
# CONSTANTS — tweak if you want a different look/feel
# =============================================================================

ANIMATION_DURATION = 0.4   # seconds for the count-up
PULSE_DURATION = 0.5       # seconds the pulse/brightness lasts after a basket
BUG_MARGIN = 20            # pixels from edge of frame
FINAL_BG_COLOR = (8, 10, 18)


# =============================================================================
# COLOR / FONT UTILITIES
# =============================================================================

def hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    h = hex_color.lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def make_gradient(width: int, height: int,
                  color1: Tuple[int, int, int],
                  color2: Tuple[int, int, int]) -> Image.Image:
    """Diagonal (top-left → bottom-right) gradient matching the CSS 135deg."""
    if width <= 0 or height <= 0:
        return Image.new('RGB', (max(1, width), max(1, height)), color1)
    c1 = np.array(color1, dtype=np.float32)
    c2 = np.array(color2, dtype=np.float32)
    y, x = np.mgrid[0:height, 0:width].astype(np.float32)
    denom = max(1, (width - 1) + (height - 1))
    t = ((x + y) / denom).clip(0, 1)
    grad = c1[None, None, :] + (c2 - c1)[None, None, :] * t[:, :, None]
    return Image.fromarray(grad.astype(np.uint8), mode='RGB')


@functools.lru_cache(maxsize=8)
def load_font(size: int) -> ImageFont.ImageFont:
    """Try several common bold sans-serif fonts; fall back to default."""
    candidates = [
        # bundled fallbacks (if user drops an Inter-Bold.ttf next to script)
        str(Path(__file__).parent / 'Inter-Bold.ttf'),
        str(Path(__file__).parent / 'fonts' / 'Inter-Bold.ttf'),
        # Linux
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
        # macOS
        '/System/Library/Fonts/Helvetica.ttc',
        '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
        '/Library/Fonts/Arial Bold.ttf',
        # Windows
        r'C:\Windows\Fonts\arialbd.ttf',
        r'C:\Windows\Fonts\segoeuib.ttf',
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def brighten(img: Image.Image, factor: float) -> Image.Image:
    """Brighten an RGB image by multiplying pixel values. factor=0 → no change."""
    if factor <= 0:
        return img
    arr = np.array(img, dtype=np.float32)
    arr = arr * (1 + factor * 0.35) + factor * 30
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8), mode=img.mode)


# =============================================================================
# SCORE BUG RENDERING
# =============================================================================

def render_score_bug(score: Dict[int, int],
                     teams: Dict,
                     bug_scale: float = 1.0,
                     highlight_team: Optional[int] = None,
                     highlight_intensity: float = 0.0) -> Image.Image:
    """Render the two-row score bug as an RGBA PIL image with rounded corners."""
    width = int(230 * bug_scale)
    row_h = int(40 * bug_scale)
    radius = int(8 * bug_scale)
    pad_x = int(14 * bug_scale)
    name_size = max(10, int(17 * bug_scale))
    score_size = max(12, int(20 * bug_scale))

    total_h = row_h * 2
    canvas = Image.new('RGBA', (width, total_h), (0, 0, 0, 0))

    name_font = load_font(name_size)
    score_font = load_font(score_size)

    for i, team_num in enumerate((1, 2)):
        team = teams.get(str(team_num), teams.get(team_num, {}))
        c1 = hex_to_rgb(team.get('color1', '#888888'))
        c2 = hex_to_rgb(team.get('color2', team.get('color1', '#888888')))
        use_gradient = bool(team.get('gradient', True))

        # Row background
        if use_gradient:
            row_bg = make_gradient(width, row_h, c1, c2)
        else:
            row_bg = Image.new('RGB', (width, row_h), c1)

        # Apply highlight/pulse on the scoring row
        if highlight_team == team_num and highlight_intensity > 0:
            row_bg = brighten(row_bg, highlight_intensity)

        canvas.paste(row_bg.convert('RGBA'), (0, i * row_h))

        # Draw text
        draw = ImageDraw.Draw(canvas)
        name = (team.get('name') or f'Team {team_num}').upper()
        score_text = str(int(score.get(team_num, 0)))

        # Team name (left)
        name_bbox = draw.textbbox((0, 0), name, font=name_font)
        name_h = name_bbox[3] - name_bbox[1]
        y_text = i * row_h + (row_h - name_h) // 2 - name_bbox[1]
        _draw_text_with_shadow(draw, (pad_x, y_text), name, name_font)

        # Score (right)
        score_bbox = draw.textbbox((0, 0), score_text, font=score_font)
        score_w = score_bbox[2] - score_bbox[0]
        score_h = score_bbox[3] - score_bbox[1]
        x_score = width - pad_x - score_w
        y_score = i * row_h + (row_h - score_h) // 2 - score_bbox[1]
        _draw_text_with_shadow(draw, (x_score, y_score), score_text, score_font)

    # Round corners by masking
    mask = Image.new('L', (width, total_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [(0, 0), (width - 1, total_h - 1)], radius=radius, fill=255
    )
    out = Image.new('RGBA', (width, total_h), (0, 0, 0, 0))
    out.paste(canvas, (0, 0), mask)

    # Drop shadow underneath (cheap: darken a slightly offset mask)
    shadow = Image.new('RGBA', (width + 8, total_h + 8), (0, 0, 0, 0))
    sh_mask = Image.new('L', (width, total_h), 0)
    ImageDraw.Draw(sh_mask).rounded_rectangle(
        [(0, 0), (width - 1, total_h - 1)], radius=radius, fill=110
    )
    shadow.paste((0, 0, 0, 110), (4, 4), sh_mask)
    shadow.alpha_composite(out, (0, 0))
    return shadow


def _draw_text_with_shadow(draw: ImageDraw.ImageDraw,
                           xy: Tuple[int, int],
                           text: str,
                           font: ImageFont.ImageFont) -> None:
    """Draw white text with a soft 1px shadow for readability."""
    x, y = xy
    draw.text((x + 1, y + 1), text, fill=(0, 0, 0, 140), font=font)
    draw.text((x, y), text, fill=(255, 255, 255, 255), font=font)


def bug_xy(position: str, frame_size: Tuple[int, int],
           bug_size: Tuple[int, int]) -> Tuple[int, int]:
    fw, fh = frame_size
    bw, bh = bug_size
    m = BUG_MARGIN
    return {
        'top-left':     (m, m),
        'top-right':    (fw - bw - m, m),
        'bottom-left':  (m, fh - bh - m),
        'bottom-right': (fw - bw - m, fh - bh - m),
    }.get(position, (m, m))


# =============================================================================
# SCORE LOGIC
# =============================================================================

def compute_running_scores(marks: List[Dict]) -> List[Dict]:
    """Return a list of {mark, prev, new} in chronological order."""
    score = {1: 0, 2: 0}
    out = []
    for m in marks:
        prev = dict(score)
        if m.get('team') in (1, 2) and m.get('points', 0):
            score[m['team']] += int(m['points'])
        out.append({'mark': m, 'prev': prev, 'new': dict(score)})
    return out


def final_totals(marks: List[Dict]) -> Dict[int, int]:
    s = {1: 0, 2: 0}
    for m in marks:
        if m.get('team') in (1, 2) and m.get('points', 0):
            s[m['team']] += int(m['points'])
    return s


# =============================================================================
# CLIP GENERATION
# =============================================================================

def make_highlight_clip(source: VideoFileClip,
                        entry: Dict,
                        config: Dict,
                        teams: Dict,
                        bug_scale: float = 1.0):
    """Cut a sub-clip around a mark and overlay the animated score bug."""
    m = entry['mark']
    t_basket = float(m['t'])
    pre = float(config.get('preRoll', 4))
    post = float(config.get('postRoll', 1))

    start = max(0.0, t_basket - pre)
    end = min(source.duration, t_basket + post)
    if end <= start:
        return None
    sub = source.subclip(start, end)

    scoring_team = m['team'] if m.get('team') in (1, 2) else None
    prev_s = entry['prev']
    new_s = entry['new']
    position = config.get('bugPosition', 'top-left')

    # Small LRU-backed cache so we don't re-render the static-bug frame 300x
    cache: Dict[Tuple, Image.Image] = {}

    def get_bug(score_tuple, highlight_t, intensity):
        key = (score_tuple, highlight_t, round(intensity, 2))
        img = cache.get(key)
        if img is None:
            score_dict = {1: score_tuple[0], 2: score_tuple[1]}
            img = render_score_bug(
                score_dict, teams, bug_scale=bug_scale,
                highlight_team=highlight_t, highlight_intensity=intensity,
            )
            cache[key] = img
        return img

    def transform(get_frame, clip_t):
        global_t = start + clip_t
        dt = global_t - t_basket  # seconds since the basket happened

        # Determine displayed score
        if dt < 0 or scoring_team is None:
            score = dict(prev_s)
            intensity = 0.0
            highlight_t = None
        elif dt < ANIMATION_DURATION:
            progress = dt / ANIMATION_DURATION
            diff = new_s[scoring_team] - prev_s[scoring_team]
            interp = int(round(prev_s[scoring_team] + diff * progress))
            score = dict(prev_s)
            score[scoring_team] = interp
            intensity = _pulse_curve(dt)
            highlight_t = scoring_team
        elif dt < PULSE_DURATION:
            score = dict(new_s)
            intensity = _pulse_curve(dt)
            highlight_t = scoring_team
        else:
            score = dict(new_s)
            intensity = 0.0
            highlight_t = None

        # Handle "mark" (team=0) — no score change, no highlight, just carry current
        if m.get('team') == 0:
            score = dict(prev_s)  # == new_s anyway for team=0
            intensity = 0.0
            highlight_t = None

        bug = get_bug((score[1], score[2]), highlight_t, intensity)

        frame = get_frame(clip_t)  # numpy (h, w, 3), uint8
        pil_frame = Image.fromarray(frame).convert('RGBA')
        pos = bug_xy(position, pil_frame.size, bug.size)
        pil_frame.alpha_composite(bug, dest=pos)
        return np.array(pil_frame.convert('RGB'))

    return sub.fl(transform, apply_to=[])


def _pulse_curve(dt: float) -> float:
    """0 → peak around 0.12s → back to 0 by PULSE_DURATION."""
    if dt < 0 or dt >= PULSE_DURATION:
        return 0.0
    peak = 0.12
    if dt <= peak:
        return dt / peak
    return max(0.0, 1.0 - (dt - peak) / (PULSE_DURATION - peak))


# =============================================================================
# FINAL SCORE SCREEN
# =============================================================================

def make_final_screen(teams: Dict, score: Dict[int, int],
                      duration: float, resolution: Tuple[int, int],
                      bug_scale: float = 1.0):
    """Big centered Team1 — Team2 card at the end of the reel."""
    w, h = resolution
    img = Image.new('RGB', (w, h), FINAL_BG_COLOR)
    draw = ImageDraw.Draw(img)

    name_size = max(20, int(h * 0.045))
    score_size = max(60, int(h * 0.22))
    dash_size = max(40, int(h * 0.13))

    card_w = int(w * 0.26)
    card_h = int(h * 0.48)
    gap = int(w * 0.06)
    total_w = card_w * 2 + gap * 2
    start_x = (w - total_w) // 2
    card_y = (h - card_h) // 2

    name_font = load_font(name_size)
    score_font = load_font(score_size)
    dash_font = load_font(dash_size)

    for i, team_num in enumerate((1, 2)):
        team = teams.get(str(team_num), teams.get(team_num, {}))
        c1 = hex_to_rgb(team.get('color1', '#888'))
        c2 = hex_to_rgb(team.get('color2', team.get('color1', '#888')))
        x = start_x + i * (card_w + gap * 2)

        # Card bg
        if team.get('gradient', True):
            bg = make_gradient(card_w, card_h, c1, c2)
        else:
            bg = Image.new('RGB', (card_w, card_h), c1)

        mask = Image.new('L', (card_w, card_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [(0, 0), (card_w - 1, card_h - 1)], radius=22, fill=255
        )
        img.paste(bg, (x, card_y), mask)

        # Text
        name = (team.get('name') or f'Team {team_num}').upper()
        score_text = str(int(score.get(team_num, 0)))

        name_bbox = draw.textbbox((0, 0), name, font=name_font)
        name_w = name_bbox[2] - name_bbox[0]
        draw.text(
            (x + (card_w - name_w) // 2, card_y + int(card_h * 0.14)),
            name, fill=(255, 255, 255), font=name_font,
        )

        score_bbox = draw.textbbox((0, 0), score_text, font=score_font)
        sw = score_bbox[2] - score_bbox[0]
        sh = score_bbox[3] - score_bbox[1]
        sy = card_y + int(card_h * 0.38)
        draw.text(
            (x + (card_w - sw) // 2, sy - score_bbox[1]),
            score_text, fill=(255, 255, 255), font=score_font,
        )

    # Dash in the middle
    dash = '—'
    dash_bbox = draw.textbbox((0, 0), dash, font=dash_font)
    dw = dash_bbox[2] - dash_bbox[0]
    dh = dash_bbox[3] - dash_bbox[1]
    draw.text(
        ((w - dw) // 2, (h - dh) // 2 - dash_bbox[1]),
        dash, fill=(140, 140, 150), font=dash_font,
    )

    return ImageClip(np.array(img)).set_duration(duration)


# =============================================================================
# YOUTUBE DOWNLOAD (optional)
# =============================================================================

def _find_ffmpeg() -> Optional[str]:
    """Locate an ffmpeg binary. Prefers the one bundled with imageio-ffmpeg
    (installed automatically as a moviepy dependency), then falls back to PATH."""
    # 1. imageio-ffmpeg's bundled binary
    try:
        import imageio_ffmpeg
        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and os.path.exists(path):
            return path
    except Exception:
        pass
    # 2. On PATH
    import shutil
    path = shutil.which("ffmpeg")
    if path:
        return path
    return None


def download_youtube(url: str, out_path: Optional[str] = None) -> str:
    try:
        import yt_dlp
    except ImportError:
        print("\nERROR: yt-dlp is not installed but --youtube was provided.")
        print("  Install with:  pip install yt-dlp")
        print("  Or download the video yourself and use --video PATH instead.\n")
        sys.exit(1)

    import tempfile
    import shutil as _shutil

    # Default download location is the OS temp directory. The current working
    # directory can be read-only or sync-locked (OneDrive on Windows intercepts
    # writes to Documents\, Desktop\, etc., which breaks yt-dlp's .part files).
    if out_path is None:
        out_path = os.path.join(tempfile.gettempdir(), 'hoops_yt_source.mp4')
    out_path = os.path.abspath(out_path)
    out_dir = os.path.dirname(out_path) or os.getcwd()
    os.makedirs(out_dir, exist_ok=True)

    # Scrub any stale .part files from a previous failed attempt — they can
    # still be locked by a killed process and will block a fresh download.
    stem = Path(out_path).stem
    for stale in Path(out_dir).glob(f"{stem}*.part"):
        try:
            stale.unlink()
        except OSError:
            pass
    # Also remove any existing final file so the new download isn't skipped
    if os.path.exists(out_path):
        try:
            os.remove(out_path)
        except OSError:
            pass

    ffmpeg_path = _find_ffmpeg()

    print(f"\nDownloading YouTube video:\n  {url}\n  → {out_path}")
    if ffmpeg_path:
        print(f"  Using ffmpeg at: {ffmpeg_path}")
        fmt = 'bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best'
    else:
        print("  WARNING: ffmpeg not found — requesting pre-merged format only "
              "(quality may be capped at 720p).")
        fmt = 'best[ext=mp4]/best'

    ydl_opts = {
        'format': fmt,
        'outtmpl': out_path,
        # `paths` tells yt-dlp where intermediate/tmp/part files go. Pinning
        # all of them to out_dir avoids the default-to-cwd surprise.
        'paths': {'home': out_dir, 'temp': out_dir},
        'merge_output_format': 'mp4',
        'quiet': False,
        'no_warnings': False,
        'file_access_retries': 5,
        'retries': 3,
    }
    if ffmpeg_path:
        ydl_opts['ffmpeg_location'] = ffmpeg_path

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    # yt-dlp may append .mp4 again or use a different name; resolve if needed
    if not os.path.exists(out_path):
        candidates = list(Path(out_dir).glob(f"{stem}*.mp4"))
        if candidates:
            out_path = str(candidates[0])
    return out_path


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Basketball highlight reel exporter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python export.py --video game.mp4 --marks markings.json
  python export.py --video game.mp4 --marks markings.json --out reel.mp4 --bug-scale 1.2
  python export.py --youtube https://youtu.be/abc --marks markings.json
""",
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument('--video', help='Path to local video file')
    src.add_argument('--youtube', help='YouTube URL (requires yt-dlp)')
    parser.add_argument('--marks', required=True, help='Path to markings JSON')
    parser.add_argument('--out', default='highlights.mp4', help='Output file (default: highlights.mp4)')
    parser.add_argument('--fps', type=int, default=30, help='Output FPS (default: 30)')
    parser.add_argument('--bug-scale', type=float, default=1.0, help='Score bug size multiplier (default: 1.0)')
    parser.add_argument('--preset', default='medium',
                        help='ffmpeg preset: ultrafast/fast/medium/slow (default: medium)')
    parser.add_argument('--crf', type=int, default=20, help='ffmpeg CRF quality 0-51, lower=better (default: 20)')
    parser.add_argument('--keep-download', action='store_true',
                        help='Keep the downloaded YouTube file after export')
    args = parser.parse_args()

    # Resolve video path
    if args.youtube:
        video_path = download_youtube(args.youtube)
        downloaded = True
    else:
        video_path = args.video
        downloaded = False

    if not os.path.exists(video_path):
        print(f"ERROR: video not found: {video_path}")
        sys.exit(1)

    # Load marks
    with open(args.marks, 'r', encoding='utf-8') as f:
        data = json.load(f)

    marks = sorted(data.get('marks', []), key=lambda m: float(m['t']))
    if not marks:
        print("ERROR: no marks in JSON — nothing to export.")
        sys.exit(1)

    teams = data.get('teams', {
        '1': {'name': 'Team 1', 'color1': '#E03A3E', 'color2': '#8B0000', 'gradient': True},
        '2': {'name': 'Team 2', 'color1': '#007A33', 'color2': '#004D20', 'gradient': True},
    })
    config = data.get('config', {})

    # Optional sanity check for YouTube source/video mismatch
    src_info = data.get('source', {})
    if src_info.get('type') == 'youtube' and not args.youtube and src_info.get('videoId'):
        print(f"NOTE: This JSON was created against YouTube video '{src_info['videoId']}'.")
        print(f"      Make sure '{video_path}' is the same video, or timestamps will be off.\n")

    # Load source video
    print(f"Loading video: {video_path}")
    source = VideoFileClip(video_path)
    print(f"  Duration: {source.duration:.1f}s · Size: {source.size[0]}x{source.size[1]} · FPS: {source.fps:.1f}")

    # Build clips
    entries = compute_running_scores(marks)
    clips = []
    for i, entry in enumerate(entries):
        mk = entry['mark']
        label = (
            f"+{mk['points']} T{mk['team']}" if mk.get('team') in (1, 2)
            else "MARK"
        )
        print(f"  [{i+1}/{len(entries)}]  t={mk['t']:.2f}s  {label}  "
              f"→ score after: {entry['new'][1]}–{entry['new'][2]}")
        clip = make_highlight_clip(source, entry, config, teams, bug_scale=args.bug_scale)
        if clip is not None:
            clips.append(clip)

    # Final score screen
    final_dur = float(config.get('finalDuration', 3))
    if final_dur > 0:
        print("  [final] final-score screen")
        totals = final_totals(marks)
        clips.append(make_final_screen(teams, totals, final_dur, source.size, args.bug_scale))

    # Concatenate + render
    print("\nConcatenating clips...")
    final = concatenate_videoclips(clips, method='compose')

    print(f"Rendering → {args.out}")
    final.write_videofile(
        args.out,
        fps=args.fps,
        codec='libx264',
        audio_codec='aac',
        preset=args.preset,
        ffmpeg_params=['-crf', str(args.crf), '-pix_fmt', 'yuv420p'],
        threads=max(2, (os.cpu_count() or 4) - 1),
    )

    # Cleanup
    try:
        source.close()
        for c in clips:
            try:
                c.close()
            except Exception:
                pass
    except Exception:
        pass

    if downloaded and not args.keep_download:
        try:
            os.remove(video_path)
            print(f"Cleaned up downloaded file: {video_path}")
        except OSError:
            pass

    print(f"\nDone. → {args.out}")


if __name__ == '__main__':
    main()
