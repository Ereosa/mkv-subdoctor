#!/usr/bin/env python3
"""
install.py  —  MKV SubDoctor setup script

Run this once to:
  1. Verify Python version
  2. Install required pip packages
  3. Check for MKVToolNix
  4. (Optional) download a custom desktop icon
  5. Create a desktop shortcut (Windows)
  6. Create the log directory

Usage:
    python install.py
    python install.py --no-shortcut
    python install.py --icon path/to/custom.ico
"""

import argparse
import io
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────────

SCRIPT_DIR   = Path(__file__).parent.resolve()
REQUIRED_PY  = (3, 10)
PACKAGES     = ["langdetect", "pyspellchecker", "pillow"]
OCR_PACKAGES = ["pytesseract"]   # optional — user must also install Tesseract binary
LOG_DIR_DEFAULT = Path(r"D:\Subtitle_Manager_logs")  # overridable via --log-dir

MKVTOOLNIX_DL = "https://mkvtoolnix.download/windows/releases/"
TESSERACT_DL  = "https://github.com/UB-Mannheim/tesseract/wiki"

# Default icon: Rem from Re:Zero (Re:Zero Wiki — for personal use)
ICON_URL  = ("https://static.wikia.nocookie.net/rezero/images/9/9b/"
             "Rem_LN_character_design.png/revision/latest?cb=20240412203717")
ICON_PATH = SCRIPT_DIR / "rem_icon.ico"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _ok(msg):   print(f"  [OK]  {msg}")
def _warn(msg): print(f"  [!!]  {msg}")
def _info(msg): print(f"        {msg}")
def _head(msg): print(f"\n{msg}\n{'-' * len(msg)}")


def _banner():
    print("=" * 60)
    print("  MKV SubDoctor — Installer")
    print("=" * 60)


# ── Step 1: Python version ────────────────────────────────────────────────────

def check_python():
    _head("Checking Python version")
    ver = sys.version_info
    if ver < REQUIRED_PY:
        print(f"  ERROR: Python {'.'.join(map(str, REQUIRED_PY))}+ is required.")
        print(f"         Current version: {ver.major}.{ver.minor}.{ver.micro}")
        print("  Download Python: https://www.python.org/downloads/")
        sys.exit(1)
    _ok(f"Python {ver.major}.{ver.minor}.{ver.micro}")


# ── Step 2: pip packages ──────────────────────────────────────────────────────

def install_packages(ocr: bool = False):
    _head("Installing Python packages")
    pkgs = PACKAGES + (OCR_PACKAGES if ocr else [])
    for pkg in pkgs:
        print(f"  Installing {pkg}...", end=" ", flush=True)
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", pkg, "--quiet", "--disable-pip-version-check"],
            capture_output=True,
        )
        if r.returncode == 0:
            print("OK")
        else:
            print("FAILED")
            _warn(f"Could not install '{pkg}'.  Try manually: pip install {pkg}")

    if not ocr:
        _info("OCR support (pytesseract) was skipped.  Re-run with --ocr to enable it.")
        _info(f"Tesseract binary: {TESSERACT_DL}")


# ── Step 3: MKVToolNix ────────────────────────────────────────────────────────

def check_mkvtoolnix():
    _head("Checking MKVToolNix")
    found = shutil.which("mkvmerge")
    if not found:
        for candidate in [
            r"C:\Program Files\MKVToolNix\mkvmerge.exe",
            r"C:\Program Files (x86)\MKVToolNix\mkvmerge.exe",
        ]:
            if Path(candidate).exists():
                found = candidate
                break

    if found:
        _ok(f"mkvmerge found: {found}")
    else:
        _warn("MKVToolNix not found!")
        _info(f"Download from: {MKVTOOLNIX_DL}")
        _info("Install it, then re-run this script (or add it to your PATH).")


# ── Step 4: Icon ──────────────────────────────────────────────────────────────

