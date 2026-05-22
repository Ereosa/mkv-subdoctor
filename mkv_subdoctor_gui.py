#!/usr/bin/env python3
"""
mkv_subdoctor_gui.py  —  MKV SubDoctor (GUI)

Graphical front-end for mkv_subdoctor.py.
Includes integrated Video Converter powered by ffmpeg.

Features — Track Manager:
  - File / folder selector
  - Multi-language keep selection (checkboxes + custom code entry)
  - Language remap pairs (for mislabeled image tracks)
  - Dry-run, Recursive, No-Log toggles
  - Configurable log directory
  - Live output display with auto-scroll
  - Start / Pause-Resume / Stop controls
  - Per-file progress bar
  - Dark / Light mode toggle (preference saved between sessions)

Features — Video Converter:
  - Batch video conversion with ffmpeg
  - 5 built-in presets (Shield Optimal, Plex Universal, 1080p Web, 4K HEVC Archive, Custom)
  - Hardware acceleration (NVIDIA NVENC, Intel QSV, AMD AMF)
  - Configurable container, codec, CRF, resolution, audio, subtitles
  - Skip already compatible files option
  - Real-time per-file progress in treeview

Requirements:
  pip install langdetect pyspellchecker pillow
  MKVToolNix in PATH or default install location
  ffmpeg / ffprobe in PATH or C:\\ffmpeg\\bin
  mkv_subdoctor.py in the same folder as this script
"""

import contextlib
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import List, Optional

try:
    from PIL import Image as _PILImage, ImageTk as _ImageTk
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

_BMC_URL = "https://buymeacoffee.com/mkvsubdoctor"

# ── Load the core module ──────────────────────────────────────────────────────

_SCRIPT_DIR = Path(__file__).parent
_BMC_IMG    = _SCRIPT_DIR / "bmc-button.png"
_CONFIG     = _SCRIPT_DIR / "mkv_subdoctor_config.json"
_HISTORY    = _SCRIPT_DIR / "mkv_subdoctor_history.json"
sys.path.insert(0, str(_SCRIPT_DIR))

try:
    import mkv_subdoctor as core
except ImportError as e:
    _r = tk.Tk()
    _r.withdraw()
    messagebox.showerror(
        "Import Error",
        f"Could not import mkv_subdoctor.py from:\n{_SCRIPT_DIR}\n\n{e}",
    )
    sys.exit(1)

# ── Colour palettes ───────────────────────────────────────────────────────────

_DARK: dict[str, str] = {
    "bg":        "#1e1e1e",   # main window / frame background
    "bg2":       "#252526",   # trough / scrollbar track
    "bg3":       "#2d2d2d",   # panel / labelframe interior
    "fg":        "#d4d4d4",   # primary text
    "entry_bg":  "#3c3c3c",   # text inputs, listboxes
    "border":    "#454545",   # widget borders
    "btn_bg":    "#3a3d41",   # button face
    "btn_act":   "#4e5256",   # button hover/active
    "sel_bg":    "#264f78",   # selection highlight
    "sel_fg":    "#ffffff",
    "out_bg":    "#1e1e1e",   # output ScrolledText / log
    "out_fg":    "#c8c8c8",
    "prog":      "#0078d4",   # progress bar fill
    "accent":    "#89b4fa",   # blue accent (headings, labels)
    "success":   "#a6e3a1",   # green (done)
    "error_fg":  "#f38ba8",   # red (error)
    "warn":      "#fab387",   # orange (converting / warning)
    "muted":     "#6c7086",   # grey (skipped / cancelled)
}

_LIGHT: dict[str, str] = {
    "bg":        "#f0f0f0",
    "bg2":       "#ffffff",
    "bg3":       "#e8e8e8",
    "fg":        "#000000",
    "entry_bg":  "#ffffff",
    "border":    "#aaaaaa",
    "btn_bg":    "#e1e1e1",
    "btn_act":   "#c8c8c8",
    "sel_bg":    "#0078d4",
    "sel_fg":    "#ffffff",
    "out_bg":    "#ffffff",
    "out_fg":    "#000000",
    "prog":      "#0078d4",
    "accent":    "#0063b1",
    "success":   "#107c10",
    "error_fg":  "#c42b1c",
    "warn":      "#ca5010",
    "muted":     "#767676",
}

# ── Language menu ─────────────────────────────────────────────────────────────

LANGUAGE_OPTIONS: list[tuple[str, str]] = [
    ("English",                "en"),
    ("Japanese",               "ja"),
    ("French",                 "fr"),
    ("German",                 "de"),
    ("Spanish",                "es"),
    ("Portuguese",             "pt"),
    ("Chinese",                "zh"),
    ("Korean",                 "ko"),
    ("Arabic",                 "ar"),
    ("Russian",                "ru"),
    ("Italian",                "it"),
    ("Indonesian",             "id"),
    ("Thai",                   "th"),
    ("Vietnamese",             "vi"),
    ("Dutch",                  "nl"),
    ("Turkish",                "tr"),
    ("Polish",                 "pl"),
    ("Czech",                  "cs"),
    ("Hungarian",              "hu"),
    ("Romanian",               "ro"),
    ("Finnish",                "fi"),
    ("Swedish",                "sv"),
    ("Norwegian",              "no"),
    ("Danish",                 "da"),
    ("Croatian",               "hr"),
    ("Bulgarian",              "bg"),
    ("Slovak",                 "sk"),
    ("Ukrainian",              "uk"),
    ("Hebrew",                 "he"),
    ("Hindi",                  "hi"),
    ("Malay",                  "ms"),
    ("Greek",                  "el"),
]

# ── Video Converter constants ─────────────────────────────────────────────────

VIDEO_EXTENSIONS = {
    ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".m4v", ".ts", ".mp4", ".m2ts", ".mts", ".divx", ".vob", ".ogv",
}

PRESETS = {
    "No Change": {
        "desc": "Skip conversion entirely — ideal with Combined Run to clean tracks without transcoding",
        "container": "mkv",  "vcodec": "copy",    "crf": 0,    "preset": "fast",
        "acodec": "copy",    "abitrate": "320k",  "resolution": "original",
        "subtitle": "copy",  "skip_compatible": False,
    },
    "Shield Optimal": {
        "desc": "NVIDIA Shield direct-play: H.265 MKV, AC3/DTS passthrough",
        "container": "mkv",  "vcodec": "libx265", "crf": 20, "preset": "fast",
        "acodec": "copy",    "abitrate": "320k",  "resolution": "original",
        "subtitle": "copy",  "skip_compatible": True,
    },
    "Plex Universal": {
        "desc": "Broadest client compatibility: H.264 MP4, AAC stereo",
        "container": "mp4",  "vcodec": "libx264", "crf": 18, "preset": "medium",
        "acodec": "aac",     "abitrate": "192k",  "resolution": "original",
        "subtitle": "copy",  "skip_compatible": True,
    },
    "1080p Web": {
        "desc": "Cap at 1080p, H.264 MP4 — good for remote streaming",
        "container": "mp4",  "vcodec": "libx264", "crf": 20, "preset": "medium",
        "acodec": "aac",     "abitrate": "192k",  "resolution": "1920x1080",
        "subtitle": "copy",  "skip_compatible": False,
    },
    "4K HEVC Archive": {
        "desc": "High-quality archive: H.265 MKV at original resolution",
        "container": "mkv",  "vcodec": "libx265", "crf": 18, "preset": "slow",
        "acodec": "copy",    "abitrate": "320k",  "resolution": "original",
        "subtitle": "copy",  "skip_compatible": True,
    },
    "Custom": {
        "desc": "Configure all settings manually below",
        "container": "mp4",  "vcodec": "libx264", "crf": 18, "preset": "medium",
        "acodec": "aac",     "abitrate": "192k",  "resolution": "original",
        "subtitle": "copy",  "skip_compatible": True,
    },
}

# ── FileInfo dataclass ────────────────────────────────────────────────────────

@dataclass
class FileInfo:
    path: Path
    video_codec: str  = "—"
    audio_codec: str  = "—"
    resolution:  str  = "—"
    duration:    float = 0.0
    size_mb:     float = 0.0
    status:      str  = "Pending"
    progress:    float = 0.0
    row_id:      str  = ""

    @property
    def is_plex_compatible(self) -> bool:
        container = self.path.suffix.lower()
        vc = self.video_codec.lower()
        ac = self.audio_codec.lower()
        return (
            container in {".mp4", ".mkv", ".mov"}
            and vc in {"h264", "hevc", "h265", "av1"}
            and ac in {"aac", "mp3", "ac3", "eac3", "dts", "opus", "flac", "truehd"}
        )

# ── Stdout redirector ─────────────────────────────────────────────────────────

class _QueueStream:
    """File-like object that funnels writes into a thread-safe Queue."""

    def __init__(self, q: queue.Queue):
        self._q   = q
        self._buf = ""

    def write(self, text: str):
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._q.put(line + "\n")

    def flush(self):
        if self._buf:
            self._q.put(self._buf)
            self._buf = ""

# ── Main application ──────────────────────────────────────────────────────────

