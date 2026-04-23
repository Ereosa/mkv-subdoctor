#!/usr/bin/env python3
"""
mkv_subdoctor_gui.py  —  MKV SubDoctor (GUI)

Graphical front-end for mkv_subdoctor.py.

Features:
  - File / folder selector
  - Multi-language keep selection (checkboxes + custom code entry)
  - Language remap pairs (for mislabeled image tracks)
  - Dry-run, Recursive, No-Log toggles
  - Configurable log directory
  - Live output display with auto-scroll
  - Start / Pause-Resume / Stop controls
  - Per-file progress bar

Requirements:
  pip install langdetect pyspellchecker pillow
  MKVToolNix in PATH or default install location
  mkv_subdoctor.py in the same folder as this script
"""

import contextlib
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

try:
    from PIL import Image as _PILImage, ImageTk as _ImageTk
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

_BMC_URL = "https://buymeacoffee.com/mkvsubdoctor"

# ── Load the core module ──────────────────────────────────────────────────────

_SCRIPT_DIR = Path(__file__).parent
_BMC_IMG    = _SCRIPT_DIR / "bmc-button.png"
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
        self.geometry("960x780")
        self.minsize(720, 560)

        self._output_q:   queue.Queue = queue.Queue()
        self._worker:     threading.Thread | None = None
        self._paused:     bool = False
        self._total:      int  = 0
        self._done:       int  = 0

        # Track custom language codes added by the user
        self._custom_langs: set[str] = set()

        self._build_ui()
        self._poll_output()   # start the 100ms GUI poll loop

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=3)   # files + options panels
        self.rowconfigure(1, weight=0)   # control bar
        self.rowconfigure(2, weight=4)   # output

        top = ttk.Frame(self, padding=5)
        top.grid(row=0, column=0, sticky="nsew")
        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, weight=1)
        top.rowconfigure(0, weight=1)

        self._build_files_panel(top)
        self._build_options_panel(top)
        self._build_controls()
        self._build_output()

    # -- Files panel -----------------------------------------------------------

    def _build_files_panel(self, parent):
        frm = ttk.LabelFrame(parent, text="Files & Folders", padding=5)
        frm.grid(row=0, column=0, sticky="nsew", padx=(0, 4), pady=3)
        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(0, weight=1)

        # Listbox + scrollbar
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

        # Side buttons
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

        # Canvas + scrollbar for the checkbox list
        canvas = tk.Canvas(lang_frm, highlightthickness=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(lang_frm, orient="vertical", command=canvas.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=vsb.set)

        inner = ttk.Frame(canvas)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_frame_configure(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        def _on_canvas_configure(e):
            canvas.itemconfig(win_id, width=e.width)

        inner.bind("<Configure>", _on_frame_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        # Mouse-wheel scrolling (bind while cursor is over the canvas)
        def _on_enter(_e):
            canvas.bind_all("<MouseWheel>",
                lambda ev: canvas.yview_scroll(-1 * (ev.delta // 120), "units"))
        def _on_leave(_e):
            canvas.unbind_all("<MouseWheel>")
        canvas.bind("<Enter>", _on_enter)
        canvas.bind("<Leave>", _on_leave)

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

    def _build_right_options(self, parent):
        right = ttk.Frame(parent)
        right.grid(row=0, column=1, sticky="nsew", pady=3)
        right.columnconfigure(0, weight=1)

        # Toggle options
        self._recursive_var = tk.BooleanVar(value=True)
        self._dry_run_var   = tk.BooleanVar(value=False)
        self._no_log_var    = tk.BooleanVar(value=False)
        ttk.Checkbutton(right, text="Recursive",      variable=self._recursive_var).pack(anchor="w", pady=2)
        ttk.Checkbutton(right, text="Dry Run",        variable=self._dry_run_var).pack(anchor="w", pady=2)
        ttk.Checkbutton(right, text="Disable Logging",variable=self._no_log_var).pack(anchor="w", pady=2)

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

    def _build_controls(self):
        bar = ttk.Frame(self, padding=(5, 3))
        bar.grid(row=1, column=0, sticky="ew")

        self._start_btn = ttk.Button(bar, text="  Start",        command=self._start,        width=12)
        self._pause_btn = ttk.Button(bar, text="  Pause",        command=self._toggle_pause, width=12, state="disabled")
        self._stop_btn  = ttk.Button(bar, text="  Stop",         command=self._stop,         width=12, state="disabled")

        self._start_btn.pack(side="left", padx=4)
        self._pause_btn.pack(side="left", padx=4)
        self._stop_btn.pack(side="left",  padx=4)

        self._status_lbl = ttk.Label(bar, text="Ready.", width=28)
        self._status_lbl.pack(side="left", padx=10)

        self._progress = ttk.Progressbar(bar, mode="determinate", length=220)
        self._progress.pack(side="left", padx=4)

        self._pct_lbl = ttk.Label(bar, text="", width=7)
        self._pct_lbl.pack(side="left")

    # -- Output area -----------------------------------------------------------

    def _build_output(self):
        out_frm = ttk.LabelFrame(self, text="Output", padding=5)
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

        # Buy Me a Coffee button — lower right
        bmc_img = self._load_bmc_image(height=32)
        if bmc_img:
            bmc = tk.Label(btn_row, image=bmc_img, cursor="hand2",
                           relief="flat", borderwidth=0)
            bmc.image = bmc_img   # keep reference so GC doesn't drop it
        else:
            # Fallback if PIL unavailable or image missing
            bmc = tk.Label(btn_row, text="☕ Buy Me a Coffee",
                           foreground="#000000", background="#FFDD00",
                           font=("Segoe UI", 9, "bold"),
                           cursor="hand2", padx=8, pady=3)
        bmc.pack(side="right", padx=(0, 2))
        bmc.bind("<Button-1>", lambda _: webbrowser.open(_BMC_URL))

    # ── BMC image loader ──────────────────────────────────────────────────────

    def _load_bmc_image(self, height: int = 32):
        """Load and scale the BMC button PNG.  Returns ImageTk.PhotoImage or None."""
        if not _PIL_OK or not _BMC_IMG.exists():
            return None
        try:
            img = _PILImage.open(_BMC_IMG).convert("RGBA")
            # Scale to desired height, preserve aspect ratio
            w = int(img.width * height / img.height)
            img = img.resize((w, height), _PILImage.LANCZOS)
            return _ImageTk.PhotoImage(img)
        except Exception:
            return None

    # ── File helpers ──────────────────────────────────────────────────────────

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

    # ── Language helpers ──────────────────────────────────────────────────────

    def _add_custom_lang(self):
        raw  = self._custom_lang_entry.get().strip().lower()
        code = core._normalize_lang(raw)   # normalise to 639-1 where possible
        if not code or code in ("und", "mul"):
            messagebox.showwarning("Invalid Code",
                "Enter a valid ISO 639-1 (2-letter) or 639-2 (3-letter) language code.")
            return
        if code in self._lang_vars:
            # It's a built-in — just tick it
            self._lang_vars[code].set(True)
            self._custom_lang_entry.set("") if hasattr(self._custom_lang_entry, "set") else None
            messagebox.showinfo("Language Selected",
                f"'{code}' is already in the list — checkbox ticked.")
            return
        if code not in self._custom_langs:
            self._custom_langs.add(code)
        self._custom_lang_entry.set("") if hasattr(self._custom_lang_entry, "set") else \
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

    # ── Remap helpers ─────────────────────────────────────────────────────────

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

    # ── Output helpers ────────────────────────────────────────────────────────

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

    def _poll_output(self):
        """Drain the queue into the text widget — called every 100 ms on the main thread."""
        try:
            while True:
                line = self._output_q.get_nowait()
                # Count processed files for progress bar
                stripped = line.strip()
                if stripped.startswith("Processing:") or stripped.startswith("[DRY RUN] Processing:"):
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
        self.after(100, self._poll_output)

    # ── Processing control ────────────────────────────────────────────────────

    def _start(self):
        paths = list(self._path_lb.get(0, "end"))
        if not paths:
            messagebox.showwarning("No Input",
                "Add at least one MKV file or folder before starting.")
            return

        keep_langs = self._get_keep_langs()
        remaps     = self._get_remaps()
        dry_run    = self._dry_run_var.get()
        recursive  = self._recursive_var.get()
        no_log     = self._no_log_var.get()
        log_dir    = self._log_dir_var.get()

        # Reset core events
        core._pause_event.set()
        core._stop_event.clear()

        # Reset progress
        self._done  = 0
        self._total = 0
        self._progress["value"] = 0
        self._pct_lbl.configure(text="")
        self._status_lbl.configure(text="Starting…")
        self._paused = False

        # Update button states
        self._start_btn.configure(state="disabled")
        self._pause_btn.configure(state="normal", text="  Pause")
        self._stop_btn.configure(state="normal")

        self._worker = threading.Thread(
            target=self._worker_func,
            args=(paths, keep_langs, remaps, dry_run, recursive, no_log, log_dir),
            daemon=True,
        )
        self._worker.start()

    def _toggle_pause(self):
        if self._paused:
            # Resume
            core._pause_event.set()
            self._paused = False
            self._pause_btn.configure(text="  Pause")
            self._status_lbl.configure(text="Resuming…")
        else:
            # Pause (takes effect after the current file finishes)
            core._pause_event.clear()
            self._paused = True
            self._pause_btn.configure(text="  Resume")
            self._status_lbl.configure(text="Pausing after current file…")

    def _stop(self):
        core._stop_event.set()
        core._pause_event.set()   # unblock if currently paused
        self._status_lbl.configure(text="Stopping after current file…")

    def _on_done(self):
        """Called on the main thread when the worker finishes."""
        self._start_btn.configure(state="normal")
        self._pause_btn.configure(state="disabled", text="  Pause")
        self._stop_btn.configure(state="disabled")
        self._progress["value"] = 100
        self._pct_lbl.configure(text="100%")
        stopped = core._stop_event.is_set()
        self._status_lbl.configure(
            text="Stopped." if stopped else
            f"Done.  {self._done}/{self._total} files")

    # ── Worker thread ─────────────────────────────────────────────────────────

    def _worker_func(self, paths, keep_langs, remaps, dry_run, recursive, no_log, log_dir):
        # Configure logging
        if no_log:
            core._LOG_DIR = None
        else:
            core._LOG_DIR = Path(log_dir)

        # Collect MKV files
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

        modified = 0
        errors   = 0

        stream = _QueueStream(self._output_q)
        with contextlib.redirect_stdout(stream):
            for f in mkv_files:
                if core._check_pause_stop():
                    self._output_q.put("\nProcessing stopped by user.\n")
                    break
                try:
                    if core.process_mkv(str(f), dry_run=dry_run,
                                        remap_langs=remaps, keep_langs=keep_langs):
                        modified += 1
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


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