def download_icon(custom_icon: str | None = None) -> Path | None:
    _head("Setting up icon")

    # User supplied their own .ico
    if custom_icon:
        p = Path(custom_icon)
        if p.exists() and p.suffix.lower() == ".ico":
            _ok(f"Using custom icon: {p}")
            return p
        else:
            _warn(f"Custom icon not found or not .ico: {p}  — skipping.")
            return None

    # Already downloaded previously
    if ICON_PATH.exists():
        _ok(f"Icon already exists: {ICON_PATH}")
        return ICON_PATH

    # Download and convert
    try:
        from PIL import Image
    except ImportError:
        _warn("Pillow not available — cannot create icon.  Run with --no-icon to skip.")
        return None

    try:
        print(f"  Downloading icon from Re:Zero wiki...", end=" ", flush=True)
        req = urllib.request.Request(ICON_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        print("OK")

        img = Image.open(io.BytesIO(data)).convert("RGBA")
        # Portrait crop — top square captures face + upper body
        w, h = img.size
        side = min(w, h)
        img  = img.crop((( w - side) // 2, 0, (w - side) // 2 + side, side))

        sizes  = [256, 128, 64, 48, 32, 16]
        frames = []
        for s in sizes:
            canvas = Image.new("RGBA", (s, s), (0, 0, 0, 0))
            thumb  = img.copy()
            thumb.thumbnail((s, s), Image.LANCZOS)
            canvas.paste(thumb, ((s - thumb.width) // 2, (s - thumb.height) // 2))
            frames.append(canvas)

        frames[0].save(str(ICON_PATH), format="ICO",
                       sizes=[(s, s) for s in sizes],
                       append_images=frames[1:])
        _ok(f"Icon saved: {ICON_PATH}")
        return ICON_PATH

    except Exception as e:
        _warn(f"Icon download failed: {e}")
        _info("You can supply your own with: python install.py --icon youricon.ico")
        return None


# ── Step 5: Log directory ─────────────────────────────────────────────────────

def create_log_dir(log_dir: Path):
    _head("Creating log directory")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        _ok(f"{log_dir}")
    except OSError as e:
        _warn(f"Could not create log directory: {e}")


# ── Step 6: Desktop shortcut (Windows only) ───────────────────────────────────

def create_shortcut(icon_path: Path | None, log_dir: Path):
    _head("Creating desktop shortcut")

    if sys.platform != "win32":
        _info("Desktop shortcut creation is Windows-only — skipping.")
        return

    pythonw = shutil.which("pythonw") or shutil.which("python") or sys.executable
    gui_script = SCRIPT_DIR / "mkv_subdoctor_gui.py"

    if not gui_script.exists():
        _warn(f"GUI script not found: {gui_script}")
        return

    # Prefer OneDrive Desktop if it exists (synced desktop)
    home = Path.home()
    for candidate in [
        home / "OneDrive" / "Desktop",
        home / "Desktop",
    ]:
        if candidate.exists():
            desktop = candidate
            break
    else:
        _warn("Could not locate Desktop folder.")
        return

    lnk_path   = desktop / "MKV SubDoctor.lnk"
    icon_str   = str(icon_path) if icon_path and icon_path.exists() else ""
    icon_clause = f'$lnk.IconLocation = "{icon_str},0"' if icon_str else ""

    ps = f"""
$ws  = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut('{lnk_path}')
$lnk.TargetPath       = '{pythonw}'
$lnk.Arguments        = '"{gui_script}"'
$lnk.WorkingDirectory = '{SCRIPT_DIR}'
$lnk.Description      = 'MKV SubDoctor — manage subtitle tracks in MKV files'
{icon_clause}
$lnk.Save()
Write-Host 'Shortcut saved.'
"""
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        _ok(f"Shortcut created: {lnk_path}")
    else:
        _warn(f"Shortcut creation failed:\n{r.stderr.strip()}")


# ── Summary ───────────────────────────────────────────────────────────────────

def _summary(log_dir: Path):
    gui  = SCRIPT_DIR / "mkv_subdoctor_gui.py"
    cli  = SCRIPT_DIR / "mkv_subdoctor.py"
    print("\n" + "=" * 60)
    print("  Installation complete!")
    print("=" * 60)
    print(f"\n  GUI:  python \"{gui}\"")
    print(f"  CLI:  python \"{cli}\" --help")
    print(f"  Logs: {log_dir}")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="MKV SubDoctor installer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--ocr",          action="store_true",
                    help="Also install pytesseract for image-based subtitle OCR")
    ap.add_argument("--no-shortcut",  action="store_true",
                    help="Skip desktop shortcut creation")
    ap.add_argument("--no-icon",      action="store_true",
                    help="Skip icon download")
    ap.add_argument("--icon",         metavar="PATH",
                    help="Use a custom .ico file instead of downloading one")
    ap.add_argument("--log-dir",      metavar="DIR",  default=str(LOG_DIR_DEFAULT),
                    help=f"Directory for per-series log files (default: {LOG_DIR_DEFAULT})")
    args = ap.parse_args()

    _banner()
    check_python()
    install_packages(ocr=args.ocr)
    check_mkvtoolnix()

    icon_path = None
    if not args.no_icon:
        icon_path = download_icon(custom_icon=args.icon)

    log_dir = Path(args.log_dir)
    create_log_dir(log_dir)

    if not args.no_shortcut:
        create_shortcut(icon_path, log_dir)

    _summary(log_dir)


if __name__ == "__main__":
    main()