class App(tk.Tk):
    # ── Init ──────────────────────────────────────────────────────────────────

    def __init__(self):
        super().__init__()
        self.title("MKV SubDoctor")
        self.geometry("1100x820")
        self.minsize(900, 640)

        _ico = _SCRIPT_DIR / "rem_icon.ico"
        if _ico.exists():
            try:
                self.iconbitmap(str(_ico))
            except Exception:
                pass

        # Track Manager state
        self._output_q:   queue.Queue = queue.Queue()
        self._worker:     threading.Thread | None = None
        self._paused:     bool = False
        self._total:      int  = 0
        self._done:       int  = 0
        self._custom_langs: set[str] = set()

        # Video Converter state
        self._conv_files:        List[FileInfo]             = []
        self._conv_converting:   bool                       = False
        self._conv_stop_flag:    threading.Event            = threading.Event()
        self._conv_log_q:        queue.Queue                = queue.Queue()
        self._conv_current_proc: Optional[subprocess.Popen] = None
        self._ffmpeg  = self._find_exe("ffmpeg")
        self._ffprobe = self._find_exe("ffprobe")
        self._conv_probe_sem = threading.Semaphore(4)
        self._history_lock   = threading.Lock()

        # Load saved preferences
        prefs = self._load_prefs()
        self._dark_mode = tk.BooleanVar(value=prefs.get("dark_mode", True))

        self._build_ui()
        self._restore_prefs(prefs)   # re-apply saved widget states
        self._apply_theme()
        self._poll_output()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Preference persistence ────────────────────────────────────────────────

    def _load_prefs(self) -> dict:
        try:
            if _CONFIG.exists():
                return json.loads(_CONFIG.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save_prefs(self):
        try:
            prefs: dict = {"dark_mode": self._dark_mode.get()}

            # ── Track Manager ─────────────────────────────────────────────
            if hasattr(self, "_recursive_var"):
                prefs.update({
                    "tm_recursive":      self._recursive_var.get(),
                    "tm_dry_run":        self._dry_run_var.get(),
                    "tm_no_log":         self._no_log_var.get(),
                    "tm_spell_check":    self._spell_check_var.get(),
                    "tm_manage_audio":   self._manage_audio_var.get(),
                    "tm_keep_langs":     [c for c, v in self._lang_vars.items() if v.get()],
                    "tm_custom_langs":   sorted(self._custom_langs),
                    "tm_audio_langs":    list(self._audio_lang_lb.get(0, "end")),
                    "tm_log_dir":        self._log_dir_var.get(),
                    "tm_remaps":         list(self._remap_lb.get(0, "end")),
                    "tm_sub_primary":      self._sub_primary_var.get(),
                    "tm_audio_primary":    self._audio_primary_var.get(),
                    "tm_skip_processed":   self._tm_skip_processed_var.get(),
                })

            # ── Video Converter ───────────────────────────────────────────
            if hasattr(self, "_conv_preset_var"):
                prefs.update({
                    "conv_preset":       self._conv_preset_var.get(),
                    "conv_output_dir":   self._conv_output_var.get(),
                    "conv_container":    self._conv_container_var.get(),
                    "conv_vcodec":       self._conv_vcodec_var.get(),
                    "conv_crf":          self._conv_crf_var.get(),
                    "conv_enc_preset":   self._conv_enc_preset_var.get(),
                    "conv_resolution":   self._conv_resolution_var.get(),
                    "conv_hwaccel":      self._conv_hwaccel_var.get(),
                    "conv_acodec":       self._conv_acodec_var.get(),
                    "conv_abitrate":     self._conv_abitrate_var.get(),
                    "conv_subtitle":     self._conv_subtitle_var.get(),
                    "conv_skip_compat":  self._conv_skip_compat_var.get(),
                    "conv_overwrite":    self._conv_overwrite_var.get(),
                    "conv_del_orig":        self._conv_del_orig_var.get(),
                    "conv_replace_orig":    self._conv_replace_orig_var.get(),
                    "conv_skip_processed":  self._conv_skip_processed_var.get(),
                })

            _CONFIG.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _restore_prefs(self, prefs: dict):
        """Apply saved settings to all widgets after _build_ui() completes."""
        if not prefs:
            return

        # ── Track Manager ─────────────────────────────────────────────────
        self._recursive_var.set(  prefs.get("tm_recursive",    True))
        self._dry_run_var.set(    prefs.get("tm_dry_run",       False))
        self._no_log_var.set(     prefs.get("tm_no_log",        False))
        self._spell_check_var.set(prefs.get("tm_spell_check",   False))
        self._manage_audio_var.set(prefs.get("tm_manage_audio", False))

        # Language checkboxes
        keep = set(prefs.get("tm_keep_langs", ["en"]))
        for code, var in self._lang_vars.items():
            var.set(code in keep)

        # Custom language codes
        for code in prefs.get("tm_custom_langs", []):
            self._custom_langs.add(code)
        if self._custom_langs:
            self._custom_lang_display.configure(
                text="Custom: " + ", ".join(sorted(self._custom_langs)))

        # Audio language listbox
        self._audio_lang_lb.delete(0, "end")
        for lang in prefs.get("tm_audio_langs", ["en"]):
            self._audio_lang_lb.insert("end", lang)

        # Language remap listbox
        self._remap_lb.delete(0, "end")
        for entry in prefs.get("tm_remaps", []):
            self._remap_lb.insert("end", entry)

        # Log directory
        self._log_dir_var.set(prefs.get("tm_log_dir", str(core._LOG_DIR_DEFAULT)))

        # Primary language selectors
        self._sub_primary_var.set(  prefs.get("tm_sub_primary",   "(auto)"))
        self._audio_primary_var.set(prefs.get("tm_audio_primary", "(auto)"))

        self._tm_skip_processed_var.set(prefs.get("tm_skip_processed", False))

        # Refresh disabled state of audio options sub-panel
        self._on_manage_audio_toggle()

        # ── Video Converter ───────────────────────────────────────────────
        if "conv_preset" not in prefs:
            return

        self._conv_preset_var.set(    prefs.get("conv_preset",     "Shield Optimal"))
        self._conv_output_var.set(    prefs.get("conv_output_dir", "Same as source"))
        self._conv_container_var.set( prefs.get("conv_container",  "mkv"))
        self._conv_vcodec_var.set(    prefs.get("conv_vcodec",     "libx265"))

        crf = prefs.get("conv_crf", 20)
        self._conv_crf_var.set(crf)
        self._conv_crf_lbl.config(text=str(crf))

        self._conv_enc_preset_var.set( prefs.get("conv_enc_preset",  "fast"))
        self._conv_resolution_var.set( prefs.get("conv_resolution",  "original"))
        self._conv_hwaccel_var.set(    prefs.get("conv_hwaccel",     "none"))
        self._conv_acodec_var.set(     prefs.get("conv_acodec",      "copy"))
        self._conv_abitrate_var.set(   prefs.get("conv_abitrate",    "320k"))
        self._conv_subtitle_var.set(   prefs.get("conv_subtitle",    "copy"))
        self._conv_skip_compat_var.set( prefs.get("conv_skip_compat", True))
        self._conv_overwrite_var.set(  prefs.get("conv_overwrite",   False))
        self._conv_del_orig_var.set(    prefs.get("conv_del_orig",       False))
        self._conv_replace_orig_var.set(prefs.get("conv_replace_orig",  False))
        self._conv_skip_processed_var.set(prefs.get("conv_skip_processed", False))

        # Update preset description label
        p = PRESETS.get(prefs.get("conv_preset", "Shield Optimal"), PRESETS["Custom"])
        self._conv_preset_desc_lbl.config(text=p["desc"])

    def _on_close(self):
        """Save all settings then close the window."""
        self._save_prefs()
        self.destroy()

    # ── Processing history ────────────────────────────────────────────────────

    def _history_load(self) -> dict:
        """Load the history database from disk; returns {} on any error."""
        try:
            if _HISTORY.exists():
                return json.loads(_HISTORY.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _history_save(self, history: dict):
        """Write the history database to disk."""
        try:
            _HISTORY.write_text(json.dumps(history, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _history_check(self, path: Path, op_hash: str) -> bool:
        """Return True if this file was already processed with the same options.

        Match criteria:
          • file size in bytes must be identical
          • mtime must be within 2 seconds (FAT32 tolerance)
          • op_hash (MD5 of options) must be identical
        """
        try:
            st = path.stat()
        except OSError:
            return False
        with self._history_lock:
            history = self._history_load()
        entry = history.get(str(path))
        if not entry:
            return False
        return (
            entry.get("size")    == st.st_size
            and abs(entry.get("mtime", 0) - st.st_mtime) <= 2.0
            and entry.get("op_hash") == op_hash
        )

    def _history_record(self, path: Path, op_hash: str):
        """Record a successful processing run for *path* using its current stats."""
        try:
            st = path.stat()
            self._history_record_at(path, st.st_size, st.st_mtime, op_hash)
        except OSError:
            pass

    def _history_record_at(self, path: Path, size: int, mtime: float, op_hash: str):
        """Record a successful processing run using pre-captured file stats.

        Use this variant when the file may have been renamed/deleted (replace-original)
        and you captured size/mtime BEFORE the operation.
        """
        import datetime
        with self._history_lock:
            history = self._history_load()
            history[str(path)] = {
                "size":         size,
                "mtime":        mtime,
                "op_hash":      op_hash,
                "processed_at": datetime.datetime.now().isoformat(timespec="seconds"),
            }
            self._history_save(history)

    def _history_clear(self):
        """Delete the history database file and log the action."""
        try:
            if _HISTORY.exists():
                _HISTORY.unlink()
            self._output_q.put("[history] Processing history cleared.\n")
        except Exception as e:
            self._output_q.put(f"[history] Could not clear history: {e}\n")

    def _compute_tm_hash(self, keep_langs, remaps, manage_audio, audio_langs,
                         preferred_sub_lang, preferred_audio_lang, spell_check) -> str:
        """MD5 (first 16 hex chars) of the Track Manager option set."""
        payload = json.dumps({
            "keep_langs":           sorted(keep_langs),
            "remaps":               sorted(remaps) if remaps else [],
            "manage_audio":         manage_audio,
            "audio_langs":          sorted(audio_langs) if audio_langs else [],
            "preferred_sub_lang":   preferred_sub_lang,
            "preferred_audio_lang": preferred_audio_lang,
            "spell_check":          spell_check,
        }, sort_keys=True)
        return hashlib.md5(payload.encode()).hexdigest()[:16]

    def _compute_conv_hash(self) -> str:
        """MD5 (first 16 hex chars) of the Video Converter option set."""
        payload = json.dumps({
            "preset":      self._conv_preset_var.get(),
            "container":   self._conv_container_var.get(),
            "vcodec":      self._conv_vcodec_var.get(),
            "crf":         self._conv_crf_var.get(),
            "enc_preset":  self._conv_enc_preset_var.get(),
            "resolution":  self._conv_resolution_var.get(),
            "hwaccel":     self._conv_hwaccel_var.get(),
            "acodec":      self._conv_acodec_var.get(),
            "abitrate":    self._conv_abitrate_var.get(),
            "subtitle":    self._conv_subtitle_var.get(),
        }, sort_keys=True)
        return hashlib.md5(payload.encode()).hexdigest()[:16]

    # ── ffmpeg / ffprobe discovery ────────────────────────────────────────────

    def _find_exe(self, name: str) -> Optional[str]:
        found = shutil.which(name)
        if found:
            return found
        candidates = [
            rf"C:\ffmpeg\bin\{name}.exe",
            rf"C:\Program Files\ffmpeg\bin\{name}.exe",
            rf"C:\Program Files (x86)\ffmpeg\bin\{name}.exe",
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
        return None

    # ── Theme engine ──────────────────────────────────────────────────────────

    def _apply_theme(self):
        c = _DARK if self._dark_mode.get() else _LIGHT

        # ── ttk style ────────────────────────────────────────────────────────
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure(".",
            background=c["bg"],
            foreground=c["fg"],
            fieldbackground=c["entry_bg"],
            troughcolor=c["bg2"],
            bordercolor=c["border"],
            darkcolor=c["bg3"],
            lightcolor=c["bg3"],
            selectbackground=c["sel_bg"],
            selectforeground=c["sel_fg"],
        )
        style.configure("TFrame",        background=c["bg"])
        style.configure("TLabel",        background=c["bg"],  foreground=c["fg"])
        style.configure("TLabelframe",   background=c["bg3"], bordercolor=c["border"],
                        darkcolor=c["border"], lightcolor=c["border"])
        style.configure("TLabelframe.Label", background=c["bg3"], foreground=c["fg"])
        style.configure("TCheckbutton",  background=c["bg"],  foreground=c["fg"],
                        focuscolor=c["bg"])
        style.map("TCheckbutton",
            background=[("active", c["bg"]), ("disabled", c["bg"])],
            foreground=[("active", c["fg"])])
        style.configure("TRadiobutton",  background=c["bg"],  foreground=c["fg"],
                        focuscolor=c["bg"])
        style.map("TRadiobutton",
            background=[("active", c["bg"])],
            foreground=[("active", c["fg"])])
        style.configure("TButton",
            background=c["btn_bg"], foreground=c["fg"],
            bordercolor=c["border"],
            darkcolor=c["btn_bg"], lightcolor=c["btn_bg"],
            padding=(6, 3),
        )
        style.map("TButton",
            background=[("active", c["btn_act"]), ("disabled", c["bg2"])],
            foreground=[("disabled", c["border"])],
            darkcolor=[("active", c["btn_act"])],
            lightcolor=[("active", c["btn_act"])],
        )
        style.configure("Accent.TButton",
            background=c["accent"], foreground=c["bg"],
            font=("Segoe UI", 9, "bold"), padding=(10, 5),
        )
        style.map("Accent.TButton",
            background=[("active", c["sel_bg"]), ("disabled", c["bg2"])],
            foreground=[("disabled", c["border"])],
        )
        style.configure("Danger.TButton",
            background=c["error_fg"], foreground=c["bg"],
            font=("Segoe UI", 9, "bold"), padding=(10, 5),
        )
        style.map("Danger.TButton",
            background=[("active", c["warn"]), ("disabled", c["bg2"])],
            foreground=[("disabled", c["border"])],
        )
        style.configure("TEntry",
            fieldbackground=c["entry_bg"], foreground=c["fg"],
            insertcolor=c["fg"], bordercolor=c["border"],
            selectbackground=c["sel_bg"], selectforeground=c["sel_fg"],
        )
        style.configure("TCombobox",
            fieldbackground=c["entry_bg"], background=c["btn_bg"],
            foreground=c["fg"], arrowcolor=c["fg"],
            selectbackground=c["entry_bg"], selectforeground=c["fg"],
        )
        style.map("TCombobox",
            fieldbackground=[("readonly", c["entry_bg"])],
            background=[("readonly", c["btn_bg"])],
        )
        style.configure("TScrollbar",
            background=c["btn_bg"], troughcolor=c["bg2"],
            arrowcolor=c["fg"], bordercolor=c["border"],
            darkcolor=c["bg2"], lightcolor=c["bg2"],
        )
        style.map("TScrollbar",
            background=[("active", c["btn_act"])],
            arrowcolor=[("disabled", c["border"])],
        )
        style.configure("TProgressbar",
            background=c["prog"], troughcolor=c["bg2"],
            bordercolor=c["border"], darkcolor=c["bg2"], lightcolor=c["bg2"],
        )
        style.configure("Horizontal.TProgressbar",
            background=c["prog"], troughcolor=c["bg2"],
            thickness=10,
        )
        style.configure("TSeparator", background=c["border"])
        style.configure("TNotebook",
            background=c["bg2"], tabmargins=0,
        )
        style.configure("TNotebook.Tab",
            background=c["bg3"], foreground=c["fg"],
            padding=(14, 6),
        )
        style.map("TNotebook.Tab",
            background=[("selected", c["bg"])],
            foreground=[("selected", c["fg"])],
        )
        style.configure("Treeview",
            background=c["bg2"], foreground=c["fg"],
            fieldbackground=c["bg2"], rowheight=26,
        )
        style.configure("Treeview.Heading",
            background=c["bg3"], foreground=c["accent"],
            font=("Segoe UI", 9, "bold"), relief="flat",
        )
        style.map("Treeview",
            background=[("selected", c["sel_bg"])],
            foreground=[("selected", c["sel_fg"])],
        )
        style.configure("TScale",
            background=c["bg"], troughcolor=c["bg3"],
        )

        # ── tk (non-ttk) widgets ──────────────────────────────────────────────
        self.configure(bg=c["bg"])

        for lb in (self._path_lb, self._remap_lb, self._audio_lang_lb):
            lb.configure(
                bg=c["entry_bg"], fg=c["fg"],
                selectbackground=c["sel_bg"], selectforeground=c["sel_fg"],
                highlightbackground=c["border"], highlightcolor=c["border"],
            )

        self._output_txt.configure(
            bg=c["out_bg"], fg=c["out_fg"],
            insertbackground=c["fg"],
            selectbackground=c["sel_bg"], selectforeground=c["sel_fg"],
        )

        self._lang_canvas.configure(bg=c["bg3"])
        self._right_canvas.configure(bg=c["bg"])

        if hasattr(self, "_bmc_label"):
            self._bmc_label.configure(bg=c["bg"])

        # Video Converter tk widgets
        if hasattr(self, "_conv_log_text"):
            self._conv_log_text.configure(
                bg=c["out_bg"], fg=c["out_fg"],
                insertbackground=c["fg"],
            )
        if hasattr(self, "_conv_settings_canvas"):
            self._conv_settings_canvas.configure(bg=c["bg"])
        if hasattr(self, "_conv_ffmpeg_lbl"):
            ok_color = c["success"] if self._ffmpeg else c["error_fg"]
            self._conv_ffmpeg_lbl.configure(bg=c["bg3"], fg=ok_color)
        if hasattr(self, "_conv_crf_lbl"):
            self._conv_crf_lbl.configure(bg=c["bg"], fg=c["accent"])
        if hasattr(self, "_conv_preset_desc_lbl"):
            self._conv_preset_desc_lbl.configure(bg=c["bg3"], fg=c["muted"])
        if hasattr(self, "_conv_tree"):
            self._conv_tree.tag_configure("done",       foreground=c["success"])
            self._conv_tree.tag_configure("error",      foreground=c["error_fg"])
            self._conv_tree.tag_configure("converting", foreground=c["warn"])
            self._conv_tree.tag_configure("skipped",    foreground=c["muted"])
            self._conv_tree.tag_configure("cancelled",  foreground=c["muted"])
            self._conv_tree.tag_configure("pending",    foreground=c["fg"])

        self._save_prefs()

    def _toggle_theme(self):
        self._apply_theme()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self._notebook = ttk.Notebook(self, padding=0)
        self._notebook.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

        # Tab 1: Track Manager
        tab1 = ttk.Frame(self._notebook)
        self._notebook.add(tab1, text="  Track Manager  ")
        self._build_track_manager_tab(tab1)

        # Tab 2: Video Converter
        tab2 = ttk.Frame(self._notebook)
        self._notebook.add(tab2, text="  Video Converter  ")
        self._build_video_converter_tab(tab2)

    # ── Tab 1: Track Manager ──────────────────────────────────────────────────

    def _build_track_manager_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=3)   # files + options
        parent.rowconfigure(1, weight=0)   # controls bar
        parent.rowconfigure(2, weight=4)   # output

        top = ttk.Frame(parent, padding=5)
        top.grid(row=0, column=0, sticky="nsew")
        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, weight=1)
        top.rowconfigure(0, weight=1)

        self._build_files_panel(top)
        self._build_options_panel(top)
        self._build_controls(parent)
        self._build_output(parent)

    # -- Files panel -----------------------------------------------------------

    def _build_files_panel(self, parent):
        frm = ttk.LabelFrame(parent, text="Files & Folders", padding=5)
        frm.grid(row=0, column=0, sticky="nsew", padx=(0, 4), pady=3)
        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(0, weight=1)

        lbf = ttk.Frame(frm)
        lbf.grid(row=0, column=0, sticky="nsew")
        lbf.columnconfigure(0, weight=1)
        lbf.rowconfigure(0, weight=1)

        self._path_lb = tk.Listbox(lbf, selectmode=tk.EXTENDED, height=9,
                                    font=("Segoe UI", 9))
        self._path_lb.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(lbf, orient="vertical", command=self._path_lb.yview)
        sb.grid(row=0, column=1, sticky="ns")
        sb_h = ttk.Scrollbar(lbf, orient="horizontal", command=self._path_lb.xview)
        sb_h.grid(row=1, column=0, sticky="ew")
        self._path_lb.configure(yscrollcommand=sb.set, xscrollcommand=sb_h.set)

        bf = ttk.Frame(frm)
        bf.grid(row=0, column=1, sticky="n", padx=(6, 0))
        ttk.Button(bf, text="Add Files…",  command=self._add_files,   width=13).pack(fill="x", pady=2)
        ttk.Button(bf, text="Add Folder…", command=self._add_folder,  width=13).pack(fill="x", pady=2)
        ttk.Separator(bf).pack(fill="x", pady=5)
        ttk.Button(bf, text="Remove Sel.", command=self._remove_sel,  width=13).pack(fill="x", pady=2)
        ttk.Button(bf, text="Clear All",   command=self._clear_paths, width=13).pack(fill="x", pady=2)

    # -- Options panel ---------------------------------------------------------

    def _build_options_panel(self, parent):
        frm = ttk.LabelFrame(parent, text="Options", padding=5)
        frm.grid(row=0, column=1, sticky="nsew", padx=(4, 0), pady=3)
        frm.columnconfigure(0, weight=3)
        frm.columnconfigure(1, weight=2)
        frm.rowconfigure(0, weight=1)

        self._build_lang_panel(frm)
        self._build_right_options(frm)

    def _build_lang_panel(self, parent):
        lang_frm = ttk.LabelFrame(parent, text="Languages to Keep", padding=5)
        lang_frm.grid(row=0, column=0, sticky="nsew", padx=(0, 4), pady=3)
        lang_frm.columnconfigure(0, weight=1)
        lang_frm.rowconfigure(0, weight=1)

        self._lang_canvas = tk.Canvas(lang_frm, highlightthickness=0)
        self._lang_canvas.grid(row=0, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(lang_frm, orient="vertical", command=self._lang_canvas.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        self._lang_canvas.configure(yscrollcommand=vsb.set)

        inner = ttk.Frame(self._lang_canvas)
        win_id = self._lang_canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_frame_configure(e):
            self._lang_canvas.configure(scrollregion=self._lang_canvas.bbox("all"))
        def _on_canvas_configure(e):
            self._lang_canvas.itemconfig(win_id, width=e.width)

        inner.bind("<Configure>", _on_frame_configure)
        self._lang_canvas.bind("<Configure>", _on_canvas_configure)

        def _on_enter(_e):
            self._lang_canvas.bind_all("<MouseWheel>",
                lambda ev: self._lang_canvas.yview_scroll(-1 * (ev.delta // 120), "units"))
        def _on_leave(_e):
            self._lang_canvas.unbind_all("<MouseWheel>")
        self._lang_canvas.bind("<Enter>", _on_enter)
        self._lang_canvas.bind("<Leave>", _on_leave)

        self._lang_vars: dict[str, tk.BooleanVar] = {}
        for label, code in LANGUAGE_OPTIONS:
            var = tk.BooleanVar(value=(code == "en"))
            ttk.Checkbutton(inner, text=f"{label}  ({code})",
                            variable=var).pack(anchor="w", padx=4, pady=1)
            self._lang_vars[code] = var

        # Custom language entry
        cust = ttk.Frame(lang_frm)
        cust.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Label(cust, text="Custom code:").pack(side="left")
        self._custom_lang_entry = tk.StringVar()
        ttk.Entry(cust, textvariable=self._custom_lang_entry,
                  width=6).pack(side="left", padx=4)
        ttk.Button(cust, text="Add", command=self._add_custom_lang,
                   width=5).pack(side="left")
        self._custom_lang_display = ttk.Label(cust, text="", foreground="gray")
        self._custom_lang_display.pack(side="left", padx=6)

        # Primary (preferred) subtitle language selector
        prim = ttk.Frame(lang_frm)
        prim.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Label(prim, text="Primary lang:").pack(side="left")
        self._sub_primary_var = tk.StringVar(value="(auto)")
        self._sub_primary_cb = ttk.Combobox(
            prim, textvariable=self._sub_primary_var, width=8, state="readonly")
        self._sub_primary_cb["postcommand"] = self._update_sub_primary_options
        self._sub_primary_cb.pack(side="left", padx=4)
        ttk.Label(prim, text="default track language").pack(side="left")

    def _build_right_options(self, parent):
        outer = ttk.Frame(parent)
        outer.grid(row=0, column=1, sticky="nsew", pady=3)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        self._right_canvas = tk.Canvas(outer, highlightthickness=0)
        self._right_canvas.grid(row=0, column=0, sticky="nsew")
        _vsb = ttk.Scrollbar(outer, orient="vertical", command=self._right_canvas.yview)
        _vsb.grid(row=0, column=1, sticky="ns")
        self._right_canvas.configure(yscrollcommand=_vsb.set)

        right = ttk.Frame(self._right_canvas)
        right.columnconfigure(0, weight=1)
        _win_id = self._right_canvas.create_window((0, 0), window=right, anchor="nw")

        right.bind("<Configure>",
                   lambda e: self._right_canvas.configure(
                       scrollregion=self._right_canvas.bbox("all")))
        self._right_canvas.bind("<Configure>",
                                lambda e: self._right_canvas.itemconfig(_win_id, width=e.width))

        self._right_canvas.bind("<Enter>", lambda _e: self._right_canvas.bind_all(
            "<MouseWheel>",
            lambda ev: self._right_canvas.yview_scroll(-1 * (ev.delta // 120), "units")))
        self._right_canvas.bind("<Leave>", lambda _e: self._right_canvas.unbind_all("<MouseWheel>"))

        # Toggle options
        self._recursive_var        = tk.BooleanVar(value=True)
        self._dry_run_var          = tk.BooleanVar(value=False)
        self._no_log_var           = tk.BooleanVar(value=False)
        self._spell_check_var      = tk.BooleanVar(value=False)
        self._tm_skip_processed_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(right, text="Recursive",       variable=self._recursive_var).pack(anchor="w", pady=2)
        ttk.Checkbutton(right, text="Dry Run",         variable=self._dry_run_var).pack(anchor="w", pady=2)
        ttk.Checkbutton(right, text="Disable Logging", variable=self._no_log_var).pack(anchor="w", pady=2)
        ttk.Checkbutton(right, text="Spell Check",     variable=self._spell_check_var).pack(anchor="w", pady=2)

        skip_row = ttk.Frame(right)
        skip_row.pack(fill="x", pady=2)
        ttk.Checkbutton(skip_row, text="Skip already processed",
                        variable=self._tm_skip_processed_var).pack(side="left")
        ttk.Button(skip_row, text="Clear History", command=self._history_clear,
                   width=13).pack(side="right")

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=8)

        # Language remaps
        remap_frm = ttk.LabelFrame(right, text="Language Remaps", padding=4)
        remap_frm.pack(fill="x", pady=2)
        remap_frm.columnconfigure(0, weight=1)

        self._remap_lb = tk.Listbox(remap_frm, height=4, font=("Consolas", 9))
        self._remap_lb.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 4))
        rsb = ttk.Scrollbar(remap_frm, orient="vertical", command=self._remap_lb.yview)
        rsb.grid(row=0, column=3, sticky="ns")
        self._remap_lb.configure(yscrollcommand=rsb.set)

        row2 = ttk.Frame(remap_frm)
        row2.grid(row=1, column=0, columnspan=4, sticky="ew")
        ttk.Label(row2, text="From:").pack(side="left")
        self._remap_from = tk.StringVar()
        ttk.Entry(row2, textvariable=self._remap_from, width=6).pack(side="left", padx=3)
        ttk.Label(row2, text="->").pack(side="left")
        self._remap_to = tk.StringVar()
        ttk.Entry(row2, textvariable=self._remap_to, width=6).pack(side="left", padx=3)
        ttk.Button(row2, text="Add", command=self._add_remap, width=5).pack(side="left", padx=2)
        ttk.Button(row2, text="Del", command=self._del_remap, width=5).pack(side="left")

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=8)

        # Audio track management
        audio_frm = ttk.LabelFrame(right, text="Audio Tracks", padding=4)
        audio_frm.pack(fill="x", pady=2)
        audio_frm.columnconfigure(0, weight=1)

        self._manage_audio_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(audio_frm, text="Manage Audio Tracks",
                        variable=self._manage_audio_var,
                        command=self._on_manage_audio_toggle).pack(anchor="w")

        self._audio_opts_frm = ttk.Frame(audio_frm)
        self._audio_opts_frm.pack(fill="x", pady=(4, 0))
        self._audio_opts_frm.columnconfigure(0, weight=1)

        ttk.Label(self._audio_opts_frm, text="Keep languages:").grid(
            row=0, column=0, columnspan=3, sticky="w")

        self._audio_lang_lb = tk.Listbox(
            self._audio_opts_frm, height=3, font=("Consolas", 9))
        self._audio_lang_lb.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 2))
        audio_sb = ttk.Scrollbar(self._audio_opts_frm, orient="vertical",
                                  command=self._audio_lang_lb.yview)
        audio_sb.grid(row=1, column=2, sticky="ns")
        self._audio_lang_lb.configure(yscrollcommand=audio_sb.set)

        audio_btn_row = ttk.Frame(self._audio_opts_frm)
        audio_btn_row.grid(row=2, column=0, columnspan=3, sticky="ew")
        self._audio_add_var = tk.StringVar()
        ttk.Entry(audio_btn_row, textvariable=self._audio_add_var,
                  width=6).pack(side="left")
        ttk.Button(audio_btn_row, text="Add", width=5,
                   command=self._audio_add_lang).pack(side="left", padx=2)
        ttk.Button(audio_btn_row, text="Del", width=5,
                   command=self._audio_del_lang).pack(side="left", padx=2)
        ttk.Button(audio_btn_row, text="↑ Match Subtitles", width=16,
                   command=self._audio_match_subtitles).pack(side="left", padx=(6, 0))

        # Primary (preferred) audio language selector
        audio_prim_row = ttk.Frame(self._audio_opts_frm)
        audio_prim_row.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        ttk.Label(audio_prim_row, text="Primary lang:").pack(side="left")
        self._audio_primary_var = tk.StringVar(value="(auto)")
        self._audio_primary_cb = ttk.Combobox(
            audio_prim_row, textvariable=self._audio_primary_var, width=8, state="readonly")
        self._audio_primary_cb["postcommand"] = self._update_audio_primary_options
        self._audio_primary_cb.pack(side="left", padx=4)
        ttk.Label(audio_prim_row, text="(auto = original release language)").pack(side="left")

        self._audio_lang_lb.insert("end", "en")
        self._on_manage_audio_toggle()

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=8)

        # Log directory
        ttk.Label(right, text="Log Directory:").pack(anchor="w")
        log_row = ttk.Frame(right)
        log_row.pack(fill="x", pady=2)
        log_row.columnconfigure(0, weight=1)
        self._log_dir_var = tk.StringVar(value=str(core._LOG_DIR_DEFAULT))
        ttk.Entry(log_row, textvariable=self._log_dir_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(log_row, text="…", width=3,
                   command=self._browse_log_dir).grid(row=0, column=1, padx=(4, 0))

    # -- Controls bar ----------------------------------------------------------

    def _build_controls(self, parent):
        bar = ttk.Frame(parent, padding=(5, 3))
        bar.grid(row=1, column=0, sticky="ew")

        self._start_btn    = ttk.Button(bar, text="  Start",        command=self._start,          width=12)
        self._pause_btn    = ttk.Button(bar, text="  Pause",        command=self._toggle_pause,   width=12, state="disabled")
        self._stop_btn     = ttk.Button(bar, text="  Stop",         command=self._stop,           width=12, state="disabled")
        self._combined_btn = ttk.Button(bar, text="▶ Combined Run", command=self._combined_start, width=16)

        self._start_btn.pack(side="left", padx=4)
        self._pause_btn.pack(side="left", padx=4)
        self._stop_btn.pack(side="left",  padx=4)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6, pady=3)
        self._combined_btn.pack(side="left", padx=4)

        self._status_lbl = ttk.Label(bar, text="Ready.", width=28)
        self._status_lbl.pack(side="left", padx=10)

        self._progress = ttk.Progressbar(bar, mode="determinate", length=220)
        self._progress.pack(side="left", padx=4)

        self._pct_lbl = ttk.Label(bar, text="", width=7)
        self._pct_lbl.pack(side="left")

    # -- Output area -----------------------------------------------------------

    def _build_output(self, parent):
        out_frm = ttk.LabelFrame(parent, text="Output", padding=5)
        out_frm.grid(row=2, column=0, sticky="nsew", padx=5, pady=(0, 5))
        out_frm.columnconfigure(0, weight=1)
        out_frm.rowconfigure(0, weight=1)

        self._output_txt = scrolledtext.ScrolledText(
            out_frm, wrap="none", state="disabled",
            font=("Consolas", 9), height=16,
        )
        self._output_txt.grid(row=0, column=0, sticky="nsew")

        btn_row = ttk.Frame(out_frm)
        btn_row.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        ttk.Button(btn_row, text="Clear", command=self._clear_output).pack(side="left")
        self._autoscroll_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(btn_row, text="Auto-scroll",
                        variable=self._autoscroll_var).pack(side="left", padx=8)
        ttk.Checkbutton(btn_row, text="Dark Mode",
                        variable=self._dark_mode,
                        command=self._toggle_theme).pack(side="left", padx=8)

        bmc_img = self._load_bmc_image(height=32)
        if bmc_img:
            self._bmc_label = tk.Label(btn_row, image=bmc_img, cursor="hand2",
                                       relief="flat", borderwidth=0)
            self._bmc_label.image = bmc_img
        else:
            self._bmc_label = tk.Label(btn_row, text="☕ Buy Me a Coffee",
                                       foreground="#000000", background="#FFDD00",
                                       font=("Segoe UI", 9, "bold"),
                                       cursor="hand2", padx=8, pady=3)
        self._bmc_label.pack(side="right", padx=(0, 2))
        self._bmc_label.bind("<Button-1>", lambda _: webbrowser.open(_BMC_URL))

    # ── Tab 2: Video Converter ────────────────────────────────────────────────

    def _build_video_converter_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=0)   # toolbar
        parent.rowconfigure(1, weight=1)   # paned window (file list + settings)
        parent.rowconfigure(2, weight=0)   # log panel

        self._build_conv_toolbar(parent)
        self._build_conv_paned(parent)
        self._build_conv_log(parent)

    def _build_conv_toolbar(self, parent):
        bar = ttk.Frame(parent, padding=(6, 6))
        bar.grid(row=0, column=0, sticky="ew")

        ttk.Button(bar, text="＋ Add Files",  command=self._conv_add_files).pack(side="left", padx=(0, 4))
        ttk.Button(bar, text="＋ Add Folder", command=self._conv_add_folder).pack(side="left", padx=(0, 4))
        ttk.Button(bar, text="✕ Remove",      command=self._conv_remove_selected).pack(side="left", padx=(0, 4))
        ttk.Button(bar, text="Clear All",     command=self._conv_clear_all).pack(side="left", padx=(0, 16))

        # ffmpeg status badge (tk.Label so we can colour it)
        lbl_text = "  ffmpeg ✓  " if self._ffmpeg else "  ffmpeg ✗ NOT FOUND  "
        self._conv_ffmpeg_lbl = tk.Label(
            bar, text=lbl_text,
            font=("Segoe UI", 8, "bold"), padx=6, pady=3, relief="flat",
        )
        self._conv_ffmpeg_lbl.pack(side="left")

        self._conv_stop_btn = ttk.Button(
            bar, text="■  Stop", style="Danger.TButton",
            command=self._conv_stop, state="disabled",
        )
        self._conv_stop_btn.pack(side="right")

        self._conv_start_btn = ttk.Button(
            bar, text="▶  Start Conversion", style="Accent.TButton",
            command=self._conv_start,
        )
        self._conv_start_btn.pack(side="right", padx=(0, 6))

        self._conv_combined_btn = ttk.Button(
            bar, text="▶ Combined Run",
            command=self._combined_start_conv,
        )
        self._conv_combined_btn.pack(side="right", padx=(0, 6))

    def _build_conv_paned(self, parent):
        pw = ttk.PanedWindow(parent, orient="horizontal")
        pw.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 4))

        self._build_conv_file_panel(pw)
        self._build_conv_settings_panel(pw)

    def _build_conv_file_panel(self, parent):
        frame = ttk.Frame(parent)
        parent.add(frame, weight=3)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        cols   = ("File", "Resolution", "Video", "Audio", "Size", "Status", "Progress")
        widths = (260, 95, 80, 80, 72, 84, 100)

        self._conv_tree = ttk.Treeview(frame, columns=cols, show="headings",
                                        selectmode="extended")
        for col, w in zip(cols, widths):
            self._conv_tree.heading(col, text=col,
                                    command=lambda c=col: self._conv_sort_tree(c))
            self._conv_tree.column(col, width=w, minwidth=50, stretch=(col == "File"))

        vsb = ttk.Scrollbar(frame, orient="vertical",   command=self._conv_tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=self._conv_tree.xview)
        self._conv_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._conv_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        # Overall progress bar
        pf = ttk.Frame(frame)
        pf.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Label(pf, text="Overall:").pack(side="left", padx=(0, 6))
        self._conv_overall_bar = ttk.Progressbar(
            pf, mode="determinate", style="Horizontal.TProgressbar")
        self._conv_overall_bar.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._conv_overall_lbl = ttk.Label(pf, text="0 / 0", width=9)
        self._conv_overall_lbl.pack(side="left")

        # Tree tag colours (set again by _apply_theme)
        self._conv_tree.tag_configure("done",       foreground="#a6e3a1")
        self._conv_tree.tag_configure("error",      foreground="#f38ba8")
        self._conv_tree.tag_configure("converting", foreground="#fab387")
        self._conv_tree.tag_configure("skipped",    foreground="#6c7086")
        self._conv_tree.tag_configure("cancelled",  foreground="#6c7086")
        self._conv_tree.tag_configure("pending",    foreground="#d4d4d4")

    def _build_conv_settings_panel(self, parent):
        outer = ttk.Frame(parent)
        parent.add(outer, weight=1)

        self._conv_settings_canvas = tk.Canvas(
            outer, highlightthickness=0, width=270)
        sb = ttk.Scrollbar(outer, orient="vertical",
                           command=self._conv_settings_canvas.yview)
        self._conv_settings_canvas.configure(yscrollcommand=sb.set)
        self._conv_settings_canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        inner = ttk.Frame(self._conv_settings_canvas)
        win_id = self._conv_settings_canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: self._conv_settings_canvas.configure(
                       scrollregion=self._conv_settings_canvas.bbox("all")))
        self._conv_settings_canvas.bind("<Configure>",
            lambda e: self._conv_settings_canvas.itemconfig(win_id, width=e.width))

        # Scoped mouse-wheel: only scroll settings canvas when cursor is over it
        self._conv_settings_canvas.bind("<Enter>", lambda _e:
            self._conv_settings_canvas.bind_all("<MouseWheel>",
                lambda ev: self._conv_settings_canvas.yview_scroll(
                    -1 * (ev.delta // 120), "units")))
        self._conv_settings_canvas.bind("<Leave>", lambda _e:
            self._conv_settings_canvas.unbind_all("<MouseWheel>"))

        self._build_conv_settings_content(inner)

    def _build_conv_settings_content(self, p):
        pad = {"padx": 8, "pady": 4}
        W   = 22  # combobox width

        # -- Preset selector --
        pf = ttk.LabelFrame(p, text="Preset")
        pf.pack(fill="x", **pad)

        self._conv_preset_var = tk.StringVar(value="Shield Optimal")
        cb = ttk.Combobox(pf, textvariable=self._conv_preset_var,
                          values=list(PRESETS.keys()), state="readonly", width=W)
        cb.pack(fill="x", padx=6, pady=(4, 2))
        cb.bind("<<ComboboxSelected>>", self._conv_apply_preset)

        self._conv_preset_desc_lbl = tk.Label(
            pf, text="", font=("Segoe UI", 8),
            wraplength=230, justify="left")
        self._conv_preset_desc_lbl.pack(fill="x", padx=6, pady=(0, 4))

        # -- Output directory --
        of = ttk.LabelFrame(p, text="Output Directory")
        of.pack(fill="x", **pad)
        self._conv_output_var = tk.StringVar(value="Same as source")
        row = ttk.Frame(of)
        row.pack(fill="x", padx=6, pady=4)
        ttk.Entry(row, textvariable=self._conv_output_var).pack(
            side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(row, text="Browse", command=self._conv_browse_output).pack(side="right")

        # -- Container --
        cf = ttk.LabelFrame(p, text="Container Format")
        cf.pack(fill="x", **pad)
        self._conv_container_var = tk.StringVar(value="mkv")
        for text, val in [("MKV  (best codec/audio support)", "mkv"),
                           ("MP4  (widest client support)",   "mp4")]:
            ttk.Radiobutton(cf, text=text,
                            variable=self._conv_container_var, value=val).pack(
                anchor="w", padx=8, pady=2)

        # -- Video --
        vf = ttk.LabelFrame(p, text="Video")
        vf.pack(fill="x", **pad)
        vf.columnconfigure(1, weight=1)

        ttk.Label(vf, text="Codec:").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self._conv_vcodec_var = tk.StringVar(value="libx265")
        self._conv_vcodec_cb = ttk.Combobox(
            vf, textvariable=self._conv_vcodec_var, width=W, state="readonly",
            values=["libx264  (H.264)",
                    "libx265  (H.265/HEVC)",
                    "copy  (no re-encode)"])
        self._conv_vcodec_cb.grid(row=0, column=1, padx=6, pady=3, sticky="ew")
        self._conv_vcodec_cb.bind("<<ComboboxSelected>>", self._conv_normalise_vcodec)

        ttk.Label(vf, text="Quality (CRF):").grid(row=1, column=0, sticky="w", padx=6, pady=3)
        crf_row = ttk.Frame(vf)
        crf_row.grid(row=1, column=1, padx=6, pady=3, sticky="ew")
        self._conv_crf_var = tk.IntVar(value=20)
        self._conv_crf_lbl = tk.Label(
            crf_row, text="20", width=3,
            font=("Segoe UI", 9, "bold"))
        self._conv_crf_lbl.pack(side="right")
        ttk.Scale(crf_row, from_=0, to=51, variable=self._conv_crf_var,
                  command=lambda v: self._conv_crf_lbl.config(
                      text=str(int(float(v))))).pack(
            side="left", fill="x", expand=True)

        ttk.Label(vf, text="Encode speed:").grid(row=2, column=0, sticky="w", padx=6, pady=3)
        self._conv_enc_preset_var = tk.StringVar(value="fast")
        ttk.Combobox(vf, textvariable=self._conv_enc_preset_var, width=W,
                     state="readonly",
                     values=["ultrafast", "superfast", "veryfast", "faster",
                             "fast", "medium", "slow", "slower", "veryslow"]
                     ).grid(row=2, column=1, padx=6, pady=3, sticky="ew")

        ttk.Label(vf, text="Max resolution:").grid(row=3, column=0, sticky="w", padx=6, pady=3)
        self._conv_resolution_var = tk.StringVar(value="original")
        ttk.Combobox(vf, textvariable=self._conv_resolution_var, width=W,
                     state="readonly",
                     values=["original", "3840x2160", "1920x1080",
                             "1280x720", "854x480"]
                     ).grid(row=3, column=1, padx=6, pady=3, sticky="ew")

        ttk.Label(vf, text="HW acceleration:").grid(row=4, column=0, sticky="w", padx=6, pady=3)
        self._conv_hwaccel_var = tk.StringVar(value="none")
        ttk.Combobox(vf, textvariable=self._conv_hwaccel_var, width=W,
                     state="readonly",
                     values=["none", "NVIDIA NVENC", "Intel Quick Sync", "AMD AMF"]
                     ).grid(row=4, column=1, padx=6, pady=3, sticky="ew")

        # -- Audio --
        af = ttk.LabelFrame(p, text="Audio")
        af.pack(fill="x", **pad)
        af.columnconfigure(1, weight=1)

        ttk.Label(af, text="Codec:").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self._conv_acodec_var = tk.StringVar(value="copy")
        ttk.Combobox(af, textvariable=self._conv_acodec_var, width=W,
                     state="readonly",
                     values=["copy  (passthrough)", "aac", "ac3", "eac3", "flac"]
                     ).grid(row=0, column=1, padx=6, pady=3, sticky="ew")

        ttk.Label(af, text="Bitrate:").grid(row=1, column=0, sticky="w", padx=6, pady=3)
        self._conv_abitrate_var = tk.StringVar(value="320k")
        ttk.Combobox(af, textvariable=self._conv_abitrate_var, width=W,
                     state="readonly",
                     values=["128k", "192k", "256k", "320k"]
                     ).grid(row=1, column=1, padx=6, pady=3, sticky="ew")

        # -- Subtitles --
        sf = ttk.LabelFrame(p, text="Subtitles")
        sf.pack(fill="x", **pad)
        self._conv_subtitle_var = tk.StringVar(value="copy")
        for text, val in [("Copy / embed", "copy"),
                           ("Strip (remove all)", "strip")]:
            ttk.Radiobutton(sf, text=text,
                            variable=self._conv_subtitle_var, value=val).pack(
                anchor="w", padx=8, pady=2)

        # -- Options --
        optf = ttk.LabelFrame(p, text="Options")
        optf.pack(fill="x", **pad)
        self._conv_skip_compat_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(optf, text="Skip already compatible files",
                        variable=self._conv_skip_compat_var).pack(anchor="w", padx=8, pady=2)
        self._conv_overwrite_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(optf, text="Overwrite existing output files",
                        variable=self._conv_overwrite_var).pack(anchor="w", padx=8, pady=2)
        self._conv_del_orig_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(optf, text="Delete original after success",
                        variable=self._conv_del_orig_var).pack(anchor="w", padx=8, pady=2)
        self._conv_replace_orig_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(optf, text="Replace original (delete + rename, removes _plex)",
                        variable=self._conv_replace_orig_var).pack(anchor="w", padx=8, pady=2)
        self._conv_skip_processed_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(optf, text="Skip already processed files",
                        variable=self._conv_skip_processed_var).pack(anchor="w", padx=8, pady=2)

        # Apply initial preset
        self._conv_apply_preset()

    def _build_conv_log(self, parent):
        lf = ttk.LabelFrame(parent, text="Log", padding=(4, 4))
        lf.grid(row=2, column=0, sticky="ew", padx=6, pady=(0, 6))
        lf.columnconfigure(0, weight=1)

        self._conv_log_text = tk.Text(
            lf, height=7, font=("Consolas", 8), state="disabled",
            wrap="word", relief="flat",
        )
        ls = ttk.Scrollbar(lf, orient="vertical", command=self._conv_log_text.yview)
        self._conv_log_text.configure(yscrollcommand=ls.set)
        self._conv_log_text.pack(side="left", fill="both", expand=True, padx=(0, 0), pady=2)
        ls.pack(side="right", fill="y", pady=2, padx=(0, 0))

    # ── BMC image loader ──────────────────────────────────────────────────────

    def _load_bmc_image(self, height: int = 32):
        if not _PIL_OK or not _BMC_IMG.exists():
            return None
        try:
            img = _PILImage.open(_BMC_IMG).convert("RGBA")
            w   = int(img.width * height / img.height)
            img = img.resize((w, height), _PILImage.LANCZOS)
            return _ImageTk.PhotoImage(img)
        except Exception:
            return None

    # ── Track Manager: file helpers ───────────────────────────────────────────

    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title="Select MKV file(s)",
            filetypes=[("MKV files", "*.mkv"), ("All files", "*.*")],
        )
        existing = set(self._path_lb.get(0, "end"))
        for p in paths:
            if p not in existing:
                self._path_lb.insert("end", p)

    def _add_folder(self):
        p = filedialog.askdirectory(title="Select folder containing MKV files")
        if p and p not in self._path_lb.get(0, "end"):
            self._path_lb.insert("end", p)

    def _remove_sel(self):
        for i in reversed(self._path_lb.curselection()):
            self._path_lb.delete(i)

    def _clear_paths(self):
        self._path_lb.delete(0, "end")

    def _browse_log_dir(self):
        p = filedialog.askdirectory(title="Select log directory")
        if p:
            self._log_dir_var.set(p)

    # ── Track Manager: language helpers ──────────────────────────────────────

    def _add_custom_lang(self):
        raw  = self._custom_lang_entry.get().strip().lower()
        code = core._normalize_lang(raw)
        if not code or code in ("und", "mul"):
            messagebox.showwarning("Invalid Code",
                "Enter a valid ISO 639-1 (2-letter) or 639-2 (3-letter) language code.")
            return
        if code in self._lang_vars:
            self._lang_vars[code].set(True)
            self._custom_lang_entry.set("")
            messagebox.showinfo("Language Selected",
                f"'{code}' is already in the list — checkbox ticked.")
            return
        if code not in self._custom_langs:
            self._custom_langs.add(code)
        self._custom_lang_entry.set("")
        self._custom_lang_display.configure(
            text="Custom: " + ", ".join(sorted(self._custom_langs)))

    def _get_keep_langs(self) -> frozenset[str]:
        langs = {code for code, var in self._lang_vars.items() if var.get()}
        langs.update(self._custom_langs)
        if not langs:
            messagebox.showwarning("No Languages",
                "No languages selected — defaulting to English.")
            langs = {"en"}
        return frozenset(langs)

    # ── Track Manager: audio helpers ──────────────────────────────────────────

    def _on_manage_audio_toggle(self):
        state = "normal" if self._manage_audio_var.get() else "disabled"
        for child in self._audio_opts_frm.winfo_children():
            try:
                child.configure(state=state)
            except Exception:
                pass
            for sub in child.winfo_children():
                try:
                    sub.configure(state=state)
                except Exception:
                    pass

    def _audio_add_lang(self):
        raw  = self._audio_add_var.get().strip().lower()
        code = core._normalize_lang(raw)
        if not code or code in ("und", "mul"):
            messagebox.showwarning("Invalid Code",
                "Enter a valid ISO 639-1 (2-letter) or 639-2 (3-letter) code.")
            return
        if code not in self._audio_lang_lb.get(0, "end"):
            self._audio_lang_lb.insert("end", code)
        self._audio_add_var.set("")

    def _audio_del_lang(self):
        for i in reversed(self._audio_lang_lb.curselection()):
            self._audio_lang_lb.delete(i)

    def _audio_match_subtitles(self):
        langs = sorted(code for code, var in self._lang_vars.items() if var.get())
        langs += sorted(self._custom_langs)
        self._audio_lang_lb.delete(0, "end")
        for lang in langs:
            self._audio_lang_lb.insert("end", lang)

    def _update_sub_primary_options(self):
        langs   = sorted(code for code, var in self._lang_vars.items() if var.get())
        langs  += sorted(self._custom_langs)
        options = ["(auto)"] + langs
        self._sub_primary_cb["values"] = options
        if self._sub_primary_var.get() not in options:
            self._sub_primary_var.set("(auto)")

    def _update_audio_primary_options(self):
        langs   = list(self._audio_lang_lb.get(0, "end"))
        options = ["(auto)"] + langs
        self._audio_primary_cb["values"] = options
        if self._audio_primary_var.get() not in options:
            self._audio_primary_var.set("(auto)")

    def _get_audio_langs(self) -> frozenset[str] | None:
        if not self._manage_audio_var.get():
            return None
        langs = list(self._audio_lang_lb.get(0, "end"))
        if not langs:
            return frozenset({"en"})
        return frozenset(langs)

    # ── Track Manager: remap helpers ─────────────────────────────────────────

    def _add_remap(self):
        frm = self._remap_from.get().strip().lower()
        to  = self._remap_to.get().strip().lower()
        if not frm or not to:
            messagebox.showwarning("Remap Error", "Both From and To fields are required.")
            return
        entry = f"{frm}:{to}"
        if entry not in self._remap_lb.get(0, "end"):
            self._remap_lb.insert("end", entry)
        self._remap_from.set("")
        self._remap_to.set("")

    def _del_remap(self):
        for i in reversed(self._remap_lb.curselection()):
            self._remap_lb.delete(i)

    def _get_remaps(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for item in self._remap_lb.get(0, "end"):
            if ":" in item:
                old, new = item.split(":", 1)
                result[old.strip()] = new.strip()
        return result

    # ── Track Manager: output helpers ────────────────────────────────────────

    def _append_output(self, text: str):
        self._output_txt.configure(state="normal")
        self._output_txt.insert("end", text)
        if self._autoscroll_var.get():
            self._output_txt.see("end")
        self._output_txt.configure(state="disabled")

    def _clear_output(self):
        self._output_txt.configure(state="normal")
        self._output_txt.delete("1.0", "end")
        self._output_txt.configure(state="disabled")

    # ── Unified output poll (Track Manager + Converter) ───────────────────────

    def _poll_output(self):
        # --- Track Manager queue ---
        try:
            while True:
                line = self._output_q.get_nowait()
                stripped = line.strip()
                if stripped.startswith("Processing:") or \
                        stripped.startswith("[DRY RUN] Processing:"):
                    self._done += 1
                    if self._total:
                        pct = int(100 * self._done / self._total)
                        self._progress["value"] = pct
                        self._pct_lbl.configure(text=f"{pct}%")
                        self._status_lbl.configure(
                            text=f"File {self._done} / {self._total}")
                self._append_output(line)
        except queue.Empty:
            pass

        # --- Video Converter log queue ---
        try:
            while True:
                msg = self._conv_log_q.get_nowait()
                self._conv_log_text.configure(state="normal")
                self._conv_log_text.insert("end", msg + "\n")
                self._conv_log_text.see("end")
                self._conv_log_text.configure(state="disabled")
        except queue.Empty:
            pass

        self.after(100, self._poll_output)

    # ── Track Manager: processing control ────────────────────────────────────

    def _start(self):
        paths = list(self._path_lb.get(0, "end"))
        if not paths:
            messagebox.showwarning("No Input",
                "Add at least one MKV file or folder before starting.")
            return

        keep_langs         = self._get_keep_langs()
        remaps             = self._get_remaps()
        dry_run            = self._dry_run_var.get()
        recursive          = self._recursive_var.get()
        no_log             = self._no_log_var.get()
        spell_check        = self._spell_check_var.get()
        manage_audio       = self._manage_audio_var.get()
        audio_langs        = self._get_audio_langs()
        log_dir            = self._log_dir_var.get()
        _sp = self._sub_primary_var.get()
        _ap = self._audio_primary_var.get()
        preferred_sub_lang   = None if _sp == "(auto)" else _sp
        preferred_audio_lang = None if _ap == "(auto)" else _ap

        core._pause_event.set()
        core._stop_event.clear()

        self._done  = 0
        self._total = 0
        self._progress["value"] = 0
        self._pct_lbl.configure(text="")
        self._status_lbl.configure(text="Starting…")
        self._paused = False

        self._start_btn.configure(state="disabled")
        self._pause_btn.configure(state="normal", text="  Pause")
        self._stop_btn.configure(state="normal")
        self._combined_btn.configure(state="disabled")
        self._conv_start_btn.configure(state="disabled")
        self._conv_combined_btn.configure(state="disabled")

        self._worker = threading.Thread(
            target=self._worker_func,
            args=(paths, keep_langs, remaps, dry_run, recursive, no_log,
                  spell_check, manage_audio, audio_langs, log_dir,
                  preferred_sub_lang, preferred_audio_lang),
            daemon=True,
        )
        self._worker.start()

    def _toggle_pause(self):
        if self._paused:
            core._pause_event.set()
            self._paused = False
            self._pause_btn.configure(text="  Pause")
            self._status_lbl.configure(text="Resuming…")
        else:
            core._pause_event.clear()
            self._paused = True
            self._pause_btn.configure(text="  Resume")
            self._status_lbl.configure(text="Pausing after current file…")

    def _stop(self):
        core._stop_event.set()
        core._pause_event.set()
        self._status_lbl.configure(text="Stopping after current file…")

    def _on_done(self):
        self._start_btn.configure(state="normal")
        self._pause_btn.configure(state="disabled", text="  Pause")
        self._stop_btn.configure(state="disabled")
        self._combined_btn.configure(state="normal")
        self._conv_start_btn.configure(state="normal")
        self._conv_combined_btn.configure(state="normal")
        self._progress["value"] = 100
        self._pct_lbl.configure(text="100%")
        stopped = core._stop_event.is_set()
        self._status_lbl.configure(
            text="Stopped." if stopped else
            f"Done.  {self._done}/{self._total} files")

    # ── Track Manager: worker thread ──────────────────────────────────────────

    def _worker_func(self, paths, keep_langs, remaps, dry_run, recursive, no_log,
                     spell_check, manage_audio, audio_langs, log_dir,
                     preferred_sub_lang=None, preferred_audio_lang=None):
        if no_log:
            core._LOG_DIR = None
        else:
            core._LOG_DIR = Path(log_dir)

        mkv_files: list[Path] = []
        for raw in paths:
            p = Path(raw)
            if p.is_file() and p.suffix.lower() == ".mkv":
                mkv_files.append(p)
            elif p.is_dir():
                pattern = "**/*.mkv" if recursive else "*.mkv"
                mkv_files.extend(sorted(p.glob(pattern)))

        if not mkv_files:
            self._output_q.put("No MKV files found in the selected paths.\n")
            self.after(0, self._on_done)
            return

        self._total = len(mkv_files)
        self._output_q.put(f"Found {len(mkv_files)} MKV file(s).\n")
        self._output_q.put(f"Keeping languages: {sorted(keep_langs)}\n")
        if remaps:
            self._output_q.put(f"Language remaps: {remaps}\n")
        if dry_run:
            self._output_q.put("[DRY RUN MODE — no files will be modified]\n")
        self._output_q.put("\n")

        modified       = 0
        errors         = 0
        skip_processed = self._tm_skip_processed_var.get()
        tm_hash        = self._compute_tm_hash(keep_langs, remaps, manage_audio,
                                               audio_langs, preferred_sub_lang,
                                               preferred_audio_lang, spell_check)

        stream = _QueueStream(self._output_q)
        with contextlib.redirect_stdout(stream):
            for f in mkv_files:
                if core._check_pause_stop():
                    self._output_q.put("\nProcessing stopped by user.\n")
                    break
                # Skip if already processed with current settings
                if skip_processed and not dry_run and self._history_check(f, tm_hash):
                    print(f"Processing: {f}")
                    print("  Already processed with current settings — skipping.")
                    continue
                try:
                    if core.process_mkv(str(f), dry_run=dry_run,
                                        remap_langs=remaps, keep_langs=keep_langs,
                                        spell_check=spell_check,
                                        manage_audio=manage_audio,
                                        audio_langs=audio_langs,
                                        preferred_sub_lang=preferred_sub_lang,
                                        preferred_audio_lang=preferred_audio_lang):
                        modified += 1
                    if not dry_run:
                        self._history_record(f, tm_hash)
                except Exception as exc:
                    self._output_q.put(f"  UNHANDLED ERROR for '{f}': {exc}\n")
                    errors += 1

        sep = "=" * 60
        action = "would be modified" if dry_run else "modified"
        self._output_q.put(f"\n{sep}\n")
        self._output_q.put(
            f"Complete: {modified}/{len(mkv_files)} file(s) {action}."
            f"  Errors: {errors}.\n"
        )

        self.after(0, self._on_done)

    # ── Video Converter: preset logic ────────────────────────────────────────

    def _conv_apply_preset(self, event=None):
        name = self._conv_preset_var.get()
        p    = PRESETS.get(name, PRESETS["Custom"])
        self._conv_preset_desc_lbl.config(text=p["desc"])

        if name == "Custom":
            return

        if name == "No Change":
            self._conv_vcodec_var.set("copy  (no re-encode)")
            self._conv_acodec_var.set("copy  (passthrough)")
            self._conv_subtitle_var.set("copy")
            self._conv_skip_compat_var.set(False)
            return

        self._conv_container_var.set(p["container"])

        vmap = {"libx264": "libx264  (H.264)",
                "libx265": "libx265  (H.265/HEVC)",
                "copy":    "copy  (no re-encode)"}
        self._conv_vcodec_var.set(vmap.get(p["vcodec"], p["vcodec"]))

        self._conv_crf_var.set(p["crf"])
        self._conv_crf_lbl.config(text=str(p["crf"]))
        self._conv_enc_preset_var.set(p["preset"])
        self._conv_resolution_var.set(p["resolution"])

        amap = {"copy": "copy  (passthrough)", "aac": "aac",
                "ac3": "ac3", "eac3": "eac3", "flac": "flac"}
        self._conv_acodec_var.set(amap.get(p["acodec"], p["acodec"]))

        self._conv_abitrate_var.set(p["abitrate"])
        self._conv_subtitle_var.set(p["subtitle"])
        self._conv_skip_compat_var.set(p["skip_compatible"])

    def _conv_normalise_vcodec(self, event=None):
        v = self._conv_vcodec_var.get().split("  ")[0]
        self._conv_vcodec_var.set(v)

    # ── Video Converter: file management ─────────────────────────────────────

    def _conv_add_files(self):
        exts_str = " ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS))
        paths = filedialog.askopenfilenames(
            title="Select video file(s)",
            filetypes=[("Video files", exts_str), ("All files", "*.*")],
        )
        for p in paths:
            self._conv_add_path(Path(p))

    def _conv_add_folder(self):
        folder = filedialog.askdirectory(title="Select folder containing videos")
        if not folder:
            return
        added = 0
        for p in Path(folder).rglob("*"):
            if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS:
                self._conv_add_path(p)
                added += 1
        self._conv_log(f"Scanned folder — {added} file(s) added: {folder}")

    def _conv_add_path(self, path: Path):
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            return
        if any(f.path == path for f in self._conv_files):
            return

        info    = FileInfo(path=path, size_mb=path.stat().st_size / 1_048_576)
        row_id  = self._conv_tree.insert("", "end", tags=("pending",), values=(
            path.name, "…", "…", "…",
            f"{info.size_mb:.1f} MB", "Pending", "",
        ))
        info.row_id = row_id
        self._conv_files.append(info)

        threading.Thread(
            target=self._conv_probe_file, args=(info,), daemon=True).start()

    def _conv_remove_selected(self):
        for item in self._conv_tree.selection():
            self._conv_tree.delete(item)
            self._conv_files = [f for f in self._conv_files if f.row_id != item]

    def _conv_clear_all(self):
        self._conv_tree.delete(*self._conv_tree.get_children())
        self._conv_files.clear()

    def _conv_browse_output(self):
        d = filedialog.askdirectory(title="Choose output directory")
        if d:
            self._conv_output_var.set(d)

    def _conv_sort_tree(self, col: str):
        items = [(self._conv_tree.set(k, col), k)
                 for k in self._conv_tree.get_children("")]
        items.sort(key=lambda t: t[0].lower())
        for idx, (_, k) in enumerate(items):
            self._conv_tree.move(k, "", idx)

    # ── Video Converter: ffprobe ──────────────────────────────────────────────

    def _conv_probe_file(self, info: FileInfo):
        if not self._ffprobe:
            return
        with self._conv_probe_sem:
            self._conv_do_probe(info)

    def _conv_do_probe(self, info: FileInfo):
        try:
            result = subprocess.run(
                [self._ffprobe, "-v", "quiet", "-print_format", "json",
                 "-show_streams", "-show_format", str(info.path)],
                capture_output=True, text=True, timeout=30,
            )
            if not result.stdout:
                self._conv_log(f"Probe warning — no output for: {info.path.name}")
                return
            data = json.loads(result.stdout)

            for stream in data.get("streams", []):
                ct = stream.get("codec_type", "")
                if ct == "video" and info.video_codec == "—":
                    info.video_codec = stream.get("codec_name", "?")
                    w = stream.get("width",  0)
                    h = stream.get("height", 0)
                    info.resolution  = f"{w}×{h}" if w else "?"
                elif ct == "audio" and info.audio_codec == "—":
                    info.audio_codec = stream.get("codec_name", "?")

            fmt           = data.get("format", {})
            info.duration = float(fmt.get("duration", 0))
            self.after(0, self._conv_refresh_row, info)
        except json.JSONDecodeError as e:
            self._conv_log(f"Probe error (bad JSON) — {info.path.name}: {e}")
        except Exception as e:
            self._conv_log(f"Probe error — {info.path.name}: {e}")

    # ── Video Converter: synchronous probe (no treeview update) ──────────────

    def _conv_probe_sync(self, info: FileInfo):
        """Probe a file for duration/codec info without touching the treeview.
        Intended for use from worker threads (combined run mode)."""
        if not self._ffprobe:
            return
        try:
            result = subprocess.run(
                [self._ffprobe, "-v", "quiet", "-print_format", "json",
                 "-show_streams", "-show_format", str(info.path)],
                capture_output=True, text=True, timeout=30,
            )
            if not result.stdout:
                return
            data = json.loads(result.stdout)
            for stream in data.get("streams", []):
                ct = stream.get("codec_type", "")
                if ct == "video" and info.video_codec == "—":
                    info.video_codec = stream.get("codec_name", "?")
                    w = stream.get("width",  0)
                    h = stream.get("height", 0)
                    info.resolution  = f"{w}×{h}" if w else "?"
                elif ct == "audio" and info.audio_codec == "—":
                    info.audio_codec = stream.get("codec_name", "?")
            fmt           = data.get("format", {})
            info.duration = float(fmt.get("duration", 0))
        except Exception:
            pass  # duration stays 0; progress won't show percentage

    # ── Video Converter: tree row update ──────────────────────────────────────

    def _conv_refresh_row(self, info: FileInfo, prog_text: str = ""):
        if not info.row_id or not self._conv_tree.exists(info.row_id):
            return
        tag   = info.status.lower()
        ptext = prog_text or (
            "" if info.status in ("Pending", "Converting") else info.status)
        self._conv_tree.item(info.row_id,
                             tags=(tag,),
                             values=(
                                 info.path.name,
                                 info.resolution,
                                 info.video_codec,
                                 info.audio_codec,
                                 f"{info.size_mb:.1f} MB",
                                 info.status,
                                 ptext,
                             ))

    # ── Video Converter: replace-original helper ──────────────────────────────

    def _conv_do_replace_original(self, info: FileInfo, output: Path) -> Path:
        """Delete the original file and rename the output to the original stem
        (keeping the new extension).  Returns the final path.

        e.g.  Movie.mkv  +  Movie_plex.mp4  →  Movie.mp4
        """
        final = output.parent / f"{info.path.stem}{output.suffix}"
        # Delete original
        try:
            info.path.unlink()
            self._conv_log(f"  [del] original removed: {info.path.name}")
        except Exception as e:
            self._conv_log(f"  [warn] could not delete original: {e}")
            return output
        # Rename with retry (Plex may briefly hold the file)
        for attempt in range(6):
            try:
                output.rename(final)
                self._conv_log(f"  [renamed] {output.name}  →  {final.name}")
                return final
            except OSError as e:
                if attempt < 5:
                    self._conv_log(
                        f"  [retry {attempt + 1}/5] rename locked — waiting 5 s…")
                    time.sleep(5)
                else:
                    self._conv_log(f"  [err] rename failed after retries: {e}")
        return output

    # ── Video Converter: log helper ───────────────────────────────────────────

    def _conv_log(self, msg: str):
        self._conv_log_q.put(msg)

    # ── Video Converter: conversion control ───────────────────────────────────

    def _conv_start(self):
        if not self._ffmpeg:
            messagebox.showerror(
                "ffmpeg not found",
                "ffmpeg.exe was not found on PATH or in C:\\ffmpeg\\bin.\n\n"
                "Download from https://ffmpeg.org/download.html\n"
                "and add the bin\\ folder to your system PATH.",
            )
            return

        pending = [f for f in self._conv_files if f.status in ("Pending", "Error")]
        if not pending:
            messagebox.showinfo("Nothing to do", "No pending files to convert.")
            return

        self._conv_converting = True
        self._conv_stop_flag.clear()
        self._conv_start_btn.configure(state="disabled")
        self._conv_stop_btn.configure(state="normal")
        self._combined_btn.configure(state="disabled")
        self._conv_combined_btn.configure(state="disabled")
        self._start_btn.configure(state="disabled")
        threading.Thread(
            target=self._conv_worker, args=(pending,), daemon=True).start()

    def _conv_stop(self):
        self._conv_stop_flag.set()
        if self._conv_current_proc:
            try:
                self._conv_current_proc.terminate()
            except Exception:
                pass
        self._conv_log("Stop requested — finishing current file…")
        self._conv_stop_btn.configure(state="disabled")

    def _conv_on_done(self):
        self._conv_converting = False
        self._conv_start_btn.configure(state="normal")
        self._conv_stop_btn.configure(state="disabled")
        self._combined_btn.configure(state="normal")
        self._conv_combined_btn.configure(state="normal")
        self._start_btn.configure(state="normal")

    def _conv_set_overall(self, done: int, total: int):
        self._conv_overall_bar["value"] = done / total * 100 if total else 0
        self._conv_overall_lbl["text"]  = f"{done} / {total}"

    # ── Video Converter: worker thread ────────────────────────────────────────

    def _conv_worker(self, files: List[FileInfo]):
        total          = len(files)
        done           = 0
        time_re        = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")
        no_change      = (self._conv_preset_var.get() == "No Change")
        skip_processed = self._conv_skip_processed_var.get()
        conv_hash      = self._compute_conv_hash()

        for info in files:
            if self._conv_stop_flag.is_set():
                info.status = "Cancelled"
                self.after(0, self._conv_refresh_row, info)
                continue

            # No Change preset — skip conversion entirely
            if no_change:
                info.status = "Skipped"
                self._conv_log(f"[no-change] {info.path.name}  (conversion skipped by preset)")
                self.after(0, self._conv_refresh_row, info)
                done += 1
                self.after(0, self._conv_set_overall, done, total)
                continue

            # Skip if already processed with current settings
            if skip_processed and self._history_check(info.path, conv_hash):
                info.status = "Skipped"
                self._conv_log(
                    f"[skip] {info.path.name}  (already processed with current settings)")
                self.after(0, self._conv_refresh_row, info)
                done += 1
                self.after(0, self._conv_set_overall, done, total)
                continue

            # Skip compatible?
            if self._conv_skip_compat_var.get() and info.is_plex_compatible:
                info.status = "Skipped"
                self._conv_log(f"[skip] {info.path.name}  (already compatible)")
                self.after(0, self._conv_refresh_row, info)
                done += 1
                self.after(0, self._conv_set_overall, done, total)
                continue

            output = self._conv_output_path(info)

            if output.exists() and not self._conv_overwrite_var.get():
                info.status = "Skipped"
                self._conv_log(f"[skip] {info.path.name}  (output exists)")
                self.after(0, self._conv_refresh_row, info)
                done += 1
                self.after(0, self._conv_set_overall, done, total)
                continue

            # Capture pre-conversion stats for history (before file may be renamed/deleted)
            pre_size, pre_mtime = 0, 0.0
            try:
                _st = info.path.stat()
                pre_size, pre_mtime = _st.st_size, _st.st_mtime
            except OSError:
                pass

            cmd = self._conv_build_cmd(info, output)
            self._conv_log(f"\n[start] {info.path.name}")
            self._conv_log(f"    →   {output}")

            info.status = "Converting"
            self.after(0, self._conv_refresh_row, info)

            try:
                self._conv_current_proc = subprocess.Popen(
                    cmd,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    encoding="utf-8",
                    errors="replace",
                )

                for line in self._conv_current_proc.stderr:
                    if self._conv_stop_flag.is_set():
                        self._conv_current_proc.terminate()
                        break
                    m = time_re.search(line)
                    if m and info.duration > 0:
                        h, mn, s, cs = (int(m.group(i)) for i in range(1, 5))
                        elapsed = h * 3600 + mn * 60 + s + cs / 100
                        pct     = min(100.0, elapsed / info.duration * 100)
                        info.progress = pct
                        self.after(0, self._conv_refresh_row, info, f"{pct:.0f}%")
                    elif "error" in line.lower() and "nonfatal" not in line.lower():
                        self._conv_log(f"    ! {line.rstrip()}")

                ret = self._conv_current_proc.wait()
                self._conv_current_proc = None

                if ret == 0 and not self._conv_stop_flag.is_set():
                    info.status = "Done"
                    out_mb = output.stat().st_size / 1_048_576 if output.exists() else 0
                    ratio  = out_mb / info.size_mb * 100 if info.size_mb else 0
                    self._conv_log(
                        f"[done] {info.path.name}  "
                        f"{info.size_mb:.0f} MB → {out_mb:.0f} MB  ({ratio:.0f}%)")

                    # Record history before any rename/delete changes the path
                    self._history_record_at(info.path, pre_size, pre_mtime, conv_hash)

                    if self._conv_replace_orig_var.get() and output.exists():
                        output = self._conv_do_replace_original(info, output)
                    elif self._conv_del_orig_var.get() and output.exists():
                        info.path.unlink()
                        self._conv_log("  [del] original removed")
                else:
                    info.status = "Cancelled" if self._conv_stop_flag.is_set() else "Error"
                    self._conv_log(f"[fail] {info.path.name}  exit={ret}")
                    if output.exists():
                        output.unlink()

            except Exception as exc:
                info.status = "Error"
                self._conv_log(f"[err]  {info.path.name}: {exc}")
                self._conv_current_proc = None

            done += 1
            self.after(0, self._conv_refresh_row, info,
                       "100%" if info.status == "Done" else "")
            self.after(0, self._conv_set_overall, done, total)

        counts = {s: sum(1 for f in files if f.status == s)
                  for s in ("Done", "Skipped", "Error", "Cancelled")}
        self._conv_log(
            f"\n── Finished ──────────────────────────────────────────\n"
            f"   Done: {counts['Done']}  Skipped: {counts['Skipped']}  "
            f"Error: {counts['Error']}  Cancelled: {counts['Cancelled']}"
        )
        self.after(0, self._conv_on_done)

    # ── Video Converter: ffmpeg command builder ───────────────────────────────

    def _conv_output_path(self, info: FileInfo) -> Path:
        ext     = self._conv_container_var.get()
        out_s   = self._conv_output_var.get()
        out_dir = info.path.parent if out_s == "Same as source" else Path(out_s)
        out_dir.mkdir(parents=True, exist_ok=True)

        stem = info.path.stem
        if info.path.parent == out_dir:
            stem += "_plex"
        return out_dir / f"{stem}.{ext}"

    def _conv_build_cmd(self, info: FileInfo, output: Path) -> List[str]:
        cmd: List[str] = [self._ffmpeg, "-hide_banner", "-loglevel", "info"]

        # Hardware-accelerated decode
        hw = self._conv_hwaccel_var.get()
        if "NVIDIA" in hw:
            cmd += ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
        elif "Intel" in hw:
            cmd += ["-hwaccel", "qsv"]
        elif "AMD" in hw:
            cmd += ["-hwaccel", "d3d11va"]

        cmd += ["-i", str(info.path)]
        cmd += ["-y" if self._conv_overwrite_var.get() else "-n"]

        # Video codec
        raw_vc      = self._conv_vcodec_var.get().split("  ")[0]
        final_vcodec = raw_vc

        if raw_vc == "copy":
            cmd += ["-c:v", "copy"]
        else:
            hw_enc_map = {
                "NVIDIA NVENC":    {"libx264": "h264_nvenc",  "libx265": "hevc_nvenc"},
                "Intel Quick Sync":{"libx264": "h264_qsv",    "libx265": "hevc_qsv"},
                "AMD AMF":         {"libx264": "h264_amf",    "libx265": "hevc_amf"},
            }
            final_vcodec = hw_enc_map.get(hw, {}).get(raw_vc, raw_vc)
            cmd += ["-c:v", final_vcodec]

            crf = str(self._conv_crf_var.get())
            spd = self._conv_enc_preset_var.get()
            if final_vcodec in ("libx264", "libx265"):
                cmd += ["-crf", crf, "-preset", spd]
            elif "nvenc" in final_vcodec:
                cmd += ["-rc", "vbr", "-cq", crf, "-preset", "p4"]
            elif "qsv" in final_vcodec:
                cmd += ["-global_quality", crf, "-preset", "medium"]
            elif "amf" in final_vcodec:
                cmd += ["-rc", "cqp", "-qp_i", crf, "-qp_p", crf]

        is_hevc = final_vcodec in ("libx265", "hevc_nvenc", "hevc_qsv", "hevc_amf")

        # Resolution downscale / H.265 even-dimension enforcement
        res = self._conv_resolution_var.get()
        if res != "original":
            w, h = res.split("x")
            vf   = (f"scale={w}:{h}:force_original_aspect_ratio=decrease"
                    ":flags=lanczos,pad=ceil(iw/2)*2:ceil(ih/2)*2")
            cmd += ["-vf", vf]
        elif is_hevc:
            cmd += ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"]

        # Audio codec
        raw_ac = self._conv_acodec_var.get().split("  ")[0]
        if raw_ac == "copy":
            cmd += ["-c:a", "copy"]
        else:
            cmd += ["-c:a", raw_ac, "-b:a", self._conv_abitrate_var.get()]

        # Subtitles
        if self._conv_subtitle_var.get() == "strip":
            cmd += ["-sn"]
        else:
            container = self._conv_container_var.get()
            if container == "mp4":
                cmd += ["-c:s", "mov_text"]
            else:
                cmd += ["-c:s", "copy"]

        # Map primary video, all audio, optional subtitles — skip embedded cover art
        cmd += ["-map", "0:v:0", "-map", "0:a?", "-map", "0:s?"]

        # MP4 streaming optimisation
        if self._conv_container_var.get() == "mp4":
            cmd += ["-movflags", "+faststart"]

        cmd.append(str(output))
        return cmd


    # ── Combined Run (Track Manager → Video Converter) ────────────────────────

    def _combined_start(self):
        paths = list(self._path_lb.get(0, "end"))
        if not paths:
            messagebox.showwarning("No Input",
                "Add at least one MKV file or folder in the Track Manager tab before starting.")
            return

        if not self._ffmpeg:
            messagebox.showerror(
                "ffmpeg not found",
                "Combined mode requires ffmpeg for the conversion step.\n\n"
                "ffmpeg.exe was not found on PATH or in C:\\ffmpeg\\bin.\n"
                "Download from https://ffmpeg.org/download.html",
            )
            return

        keep_langs         = self._get_keep_langs()
        remaps             = self._get_remaps()
        dry_run            = self._dry_run_var.get()
        recursive          = self._recursive_var.get()
        no_log             = self._no_log_var.get()
        spell_check        = self._spell_check_var.get()
        manage_audio       = self._manage_audio_var.get()
        audio_langs        = self._get_audio_langs()
        log_dir            = self._log_dir_var.get()
        _sp = self._sub_primary_var.get()
        _ap = self._audio_primary_var.get()
        preferred_sub_lang   = None if _sp == "(auto)" else _sp
        preferred_audio_lang = None if _ap == "(auto)" else _ap

        core._pause_event.set()
        core._stop_event.clear()
        self._conv_stop_flag.clear()

        self._done  = 0
        self._total = 0
        self._progress["value"] = 0
        self._pct_lbl.configure(text="")
        self._status_lbl.configure(text="Combined: starting…")
        self._paused = False

        # Lock all start/stop buttons — Stop in TM bar halts everything
        self._start_btn.configure(state="disabled")
        self._pause_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._combined_btn.configure(state="disabled")
        self._conv_combined_btn.configure(state="disabled")
        self._conv_start_btn.configure(state="disabled")
        self._conv_stop_btn.configure(state="disabled")

        self._worker = threading.Thread(
            target=self._combined_worker,
            args=(paths, keep_langs, remaps, dry_run, recursive, no_log,
                  spell_check, manage_audio, audio_langs, log_dir,
                  preferred_sub_lang, preferred_audio_lang),
            daemon=True,
        )
        self._worker.start()

    def _combined_on_done(self):
        self._start_btn.configure(state="normal")
        self._pause_btn.configure(state="disabled", text="  Pause")
        self._stop_btn.configure(state="disabled")
        self._combined_btn.configure(state="normal")
        self._conv_combined_btn.configure(state="normal")
        self._conv_start_btn.configure(state="normal")
        self._conv_stop_btn.configure(state="disabled")
        self._progress["value"] = 100
        self._pct_lbl.configure(text="100%")
        stopped = core._stop_event.is_set()
        self._status_lbl.configure(
            text="Combined: stopped." if stopped else
            f"Combined: done.  {self._done}/{self._total} files")

    def _combined_worker(self, paths, keep_langs, remaps, dry_run, recursive, no_log,
                         spell_check, manage_audio, audio_langs, log_dir,
                         preferred_sub_lang=None, preferred_audio_lang=None):
        if no_log:
            core._LOG_DIR = None
        else:
            core._LOG_DIR = Path(log_dir)

        # Collect MKV files (same logic as _worker_func)
        mkv_files: list[Path] = []
        for raw in paths:
            p = Path(raw)
            if p.is_file() and p.suffix.lower() == ".mkv":
                mkv_files.append(p)
            elif p.is_dir():
                pattern = "**/*.mkv" if recursive else "*.mkv"
                mkv_files.extend(sorted(p.glob(pattern)))

        if not mkv_files:
            self._output_q.put("No MKV files found in the selected paths.\n")
            self.after(0, self._combined_on_done)
            return

        total = len(mkv_files)
        self._total = total
        self._output_q.put(
            f"[Combined Mode] {total} file(s)  "
            f"—  Track Manager  →  Video Converter\n")
        self._output_q.put(f"Keeping languages: {sorted(keep_langs)}\n")
        if remaps:
            self._output_q.put(f"Language remaps: {remaps}\n")
        if dry_run:
            self._output_q.put(
                "[DRY RUN — Track Manager will not modify files; "
                "conversion step will still run]\n")
        self._output_q.put("\n")

        tm_changed     = 0
        converted      = 0
        errors         = 0
        time_re        = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")
        stream         = _QueueStream(self._output_q)
        skip_processed = self._tm_skip_processed_var.get()
        tm_hash        = self._compute_tm_hash(keep_langs, remaps, manage_audio,
                                               audio_langs, preferred_sub_lang,
                                               preferred_audio_lang, spell_check)
        conv_hash      = self._compute_conv_hash()

        for idx, f in enumerate(mkv_files):
            if core._stop_event.is_set():
                self._output_q.put("\nCombined run stopped by user.\n")
                break

            file_num = f"{idx + 1}/{total}"
            self._done = idx + 1

            def _upd_progress(pct, label, d=self._done, t=total):
                self._progress["value"] = pct
                self._pct_lbl.configure(text=f"{pct}%")
                self._status_lbl.configure(text=f"{label}: {d}/{t}")

            pct_step = int(100 * (idx + 1) / total)

            # ── Step 1: Track Manager ─────────────────────────────────────
            self.after(0, _upd_progress, pct_step, "TM")
            self._output_q.put(f"── [{file_num}] Track Manager ──────────────────\n")
            # Skip TM step if already processed with current settings
            if skip_processed and not dry_run and self._history_check(f, tm_hash):
                self._output_q.put(
                    f"  Already processed with current TM settings — skipping TM.\n")
                tm_skipped = True
            else:
                tm_skipped = False
                try:
                    with contextlib.redirect_stdout(stream):
                        changed = core.process_mkv(
                            str(f), dry_run=dry_run, remap_langs=remaps,
                            keep_langs=keep_langs, spell_check=spell_check,
                            manage_audio=manage_audio, audio_langs=audio_langs,
                            preferred_sub_lang=preferred_sub_lang,
                            preferred_audio_lang=preferred_audio_lang,
                        )
                    if changed:
                        tm_changed += 1
                    if not dry_run:
                        self._history_record(f, tm_hash)
                except Exception as exc:
                    self._output_q.put(f"  TM ERROR: {exc}\n")
                    errors += 1
                    continue

            if core._stop_event.is_set():
                self._output_q.put("\nCombined run stopped by user.\n")
                break

            # ── Step 2: Video Converter ───────────────────────────────────
            if self._conv_preset_var.get() == "No Change":
                self._conv_log(f"[no-change] {f.name}  (conversion skipped by preset)")
                continue

            # Skip conv step if already processed with current conv settings
            if self._conv_skip_processed_var.get() and self._history_check(f, conv_hash):
                self._conv_log(
                    f"[skip] {f.name}  (already processed with current conv settings)")
                continue

            self.after(0, _upd_progress, pct_step, "Converting")
            info = FileInfo(path=f, size_mb=f.stat().st_size / 1_048_576)
            self._conv_probe_sync(info)   # fills duration for progress %

            if not dry_run:
                output = self._conv_output_path(info)
                cmd    = self._conv_build_cmd(info, output)
                self._conv_log(f"\n[Combined {file_num}] {f.name}")
                self._conv_log(f"    →  {output}")

                # Capture pre-conversion stats for history
                pre_size, pre_mtime = 0, 0.0
                try:
                    _st = f.stat()
                    pre_size, pre_mtime = _st.st_size, _st.st_mtime
                except OSError:
                    pass

                try:
                    proc = subprocess.Popen(
                        cmd, stderr=subprocess.PIPE,
                        universal_newlines=True, encoding="utf-8", errors="replace",
                    )
                    self._conv_current_proc = proc

                    for line in proc.stderr:
                        if core._stop_event.is_set():
                            proc.terminate()
                            break
                        m = time_re.search(line)
                        if m and info.duration > 0:
                            h, mn, s, cs = (int(m.group(i)) for i in range(1, 5))
                            elapsed = h * 3600 + mn * 60 + s + cs / 100
                            pct_c   = min(100.0, elapsed / info.duration * 100)
                            self._conv_log(f"  {f.name}  {pct_c:.0f}%")
                        elif "error" in line.lower() and "nonfatal" not in line.lower():
                            self._conv_log(f"    ! {line.rstrip()}")

                    ret = proc.wait()
                    self._conv_current_proc = None

                    if ret == 0 and not core._stop_event.is_set():
                        out_mb = output.stat().st_size / 1_048_576 if output.exists() else 0
                        ratio  = out_mb / info.size_mb * 100 if info.size_mb else 0
                        self._conv_log(
                            f"[done] {f.name}  "
                            f"{info.size_mb:.0f} MB → {out_mb:.0f} MB  ({ratio:.0f}%)")
                        converted += 1
                        self._history_record_at(f, pre_size, pre_mtime, conv_hash)
                        if self._conv_replace_orig_var.get() and output.exists():
                            self._conv_do_replace_original(info, output)
                        elif self._conv_del_orig_var.get() and output.exists():
                            f.unlink()
                            self._conv_log("  [del] original removed")
                    else:
                        self._conv_log(f"[fail] {f.name}  exit={ret}")
                        if output.exists():
                            output.unlink()
                        errors += 1

                except Exception as exc:
                    self._conv_log(f"[err]  {f.name}: {exc}")
                    self._conv_current_proc = None
                    errors += 1
            else:
                self._conv_log(f"[dry run] Would convert: {f.name}")

        sep = "=" * 60
        self._output_q.put(f"\n{sep}\n")
        self._output_q.put(
            f"Combined complete: {total} file(s)\n"
            f"  Track Manager changes : {tm_changed}\n"
            f"  Converted             : {converted}\n"
            f"  Errors                : {errors}\n"
        )
        self.after(0, self._combined_on_done)

    # ── Combined Run from Converter tab ───────────────────────────────────────

    def _combined_start_conv(self):
        """Combined Run initiated from the Video Converter tab.
        Uses the Converter treeview file list + TM settings from Tab 1."""
        pending = [f for f in self._conv_files if f.status in ("Pending", "Error")]
        if not pending:
            messagebox.showinfo("Nothing to do",
                "No pending files in the Video Converter list.")
            return

        if not self._ffmpeg:
            messagebox.showerror(
                "ffmpeg not found",
                "Combined mode requires ffmpeg.\n\n"
                "ffmpeg.exe was not found on PATH or in C:\\ffmpeg\\bin.\n"
                "Download from https://ffmpeg.org/download.html",
            )
            return

        # Collect Track Manager settings from Tab 1
        keep_langs         = self._get_keep_langs()
        remaps             = self._get_remaps()
        dry_run            = self._dry_run_var.get()
        no_log             = self._no_log_var.get()
        spell_check        = self._spell_check_var.get()
        manage_audio       = self._manage_audio_var.get()
        audio_langs        = self._get_audio_langs()
        log_dir            = self._log_dir_var.get()
        _sp = self._sub_primary_var.get()
        _ap = self._audio_primary_var.get()
        preferred_sub_lang   = None if _sp == "(auto)" else _sp
        preferred_audio_lang = None if _ap == "(auto)" else _ap

        core._pause_event.set()
        core._stop_event.clear()
        self._conv_stop_flag.clear()

        self._start_btn.configure(state="disabled")
        self._pause_btn.configure(state="disabled")
        self._stop_btn.configure(state="disabled")        # no TM stop for this mode
        self._combined_btn.configure(state="disabled")
        self._conv_start_btn.configure(state="disabled")
        self._conv_stop_btn.configure(state="normal")     # conv Stop halts ffmpeg
        self._conv_combined_btn.configure(state="disabled")

        threading.Thread(
            target=self._combined_worker_conv,
            args=(pending, keep_langs, remaps, dry_run, no_log,
                  spell_check, manage_audio, audio_langs, log_dir,
                  preferred_sub_lang, preferred_audio_lang),
            daemon=True,
        ).start()

    def _combined_on_done_conv(self):
        self._start_btn.configure(state="normal")
        self._pause_btn.configure(state="disabled", text="  Pause")
        self._stop_btn.configure(state="disabled")
        self._combined_btn.configure(state="normal")
        self._conv_start_btn.configure(state="normal")
        self._conv_stop_btn.configure(state="disabled")
        self._conv_combined_btn.configure(state="normal")
        self._conv_converting = False

    def _combined_worker_conv(self, files: List[FileInfo],
                               keep_langs, remaps, dry_run, no_log,
                               spell_check, manage_audio, audio_langs, log_dir,
                               preferred_sub_lang=None, preferred_audio_lang=None):
        """Combined pipeline from the Converter tab: TM (MKV only) → ffmpeg.
        Updates the Converter treeview rows throughout."""
        if no_log:
            core._LOG_DIR = None
        else:
            core._LOG_DIR = Path(log_dir)

        total          = len(files)
        done           = 0
        tm_changed     = 0
        converted      = 0
        errors         = 0
        time_re        = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")
        stream         = _QueueStream(self._output_q)
        skip_processed = self._tm_skip_processed_var.get()
        tm_hash        = self._compute_tm_hash(keep_langs, remaps, manage_audio,
                                               audio_langs, preferred_sub_lang,
                                               preferred_audio_lang, spell_check)
        conv_hash      = self._compute_conv_hash()

        self._conv_log(
            f"[Combined Mode — Converter] {total} file(s)  "
            f"—  Track Manager (MKV only)  →  Video Converter")
        if dry_run:
            self._conv_log(
                "[DRY RUN — TM will not modify files; conversion will still run]")

        for idx, info in enumerate(files):
            if self._conv_stop_flag.is_set():
                info.status = "Cancelled"
                self.after(0, self._conv_refresh_row, info)
                continue

            file_num = f"{idx + 1}/{total}"

            # ── Step 1: Track Manager (MKV files only) ────────────────────
            if info.path.suffix.lower() == ".mkv":
                # Skip TM if already processed with current settings
                if skip_processed and not dry_run and self._history_check(info.path, tm_hash):
                    self._output_q.put(
                        f"── [{file_num}] TM skipped (already processed): {info.path.name}\n")
                else:
                    info.status = "TM Clean…"
                    self.after(0, self._conv_refresh_row, info)
                    self._output_q.put(
                        f"── [{file_num}] Track Manager: {info.path.name}\n")
                    try:
                        with contextlib.redirect_stdout(stream):
                            changed = core.process_mkv(
                                str(info.path), dry_run=dry_run, remap_langs=remaps,
                                keep_langs=keep_langs, spell_check=spell_check,
                                manage_audio=manage_audio, audio_langs=audio_langs,
                                preferred_sub_lang=preferred_sub_lang,
                                preferred_audio_lang=preferred_audio_lang,
                            )
                        if changed:
                            tm_changed += 1
                        if not dry_run:
                            self._history_record(info.path, tm_hash)
                    except Exception as exc:
                        self._output_q.put(f"  TM ERROR: {exc}\n")
                        info.status = "Error"
                        self.after(0, self._conv_refresh_row, info)
                        errors += 1
                        done += 1
                        self.after(0, self._conv_set_overall, done, total)
                        continue
            else:
                self._conv_log(
                    f"[{file_num}] Skipping Track Manager (not MKV): {info.path.name}")

            if self._conv_stop_flag.is_set():
                info.status = "Cancelled"
                self.after(0, self._conv_refresh_row, info)
                continue

            # ── Step 2: Video Converter ───────────────────────────────────
            if self._conv_preset_var.get() == "No Change":
                info.status = "Skipped"
                self._conv_log(f"[no-change] {info.path.name}  (conversion skipped by preset)")
                self.after(0, self._conv_refresh_row, info)
                done += 1
                self.after(0, self._conv_set_overall, done, total)
                continue

            # Skip conv step if already processed with current conv settings
            if self._conv_skip_processed_var.get() and self._history_check(info.path, conv_hash):
                info.status = "Skipped"
                self._conv_log(
                    f"[skip] {info.path.name}  (already processed with current conv settings)")
                self.after(0, self._conv_refresh_row, info)
                done += 1
                self.after(0, self._conv_set_overall, done, total)
                continue

            # Refresh probe data if not yet populated
            if info.duration == 0:
                self._conv_probe_sync(info)

            # Skip if already compatible (respects the checkbox)
            if self._conv_skip_compat_var.get() and info.is_plex_compatible:
                info.status = "Skipped"
                self._conv_log(f"[skip] {info.path.name}  (already compatible)")
                self.after(0, self._conv_refresh_row, info)
                done += 1
                self.after(0, self._conv_set_overall, done, total)
                continue

            output = self._conv_output_path(info)
            if output.exists() and not self._conv_overwrite_var.get():
                info.status = "Skipped"
                self._conv_log(f"[skip] {info.path.name}  (output exists)")
                self.after(0, self._conv_refresh_row, info)
                done += 1
                self.after(0, self._conv_set_overall, done, total)
                continue

            # Capture pre-conversion stats for history
            pre_size, pre_mtime = 0, 0.0
            try:
                _st = info.path.stat()
                pre_size, pre_mtime = _st.st_size, _st.st_mtime
            except OSError:
                pass

            cmd = self._conv_build_cmd(info, output)
            self._conv_log(f"\n[Combined {file_num}] {info.path.name}")
            self._conv_log(f"    →  {output}")

            info.status = "Converting"
            self.after(0, self._conv_refresh_row, info)

            try:
                proc = subprocess.Popen(
                    cmd, stderr=subprocess.PIPE,
                    universal_newlines=True, encoding="utf-8", errors="replace",
                )
                self._conv_current_proc = proc

                for line in proc.stderr:
                    if self._conv_stop_flag.is_set():
                        proc.terminate()
                        break
                    m = time_re.search(line)
                    if m and info.duration > 0:
                        h, mn, s, cs = (int(m.group(i)) for i in range(1, 5))
                        elapsed = h * 3600 + mn * 60 + s + cs / 100
                        pct_c   = min(100.0, elapsed / info.duration * 100)
                        info.progress = pct_c
                        self.after(0, self._conv_refresh_row, info, f"{pct_c:.0f}%")
                    elif "error" in line.lower() and "nonfatal" not in line.lower():
                        self._conv_log(f"    ! {line.rstrip()}")

                ret = proc.wait()
                self._conv_current_proc = None

                if ret == 0 and not self._conv_stop_flag.is_set():
                    info.status = "Done"
                    out_mb = output.stat().st_size / 1_048_576 if output.exists() else 0
                    ratio  = out_mb / info.size_mb * 100 if info.size_mb else 0
                    self._conv_log(
                        f"[done] {info.path.name}  "
                        f"{info.size_mb:.0f} MB → {out_mb:.0f} MB  ({ratio:.0f}%)")
                    converted += 1
                    self._history_record_at(info.path, pre_size, pre_mtime, conv_hash)
                    if self._conv_replace_orig_var.get() and output.exists():
                        self._conv_do_replace_original(info, output)
                    elif self._conv_del_orig_var.get() and output.exists():
                        info.path.unlink()
                        self._conv_log("  [del] original removed")
                else:
                    info.status = "Cancelled" if self._conv_stop_flag.is_set() else "Error"
                    self._conv_log(f"[fail] {info.path.name}  exit={ret}")
                    if output.exists():
                        output.unlink()
                    if info.status == "Error":
                        errors += 1

            except Exception as exc:
                info.status = "Error"
                self._conv_log(f"[err]  {info.path.name}: {exc}")
                self._conv_current_proc = None
                errors += 1

            self.after(0, self._conv_refresh_row, info,
                       "100%" if info.status == "Done" else "")
            done += 1
            self.after(0, self._conv_set_overall, done, total)

        counts = {s: sum(1 for f in files if f.status == s)
                  for s in ("Done", "Skipped", "Error", "Cancelled")}
        self._conv_log(
            f"\n── Combined (Converter) Finished ────────────────────────\n"
            f"   TM changes: {tm_changed}  Done: {counts['Done']}  "
            f"Skipped: {counts['Skipped']}  Error: {counts['Error']}  "
            f"Cancelled: {counts['Cancelled']}"
        )
        self.after(0, self._combined_on_done_conv)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
