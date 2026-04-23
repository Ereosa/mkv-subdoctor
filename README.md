# MKV SubDoctor

A Python tool for cleaning up subtitle tracks in MKV files — with both a **GUI** and a **command-line interface**.

Built primarily for anime libraries, it strips out unwanted language tracks, fixes the default subtitle selection, corrects spelling in CC/SDH tracks, and handles mislabeled subtitles — all non-destructively.

---

## Features

- **Multi-language content verification** — detects the actual language of subtitle text, not just the metadata tag. A track labelled Japanese that contains English dialogue will be kept and retagged correctly.
- **Configurable language keep list** — keep English only, or any combination of languages (English + Japanese, etc.). Supports all ISO 639-1/639-2 codes.
- **Non-English track removal** — strips out unwanted language tracks in a single remux pass.
- **Track reordering** — ensures the correct subtitle is default: regular → CC/SDH → Forced.
- **Default track flag correction** — explicitly sets the MKV default flag so your media player picks the right track automatically.
- **Spelling correction** — conservative spell-check on SRT/CC tracks. Anime honorifics, Japanese terms, gaming vocabulary, and informal speech are all allowlisted so they are never changed.
- **Mislabeled track detection** — tracks tagged with the wrong language (e.g. `zxx`, `und`, or another language) are detected by content and retagged.
- **Image subtitle support** — PGS and VobSub tracks are handled via optional OCR (requires Tesseract). Without OCR, language remap rules can be applied.
- **Dry-run mode** — preview every change before touching a single file.
- **Atomic file replacement** — writes to a `.new.mkv` first, then swaps. Original is never modified until the new file is confirmed good.
- **Per-series logs** — every change is recorded in a JSON Lines log file, one per series, for easy auditing and reversion reference.
- **Pause / Stop** — GUI supports pausing between files and clean early exit.

---

