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
# ASSET LOADING  (logos, player photos, team registry)
# =============================================================================

@functools.lru_cache(maxsize=64)
def _load_rgba_cached(path: str) -> Optional[Image.Image]:
    """Open an image as RGBA. Cached by absolute path."""
    try:
        return Image.open(path).convert('RGBA')
    except Exception:
        return None


def load_logo(path: Optional[str], target_height: int) -> Optional[Image.Image]:
    """Load an image and resize to the given height, preserving aspect ratio."""
    if not path or not os.path.exists(path) or target_height <= 0:
        return None
    img = _load_rgba_cached(path)
    if img is None:
        return None
    w, h = img.size
    if h <= 0:
        return None
    new_h = target_height
    new_w = max(1, int(round(w * (new_h / h))))
    return img.resize((new_w, new_h), Image.LANCZOS)


def load_teams_config(path: Optional[str]) -> Optional[Dict]:
    """Load a teams.json file and resolve asset paths to absolute ones."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Warning: could not parse teams config '{path}': {e}")
        return None

    base = Path(path).parent.resolve()

    def _resolve(p: Optional[str]) -> Optional[str]:
        if not p:
            return None
        pp = Path(p)
        return str(pp if pp.is_absolute() else base / pp)

    league = data.get('league') or {}
    if league.get('logo'):
        league['logo_abs'] = _resolve(league['logo'])

    for team in (data.get('teams') or []):
        if team.get('logo'):
            team['logo_abs'] = _resolve(team['logo'])

    for player in (data.get('players') or []):
        if player.get('photo'):
            player['photo_abs'] = _resolve(player['photo'])

    return data


def auto_find_teams_config(marks_path: Optional[str] = None) -> Optional[str]:
    """Look for teams.json near the markings file or the exporter script.
    Returns the first match found, or None."""
    candidates: List[Path] = []
    if marks_path:
        mp = Path(marks_path).resolve()
        candidates += [
            mp.parent / 'teams.json',
            mp.parent / 'assets' / 'teams.json',
            mp.parent.parent / 'assets' / 'teams.json',
        ]
    try:
        script_p = Path(__file__).resolve()
        candidates += [
            script_p.parent / 'teams.json',
            script_p.parent / 'assets' / 'teams.json',
            script_p.parent.parent / 'assets' / 'teams.json',
        ]
    except NameError:
        pass
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def find_team_in_registry(team_marks: Dict, registry: Optional[Dict]) -> Optional[Dict]:
    """Look up a team in the registry by `id`/`teamId`, then by case-insensitive name."""
    if not registry:
        return None
    team_id = team_marks.get('id') or team_marks.get('teamId')
    name_lower = (team_marks.get('name') or '').strip().lower()
    for t in (registry.get('teams') or []):
        if team_id and t.get('id') == team_id:
            return t
    if name_lower:
        for t in (registry.get('teams') or []):
            if (t.get('name') or '').strip().lower() == name_lower:
                return t
    return None


def find_player_in_registry(player_name: str, registry: Optional[Dict]) -> Optional[Dict]:
    if not registry or not player_name:
        return None
    name_lower = player_name.strip().lower()
    for p in (registry.get('players') or []):
        if (p.get('name') or '').strip().lower() == name_lower:
            return p
    return None


def enrich_teams_from_registry(teams: Dict, registry: Optional[Dict]) -> Dict:
    """Fill in `logo` and `players` on each team from the registry, in-place."""
    if not registry:
        return teams
    for key in ('1', '2', 1, 2):
        if key not in teams:
            continue
        team = teams[key]
        match = find_team_in_registry(team, registry)
        if not match:
            continue
        if match.get('logo_abs') and not team.get('logo'):
            team['logo'] = match['logo_abs']
        if match.get('players') and not team.get('players'):
            team['players'] = list(match['players'])
    return teams


def _render_initial_avatar(initial: str, size: int,
                           bg_rgb: Tuple[int, int, int]) -> Image.Image:
    """Fallback avatar when a player photo is missing — colored circle with initial."""
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([(0, 0), (size - 1, size - 1)],
                 fill=(bg_rgb[0], bg_rgb[1], bg_rgb[2], 255))
    font = load_font(max(10, int(size * 0.45)))
    letter = (initial or '?')[:1].upper()
    bbox = draw.textbbox((0, 0), letter, font=font)
    iw = bbox[2] - bbox[0]
    ih = bbox[3] - bbox[1]
    draw.text(((size - iw) // 2, (size - ih) // 2 - bbox[1]),
              letter, fill=(255, 255, 255, 255), font=font)
    return img


def _crop_square_circular(photo: Image.Image, size: int) -> Image.Image:
    """Center-crop to square, resize to `size`, apply circular mask."""
    pw, ph = photo.size
    sq = min(pw, ph)
    left = (pw - sq) // 2
    top = (ph - sq) // 2
    photo = photo.crop((left, top, left + sq, top + sq))
    photo = photo.resize((size, size), Image.LANCZOS)
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).ellipse([(0, 0), (size - 1, size - 1)], fill=255)
    out = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    out.paste(photo, (0, 0), mask)
    return out


def _fit_rounded_rect(photo: Image.Image, max_h: int, max_w: int,
                      radius_ratio: float = 0.16) -> Image.Image:
    """Resize `photo` to fit inside (max_w, max_h) preserving its aspect ratio,
    then round the corners. Returns an RGBA image sized exactly to the fit
    result (not the full box), so callers can center it however they like."""
    pw, ph = photo.size
    if pw <= 0 or ph <= 0 or max_h <= 0 or max_w <= 0:
        return Image.new('RGBA', (1, 1), (0, 0, 0, 0))
    scale = min(max_w / pw, max_h / ph)
    new_w = max(1, int(round(pw * scale)))
    new_h = max(1, int(round(ph * scale)))
    photo = photo.resize((new_w, new_h), Image.LANCZOS)
    radius = max(2, int(min(new_w, new_h) * radius_ratio))
    mask = Image.new('L', (new_w, new_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [(0, 0), (new_w - 1, new_h - 1)], radius=radius, fill=255
    )
    out = Image.new('RGBA', (new_w, new_h), (0, 0, 0, 0))
    out.paste(photo, (0, 0), mask)
    return out


def _render_rect_placeholder(initial: str, w: int, h: int,
                             bg_rgb: Tuple[int, int, int],
                             radius_ratio: float = 0.16) -> Image.Image:
    """Fallback rounded-rectangle tile with a colored background + initial.
    Used when a player photo is missing."""
    w = max(1, w)
    h = max(1, h)
    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    radius = max(2, int(min(w, h) * radius_ratio))
    mask = Image.new('L', (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [(0, 0), (w - 1, h - 1)], radius=radius, fill=255
    )
    bg = Image.new('RGBA', (w, h), (bg_rgb[0], bg_rgb[1], bg_rgb[2], 255))
    img.paste(bg, (0, 0), mask)
    draw = ImageDraw.Draw(img)
    font = load_font(max(12, int(min(w, h) * 0.5)))
    letter = (initial or '?')[:1].upper()
    bb = draw.textbbox((0, 0), letter, font=font)
    iw = bb[2] - bb[0]
    ih = bb[3] - bb[1]
    draw.text(((w - iw) // 2, (h - ih) // 2 - bb[1]),
              letter, fill=(255, 255, 255, 255), font=font)
    return img


# =============================================================================
# SCORE BUG RENDERING
# =============================================================================

def render_score_bug(score: Dict[int, int],
                     teams: Dict,
                     bug_scale: float = 1.0,
                     highlight_team: Optional[int] = None,
                     highlight_intensity: float = 0.0) -> Image.Image:
    """Render the two-row score bug as an RGBA PIL image with rounded corners.
    If a team has a `logo` key pointing to an image file, it's drawn on the
    left side of that team's row."""
    width = int(300 * bug_scale)
    row_h = int(52 * bug_scale)
    radius = int(9 * bug_scale)
    pad_x = int(14 * bug_scale)
    logo_size = int(row_h * 0.78)
    logo_gap = int(10 * bug_scale)
    name_size = max(12, int(20 * bug_scale))
    score_size = max(14, int(26 * bug_scale))

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

        if highlight_team == team_num and highlight_intensity > 0:
            row_bg = brighten(row_bg, highlight_intensity)

        canvas.paste(row_bg.convert('RGBA'), (0, i * row_h))

        # Team logo on the left
        text_x = pad_x
        logo_path = team.get('logo')
        if logo_path:
            logo = load_logo(logo_path, logo_size)
            if logo is not None:
                lx = pad_x
                ly = i * row_h + (row_h - logo.size[1]) // 2
                canvas.alpha_composite(logo, (lx, ly))
                text_x = pad_x + logo.size[0] + logo_gap

        # Draw text
        draw = ImageDraw.Draw(canvas)
        name = (team.get('name') or f'Team {team_num}').upper()
        score_text = str(int(score.get(team_num, 0)))

        # Team name (left, after optional logo)
        name_bbox = draw.textbbox((0, 0), name, font=name_font)
        name_h = name_bbox[3] - name_bbox[1]
        y_text = i * row_h + (row_h - name_h) // 2 - name_bbox[1]
        _draw_text_with_shadow(draw, (text_x, y_text), name, name_font)

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

    # Drop shadow underneath
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
                      bug_scale: float = 1.0,
                      teams_registry: Optional[Dict] = None):
    """Broadcast-style final screen: league logo at top, two team cards
    (team logo, name, score, player photos with names), dash between them.
    Gracefully degrades if logos/players aren't available."""
    w, h = resolution
    img = Image.new('RGB', (w, h), FINAL_BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Font sizing proportional to frame height
    league_name_size = max(16, int(h * 0.028))
    team_name_size = max(22, int(h * 0.042))
    score_size = max(60, int(h * 0.20))
    dash_size = max(40, int(h * 0.14))
    player_name_size = max(12, int(h * 0.022))

    league_font = load_font(league_name_size)
    team_name_font = load_font(team_name_size)
    score_font = load_font(score_size)
    dash_font = load_font(dash_size)
    player_font = load_font(player_name_size)

    # --- League logo / name at top ---
    league = (teams_registry or {}).get('league') or {}
    league_bottom_y = int(h * 0.04)
    if league.get('logo_abs'):
        league_logo = load_logo(league['logo_abs'], int(h * 0.10))
        if league_logo is not None:
            lx = (w - league_logo.size[0]) // 2
            img.paste(league_logo, (lx, int(h * 0.04)), league_logo)
            league_bottom_y = int(h * 0.04) + league_logo.size[1] + int(h * 0.01)
    elif league.get('name'):
        name = league['name'].upper()
        bb = draw.textbbox((0, 0), name, font=league_font)
        nw = bb[2] - bb[0]
        y0 = int(h * 0.05)
        _draw_text_with_shadow(draw, ((w - nw) // 2, y0 - bb[1]), name, league_font)
        league_bottom_y = y0 + (bb[3] - bb[1]) + int(h * 0.01)

    # --- Team cards ---
    card_w = int(w * 0.33)
    card_h = int(h * 0.68)
    gap = int(w * 0.06)
    total_w = card_w * 2 + gap
    start_x = (w - total_w) // 2
    card_y = max(league_bottom_y + int(h * 0.015), int(h * 0.16))

    # If cards would overflow the frame, pull the card_y up and/or shorten card_h
    if card_y + card_h > h - int(h * 0.02):
        card_h = h - card_y - int(h * 0.02)

    for i, team_num in enumerate((1, 2)):
        team = teams.get(str(team_num), teams.get(team_num, {}))
        c1 = hex_to_rgb(team.get('color1', '#888'))
        c2 = hex_to_rgb(team.get('color2', team.get('color1', '#888')))
        use_gradient = bool(team.get('gradient', True))
        x = start_x + i * (card_w + gap)

        # Card background
        if use_gradient:
            card_bg = make_gradient(card_w, card_h, c1, c2)
        else:
            card_bg = Image.new('RGB', (card_w, card_h), c1)

        # Rounded card
        mask = Image.new('L', (card_w, card_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [(0, 0), (card_w - 1, card_h - 1)], radius=28, fill=255
        )
        img.paste(card_bg, (x, card_y), mask)

        players = team.get('players') or []
        has_players = bool(players)

        # Vertical zones adapt based on whether we have players to show
        if has_players:
            z_logo_top = 0.05
            z_logo_h = 0.22
            z_name_y = 0.30
            z_score_y = 0.42
            z_players_y = 0.74
        else:
            z_logo_top = 0.08
            z_logo_h = 0.28
            z_name_y = 0.40
            z_score_y = 0.55
            z_players_y = 1.0  # unused

        # 1. Team logo
        logo_path = team.get('logo')
        if logo_path:
            target_logo_h = int(card_h * z_logo_h)
            team_logo = load_logo(logo_path, target_logo_h)
            if team_logo is not None:
                tlx = x + (card_w - team_logo.size[0]) // 2
                tly = card_y + int(card_h * z_logo_top)
                img.paste(team_logo, (tlx, tly), team_logo)

        # 2. Team name
        name = (team.get('name') or f'Team {team_num}').upper()
        nb = draw.textbbox((0, 0), name, font=team_name_font)
        nw = nb[2] - nb[0]
        name_y = card_y + int(card_h * z_name_y)
        _draw_text_with_shadow(draw,
                               (x + (card_w - nw) // 2, name_y - nb[1]),
                               name, team_name_font)

        # 3. Score (huge)
        score_text = str(int(score.get(team_num, 0)))
        sb = draw.textbbox((0, 0), score_text, font=score_font)
        sw = sb[2] - sb[0]
        score_y = card_y + int(card_h * z_score_y)
        _draw_text_with_shadow(draw,
                               (x + (card_w - sw) // 2, score_y - sb[1]),
                               score_text, score_font)

        # 4. Player photos (rounded-rectangle tiles, aspect ratio preserved)
        if has_players:
            _paste_player_row(
                img, draw, players, teams_registry, player_font,
                x=x, card_w=card_w, card_y=card_y, card_h=card_h,
                z_players_y=z_players_y, team_color2=c2,
            )

    # --- Dash between cards (aligned with score row of the cards) ---
    dash = '—'
    db = draw.textbbox((0, 0), dash, font=dash_font)
    dw = db[2] - db[0]
    dh = db[3] - db[1]
    # Vertically center the dash on the score text row
    target_score_y_center = card_y + int(card_h * 0.42) + (score_size // 2)
    dash_y = target_score_y_center - dh // 2 - db[1]
    draw.text(((w - dw) // 2, dash_y), dash, fill=(150, 150, 160), font=dash_font)

    return ImageClip(np.array(img)).set_duration(duration)


def _paste_player_row(img: Image.Image, draw: ImageDraw.ImageDraw,
                      players: List[str], teams_registry: Optional[Dict],
                      player_font: ImageFont.ImageFont,
                      x: int, card_w: int, card_y: int, card_h: int,
                      z_players_y: float,
                      team_color2: Tuple[int, int, int]) -> None:
    """Draw a row of player photos (rounded-rectangle, aspect-ratio preserved)
    with names below, horizontally centered within a card. Mutates `img`."""
    n = len(players)
    if n <= 0:
        return
    tile_h = int(card_h * 0.18)
    tile_w = int(card_h * 0.14)  # portrait-biased slot; photos fit inside
    spacing = int(card_w * 0.04)
    total_row_w = n * tile_w + (n - 1) * spacing
    if total_row_w > card_w - 20:
        tile_w = max(28, (card_w - 20 - (n - 1) * spacing) // n)
        total_row_w = n * tile_w + (n - 1) * spacing

    row_x = x + (card_w - total_row_w) // 2
    row_y = card_y + int(card_h * z_players_y)

    for pi, player_name in enumerate(players):
        slot_x = row_x + pi * (tile_w + spacing)

        player_info = find_player_in_registry(player_name, teams_registry)
        fitted: Optional[Image.Image] = None
        if player_info and player_info.get('photo_abs'):
            photo = _load_rgba_cached(player_info['photo_abs'])
            if photo is not None:
                fitted = _fit_rounded_rect(photo, tile_h, tile_w)
        if fitted is None:
            fitted = _render_rect_placeholder(player_name, tile_w, tile_h, team_color2)

        # Center the fitted image inside the (tile_w × tile_h) slot
        fw, fh = fitted.size
        fx = slot_x + (tile_w - fw) // 2
        fy = row_y + (tile_h - fh) // 2
        img.paste(fitted, (fx, fy), fitted)

        # Thin rounded-rect ring hugging the actual photo bounds
        ring_radius = max(2, int(min(fw, fh) * 0.16))
        ImageDraw.Draw(img).rounded_rectangle(
            [(fx - 2, fy - 2), (fx + fw + 1, fy + fh + 1)],
            radius=ring_radius + 2,
            outline=(255, 255, 255, 235), width=2,
        )

        # Player name below the tile slot
        pnb = draw.textbbox((0, 0), player_name, font=player_font)
        pnw = pnb[2] - pnb[0]
        pname_y = row_y + tile_h + 10
        _draw_text_with_shadow(
            draw, (slot_x + (tile_w - pnw) // 2, pname_y - pnb[1]),
            player_name, player_font,
        )


def make_pre_game_screen(teams: Dict, duration: float,
                         resolution: Tuple[int, int],
                         bug_scale: float = 1.0,
                         teams_registry: Optional[Dict] = None):
    """Broadcast-style pre-game intro: league logo at top, two team cards
    (logo, name, player photos), and a 'VS' in the middle. Same visual
    language as `make_final_screen` but with no scores."""
    w, h = resolution
    img = Image.new('RGB', (w, h), FINAL_BG_COLOR)
    draw = ImageDraw.Draw(img)

    league_name_size = max(16, int(h * 0.028))
    team_name_size = max(22, int(h * 0.045))
    vs_size = max(48, int(h * 0.18))
    player_name_size = max(12, int(h * 0.022))

    league_font = load_font(league_name_size)
    team_name_font = load_font(team_name_size)
    vs_font = load_font(vs_size)
    player_font = load_font(player_name_size)

    # --- League logo / name at top ---
    league = (teams_registry or {}).get('league') or {}
    league_bottom_y = int(h * 0.04)
    if league.get('logo_abs'):
        league_logo = load_logo(league['logo_abs'], int(h * 0.10))
        if league_logo is not None:
            lx = (w - league_logo.size[0]) // 2
            img.paste(league_logo, (lx, int(h * 0.04)), league_logo)
            league_bottom_y = int(h * 0.04) + league_logo.size[1] + int(h * 0.01)
    elif league.get('name'):
        name = league['name'].upper()
        bb = draw.textbbox((0, 0), name, font=league_font)
        nw = bb[2] - bb[0]
        y0 = int(h * 0.05)
        _draw_text_with_shadow(draw, ((w - nw) // 2, y0 - bb[1]), name, league_font)
        league_bottom_y = y0 + (bb[3] - bb[1]) + int(h * 0.01)

    # "TIP-OFF" badge
    tip_font = load_font(max(14, int(h * 0.028)))
    tip_text = 'TIP-OFF'
    tb = draw.textbbox((0, 0), tip_text, font=tip_font)
    tw = tb[2] - tb[0]
    th = tb[3] - tb[1]
    tip_y = league_bottom_y + int(h * 0.005)
    tip_pad_x = int(tw * 0.25)
    tip_pad_y = int(th * 0.35)
    pill_w = tw + tip_pad_x * 2
    pill_h = th + tip_pad_y * 2
    pill_x = (w - pill_w) // 2
    pill = Image.new('RGBA', (pill_w, pill_h), (0, 0, 0, 0))
    ImageDraw.Draw(pill).rounded_rectangle(
        [(0, 0), (pill_w - 1, pill_h - 1)],
        radius=pill_h // 2, fill=(255, 255, 255, 30),
        outline=(255, 255, 255, 120), width=1,
    )
    img.paste(pill, (pill_x, tip_y), pill)
    _draw_text_with_shadow(
        draw, (pill_x + tip_pad_x, tip_y + tip_pad_y - tb[1]),
        tip_text, tip_font,
    )
    league_bottom_y = tip_y + pill_h

    # --- Team cards (same geometry as final screen) ---
    card_w = int(w * 0.33)
    card_h = int(h * 0.68)
    gap = int(w * 0.06)
    total_w = card_w * 2 + gap
    start_x = (w - total_w) // 2
    card_y = max(league_bottom_y + int(h * 0.020), int(h * 0.18))
    if card_y + card_h > h - int(h * 0.02):
        card_h = h - card_y - int(h * 0.02)

    for i, team_num in enumerate((1, 2)):
        team = teams.get(str(team_num), teams.get(team_num, {}))
        c1 = hex_to_rgb(team.get('color1', '#888'))
        c2 = hex_to_rgb(team.get('color2', team.get('color1', '#888')))
        use_gradient = bool(team.get('gradient', True))
        x = start_x + i * (card_w + gap)

        # Card background
        if use_gradient:
            card_bg = make_gradient(card_w, card_h, c1, c2)
        else:
            card_bg = Image.new('RGB', (card_w, card_h), c1)
        mask = Image.new('L', (card_w, card_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [(0, 0), (card_w - 1, card_h - 1)], radius=28, fill=255
        )
        img.paste(card_bg, (x, card_y), mask)

        players = team.get('players') or []
        has_players = bool(players)

        # Bigger logo + name since there's no score to fit
        if has_players:
            z_logo_top = 0.08
            z_logo_h = 0.32
            z_name_y = 0.48
            z_players_y = 0.70
        else:
            z_logo_top = 0.12
            z_logo_h = 0.38
            z_name_y = 0.60
            z_players_y = 1.0

        # 1. Team logo
        logo_path = team.get('logo')
        if logo_path:
            target_logo_h = int(card_h * z_logo_h)
            team_logo = load_logo(logo_path, target_logo_h)
            if team_logo is not None:
                tlx = x + (card_w - team_logo.size[0]) // 2
                tly = card_y + int(card_h * z_logo_top)
                img.paste(team_logo, (tlx, tly), team_logo)

        # 2. Team name
        name = (team.get('name') or f'Team {team_num}').upper()
        nb = draw.textbbox((0, 0), name, font=team_name_font)
        nw = nb[2] - nb[0]
        name_y = card_y + int(card_h * z_name_y)
        _draw_text_with_shadow(
            draw, (x + (card_w - nw) // 2, name_y - nb[1]),
            name, team_name_font,
        )

        # 3. Players row
        if has_players:
            _paste_player_row(
                img, draw, players, teams_registry, player_font,
                x=x, card_w=card_w, card_y=card_y, card_h=card_h,
                z_players_y=z_players_y, team_color2=c2,
            )

    # --- VS in the middle ---
    vs = 'VS'
    vb = draw.textbbox((0, 0), vs, font=vs_font)
    vw = vb[2] - vb[0]
    vh_ = vb[3] - vb[1]
    vs_y = card_y + (card_h // 2) - vh_ // 2 - vb[1]
    _draw_text_with_shadow(
        draw, ((w - vw) // 2, vs_y), vs, vs_font,
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
    parser.add_argument('--bug-scale', type=float, default=1.5, help='Score bug size multiplier (default: 1.5)')
    parser.add_argument('--preset', default='medium',
                        help='ffmpeg preset: ultrafast/fast/medium/slow (default: medium)')
    parser.add_argument('--crf', type=int, default=18, help='ffmpeg CRF quality 0-51, lower=better (default: 18)')
    parser.add_argument('--keep-download', action='store_true',
                        help='Keep the downloaded YouTube file after export')
    parser.add_argument('--teams', default=None,
                        help='Path to teams.json with logos/players '
                             '(auto-detected in ./assets or next to markings if omitted)')
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

    # Load team registry (logos + players)
    teams_config_path = args.teams or auto_find_teams_config(args.marks)
    teams_registry = load_teams_config(teams_config_path) if teams_config_path else None
    if teams_registry:
        print(f"Using teams config: {teams_config_path}")
        enrich_teams_from_registry(teams, teams_registry)

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

    # Pre-game intro
    pregame_dur = float(config.get('preGameDuration', 3))
    if pregame_dur > 0:
        print("  [pregame] pre-game intro screen")
        clips.append(make_pre_game_screen(
            teams, pregame_dur, source.size, args.bug_scale,
            teams_registry=teams_registry,
        ))

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
        clips.append(make_final_screen(
            teams, totals, final_dur, source.size, args.bug_scale,
            teams_registry=teams_registry,
        ))

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
