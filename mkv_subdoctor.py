#!/usr/bin/env python3
"""
mkv_subdoctor.py  —  MKV SubDoctor (core)

Processes MKV files to:
  1. Remove unwanted-language subtitle tracks (verified by content analysis, not just metadata)
  2. Fix common spelling errors in SRT/CC subtitle tracks
  3. Reorder subtitle tracks: regular → CC/SDH → Forced
  4. Correct mislabeled subtitle language tags

Requirements:
  pip install langdetect pyspellchecker pillow
  MKVToolNix installed (mkvmerge, mkvextract must be in PATH or default install location)

Usage:
  python mkv_subdoctor.py movie.mkv
  python mkv_subdoctor.py /movies/  --recursive
  python mkv_subdoctor.py movie.mkv --dry-run
"""

import argparse
import datetime
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── Tool discovery ─────────────────────────────────────────────────────────────

def find_tool(name: str) -> str:
    """Locate an MKVToolNix binary in PATH or common install dirs."""
    path = shutil.which(name)
    if path:
        return path
    candidates = [
        rf"C:\Program Files\MKVToolNix\{name}.exe",
        rf"C:\Program Files (x86)\MKVToolNix\{name}.exe",
        f"/usr/bin/{name}",
        f"/usr/local/bin/{name}",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    sys.exit(
        f"ERROR: '{name}' not found.\n"
        "Install MKVToolNix: https://mkvtoolnix.download/\n"
        "Ensure it is in your PATH or default install directory."
    )

MKVMERGE   = find_tool("mkvmerge")
MKVEXTRACT = find_tool("mkvextract")

# ── Python dependency checks ───────────────────────────────────────────────────

try:
    from langdetect import detect, LangDetectException, DetectorFactory
    DetectorFactory.seed = 42  # deterministic results
except ImportError:
    sys.exit("ERROR: pip install langdetect")

try:
    from spellchecker import SpellChecker
    SPELL = SpellChecker()
except ImportError:
    sys.exit("ERROR: pip install pyspellchecker")

# OCR support is optional — gracefully disabled if Tesseract/Pillow are absent
try:
    import pytesseract
    from PIL import Image as PILImage
    pytesseract.get_tesseract_version()   # raises if tesseract binary not found
    OCR_AVAILABLE = True
except Exception:
    OCR_AVAILABLE = False

# ── Constants ──────────────────────────────────────────────────────────────────

# Subtitle codecs that contain extractable text
TEXT_CODEC_EXT: dict[str, str] = {
    "S_TEXT/UTF8":   "srt",
    "S_TEXT/ASS":    "ass",
    "S_TEXT/SSA":    "ssa",
    "S_TEXT/WEBVTT": "vtt",
}

# Image-based subtitle codecs — extension used when extracting for OCR
IMAGE_CODEC_EXT: dict[str, str] = {
    "S_HDMV/PGS": "sup",
    "S_VOBSUB":   "sub",
}
IMAGE_CODECS = set(IMAGE_CODEC_EXT) | {"S_HDMV/TEXTST", "S_DVBSUB"}

OCR_SAMPLE_COUNT = 8   # number of subtitle images to OCR per track

ENGLISH_LANG_TAGS = {"eng", "en"}  # mkvmerge may return either ISO 639-2 or 639-1

# Keywords that identify CC / SDH / hearing-impaired tracks
CC_MARKERS = frozenset({"sdh", "hearing impaired", "hearing-impaired", "closed caption", "cc", "hi"})

# Keywords that identify forced subtitle tracks
FORCED_MARKERS = frozenset({"forced"})

# Need at least this many words before trusting langdetect
MIN_DETECT_WORDS = 30

# ── Language normalisation ────────────────────────────────────────────────────

# ISO 639-2/B → ISO 639-1 (langdetect returns 639-1; MKV tags are usually 639-2)
_LANG_639_2_TO_1: dict[str, str] = {
    "eng": "en", "jpn": "ja", "fra": "fr", "fre": "fr",
    "deu": "de", "ger": "de", "spa": "es", "por": "pt",
    "zho": "zh", "chi": "zh", "kor": "ko", "ara": "ar",
    "rus": "ru", "ita": "it", "ind": "id", "tha": "th",
    "vie": "vi", "nld": "nl", "dut": "nl", "tur": "tr",
    "pol": "pl", "ces": "cs", "cze": "cs", "hun": "hu",
    "ron": "ro", "rum": "ro", "fin": "fi", "swe": "sv",
    "nor": "no", "dan": "da", "hrv": "hr", "bul": "bg",
    "slk": "sk", "slv": "sl", "srp": "sr", "ukr": "uk",
    "heb": "he", "hin": "hi", "ben": "bn", "tam": "ta",
    "tel": "te", "mal": "ml", "kan": "kn", "msa": "ms",
    "may": "ms", "cat": "ca", "ell": "el", "lat": "la",
    "zxx": "zxx", "und": "und", "mul": "mul",
}

# ISO 639-1 → preferred ISO 639-2/B (used when retagging mislabeled tracks)
_LANG_639_1_TO_2: dict[str, str] = {
    "en": "eng", "ja": "jpn", "fr": "fra", "de": "deu",
    "es": "spa", "pt": "por", "zh": "zho", "ko": "kor",
    "ar": "ara", "ru": "rus", "it": "ita", "id": "ind",
    "th": "tha", "vi": "vie", "nl": "nld", "tr": "tur",
    "pl": "pol", "cs": "ces", "hu": "hun", "ro": "ron",
    "fi": "fin", "sv": "swe", "no": "nor", "da": "dan",
    "hr": "hrv", "bg": "bul", "sk": "slk", "sl": "slv",
    "sr": "srp", "uk": "ukr", "he": "heb", "hi": "hin",
    "bn": "ben", "ta": "tam", "te": "tel", "ml": "mal",
    "kn": "kan", "ms": "msa", "ca": "cat", "el": "ell",
    "la": "lat",
}


def _normalize_lang(lang: str) -> str:
    """Normalise any language tag to ISO 639-1 where possible."""
    lang = lang.lower().split("-")[0]
    return _LANG_639_2_TO_1.get(lang, lang)


# Default set of languages to keep (ISO 639-1).  Override via --keep-lang or keep_langs param.
KEEP_LANGS_DEFAULT: frozenset[str] = frozenset({"en"})

# ── GUI / external pause-stop hooks ──────────────────────────────────────────
# External code (e.g. the GUI) can control processing by manipulating these events.
# _pause_event cleared  → processing waits after the current file finishes.
# _stop_event  set      → processing exits cleanly after the current file.
_pause_event = threading.Event()
_pause_event.set()   # not paused initially
_stop_event  = threading.Event()  # not stopping initially


def _check_pause_stop() -> bool:
    """Block while paused.  Return True if caller should stop."""
    _pause_event.wait()
    return _stop_event.is_set()

# ── MKV metadata ──────────────────────────────────────────────────────────────

def mkv_json(path: str) -> dict:
    r = subprocess.run(
        [MKVMERGE, "-J", path],
        capture_output=True, text=True, encoding="utf-8",
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return json.loads(r.stdout)


def extract_all_tracks(mkv: str, sub_tracks: list[dict], outdir: str) -> dict[int, str]:
    """Extract all subtitle tracks in a single mkvextract call.
    Extracts text-based tracks always; image-based tracks only when OCR is available.
    Returns a dict of {track_id: output_path} for tracks that extracted successfully."""
    specs = []
    paths: dict[int, str] = {}
    for track in sub_tracks:
        tid   = track["id"]
        props = track.get("properties", {})
        codec = props.get("codec_id") or track.get("codec", "")
        ext   = TEXT_CODEC_EXT.get(codec)
        if ext is None:
            # Try image codec if OCR is available
            if OCR_AVAILABLE:
                ext = IMAGE_CODEC_EXT.get(codec)
            if ext is None:
                continue
        dst = os.path.join(outdir, f"track_{tid}.{ext}")
        specs.append(f"{tid}:{dst}")
        paths[tid] = dst

    if not specs:
        return {}

    r = subprocess.run(
        [MKVEXTRACT, mkv, "tracks"] + specs,
        capture_output=True, text=True, encoding="utf-8",
    )
    # mkvextract returns 0 (ok) or 1 (warnings) — both are usable
    if r.returncode > 1:
        return {}

    # Only return paths that actually exist and have content
    return {tid: p for tid, p in paths.items()
            if os.path.exists(p) and os.path.getsize(p) > 0}

# ── Text stripping from subtitle formats ──────────────────────────────────────

def _srt_plain(content: str) -> str:
    content = re.sub(r"^\d+\s*$", "", content, flags=re.MULTILINE)
    content = re.sub(r"\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}[^\n]*", "", content)
    content = re.sub(r"<[^>]+>", "", content)
    content = re.sub(r"\{[^}]+\}", "", content)
    return content

def _ass_plain(content: str) -> str:
    lines = []
    for line in content.splitlines():
        if line.startswith("Dialogue:"):
            parts = line.split(",", 9)
            if len(parts) == 10:
                lines.append(re.sub(r"\{[^}]+\}", "", parts[9]))
    return "\n".join(lines)

def _vtt_plain(content: str) -> str:
    content = re.sub(r"WEBVTT[^\n]*", "", content)
    content = re.sub(r"[\d:.]+\s*-->\s*[\d:.]+[^\n]*", "", content)
    content = re.sub(r"<[^>]+>", "", content)
    return content

def subtitle_to_plain_text(path: str) -> str:
    """Return stripped dialogue text from a subtitle file."""
    with open(path, encoding="utf-8", errors="replace") as f:
        raw = f.read()
    ext = Path(path).suffix.lower().lstrip(".")
    handlers = {"srt": _srt_plain, "ass": _ass_plain, "ssa": _ass_plain, "vtt": _vtt_plain}
    text = handlers.get(ext, lambda x: x)(raw)
    return re.sub(r"\s+", " ", text).strip()

# ── OCR for image-based subtitles ────────────────────────────────────────────

def _ycbcr_to_rgb(Y: int, Cb: int, Cr: int) -> tuple[int, int, int]:
    """BT.601 YCbCr → RGB conversion used in PGS palette entries."""
    R = Y + 1.402   * (Cr - 128)
    G = Y - 0.34414 * (Cb - 128) - 0.71414 * (Cr - 128)
    B = Y + 1.772   * (Cb - 128)
    return (max(0, min(255, int(R))),
            max(0, min(255, int(G))),
            max(0, min(255, int(B))))


def _decode_pgs_rle(data: bytes) -> list[int]:
    """Decode PGS RLE-encoded pixel data into a flat list of palette indices."""
    pixels: list[int] = []
    i = 0
    while i < len(data):
        b = data[i]; i += 1
        if b != 0:
            pixels.append(b)                      # single coloured pixel
        else:
            if i >= len(data): break
            b2 = data[i]; i += 1
            kind = (b2 >> 6) & 0x3
            if kind == 0:
                if b2 == 0:
                    pass                          # end-of-line marker
                else:
                    pixels.extend([0] * (b2 & 0x3F))          # short run of transparent
            elif kind == 1:
                if i >= len(data): break
                b3 = data[i]; i += 1
                pixels.extend([0] * (((b2 & 0x3F) << 8) | b3))  # long run of transparent
            elif kind == 2:
                count = b2 & 0x3F
                if i >= len(data): break
                color = data[i]; i += 1
                pixels.extend([color] * count)    # short run of colour
            else:
                if i + 1 >= len(data): break
                b3 = data[i]; i += 1
                count = ((b2 & 0x3F) << 8) | b3
                color = data[i]; i += 1
                pixels.extend([color] * count)    # long run of colour
    return pixels


def _pgs_image_to_text(w: int, h: int, pixels: list[int],
                        palette: dict[int, tuple[int, int, int, int]]) -> str:
    """Render a PGS bitmap and return OCR text."""
    # Black background so white/yellow subtitle text shows up clearly
    img = PILImage.new("RGBA", (w, h), (0, 0, 0, 255))
    px  = img.load()
    for idx, p in enumerate(pixels[:w * h]):
        x, y = idx % w, idx // w
        r, g, b, a = palette.get(p, (0, 0, 0, 0))
        if a > 0:
            px[x, y] = (r, g, b, 255)

    # Scale up for better Tesseract accuracy (works best ≥ 300 dpi equivalent)
    scale = max(1, min(4, 1080 // max(h, 1)))
    if scale > 1:
        img = img.resize((w * scale, h * scale), PILImage.LANCZOS)

    return pytesseract.image_to_string(
        img.convert("RGB"), config="--psm 6 -l eng+jpn+chi_sim+ara+deu+fra"
    ).strip()


def ocr_pgs(sup_path: str, max_samples: int = OCR_SAMPLE_COUNT) -> Optional[str]:
    """OCR a sample of subtitle images from a PGS .sup file.
    Returns combined OCR text, or None on failure / OCR unavailable."""
    if not OCR_AVAILABLE:
        return None

    SEG_PDS, SEG_ODS = 0x14, 0x15
    palettes: dict[int, dict[int, tuple]] = {}
    texts: list[str] = []

    try:
        with open(sup_path, "rb") as f:
            raw = f.read()
    except OSError:
        return None

    pos = 0
    while pos < len(raw) - 13 and len(texts) < max_samples:
        if raw[pos:pos + 2] != b"PG":
            pos += 1
            continue

        seg_type = raw[pos + 10]
        seg_size = struct.unpack(">H", raw[pos + 11:pos + 13])[0]
        seg_data = raw[pos + 13: pos + 13 + seg_size]
        pos += 13 + seg_size

        if seg_type == SEG_PDS and len(seg_data) >= 2:
            # Palette Definition Segment: id, version, then 5-byte entries
            pid = seg_data[0]
            pal: dict[int, tuple] = {}
            for i in range(2, len(seg_data) - 3, 5):
                eid         = seg_data[i]
                Y, Cb, Cr, T = seg_data[i+1], seg_data[i+2], seg_data[i+3], seg_data[i+4]
                r, g, b     = _ycbcr_to_rgb(Y, Cb, Cr)
                pal[eid]    = (r, g, b, 255 - T)   # T=0 opaque, T=255 transparent
            palettes[pid] = pal

        elif seg_type == SEG_ODS and len(seg_data) >= 11:
            seq_flag = seg_data[3]
            # Only process first / only segments (bit 7 set = first in sequence)
            if not (seq_flag & 0x80):
                continue
            w = struct.unpack(">H", seg_data[7:9])[0]
            h = struct.unpack(">H", seg_data[9:11])[0]
            if not (0 < w <= 4096 and 0 < h <= 4096):
                continue
            if not palettes:
                continue

            pixels = _decode_pgs_rle(seg_data[11:])
            if len(pixels) < (w * h) // 4:
                continue   # too sparse — likely a partial/empty frame

            try:
                text = _pgs_image_to_text(w, h, pixels, next(iter(palettes.values())))
                if len(text.split()) >= 2:
                    texts.append(text)
            except Exception:
                continue

    return " ".join(texts) if texts else None


def ocr_vobsub(sub_path: str, max_samples: int = OCR_SAMPLE_COUNT) -> Optional[str]:
    """OCR a sample of subtitle images from a VobSub .sub file.
    VobSub bitmaps are stored as MPEG-2 private stream packets — we hand off to
    Tesseract via ffmpeg which can decode them directly, if ffmpeg is available."""
    if not OCR_AVAILABLE:
        return None
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None   # ffmpeg required for VobSub decoding

    idx_path = sub_path.replace(".sub", ".idx")
    if not os.path.exists(idx_path):
        return None

    texts: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        # Use ffmpeg to render VobSub frames as PNG images (one per subtitle)
        png_pattern = os.path.join(td, "frame%04d.png")
        cmd = [
            ffmpeg, "-loglevel", "error",
            "-i", sub_path,
            "-vf", "scale=iw*2:ih*2",   # scale up for better OCR
            "-vframes", str(max_samples * 4),  # grab extra frames (many may be blank)
            png_pattern,
        ]
        subprocess.run(cmd, capture_output=True)

        for png in sorted(Path(td).glob("frame*.png"))[:max_samples * 2]:
            try:
                img  = PILImage.open(str(png)).convert("RGB")
                text = pytesseract.image_to_string(img, config="--psm 6").strip()
                if len(text.split()) >= 2:
                    texts.append(text)
                    if len(texts) >= max_samples:
                        break
            except Exception:
                continue

    return " ".join(texts) if texts else None


def ocr_image_sub(path: str, codec: str) -> Optional[str]:
    """Dispatch OCR to the right handler based on codec."""
    if codec == "S_HDMV/PGS":
        return ocr_pgs(path)
    if codec == "S_VOBSUB":
        return ocr_vobsub(path)
    return None


# ── Language detection ────────────────────────────────────────────────────────

def detect_lang(text: str) -> Optional[str]:
    """Return ISO 639-1 language code or None if inconclusive."""
    if len(text.split()) < MIN_DETECT_WORDS:
        return None
    try:
        return detect(text[:10_000])
    except LangDetectException:
        return None


def track_lang_tag(track: dict) -> str:
    """Normalise MKV language tag to a two-letter code."""
    props = track.get("properties", {})
    lang = props.get("language_ietf") or props.get("language", "und")
    return lang.lower().split("-")[0]  # "en-US" → "en", "eng" stays "eng"

# ── Track classification helpers ──────────────────────────────────────────────

def is_cc_track(track: dict) -> bool:
    props = track.get("properties", {})
    if props.get("flag_hearing_impaired"):
        return True
    name = props.get("track_name", "").lower()
    return any(m in name for m in CC_MARKERS)


def is_forced_track(track: dict) -> bool:
    props = track.get("properties", {})
    if props.get("flag_forced"):
        return True
    name = props.get("track_name", "").lower()
    return any(m in name for m in FORCED_MARKERS)

# ── Spelling correction (SRT only) ────────────────────────────────────────────

# Lines that must not be spell-checked
_TIMING_RE = re.compile(r"^\d+$|^\d{2}:\d{2}|^[{\[]")

# Match whole words — but NOT words adjacent to an apostrophe (contractions).
# e.g. in "aren't": "aren" is followed by "'" so it won't match.
# (?<!')  = not preceded by apostrophe
# (?!')   = not followed by apostrophe
_WORD_RE = re.compile(r"(?<!')\b[a-zA-Z]{4,}\b(?!')")  # ≥ 4 letters, not in contractions

# Words pyspellchecker gets wrong — add anything subtitle-common here
_SPELLCHECK_ALLOWLIST = {
    # contractions that still slip through
    "dont", "cant", "wont", "didnt", "doesnt", "wasnt", "isnt", "arent",
    "wouldnt", "shouldnt", "couldnt", "hadnt", "hasnt", "havent", "werent",
    "mustnt", "neednt", "shan't", "oughtn",
    # informal / colloquial
    "peasy", "gonna", "wanna", "gotta", "kinda", "sorta", "gimme", "lemme",
    "imma", "dunno", "yeah", "yep", "nope", "okay",
    # gaming / fantasy / sci-fi common
    "teleport", "teleports", "teleported", "teleporting",
    "enchanter", "enchanters", "enchanted", "enchanting",
    "stat", "stats", "buff", "buffs", "debuff", "debuffs",
    "mana", "respawn", "respawns", "dungeon", "dungeons",
    "loot", "looted", "looting", "grind", "grinding",
    # other commonly flagged real words
    "amongst", "whilst", "colour", "honour", "valour",
    # ── Anime / Japanese honorifics and common terms ──────────────────────────
    # Honorifics (≥4 letters — shorter ones like san/kun/tan are safe via length filter)
    "sama", "chan", "senpai", "sempai", "sensei", "kouhai", "kohai",
    "dono", "nyan", "onii", "onee", "aniki", "aneue", "neesan", "niisan",
    "ojisan", "obasan", "ojiisan", "obaasan", "ojousama", "okasan", "otosan",
    # Common Japanese words that appear untranslated in anime subs
    "nani", "nande", "daijoubu", "daijoubu", "gomen", "sumimasen",
    "kawaii", "sugoi", "yabai", "ikemen", "bishoujo", "bishounen",
    "otaku", "senpai", "waifu", "husbando", "isekai", "harem",
    "tsundere", "yandere", "kuudere", "dandere", "deredere",
    "mecha", "shonen", "shounen", "shojo", "shoujo", "seinen", "josei",
    "kami", "youkai", "shinigami", "jutsu", "ninjutsu", "genjutsu",
    "chakra", "shinobi", "samurai", "ronin", "daimyo", "shogun",
    "katana", "kunai", "shuriken", "tanto", "wakizashi",
    "onmyoji", "onmyodo", "yokai", "oni", "tengu", "kitsune",
    "ryokan", "tatami", "futon", "bento", "ramen", "sushi", "sake",
    "manga", "anime", "doujin", "doujinshi", "seiyuu", "seiyuu",
    "nakama", "nindo", "taijutsu", "kekkei",
    # Exclamations / filler words common in anime dialogue
    "etto", "anno", "moshi", "yosh", "yoshi", "hora", "nope",
    "itadakimasu", "gochisousama", "tadaima", "okaeri",
}

# Seed the spell checker with the allowlist so it never "corrects" these
SPELL.word_frequency.load_words(_SPELLCHECK_ALLOWLIST)


def _maybe_fix(word: str, raw_line: str, match_start: int, match_end: int) -> str:
    """Return spell-corrected word if a high-confidence fix exists, else original.

    Extra guards:
    - Skip ALL-CAPS (acronyms) and Capitalised words (proper nouns / sentence start)
    - Skip if adjacent to apostrophe (belt-and-suspenders on top of the regex)
    - Skip if the word is in our allowlist
    - Only accept single-candidate corrections (avoids ambiguous guesses)
    """
    # Apostrophe adjacency guard (belt-and-suspenders)
    if match_start > 0 and raw_line[match_start - 1] == "'":
        return word
    if match_end < len(raw_line) and raw_line[match_end] == "'":
        return word

    if word.isupper() or word[0].isupper():
        return word

    low = word.lower()
    if low in _SPELLCHECK_ALLOWLIST or low in SPELL:
        return word

    candidates = SPELL.candidates(low)
    if not candidates or len(candidates) != 1:
        # Multiple candidates = ambiguous; don't guess
        return word

    best = candidates.pop()
    return best if best != low else word


def fix_spelling_srt(path: str) -> int:
    """Spell-correct an SRT file in-place. Returns the number of words changed."""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    changes = 0
    out_lines = []
    for line in lines:
        stripped = line.rstrip("\n\r")
        if not stripped or _TIMING_RE.match(stripped):
            out_lines.append(line)
            continue

        def replacer(m: re.Match) -> str:
            nonlocal changes
            orig  = m.group()
            fixed = _maybe_fix(orig, stripped, m.start(), m.end())
            if fixed != orig:
                changes += 1
            return fixed

        new_line = _WORD_RE.sub(replacer, stripped)
        eol = line[len(stripped):]
        out_lines.append(new_line + eol)

    if changes:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(out_lines)
    return changes

# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class AnalysedTrack:
    track:          dict
    tid:            int
    codec:          str
    is_english:     bool
    extracted_path: Optional[str]  # path to the extracted file (text codecs only)
    is_text:        bool
    is_image:       bool
    cc:             bool
    forced:         bool
    effective_lang: str  # actual language to stamp on the remuxed track (may differ from metadata)
    spell_fixes:    int = field(default=0, init=False)

# ── Core processing ───────────────────────────────────────────────────────────

def analyse_subtitle_tracks(
    mkv_path: str,
    sub_tracks: list[dict],
    tmpdir: str,
    remap_langs: dict[str, str],          # e.g. {"jpn": "eng"} — applied to image tracks
    keep_langs: frozenset[str] = KEEP_LANGS_DEFAULT,  # ISO 639-1 codes
) -> list[AnalysedTrack]:
    # Extract all text-based subtitle tracks in one mkvextract call (much faster)
    print(f"  Extracting subtitle tracks (batch)...")
    extracted_map = extract_all_tracks(mkv_path, sub_tracks, tmpdir)

    results = []
    for track in sub_tracks:
        tid   = track["id"]
        props = track.get("properties", {})
        # Use codec_id from properties (e.g. "S_TEXT/ASS") — the top-level
        # track["codec"] is a human-readable display name ("SubStationAlpha")
        # and does not match the codec map keys.
        codec         = props.get("codec_id") or track.get("codec", "")
        meta_lang     = track_lang_tag(track)   # what the metadata says
        effective_lang = meta_lang              # what we'll actually stamp; may be overridden
        name          = props.get("track_name", "—")

        print(f"  +- Track {tid}: [{codec}]  lang={meta_lang}  name='{name}'")

        ext       = TEXT_CODEC_EXT.get(codec)
        is_text   = ext is not None
        is_image  = codec in IMAGE_CODECS
        extracted = extracted_map.get(tid)  # None if image-based or extraction failed
        english   = False

        meta_norm = _normalize_lang(meta_lang)  # ISO 639-1 for comparison

        if is_text:
            if extracted:
                text     = subtitle_to_plain_text(extracted)
                detected = detect_lang(text)
                print(f"  |  Content language: {detected or 'inconclusive (too short or mixed)'}")
                det_norm = _normalize_lang(detected) if detected else None
                if det_norm and det_norm in keep_langs:
                    english = True
                    if meta_norm not in keep_langs:
                        # Mislabeled — fix the language tag
                        effective_lang = _LANG_639_1_TO_2.get(det_norm, det_norm)
                        print(f"  |  ** Mislabeled as '{meta_lang}' — will retag to '{effective_lang}'")
                elif det_norm is None:
                    english = meta_norm in keep_langs
                    print(f"  |  Falling back to metadata tag -> {'kept' if english else 'non-kept'}")
                else:
                    english = False
            else:
                english = meta_norm in keep_langs
                print(f"  |  Extraction failed -- metadata tag: {meta_lang}")
        else:
            # Image-based: try OCR first, then fall back to remap / metadata tag.
            ocr_text  = ocr_image_sub(extracted, codec) if extracted else None
            if ocr_text:
                detected = detect_lang(ocr_text)
                print(f"  |  OCR language: {detected or 'inconclusive'}")
                det_norm = _normalize_lang(detected) if detected else None
                if det_norm and det_norm in keep_langs:
                    english = True
                    if meta_norm not in keep_langs:
                        effective_lang = _LANG_639_1_TO_2.get(det_norm, det_norm)
                        print(f"  |  ** Mislabeled as '{meta_lang}' — will retag to '{effective_lang}'")
                elif det_norm is not None:
                    english = False
                else:
                    # OCR inconclusive — fall through to remap / metadata
                    remapped = remap_langs.get(meta_lang)
                    if remapped:
                        effective_lang = remapped
                        print(f"  |  OCR inconclusive; remapping '{meta_lang}' -> '{remapped}'")
                    else:
                        print(f"  |  OCR inconclusive; trusting metadata tag: {meta_lang}")
                    english = _normalize_lang(effective_lang) in keep_langs
            else:
                # No OCR — apply remap or trust metadata
                ocr_note = "OCR unavailable" if not OCR_AVAILABLE else "no subtitle frames extracted"
                remapped = remap_langs.get(meta_lang)
                if remapped:
                    effective_lang = remapped
                    print(f"  |  {ocr_note}; remapping '{meta_lang}' -> '{remapped}' per --remap-lang")
                else:
                    print(f"  |  {ocr_note}; trusting metadata tag: {meta_lang}")
                english = _normalize_lang(effective_lang) in keep_langs

        verdict = "KEEP" if english else "REMOVE"
        print(f"  \\- -> {verdict}" + (" [CC/SDH]" if is_cc_track(track) and english else "") +
              (" [FORCED]" if is_forced_track(track) and english else ""))

        results.append(AnalysedTrack(
            track=track, tid=tid, codec=codec,
            is_english=english, extracted_path=extracted,
            is_text=is_text, is_image=is_image,
            cc=is_cc_track(track), forced=is_forced_track(track),
            effective_lang=effective_lang,
        ))
    return results


def build_mkvmerge_cmd(
    mkv_path: str,
    out_path: str,
    other_tracks: list[dict],
    ordered_subs: list[AnalysedTrack],
) -> list[str]:
    """
    Construct an mkvmerge command that:
      - Keeps all non-subtitle tracks from the original file
      - Adds back subtitle tracks in the desired order
      - Injects spell-corrected text files where available
    """
    cmd = [MKVMERGE, "-o", out_path]

    # ── File 0: main MKV, no subtitles ──────────────────────────────────────
    cmd += ["--no-subtitles", mkv_path]
    file_idx = 1

    # ── Partition subtitles into "from extracted file" vs "from original" ───
    from_extracted: list[AnalysedTrack] = [
        a for a in ordered_subs if a.extracted_path and not a.is_image
    ]
    from_original: list[AnalysedTrack] = [
        a for a in ordered_subs if not a.extracted_path or a.is_image
    ]

    # Track that should receive default=1 (the first in the final ordered list)
    first_sub = ordered_subs[0] if ordered_subs else None

    # ── File 1 (optional): original MKV again, subs-only for image/failed tracks
    orig_sub_file_idx: Optional[int] = None
    if from_original:
        orig_sub_file_idx = file_idx
        file_idx += 1
        orig_tids = ",".join(str(a.tid) for a in from_original)
        cmd += [
            "--no-audio", "--no-video", "--no-attachments", "--no-chapters",
            "-s", orig_tids,
        ]
        # Fix language tags and default flag for image/failed tracks
        for a in from_original:
            meta_lang = track_lang_tag(a.track)
            if a.effective_lang != meta_lang:
                cmd += ["--language", f"{a.tid}:{a.effective_lang}"]
            # Explicitly set default flag: only the first ordered sub gets it
            cmd += ["--default-track", f"{a.tid}:{'1' if a is first_sub else '0'}"]
        cmd += [mkv_path]

    # ── Files 2+: extracted (possibly spell-fixed) text subtitle files ───────
    extracted_file_indices: dict[int, int] = {}  # tid → file_idx
    for a in from_extracted:
        props = a.track.get("properties", {})
        if props.get("track_name"):
            cmd += ["--track-name", f"0:{props['track_name']}"]
        cmd += ["--language", f"0:{a.effective_lang}"]  # use detected/remapped lang, not raw metadata
        if a.cc:
            cmd += ["--hearing-impaired-flag", "0:1"]
        if a.forced:
            cmd += ["--forced-track", "0:1"]
        # Explicitly set default flag: only the first ordered sub gets it
        cmd += ["--default-track", f"0:{'1' if a is first_sub else '0'}"]
        cmd += [a.extracted_path]
        extracted_file_indices[a.tid] = file_idx
        file_idx += 1

    # ── Build --track-order ──────────────────────────────────────────────────
    order_parts: list[str] = []

    # Non-subtitle tracks first (video, audio, attachments etc.)
    for t in other_tracks:
        order_parts.append(f"0:{t['id']}")

    # Subtitle tracks in desired order
    for a in ordered_subs:
        if a.tid in extracted_file_indices:
            order_parts.append(f"{extracted_file_indices[a.tid]}:0")
        elif orig_sub_file_idx is not None:
            order_parts.append(f"{orig_sub_file_idx}:{a.tid}")

    cmd += ["--track-order", ",".join(order_parts)]
    return cmd


def process_mkv(mkv_path: str, dry_run: bool = False,
                remap_langs: dict[str, str] | None = None,
                keep_langs: frozenset[str] = KEEP_LANGS_DEFAULT) -> bool:
    label = "[DRY RUN] " if dry_run else ""
    print(f"\n{label}Processing: {mkv_path}")

    try:
        info = mkv_json(mkv_path)
    except Exception as e:
        print(f"  ERROR reading file metadata: {e}")
        return False

    tracks       = info.get("tracks", [])
    sub_tracks   = [t for t in tracks if t["type"] == "subtitles"]
    other_tracks = [t for t in tracks if t["type"] != "subtitles"]

    if not sub_tracks:
        print("  No subtitle tracks — skipping.")
        return False

    print(f"  {len(sub_tracks)} subtitle track(s) found")

    with tempfile.TemporaryDirectory() as tmpdir:
        analysed = analyse_subtitle_tracks(mkv_path, sub_tracks, tmpdir, remap_langs or {}, keep_langs)

        english_tracks = [a for a in analysed if a.is_english]
        removed_count  = len(analysed) - len(english_tracks)

        if not english_tracks:
            print("  WARNING: No English subtitle tracks found — leaving file unchanged.")
            return False

        # Ordering: regular → CC/SDH → forced
        regular  = [a for a in english_tracks if not a.cc and not a.forced]
        cc_list  = [a for a in english_tracks if a.cc and not a.forced]
        forced   = [a for a in english_tracks if a.forced]
        ordered  = regular + cc_list + forced

        original_ids  = [a.tid for a in analysed]
        new_ids       = [a.tid for a in ordered]
        retagged      = any(a.effective_lang != track_lang_tag(a.track) for a in ordered)
        order_changed = original_ids != new_ids or removed_count > 0 or retagged
        if retagged:
            for a in ordered:
                if a.effective_lang != track_lang_tag(a.track):
                    print(f"  ** Track {a.tid} will be retagged: "
                          f"'{track_lang_tag(a.track)}' -> '{a.effective_lang}'")

        # ── Spell fix SRT tracks ─────────────────────────────────────────────
        total_fixes = 0
        if not dry_run:
            for a in ordered:
                if a.extracted_path and a.extracted_path.endswith(".srt"):
                    fixes = fix_spelling_srt(a.extracted_path)
                    a.spell_fixes = fixes
                    total_fixes += fixes
                    if fixes:
                        print(f"  Spell-fixed {fixes} word(s) in track {a.tid}")

        # Check whether the default-track flag is on the right track.
        # The first subtitle in ordered should be default=1; all others default=0.
        default_flag_wrong = False
        if ordered:
            first_props = ordered[0].track.get("properties", {})
            if not first_props.get("flag_default"):
                default_flag_wrong = True
            for a in ordered[1:]:
                if a.track.get("properties", {}).get("flag_default"):
                    default_flag_wrong = True
        if default_flag_wrong:
            print("  Default-track flag is on the wrong subtitle — will fix.")

        needs_remux = order_changed or total_fixes > 0 or default_flag_wrong

        if not needs_remux:
            print("  No changes required.")
            return False

        if dry_run:
            print(f"  Would remove {removed_count} non-English track(s)")
            print(f"  New subtitle order: {new_ids}")
            print(f"  (Spelling fix counts require a full run)")
            write_log(mkv_path, analysed, ordered, 0, dry_run=True)
            return True

        # ── Remux ────────────────────────────────────────────────────────────
        out_path = mkv_path + ".new.mkv"
        cmd = build_mkvmerge_cmd(mkv_path, out_path, other_tracks, ordered)

        print("  Remuxing …")
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")

        # mkvmerge exit: 0 = ok, 1 = warnings, 2 = error
        if r.returncode >= 2:
            print(f"  ERROR during remux:\n{r.stderr.strip()}")
            if os.path.exists(out_path):
                os.remove(out_path)
            return False

        if r.returncode == 1 and r.stderr:
            print(f"  Warnings:\n{r.stderr.strip()}")

        # ── Atomic replace ───────────────────────────────────────────────────
        bak = mkv_path + ".bak"
        try:
            os.rename(mkv_path, bak)
            os.rename(out_path, mkv_path)
            os.remove(bak)
        except OSError as e:
            print(f"  ERROR replacing file: {e}")
            if os.path.exists(bak) and not os.path.exists(mkv_path):
                os.rename(bak, mkv_path)
            if os.path.exists(out_path):
                os.remove(out_path)
            return False

        kept  = len(ordered)
        total = len(sub_tracks)
        print(
            f"  Done. {kept}/{total} track(s) kept | "
            f"{removed_count} removed | {total_fixes} spelling fix(es) | "
            f"order: {new_ids}"
        )
        write_log(mkv_path, analysed, ordered, total_fixes, dry_run=False)
        return True

# ── Persistent change log ─────────────────────────────────────────────────────

_LOG_DIR: Optional[Path] = None   # set by main()
_LOG_DIR_DEFAULT = Path(r"D:\Claude Projects\Subtitle_Manager_logs")

# Folder name patterns that indicate a season/disc sub-folder rather than the series root
_SEASON_RE = re.compile(
    r"^(season|series|part|disc|disk|volume|vol|s\d{1,2})\s*\d*$",
    re.IGNORECASE,
)


def _series_name_from_path(mkv_path: str) -> str:
    """Derive a series name from the MKV's folder structure.

    Typical layouts handled:
      .../SeriesName/Season 1/episode.mkv  -> SeriesName
      .../SeriesName/episode.mkv           -> SeriesName
      .../episode.mkv  (flat)              -> episode stem
    """
    p        = Path(mkv_path)
    parent   = p.parent        # immediate containing folder
    grandpar = parent.parent   # one level up

    # If the immediate parent looks like a season folder, go up one more level
    if _SEASON_RE.match(parent.name) and grandpar.name:
        return grandpar.name

    # Otherwise use the immediate parent name; fall back to the file stem
    return parent.name if parent.name else p.stem


def _sanitise_filename(name: str) -> str:
    """Strip characters that are illegal in Windows filenames."""
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip(". ")


def _log_path_for(mkv_path: str) -> Optional[Path]:
    """Return the log file Path for this MKV, or None if logging is disabled."""
    if _LOG_DIR is None:
        return None
    series  = _sanitise_filename(_series_name_from_path(mkv_path))
    return _LOG_DIR / f"{series}.log"


def _log_track(track: dict, role: str, new_slot: Optional[int] = None,
               retagged_from: Optional[str] = None, spell_fixes: int = 0) -> dict:
    """Serialise a track into a compact log dict."""
    props = track.get("properties", {})
    entry = {
        "id":     track["id"],
        "codec":  props.get("codec_id") or track.get("codec", "?"),
        "lang":   props.get("language", "und"),
        "name":   props.get("track_name") or "",
        "action": role,
    }
    if new_slot is not None:
        entry["new_slot"] = new_slot
    if retagged_from:
        entry["retagged_from"] = retagged_from
    if spell_fixes:
        entry["spell_fixes"] = spell_fixes
    return entry


def write_log(
    mkv_path:    str,
    analysed:    list,   # list[AnalysedTrack]
    ordered:     list,   # list[AnalysedTrack] — kept tracks in new order
    total_fixes: int,
    dry_run:     bool,
) -> None:
    """Append one JSON-Lines entry to the per-series log file."""
    log_path = _log_path_for(mkv_path)
    if log_path is None:
        return

    now     = datetime.datetime.now().isoformat(sep=" ", timespec="seconds")
    removed = [a for a in analysed if not a.is_english]

    tracks_log = []
    for a in removed:
        tracks_log.append(_log_track(a.track, "REMOVED"))
    for slot, a in enumerate(ordered, start=1):
        orig_lang     = track_lang_tag(a.track)
        retagged_from = orig_lang if a.effective_lang != orig_lang else None
        tracks_log.append(_log_track(
            a.track, "KEPT",
            new_slot=slot,
            retagged_from=retagged_from,
            spell_fixes=a.spell_fixes,
        ))

    entry = {
        "timestamp":     now,
        "dry_run":       dry_run,
        "file":          str(mkv_path),
        "tracks_before": len(analysed),
        "tracks_after":  len(ordered),
        "removed":       len(removed),
        "spelling_fixes": total_fixes,
        "tracks":        tracks_log,
    }

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"  Logged -> {log_path.name}")
    except OSError as e:
        print(f"  WARNING: could not write log: {e}")


def _render_log(log_path: Path) -> None:
    """Pretty-print one log file."""
    if not log_path.exists():
        print(f"  (no log file at {log_path})")
        return
    with open(log_path, encoding="utf-8") as f:
        entries = [json.loads(ln) for ln in f if ln.strip()]

    SEP = "-" * 72
    print(f"\n{SEP}")
    print(f"  Series : {log_path.stem}")
    print(f"  Log    : {log_path}  ({len(entries)} entries)")
    print(SEP)
    for e in entries:
        dr = " [DRY RUN]" if e.get("dry_run") else ""
        print(f"  {e['timestamp']}{dr}")
        print(f"  File   : {Path(e['file']).name}")
        print(f"  Tracks : {e['tracks_after']}/{e['tracks_before']} kept | "
              f"{e['removed']} removed | {e['spelling_fixes']} spelling fix(es)")
        for t in e.get("tracks", []):
            action = t["action"]
            tag    = f"  [{t['codec']}] lang={t['lang']}"
            name   = f"  name='{t['name']}'" if t.get("name") else ""
            retag  = f"  (retagged from {t['retagged_from']})" if t.get("retagged_from") else ""
            spell  = f"  ({t['spell_fixes']} spelling fixes)" if t.get("spell_fixes") else ""
            slot   = f"  -> slot {t['new_slot']}" if t.get("new_slot") else ""
            marker = "  KEPT   " if action == "KEPT" else "  REMOVED"
            print(f"    {marker}  #{t['id']}{tag}{name}{slot}{retag}{spell}")
    print(SEP)


def show_logs(log_dir: Path, series_filter: Optional[str] = None) -> None:
    """Print all series logs in the log directory, with optional name filter."""
    if not log_dir.exists():
        print(f"Log directory not found: {log_dir}")
        return
    logs = sorted(log_dir.glob("*.log"))
    if not logs:
        print(f"No log files found in {log_dir}")
        return
    if series_filter:
        sf   = series_filter.lower()
        logs = [l for l in logs if sf in l.stem.lower()]
        if not logs:
            print(f"No logs matching '{series_filter}'")
            return
    for log in logs:
        _render_log(log)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="MKV SubDoctor — clean, reorder, and fix subtitle tracks in MKV files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Track ordering result:
  Subtitle slot 1 -> English regular
  Subtitle slot 2 -> English CC / SDH / Hearing-Impaired
  Subtitle slot 3 -> English Forced
  (image-based tracks follow text-based tracks in each category)

Examples:
  %(prog)s movie.mkv
  %(prog)s /media/movies/ --recursive
  %(prog)s movie.mkv --dry-run
        """,
    )
    ap.add_argument("paths", nargs="*", metavar="PATH",
                    help="MKV file(s) or director(y/ies) to process")
    ap.add_argument("--dry-run", "-n", action="store_true",
                    help="Analyse and report without modifying any files")
    ap.add_argument("--recursive", "-r", action="store_true",
                    help="Recurse into directories")
    ap.add_argument("--keep-lang", metavar="LANG", action="append", default=[],
                    help="ISO 639-1 or 639-2 language code to keep (default: en). "
                         "Can be specified multiple times. "
                         "Example: --keep-lang en --keep-lang ja  (keep English and Japanese)")
    ap.add_argument("--remap-lang", metavar="OLD:NEW", action="append", default=[],
                    help="Treat image-based subtitle tracks tagged OLD as NEW language. "
                         "Can be specified multiple times. "
                         "Example: --remap-lang jpn:eng  (fixes English subs mislabeled as Japanese)")
    ap.add_argument("--log-dir", metavar="DIR", default=str(_LOG_DIR_DEFAULT),
                    help=f"Directory for per-series log files "
                         f"(default: {_LOG_DIR_DEFAULT})")
    ap.add_argument("--no-log", action="store_true",
                    help="Disable change logging for this run")
    ap.add_argument("--show-log", nargs="?", const="", metavar="SERIES",
                    help="Pretty-print logs and exit. "
                         "Optionally filter by series name, e.g. --show-log \"Jack-of-All\"")
    args = ap.parse_args()

    # Set up the global log directory
    global _LOG_DIR
    if not args.no_log:
        _LOG_DIR = Path(args.log_dir)

    # --show-log: pretty-print logs and exit
    if args.show_log is not None:
        show_logs(Path(args.log_dir), series_filter=args.show_log or None)
        sys.exit(0)

    # Build keep_langs set (normalised to ISO 639-1)
    keep_langs: frozenset[str] = frozenset(
        _normalize_lang(l) for l in (args.keep_lang or ["en"])
    )
    print(f"Keeping languages: {sorted(keep_langs)}")

    # Parse --remap-lang pairs into a dict
    remap_langs: dict[str, str] = {}
    for pair in args.remap_lang:
        if ":" not in pair:
            sys.exit(f"ERROR: --remap-lang '{pair}' must be in OLD:NEW format, e.g. jpn:eng")
        old, new = pair.split(":", 1)
        remap_langs[old.strip().lower()] = new.strip().lower()
    if remap_langs:
        print(f"Language remaps active: {remap_langs}")

    mkv_files: list[Path] = []
    for raw in args.paths:
        p = Path(raw)
        if p.is_file() and p.suffix.lower() == ".mkv":
            mkv_files.append(p)
        elif p.is_dir():
            pattern = "**/*.mkv" if args.recursive else "*.mkv"
            mkv_files.extend(sorted(p.glob(pattern)))
        else:
            print(f"WARNING: '{p}' is not an MKV file or directory — skipping.")

    if not args.paths:
        ap.print_help()
        sys.exit(1)

    if not mkv_files:
        sys.exit("No MKV files found.")

    print(f"Found {len(mkv_files)} MKV file(s).\n")

    # Reset pause/stop state (in case this is called from the GUI or a loop)
    _pause_event.set()
    _stop_event.clear()

    modified = 0
    errors   = 0
    for f in mkv_files:
        if _check_pause_stop():
            print("\nProcessing stopped by user.")
            break
        try:
            if process_mkv(str(f), dry_run=args.dry_run,
                           remap_langs=remap_langs, keep_langs=keep_langs):
                modified += 1
        except Exception as e:
            print(f"  UNHANDLED ERROR for '{f}': {e}")
            errors += 1

    action = "would be modified" if args.dry_run else "modified"
    print(f"\n{'='*60}")
    print(f"Complete: {modified}/{len(mkv_files)} file(s) {action}. Errors: {errors}.")


if __name__ == "__main__":
    main()