## Requirements

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10+ | [python.org](https://www.python.org/downloads/) |
| MKVToolNix | Any recent | [mkvtoolnix.download](https://mkvtoolnix.download/) — `mkvmerge` and `mkvextract` must be in PATH or default install location |
| langdetect | 1.0.9+ | Auto-installed |
| pyspellchecker | 0.7.0+ | Auto-installed |
| Pillow | 10.0+ | Auto-installed |
| pytesseract | 0.3.10+ | **Optional** — OCR for image-based subtitles. Also requires the [Tesseract binary](https://github.com/UB-Mannheim/tesseract/wiki) |

---

## Installation

### Windows (recommended)

1. Install **Python 3.10+** from [python.org](https://www.python.org/downloads/) — tick **"Add Python to PATH"** during setup.
2. Install **MKVToolNix** from [mkvtoolnix.download](https://mkvtoolnix.download/windows/releases/).
3. Clone or download this repository.
4. Double-click **`install.bat`** — or run in a terminal:

```
python install.py
```

The installer will:
- Verify your Python version
- Install required pip packages
- Check MKVToolNix is available
- Download a desktop icon
- Create a desktop shortcut to the GUI
- Create the log directory

### Installer options

```
python install.py --ocr              # also install pytesseract for image subtitle OCR
python install.py --no-shortcut      # skip desktop shortcut creation
python install.py --no-icon          # skip icon download
python install.py --icon myicon.ico  # use a custom icon for the shortcut
python install.py --log-dir D:\Logs  # set a custom log directory
```

### Manual (any OS)

```bash
pip install -r requirements.txt
```

---

## Usage

### GUI

```bash
python mkv_subdoctor_gui.py
```

Or use the desktop shortcut created by the installer.

The GUI lets you:
- Add individual MKV files or whole folders
- Select which languages to keep (checkbox list of 32 languages + custom code entry)
- Configure language remaps for mislabeled image-based tracks
- Toggle Recursive, Dry Run, and Disable Logging
- Start, Pause, and Stop processing with a progress bar
- View live output and clear it between runs

### Command line

```bash
# Process a single file
python mkv_subdoctor.py movie.mkv

# Process a folder recursively
python mkv_subdoctor.py /media/Anime/ --recursive

# Dry run — preview without changing anything
python mkv_subdoctor.py /media/Anime/ --recursive --dry-run

# Keep English and Japanese
python mkv_subdoctor.py /media/Anime/ --recursive --keep-lang en --keep-lang ja

# Remap mislabeled image tracks (e.g. English subs tagged as Japanese)
python mkv_subdoctor.py series/ --recursive --remap-lang jpn:eng

# View change logs
python mkv_subdoctor.py --show-log
python mkv_subdoctor.py --show-log "Re:Zero"
```

---

## CLI Reference

| Argument | Description |
|---|---|
| `PATH [PATH ...]` | MKV file(s) or folder(s) to process |
| `--recursive` / `-r` | Recurse into subdirectories |
| `--dry-run` / `-n` | Analyse and report without modifying files |
| `--keep-lang LANG` | Language to keep (ISO 639-1 or 639-2). Repeatable. Default: `en` |
| `--remap-lang OLD:NEW` | Treat image-based tracks tagged `OLD` as `NEW`. Repeatable. |
| `--log-dir DIR` | Directory for per-series log files (default: `D:\Subtitle_Manager_logs`) |
| `--no-log` | Disable change logging for this run |
| `--show-log [SERIES]` | Pretty-print logs. Optional series name filter. |

---

## How It Works

### Language detection

Each text-based subtitle track (ASS, SRT, SSA, WebVTT) is extracted and its dialogue text is run through [langdetect](https://github.com/Mimino666/langdetect). A minimum of 30 words is required before trusting the result; shorter tracks fall back to the metadata language tag.

If a track's detected language differs from its metadata tag (e.g. content is English but tagged `zxx` or `jpn`), the track is kept and retagged correctly.

Image-based tracks (PGS, VobSub) are OCR'd via Tesseract if available. Without OCR, `--remap-lang` rules are applied, otherwise the metadata tag is trusted.

### Track ordering

After filtering, subtitle tracks are reordered so your media player always defaults to the right one:

```
Slot 1  →  Regular English (dialogue)
Slot 2  →  English CC / SDH / Hearing-Impaired
Slot 3  →  English Forced (signs & songs)
```

The MKV `default` flag is also explicitly set — only the first slot gets it, regardless of what the original file had.

### Spelling correction

Runs only on SRT-format tracks (typically CC/SDH). The checker is intentionally conservative:

- Only words of 4+ letters are considered
- Only corrections with a single unambiguous candidate are applied
- Words adjacent to apostrophes are skipped (protects contractions)
- Capitalised and ALL-CAPS words are skipped (proper nouns, acronyms)
- An extensive allowlist covers: anime honorifics (`senpai`, `sama`, `chan`), Japanese terms (`kawaii`, `isekai`, `jutsu`), gaming vocab, British spelling variants, and informal speech

### Atomic file replacement

```
file.mkv  →  file.mkv.new.mkv  (mkvmerge writes here)
file.mkv  →  file.mkv.bak      (original renamed)
file.mkv.new.mkv  →  file.mkv  (new file takes original name)
file.mkv.bak      deleted
```

If anything fails mid-swap, the original is restored from the `.bak`.

---

## Logs

Per-series logs are stored in the configured log directory (default: `D:\Subtitle_Manager_logs\`), one file per series in JSON Lines format. Each line is one processed episode:

```json
{
  "timestamp": "2026-04-22 14:30:01",
  "dry_run": false,
  "file": "P:\\Anime\\Re Zero\\Season 1\\Re Zero - S01E01.mkv",
  "tracks_before": 15,
  "tracks_after": 2,
  "removed": 13,
  "spelling_fixes": 0,
  "tracks": [
    { "id": 4, "codec": "S_TEXT/ASS", "lang": "en", "action": "KEPT", "new_slot": 1 },
    { "id": 3, "codec": "S_TEXT/ASS", "lang": "en", "action": "KEPT", "new_slot": 2 },
    { "id": 5, "codec": "S_TEXT/ASS", "lang": "ar", "action": "REMOVED" }
  ]
}
```

View logs with:
```bash
python mkv_subdoctor.py --show-log
python mkv_subdoctor.py --show-log "Re Zero"
```

---

## Notes

- **Backup your files** before running on a large library for the first time. Use `--dry-run` to preview changes.
- Files open in Plex or a media player cannot be renamed — the script logs the error and skips them. Close Plex's media scanner before a bulk run.
- The spell checker only operates on **SRT-format** tracks. ASS/SSA formatted subtitle tracks are not spell-checked (they are reformatted and their styling would be at risk).
- OCR accuracy depends on Tesseract and the quality of the subtitle images. For known mislabeled series, `--remap-lang` is more reliable than OCR.

---

## License

MIT License — see [LICENSE](LICENSE).

The desktop icon (Rem from Re:Zero) is © Tappei Nagatsuki / KADOKAWA. It is downloaded at install time for personal use only and is not redistributed with this software.
