#!/usr/bin/env python3
"""Gantz 2.0 - a fast, offline file toolkit for Windows.

Everything runs locally: no network calls, no telemetry, no third-party
packages.  The interface is built on Tkinter (bundled with Python) so the
packaged .exe stays small and starts instantly.

Pages
-----
* Rename  - bulk rename with live preview, copy/move into an output folder
* Find    - search & replace across text files, with per-file previews
* Zip     - compress folders/files into a .zip with a chosen level
* Unzip   - inspect archives, then extract with zip-slip protection

All destructive operations are recorded in the undo history and can be
reverted from the sidebar (``Ctrl+Z``).
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk
import tkinter as tk

import gantz_core as core

# ---------------------------------------------------------------------------
# Appearance
# ---------------------------------------------------------------------------

BG = "#eef1f6"
CARD = "#ffffff"
CARD_ALT = "#f8fafc"
INK = "#111827"
INK_SOFT = "#374151"
MUTED = "#6b7280"
LINE = "#e2e8f0"
ACCENT = "#2563eb"
ACCENT_DARK = "#1d4ed8"
ACCENT_SOFT = "#dbeafe"
SIDEBAR = "#0f172a"
SIDEBAR_HOVER = "#1b263c"
SIDEBAR_MUTED = "#94a3b8"
OK = "#047857"
WARN = "#b45309"
DANGER = "#b91c1c"

UI_FONT = "Segoe UI"
MONO_FONT = "Consolas"

PAGES = [
    ("rename", "Rename", "Bulk rename with a live preview"),
    ("find", "Find & Replace", "Search and replace text across files"),
    ("zip", "Zip", "Compress files and folders"),
    ("unzip", "Unzip", "Inspect and extract archives"),
]


def app_asset(name: str) -> Path:
    """Locate a bundled asset both in development and inside the .exe."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "assets" / name


def open_path(path: Path) -> None:
    """Open a file/folder with the operating system's default handler."""
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


def settings_path() -> Path:
    return core.data_dir() / "settings.json"


def load_settings() -> dict:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    try:
        settings_path().write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        pass


def as_int(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def enable_windows_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Small themed widgets
# ---------------------------------------------------------------------------


class Card(tk.Frame):
    """White panel with a hairline border, used for every section."""

    def __init__(self, master, padding: int = 16, bg: str = CARD, **kwargs):
        super().__init__(
            master,
            bg=bg,
            highlightbackground=LINE,
            highlightcolor=LINE,
            highlightthickness=1,
            bd=0,
            **kwargs,
        )
        self.body = tk.Frame(self, bg=bg)
        self.body.pack(fill="both", expand=True, padx=padding, pady=padding)


class FlatButton(tk.Button):
    """Flat, colour-controlled button (ttk's native buttons look dated)."""

    VARIANTS = {
        "primary": (ACCENT, "#ffffff", ACCENT_DARK, "#ffffff"),
        "secondary": ("#ffffff", INK_SOFT, "#eef2f7", INK),
        "ghost": (BG, INK_SOFT, "#e3e8f0", INK),
        "card": (CARD, INK_SOFT, CARD_ALT, INK),
        "danger": ("#fef2f2", DANGER, "#fee2e2", DANGER),
        "dark": (SIDEBAR_HOVER, "#e2e8f0", "#24324d", "#ffffff"),
        "success": (OK, "#ffffff", "#065f46", "#ffffff"),
    }

    def __init__(self, master, text: str = "", variant: str = "secondary", **kwargs):
        bg, fg, active_bg, active_fg = self.VARIANTS.get(variant, self.VARIANTS["secondary"])
        kwargs.setdefault("font", (UI_FONT, 10, "bold" if variant in ("primary", "success") else "normal"))
        kwargs.setdefault("padx", 14)
        kwargs.setdefault("pady", 7 if variant == "primary" else 6)
        kwargs.setdefault("relief", "flat")
        kwargs.setdefault("bd", 0)
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("cursor", "hand2")
        kwargs.setdefault("disabledforeground", "#9ca3af")
        super().__init__(
            master,
            text=text,
            bg=bg,
            fg=fg,
            activebackground=active_bg,
            activeforeground=active_fg,
            **kwargs,
        )
        self._bg = bg
        self._fg = fg
        self._hover_bg = active_bg
        self._hover_fg = active_fg
        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<Leave>", self._on_leave, add="+")

    def _on_enter(self, _event=None):
        if str(self["state"]) != "disabled":
            self.configure(bg=self._hover_bg, fg=self._hover_fg)

    def _on_leave(self, _event=None):
        if str(self["state"]) == "disabled":
            self.configure(bg=self._bg, fg="#9ca3af")
        else:
            self.configure(bg=self._bg, fg=self._fg)


class Glyph(tk.Canvas):
    """Crisp vector icons drawn on a canvas - no image files or emoji fonts."""

    def __init__(self, master, kind: str, size: int = 20, color: str = "#ffffff", bg: str = SIDEBAR):
        super().__init__(master, width=size, height=size, bg=bg, highlightthickness=0, bd=0)
        self.kind = kind
        self.size = size
        self.color = color
        self.draw()

    def draw(self) -> None:
        self.delete("all")
        s = self.size
        c = self.color
        pad = max(2, int(s * 0.14))
        w = max(1.6, s * 0.085)
        if self.kind == "rename":
            # Pencil
            self.create_line(pad + 1.5, s - pad - 1.5, s * 0.62, s * 0.34, fill=c, width=w * 1.35)
            self.create_polygon(
                s - pad, pad, s - pad - s * 0.1, s * 0.42, s * 0.66, pad + s * 0.16,
                fill=c, outline=c,
            )
            self.create_line(pad, s - pad, s * 0.2, s - pad, fill=c, width=w)
        elif self.kind == "find":
            # Magnifier
            r = s * 0.28
            cx, cy = s * 0.42, s * 0.42
            self.create_oval(cx - r, cy - r, cx + r, cy + r, outline=c, width=w * 1.2)
            self.create_line(cx + r * 0.72, cy + r * 0.72, s - pad, s - pad, fill=c, width=w * 1.4,
                             capstyle="round")
        elif self.kind == "zip":
            # Box with a zip line
            self.create_rectangle(pad, s * 0.3, s - pad, s - pad, outline=c, width=w)
            self.create_line(pad, s * 0.3, s - pad, s * 0.3, fill=c, width=w)
            self.create_line(s * 0.5, s * 0.3, s * 0.5, s - pad, fill=c, width=w * 0.8,
                             dash=(2, 2))
        elif self.kind == "unzip":
            # Open box + arrow pointing down into it
            self.create_line(pad, s * 0.46, s - pad, s * 0.46, fill=c, width=w)
            self.create_line(pad, s * 0.46, pad + 1.5, s - pad, fill=c, width=w)
            self.create_line(s - pad, s * 0.46, s - pad - 1.5, s - pad, fill=c, width=w)
            self.create_line(pad + 1.5, s - pad, s - pad - 1.5, s - pad, fill=c, width=w)
            self.create_line(s * 0.5, pad * 0.4, s * 0.5, s * 0.56, fill=c, width=w)
            self.create_line(s * 0.36, s * 0.42, s * 0.5, s * 0.58, fill=c, width=w)
            self.create_line(s * 0.64, s * 0.42, s * 0.5, s * 0.58, fill=c, width=w)
        elif self.kind == "undo":
            r = s * 0.28
            cx, cy = s * 0.52, s * 0.54
            self.create_arc(cx - r, cy - r, cx + r, cy + r, start=30, extent=290,
                            style="arc", outline=c, width=w * 1.2)
            self.create_line(cx - r, cy - r * 0.5, cx - r, cy + r * 0.55, fill=c, width=w)
            self.create_line(cx - r, cy - r * 0.5, cx - r * 0.25, cy - r * 0.72, fill=c, width=w)
        elif self.kind == "folder":
            self.create_line(pad, s - pad, s - pad, s - pad, fill=c, width=w)
            self.create_line(pad, s - pad, pad, s * 0.34, fill=c, width=w)
            self.create_line(pad, s * 0.34, s * 0.42, s * 0.34, fill=c, width=w)
            self.create_line(s * 0.42, s * 0.34, s * 0.5, s * 0.46, fill=c, width=w)
            self.create_line(s * 0.5, s * 0.46, s - pad, s * 0.46, fill=c, width=w)
            self.create_line(s - pad, s * 0.46, s - pad, s - pad, fill=c, width=w)
        elif self.kind == "check":
            self.create_line(pad, s * 0.52, s * 0.42, s - pad, fill=c, width=w * 1.4)
            self.create_line(s * 0.42, s - pad, s - pad, pad, fill=c, width=w * 1.4)
        elif self.kind == "alert":
            self.create_line(s * 0.5, pad, s * 0.5, s * 0.62, fill=c, width=w * 1.4)
            self.create_oval(s * 0.42, s - pad - 2, s * 0.58, s - pad + 1.6, fill=c, outline=c)


class NavItem(tk.Frame):
    """Sidebar navigation entry with hover + active states."""

    def __init__(self, master, key: str, label: str, glyph: str, command):
        super().__init__(master, bg=SIDEBAR, cursor="hand2")
        self.key = key
        self.command = command
        self.active = False
        self.glyph = Glyph(self, glyph, size=18, color=SIDEBAR_MUTED, bg=SIDEBAR)
        self.glyph.pack(side="left", padx=(14, 12), pady=10)
        self.label = tk.Label(
            self, text=label, bg=SIDEBAR, fg=SIDEBAR_MUTED, font=(UI_FONT, 10), anchor="w"
        )
        self.label.pack(side="left", fill="x", expand=True, pady=10)
        self.bar = tk.Frame(self, bg=SIDEBAR, width=3)
        self.bar.pack(side="left", fill="y")

        for widget in (self, self.label, self.glyph):
            widget.bind("<Button-1>", self._click)
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)

    def _click(self, _event=None):
        self.command(self.key)

    def _enter(self, _event=None):
        if not self.active:
            self._paint(SIDEBAR_HOVER, "#e2e8f0")

    def _leave(self, _event=None):
        if not self.active:
            self._paint(SIDEBAR, SIDEBAR_MUTED)

    def _paint(self, bg: str, fg: str, bar: str | None = None):
        self.configure(bg=bg)
        self.label.configure(bg=bg, fg=fg)
        self.glyph.configure(bg=bg)
        self.glyph.color = fg
        self.glyph.draw()
        self.bar.configure(bg=bar if bar is not None else bg)

    def set_active(self, active: bool) -> None:
        self.active = active
        if active:
            self._paint(SIDEBAR_HOVER, "#ffffff", ACCENT)
            self.label.configure(font=(UI_FONT, 10, "bold"))
        else:
            self._paint(SIDEBAR, SIDEBAR_MUTED)
            self.label.configure(font=(UI_FONT, 10))


def field_label(master, text: str) -> tk.Label:
    return tk.Label(
        master, text=text.upper(), bg=CARD, fg=MUTED,
        font=(UI_FONT, 8, "bold"), anchor="w",
    )


def bind_mousewheel(widget: ttk.Treeview) -> None:
    """Make sure the wheel scrolls tables on every platform."""

    def _on_wheel(event):
        if getattr(event, "num", None) == 4:
            widget.yview_scroll(-3, "units")
        elif getattr(event, "num", None) == 5:
            widget.yview_scroll(3, "units")
        else:
            widget.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    widget.bind("<MouseWheel>", _on_wheel)
    widget.bind("<Button-4>", _on_wheel)
    widget.bind("<Button-5>", _on_wheel)


def make_table(master, columns, height=12) -> ttk.Treeview:
    """A borderless treeview with a slim scrollbar in a plain frame."""
    wrapper = tk.Frame(master, bg=CARD)
    tree = ttk.Treeview(
        wrapper,
        columns=[c[0] for c in columns],
        show="headings",
        style="Data.Treeview",
        height=height,
        selectmode="extended",
    )
    for key, heading, width, anchor in columns:
        tree.heading(key, text=heading, anchor="w")
        tree.column(key, width=width, anchor=anchor, stretch=(key in ("name", "preview", "target", "location")))
    scroll = ttk.Scrollbar(wrapper, orient="vertical", command=tree.yview,
                           style="Slim.Vertical.TScrollbar")
    tree.configure(yscrollcommand=scroll.set)
    tree.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    bind_mousewheel(tree)
    tree.wrapper = wrapper  # type: ignore[attr-defined]
    return tree


def attach_tree_menu(app, tree, path_getter) -> None:
    """Right-click menu: open containing folder / copy path."""
    menu = tk.Menu(app, tearoff=0, font=(UI_FONT, 9))
    menu.add_command(label="Open containing folder", command=lambda: _reveal())
    menu.add_command(label="Copy full path", command=lambda: _copy())

    def _selected_path():
        selection = tree.selection()
        if not selection:
            return None
        values = path_getter(selection[0])
        return Path(values) if values else None

    def _reveal():
        path = _selected_path()
        if path is not None:
            open_path(path.parent if path.is_file() else path)

    def _copy():
        path = _selected_path()
        if path is not None:
            app.clipboard_clear()
            app.clipboard_append(str(path))
            app.toast(f"Copied path: {path}", "info")

    def _popup(event):
        row = tree.identify_row(event.y)
        if row:
            if row not in tree.selection():
                tree.selection_set(row)
            menu.tk_popup(event.x_root, event.y_root)
        return "break"

    tree.bind("<Button-3>", _popup)
    if sys.platform == "darwin":
        tree.bind("<Button-2>", _popup)


def configure_fonts(root: tk.Tk) -> None:
    """Pick fonts that exist on this machine and set the ttk theme."""
    global UI_FONT, MONO_FONT
    families = set(tkfont.families(root))

    def first(*candidates: str, fallback: str) -> str:
        return next((name for name in candidates if name in families), fallback)

    UI_FONT = first("Segoe UI Variable Text", "Segoe UI", "Inter", "Ubuntu",
                    "DejaVu Sans", fallback="TkDefaultFont")
    MONO_FONT = first("Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono",
                      "Courier New", fallback="TkFixedFont")

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", font=(UI_FONT, 10), background=BG, foreground=INK)
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=INK, font=(UI_FONT, 10))

    # Tables -----------------------------------------------------------------
    style.configure(
        "Data.Treeview",
        background=CARD,
        fieldbackground=CARD,
        foreground=INK,
        rowheight=27,
        borderwidth=0,
        font=(UI_FONT, 10),
    )
    style.configure(
        "Data.Treeview.Heading",
        background=CARD_ALT,
        foreground=MUTED,
        relief="flat",
        font=(UI_FONT, 8, "bold"),
        padding=(10, 8),
        borderwidth=0,
    )
    style.map(
        "Data.Treeview",
        background=[("selected", ACCENT_SOFT)],
        foreground=[("selected", INK)],
    )
    style.map("Data.Treeview.Heading", background=[("active", CARD_ALT)])
    try:
        style.layout("Data.Treeview", [("Data.Treeview.treearea", {"sticky": "nswe"})])
    except tk.TclError:
        pass

    # Inputs -----------------------------------------------------------------
    for name in ("Field.TEntry", "Field.TCombobox", "Field.TSpinbox"):
        style.configure(
            name,
            fieldbackground=CARD,
            background=CARD,
            foreground=INK,
            bordercolor=LINE,
            lightcolor=LINE,
            darkcolor=LINE,
            insertcolor=INK,
            padding=(8, 6),
            arrowcolor=MUTED,
        )
        style.map(
            name,
            bordercolor=[("focus", ACCENT), ("hover", "#c7d2e2")],
            lightcolor=[("focus", ACCENT)],
            darkcolor=[("focus", ACCENT)],
            fieldbackground=[("readonly", CARD)],
            foreground=[("disabled", "#9ca3af")],
        )
    root.option_add("*TCombobox*Listbox.background", CARD)
    root.option_add("*TCombobox*Listbox.foreground", INK)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_SOFT)
    root.option_add("*TCombobox*Listbox.selectForeground", INK)
    root.option_add("*TCombobox*Listbox.font", (UI_FONT, 10))

    style.configure(
        "Card.TCheckbutton",
        background=CARD,
        foreground=INK_SOFT,
        font=(UI_FONT, 10),
        focuscolor=CARD,
        indicatorcolor=CARD,
        indicatorbackground=CARD,
    )
    style.map(
        "Card.TCheckbutton",
        background=[("active", CARD)],
        foreground=[("active", INK)],
        indicatorcolor=[("selected", ACCENT), ("active", ACCENT)],
    )
    style.configure(
        "Card.TRadiobutton",
        background=CARD,
        foreground=INK_SOFT,
        font=(UI_FONT, 10),
        focuscolor=CARD,
        indicatorcolor=CARD,
    )
    style.map(
        "Card.TRadiobutton",
        background=[("active", CARD)],
        foreground=[("active", INK), ("selected", INK)],
        indicatorcolor=[("selected", ACCENT), ("active", ACCENT)],
    )

    style.configure(
        "Accent.Horizontal.TProgressbar",
        troughcolor=LINE,
        background=ACCENT,
        bordercolor=LINE,
        lightcolor=ACCENT,
        darkcolor=ACCENT,
        thickness=6,
    )
    style.configure(
        "Slim.Vertical.TScrollbar",
        background="#cfd8e5",
        troughcolor=CARD,
        bordercolor=CARD,
        lightcolor="#cfd8e5",
        darkcolor="#cfd8e5",
        arrowcolor=MUTED,
        arrowsize=11,
        gripcount=0,
    )
    style.map("Slim.Vertical.TScrollbar", background=[("active", "#aebcd1")])


class GantzApp(tk.Tk):
    """Main window."""

    def __init__(self, initial_paths: list[Path] | None = None):
        super().__init__()
        self.settings = load_settings()
        configure_fonts(self)

        self.title(f"Gantz {core.APP_VERSION} - {core.APP_TAGLINE}")
        self.configure(bg=BG)
        self.minsize(1040, 680)
        try:
            self.geometry(self.settings.get("geometry", "1240x800"))
        except tk.TclError:
            self.geometry("1240x800")

        icon = app_asset("gantz.png")
        if icon.is_file():
            try:
                self._icon_image = tk.PhotoImage(file=str(icon))
                self.iconphoto(True, self._icon_image)
            except tk.TclError:
                pass
        ico = app_asset("gantz.ico")
        if os.name == "nt" and ico.is_file():
            try:
                self.iconbitmap(str(ico))
            except tk.TclError:
                pass

        # State -----------------------------------------------------------------
        self.files: list[core.FileInfo] = []
        self.zips: list[core.FileInfo] = []
        self.extra_zips: list[Path] = []
        self.input_paths: list[Path] = []
        self.output_dir: Path | None = None
        self._plan: core.RenamePlan | None = None
        self._replace_plan: core.ReplacePlan | None = None
        self._files_dirty = True
        self._page = "rename"
        self._alive = True
        self._busy = False
        self._jobs: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()
        self._scan_timer: str | None = None
        self._preview_timer: str | None = None
        self._action_buttons: list[FlatButton] = []
        self._zip_items: list[core.FileInfo] = []
        self._zip_name_auto = True
        self._find_rows: list[core.ReplaceItem] = []
        self._zip_rows: list[core.FileInfo] = []
        self._last_zip_target: Path | None = None
        self._last_rename_destination: Path | None = None
        self._input_display = ""
        self._output_display = ""

        self._build_menu()
        self._build_layout()
        self._bind_shortcuts()
        self._load_state(initial_paths)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(60, self._drain_jobs)
        self.after(220, self._startup_scan)

    # ------------------------------------------------------------------ layout
    def _build_layout(self) -> None:
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self._build_sidebar()

        self.content = tk.Frame(self, bg=BG)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_source_bar()
        self._build_pages()
        self._build_status_bar()

    def _build_sidebar(self) -> None:
        sidebar = tk.Frame(self, bg=SIDEBAR, width=212)
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)
        sidebar.pack_propagate(False)

        brand = tk.Frame(sidebar, bg=SIDEBAR)
        brand.pack(fill="x", padx=18, pady=(22, 18))
        mark = tk.Canvas(brand, width=34, height=34, bg=SIDEBAR, highlightthickness=0)
        mark.pack(side="left")
        mark.create_rectangle(0, 0, 34, 34, fill=ACCENT, outline="")
        mark.create_text(17, 18, text="G", fill="#ffffff", font=(UI_FONT, 17, "bold"))
        titles = tk.Frame(brand, bg=SIDEBAR)
        titles.pack(side="left", padx=(10, 0))
        tk.Label(titles, text="GANTZ", bg=SIDEBAR, fg="#ffffff",
                 font=(UI_FONT, 13, "bold")).pack(anchor="w")
        tk.Label(titles, text=core.APP_TAGLINE, bg=SIDEBAR, fg=SIDEBAR_MUTED,
                 font=(UI_FONT, 8)).pack(anchor="w")

        tk.Label(sidebar, text="TOOLS", bg=SIDEBAR, fg="#64748b",
                 font=(UI_FONT, 8, "bold")).pack(anchor="w", padx=22, pady=(10, 6))

        self.nav: dict[str, NavItem] = {}
        for key, label, _subtitle in PAGES:
            glyph = {"rename": "rename", "find": "find", "zip": "zip", "unzip": "unzip"}[key]
            item = NavItem(sidebar, key, label, glyph, self.show_page)
            item.pack(fill="x", padx=10, pady=1)
            self.nav[key] = item

        tk.Frame(sidebar, bg=SIDEBAR).pack(fill="both", expand=True)

        footer = tk.Frame(sidebar, bg=SIDEBAR)
        footer.pack(fill="x", padx=16, pady=18)
        self.undo_button = FlatButton(
            footer, text="  Undo last change", variant="dark",
            font=(UI_FONT, 9), anchor="w", command=self.undo_last,
        )
        self.undo_button.pack(fill="x", pady=(0, 10))
        self.undo_hint = tk.Label(
            footer, text="Nothing to undo yet", bg=SIDEBAR, fg="#64748b",
            font=(UI_FONT, 8), anchor="w", wraplength=170, justify="left",
        )
        self.undo_hint.pack(fill="x")
        tk.Label(
            footer, text=f"v{core.APP_VERSION}  ·  offline only", bg=SIDEBAR,
            fg="#475569", font=(UI_FONT, 8), anchor="w",
        ).pack(fill="x", pady=(10, 0))

    def _build_header(self) -> None:
        header = tk.Frame(self.content, bg=BG)
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 10))
        header.grid_columnconfigure(0, weight=1)
        text = tk.Frame(header, bg=BG)
        text.grid(row=0, column=0, sticky="w")
        self.page_title = tk.Label(text, text="Rename", bg=BG, fg=INK,
                                   font=(UI_FONT, 20, "bold"), anchor="w")
        self.page_title.pack(anchor="w")
        self.page_subtitle = tk.Label(text, text="", bg=BG, fg=MUTED,
                                      font=(UI_FONT, 10), anchor="w")
        self.page_subtitle.pack(anchor="w", pady=(1, 0))

        actions = tk.Frame(header, bg=BG)
        actions.grid(row=0, column=1, sticky="e")
        FlatButton(actions, text="Open output folder", command=self.open_output).pack(
            side="left", padx=(0, 8)
        )
        FlatButton(actions, text="Refresh", command=self.scan_now).pack(side="left")

    def _build_source_bar(self) -> None:
        bar = Card(self.content, padding=14)
        bar.grid(row=1, column=0, sticky="ew", padx=24)
        body = bar.body
        body.grid_columnconfigure(1, weight=1)
        body.grid_columnconfigure(4, weight=1)
        body.grid_columnconfigure(5, weight=1)

        field_label(body, "From").grid(row=0, column=0, sticky="w", padx=(2, 10))
        self.input_var = tk.StringVar()
        self.input_entry = ttk.Entry(body, textvariable=self.input_var, style="Field.TEntry")
        self.input_entry.grid(row=0, column=1, sticky="ew", pady=(6, 0))
        self.input_entry.bind("<Return>", lambda _e: self._commit_input_entry())
        self.input_entry.bind("<FocusOut>", lambda _e: self._commit_input_entry(silent=True))
        FlatButton(body, text="Browse", command=self.browse_input).grid(
            row=0, column=2, padx=6, pady=(6, 0)
        )
        FlatButton(body, text="Add files", command=self.add_files).grid(
            row=0, column=3, padx=(0, 16), pady=(6, 0)
        )

        field_label(body, "To").grid(row=0, column=4, sticky="w", padx=(2, 10))
        self.output_var = tk.StringVar()
        self.output_entry = ttk.Entry(body, textvariable=self.output_var, style="Field.TEntry")
        self.output_entry.grid(row=0, column=5, sticky="ew", pady=(6, 0))
        self.output_entry.bind("<Return>", lambda _e: self._commit_output_entry())
        self.output_entry.bind("<FocusOut>", lambda _e: self._commit_output_entry(silent=True))
        FlatButton(body, text="Browse", command=self.browse_output).grid(
            row=0, column=6, padx=(6, 0), pady=(6, 0)
        )

        options = tk.Frame(body, bg=CARD)
        options.grid(row=1, column=0, columnspan=7, sticky="ew", pady=(10, 0))
        self.recursive_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            options, text="Include subfolders", variable=self.recursive_var,
            style="Card.TCheckbutton", command=self.schedule_scan,
        ).pack(side="left")
        tk.Label(options, text="·", bg=CARD, fg=LINE).pack(side="left", padx=10)
        tk.Label(options, text="Extensions", bg=CARD, fg=MUTED,
                 font=(UI_FONT, 9)).pack(side="left")
        self.ext_var = tk.StringVar()
        ext_entry = ttk.Entry(options, textvariable=self.ext_var, width=20, style="Field.TEntry")
        ext_entry.pack(side="left", padx=(8, 6))
        ext_entry.bind("<Return>", lambda _e: self.schedule_scan(50))
        ext_entry.bind("<FocusOut>", lambda _e: self.schedule_scan(50))
        tk.Label(options, text="e.g. .txt, .csv  (blank = all files)", bg=CARD,
                 fg="#9ca3af", font=(UI_FONT, 8)).pack(side="left")
        self.source_note = tk.Label(options, text="", bg=CARD, fg=MUTED,
                                    font=(UI_FONT, 9))
        self.source_note.pack(side="right")

    def _build_status_bar(self) -> None:
        bar = tk.Frame(self.content, bg=BG)
        bar.grid(row=3, column=0, sticky="ew", padx=24, pady=(8, 14))
        bar.grid_columnconfigure(0, weight=1)

        self.status_label = tk.Label(bar, text="Ready", bg=BG, fg=MUTED,
                                     font=(UI_FONT, 9), anchor="w")
        self.status_label.grid(row=0, column=0, sticky="w")

        self.progress = ttk.Progressbar(bar, style="Accent.Horizontal.TProgressbar",
                                        length=190, mode="determinate")
        self.cancel_button = FlatButton(bar, text="Cancel", variant="danger",
                                        command=self.cancel_job)
        self.count_label = tk.Label(bar, text="", bg=BG, fg=MUTED,
                                    font=(UI_FONT, 9), anchor="e")

        self.count_label.grid(row=0, column=1, sticky="e", padx=(0, 10))

    # ------------------------------------------------------------------- pages
    def _build_pages(self) -> None:
        self.page_host = tk.Frame(self.content, bg=BG)
        self.page_host.grid(row=2, column=0, sticky="nsew", padx=24, pady=(12, 0))
        self.page_host.grid_rowconfigure(0, weight=1)
        self.page_host.grid_columnconfigure(0, weight=1)

        self.pages: dict[str, tk.Frame] = {
            "rename": self._build_rename_page(),
            "find": self._build_find_page(),
            "zip": self._build_zip_page(),
            "unzip": self._build_unzip_page(),
        }
        for page in self.pages.values():
            page.grid(row=0, column=0, sticky="nsew")
            page.grid_remove()

    def _page_frame(self) -> tk.Frame:
        frame = tk.Frame(self.page_host, bg=BG)
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        return frame

    # ---- Rename ------------------------------------------------------------
    def _build_rename_page(self) -> tk.Frame:
        page = self._page_frame()

        card = Card(page, padding=14)
        card.grid(row=0, column=0, sticky="ew")
        body = card.body
        for col in (1, 3, 5, 7, 9):
            body.grid_columnconfigure(col, weight=1)

        field_label(body, "Base name").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.rename_prefix = tk.StringVar(value=self.settings.get("rename_prefix", "file"))
        prefix_entry = ttk.Entry(body, textvariable=self.rename_prefix, style="Field.TEntry")
        prefix_entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        self.rename_prefix.trace_add("write", lambda *_: self.schedule_rename_preview())

        field_label(body, "Numbering").grid(row=0, column=2, sticky="w", padx=(16, 8))
        self.rename_numbering = tk.StringVar(value=self.settings.get("rename_numbering", "after_first"))
        numbering = ttk.Combobox(
            body, textvariable=self.rename_numbering, state="readonly",
            values=["After the first file", "Every file", "No numbers"], style="Field.TCombobox",
        )
        numbering.grid(row=1, column=2, sticky="ew", pady=(5, 0), padx=(16, 0))
        self._numbering_labels = {
            "After the first file": "after_first",
            "Every file": "all",
            "No numbers": "none",
        }
        numbering.set(
            next((label for label, key in self._numbering_labels.items()
                  if key == self.rename_numbering.get()), "After the first file")
        )
        numbering.bind("<<ComboboxSelected>>", lambda _e: self.schedule_rename_preview())

        field_label(body, "Start at").grid(row=0, column=3, sticky="w", padx=(16, 8))
        self.rename_start = tk.IntVar(value=as_int(self.settings.get("rename_start", 1), 1))
        ttk.Spinbox(body, from_=0, to=999999, textvariable=self.rename_start,
                    style="Field.TSpinbox", width=8).grid(row=1, column=3, sticky="w",
                                                          pady=(5, 0), padx=(16, 0))
        self.rename_start.trace_add("write", lambda *_: self.schedule_rename_preview())

        field_label(body, "Digits").grid(row=0, column=4, sticky="w", padx=(14, 8))
        self.rename_padding = tk.IntVar(value=as_int(self.settings.get("rename_padding", 0), 0))
        ttk.Spinbox(body, from_=0, to=8, textvariable=self.rename_padding,
                    style="Field.TSpinbox", width=6).grid(row=1, column=4, sticky="w",
                                                          pady=(5, 0), padx=(14, 0))
        self.rename_padding.trace_add("write", lambda *_: self.schedule_rename_preview())

        field_label(body, "Separator").grid(row=0, column=5, sticky="w", padx=(14, 8))
        self.rename_separator = tk.StringVar(value=self.settings.get("rename_separator", ""))
        ttk.Entry(body, textvariable=self.rename_separator, width=8,
                  style="Field.TEntry").grid(row=1, column=5, sticky="w",
                                             pady=(5, 0), padx=(14, 0))
        self.rename_separator.trace_add("write", lambda *_: self.schedule_rename_preview())

        action_row = tk.Frame(body, bg=CARD)
        action_row.grid(row=2, column=0, columnspan=6, sticky="ew", pady=(14, 0))
        self.rename_mode = tk.StringVar(value="rename")
        for value, text in (
            ("rename", "Rename in place"),
            ("copy", "Copy to output folder"),
            ("move", "Move to output folder"),
        ):
            ttk.Radiobutton(
                action_row, text=text, value=value, variable=self.rename_mode,
                style="Card.TRadiobutton", command=self._clear_rename_plan,
            ).pack(side="left", padx=(0, 18))

        FlatButton(action_row, text="Apply", variant="primary",
                   command=self.apply_rename).pack(side="right")
        FlatButton(action_row, text="Refresh preview", command=self.refresh_rename_preview).pack(
            side="right", padx=(0, 8)
        )

        table_card = Card(page, padding=0)
        table_card.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        head = tk.Frame(table_card.body, bg=CARD)
        head.pack(fill="x", padx=16, pady=(14, 8))
        tk.Label(head, text="Preview", bg=CARD, fg=INK,
                 font=(UI_FONT, 10, "bold")).pack(side="left")
        self.rename_summary = tk.Label(head, text="", bg=CARD, fg=MUTED,
                                       font=(UI_FONT, 9))
        self.rename_summary.pack(side="right")

        self.rename_tree = make_table(
            table_card.body,
            [
                ("name", "Current name", 280, "w"),
                ("target", "New name", 280, "w"),
                ("location", "Folder", 300, "w"),
                ("size", "Size", 80, "e"),
                ("status", "Status", 120, "w"),
            ],
        )
        self.rename_tree.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        self.rename_tree.tag_configure("ready", foreground=OK)
        self.rename_tree.tag_configure("exists", foreground=WARN)
        self.rename_tree.tag_configure("duplicate", foreground=DANGER)
        self.rename_tree.tag_configure("unchanged", foreground=MUTED)
        attach_tree_menu(self, self.rename_tree,
                         lambda iid: self._tree_path(self.rename_tree, iid))
        self.rename_empty = self._empty_overlay(table_card.body, "Choose a folder to see the rename preview")

        return page

    # ---- Find & replace ----------------------------------------------------
    def _build_find_page(self) -> tk.Frame:
        page = self._page_frame()

        card = Card(page, padding=14)
        card.grid(row=0, column=0, sticky="ew")
        body = card.body
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        field_label(body, "Find").grid(row=0, column=0, sticky="w")
        self.find_var = tk.StringVar(value=self.settings.get("find", ""))
        self.find_entry = ttk.Entry(body, textvariable=self.find_var, style="Field.TEntry")
        self.find_entry.grid(row=1, column=0, sticky="ew", pady=(5, 0), padx=(0, 12))
        self.find_entry.bind("<Return>", lambda _e: self.find_matches())

        field_label(body, "Replace with  (leave empty to delete)").grid(row=0, column=1, sticky="w")
        self.replace_var = tk.StringVar(value=self.settings.get("replace", ""))
        ttk.Entry(body, textvariable=self.replace_var, style="Field.TEntry").grid(
            row=1, column=1, sticky="ew", pady=(5, 0)
        )

        options = tk.Frame(body, bg=CARD)
        options.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        self.case_var = tk.BooleanVar(value=bool(self.settings.get("case_sensitive", False)))
        self.regex_var = tk.BooleanVar(value=bool(self.settings.get("regex", False)))
        self.word_var = tk.BooleanVar(value=bool(self.settings.get("whole_word", False)))
        self.backup_var = tk.BooleanVar(value=True)
        for text, var in (
            ("Case sensitive", self.case_var),
            ("Regular expression", self.regex_var),
            ("Whole word", self.word_var),
            ("Keep backups (undo)", self.backup_var),
        ):
            ttk.Checkbutton(options, text=text, variable=var,
                            style="Card.TCheckbutton").pack(side="left", padx=(0, 16))

        FlatButton(options, text="Replace all", variant="primary",
                   command=self.apply_replace).pack(side="right")
        FlatButton(options, text="Find matches", command=self.find_matches).pack(
            side="right", padx=(0, 8)
        )

        table_card = Card(page, padding=0)
        table_card.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        head = tk.Frame(table_card.body, bg=CARD)
        head.pack(fill="x", padx=16, pady=(14, 8))
        tk.Label(head, text="Matches", bg=CARD, fg=INK,
                 font=(UI_FONT, 10, "bold")).pack(side="left")
        self.find_summary = tk.Label(head, text="", bg=CARD, fg=MUTED, font=(UI_FONT, 9))
        self.find_summary.pack(side="right")

        self.find_tree = make_table(
            table_card.body,
            [
                ("name", "File", 250, "w"),
                ("location", "Folder", 300, "w"),
                ("matches", "Matches", 80, "e"),
                ("preview", "First match", 480, "w"),
            ],
        )
        self.find_tree.pack(fill="both", expand=True, padx=16, pady=(0, 10))
        self.find_tree.tag_configure("error", foreground=DANGER)
        self.find_tree.bind("<<TreeviewSelect>>", self._show_match_detail)
        self.find_tree.bind("<Double-1>", lambda _e: self._open_selected(self.find_tree, "name", "location"))
        attach_tree_menu(self, self.find_tree,
                         lambda iid: self._tree_path(self.find_tree, iid))

        self.match_detail = tk.Text(
            table_card.body, height=4, bg=CARD_ALT, fg=INK_SOFT, relief="flat",
            font=(MONO_FONT, 9), wrap="word", padx=12, pady=10,
            highlightthickness=0, bd=0,
        )
        self.match_detail.pack(fill="x", padx=16, pady=(0, 14))
        self.match_detail.tag_configure("arrow", foreground=ACCENT, font=(MONO_FONT, 9, "bold"))
        self.match_detail.tag_configure("muted", foreground=MUTED)
        self._set_text(self.match_detail, "Select a result to preview the change.")
        self.find_empty = self._empty_overlay(table_card.body, "Run a search to see matching files")

        return page

    # ---- Zip ---------------------------------------------------------------
    def _build_zip_page(self) -> tk.Frame:
        page = self._page_frame()

        card = Card(page, padding=14)
        card.grid(row=0, column=0, sticky="ew")
        body = card.body
        body.grid_columnconfigure(1, weight=1)

        field_label(body, "Archive name").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.zip_name = tk.StringVar()
        ttk.Entry(body, textvariable=self.zip_name, style="Field.TEntry").grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0), padx=(0, 12)
        )
        self.zip_name.trace_add("write", lambda *_: self._update_zip_hint())

        field_label(body, "Compression").grid(row=0, column=2, sticky="w", padx=(0, 10))
        self.zip_level = tk.StringVar(value="Balanced")
        ttk.Combobox(
            body, textvariable=self.zip_level, state="readonly",
            values=["No compression (fastest)", "Fast", "Balanced", "Maximum (smallest)"],
            style="Field.TCombobox", width=22,
        ).grid(row=1, column=2, sticky="w", pady=(5, 0))

        FlatButton(body, text="Create .zip", variant="primary",
                   command=self.create_zip_clicked).grid(row=0, column=3, rowspan=2,
                                                         sticky="e", padx=(12, 0))
        FlatButton(body, text="Zip selected", command=self.zip_selected_clicked).grid(
            row=0, column=4, rowspan=2, sticky="e"
        )

        self.zip_hint = tk.Label(body, text="", bg=CARD, fg=MUTED, font=(UI_FONT, 9),
                                 anchor="w")
        self.zip_hint.grid(row=2, column=0, columnspan=5, sticky="ew", pady=(10, 0))

        table_card = Card(page, padding=0)
        table_card.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        head = tk.Frame(table_card.body, bg=CARD)
        head.pack(fill="x", padx=16, pady=(14, 8))
        tk.Label(head, text="Files to compress", bg=CARD, fg=INK,
                 font=(UI_FONT, 10, "bold")).pack(side="left")
        self.zip_summary = tk.Label(head, text="", bg=CARD, fg=MUTED, font=(UI_FONT, 9))
        self.zip_summary.pack(side="right")

        self.zip_tree = make_table(
            table_card.body,
            [
                ("name", "Name", 300, "w"),
                ("location", "Folder", 380, "w"),
                ("size", "Size", 90, "e"),
                ("modified", "Modified", 150, "w"),
            ],
        )
        self.zip_tree.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        self.zip_tree.bind("<<TreeviewSelect>>", lambda _e: self._update_zip_hint())
        attach_tree_menu(self, self.zip_tree,
                         lambda iid: self._tree_path(self.zip_tree, iid))
        self.zip_empty = self._empty_overlay(table_card.body, "Choose a folder to compress")

        return page

    # ---- Unzip -------------------------------------------------------------
    def _build_unzip_page(self) -> tk.Frame:
        page = self._page_frame()
        page.grid_rowconfigure(1, weight=3)
        page.grid_rowconfigure(2, weight=2)

        card = Card(page, padding=14)
        card.grid(row=0, column=0, sticky="ew")
        body = card.body
        body.grid_columnconfigure(0, weight=1)

        options = tk.Frame(body, bg=CARD)
        options.grid(row=0, column=0, sticky="w")
        self.unzip_subfolder = tk.BooleanVar(value=True)
        self.unzip_overwrite = tk.BooleanVar(value=False)
        self.unzip_skipjunk = tk.BooleanVar(value=True)
        for text, var in (
            ("Subfolder per archive", self.unzip_subfolder),
            ("Overwrite existing files", self.unzip_overwrite),
            ("Skip junk files", self.unzip_skipjunk),
        ):
            ttk.Checkbutton(options, text=text, variable=var,
                            style="Card.TCheckbutton").pack(side="left", padx=(0, 16))

        password_row = tk.Frame(body, bg=CARD)
        password_row.grid(row=1, column=0, sticky="w", pady=(10, 0))
        tk.Label(password_row, text="Password", bg=CARD, fg=MUTED,
                 font=(UI_FONT, 9)).pack(side="left", padx=(0, 8))
        self.unzip_password = tk.StringVar()
        self.password_entry = ttk.Entry(password_row, textvariable=self.unzip_password,
                                        style="Field.TEntry", width=22, show="\u2022")
        self.password_entry.pack(side="left")
        self.show_password = tk.BooleanVar(value=False)
        ttk.Checkbutton(password_row, text="Show", variable=self.show_password,
                        style="Card.TCheckbutton",
                        command=self._toggle_password).pack(side="left", padx=(10, 0))
        tk.Label(password_row, text="Only needed for protected archives", bg=CARD,
                 fg="#9ca3af", font=(UI_FONT, 8)).pack(side="left", padx=(10, 0))

        actions = tk.Frame(body, bg=CARD)
        actions.grid(row=0, column=1, rowspan=2, sticky="e")
        FlatButton(actions, text="Add archives", command=self.add_archives).pack(
            side="left", padx=(0, 8)
        )
        FlatButton(actions, text="Extract selected", command=self.extract_selected).pack(
            side="left", padx=(0, 8)
        )
        FlatButton(actions, text="Extract all", variant="primary",
                   command=self.extract_all).pack(side="left")

        archives_card = Card(page, padding=0)
        archives_card.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        head = tk.Frame(archives_card.body, bg=CARD)
        head.pack(fill="x", padx=16, pady=(14, 8))
        tk.Label(head, text="Archives", bg=CARD, fg=INK,
                 font=(UI_FONT, 10, "bold")).pack(side="left")
        self.unzip_summary = tk.Label(head, text="", bg=CARD, fg=MUTED, font=(UI_FONT, 9))
        self.unzip_summary.pack(side="right")

        self.unzip_tree = make_table(
            archives_card.body,
            [
                ("name", "Archive", 300, "w"),
                ("location", "Folder", 360, "w"),
                ("size", "File size", 100, "e"),
                ("files", "Files", 70, "e"),
                ("contents", "Contents", 110, "e"),
            ],
            height=8,
        )
        self.unzip_tree.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        self.unzip_tree.bind("<<TreeviewSelect>>", self._preview_archive)
        self.unzip_empty = self._empty_overlay(
            archives_card.body, "Pick a folder containing .zip files, or use Add archives"
        )

        contents_card = Card(page, padding=0)
        contents_card.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        chead = tk.Frame(contents_card.body, bg=CARD)
        chead.pack(fill="x", padx=16, pady=(14, 8))
        tk.Label(chead, text="Archive contents", bg=CARD, fg=INK,
                 font=(UI_FONT, 10, "bold")).pack(side="left")
        self.contents_summary = tk.Label(chead, text="", bg=CARD, fg=MUTED, font=(UI_FONT, 9))
        self.contents_summary.pack(side="right")

        self.contents_tree = make_table(
            contents_card.body,
            [
                ("name", "Path inside archive", 520, "w"),
                ("size", "Size", 100, "e"),
                ("date", "Modified", 130, "w"),
                ("type", "Type", 80, "w"),
            ],
            height=6,
        )
        self.contents_tree.pack(fill="both", expand=True, padx=16, pady=(0, 14))

        return page

    # ------------------------------------------------------------------ helpers
    def _empty_overlay(self, master, text: str) -> tk.Label:
        label = tk.Label(master, text=text, bg=CARD, fg="#94a3b8",
                         font=(UI_FONT, 10), justify="center")
        label.place(relx=0.5, rely=0.45, anchor="center")
        return label

    def _set_text(self, widget: tk.Text, content: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.configure(state="disabled")

    def _open_selected(self, tree: ttk.Treeview, name_col: str, folder_col: str) -> None:
        selection = tree.selection()
        if not selection:
            return
        iid = selection[0]
        path = Path(tree.set(iid, folder_col)) / tree.set(iid, name_col)
        if path.exists():
            open_path(path)
        else:
            self.toast(f"No longer exists: {path.name}", "warn")

    def _toggle_password(self) -> None:
        self.password_entry.configure(show="" if self.show_password.get() else "\u2022")

    # -------------------------------------------------------------- state/scan
    def _load_state(self, initial_paths: list[Path] | None) -> None:
        paths: list[Path] = []
        if initial_paths:
            paths = [p for p in initial_paths if p.exists()]
        else:
            saved = self.settings.get("input", "")
            if saved and Path(saved).exists():
                paths = [Path(saved)]

        zips = [p for p in paths if p.is_file() and p.suffix.casefold() == ".zip"]
        if zips:
            self.extra_zips = zips
            paths = [p for p in paths if p not in zips]
        self._set_input(paths, scan=False)

        saved_out = self.settings.get("output", "")
        if saved_out:
            self._set_output(Path(saved_out))

        self.recursive_var.set(bool(self.settings.get("recursive", True)))
        self.ext_var.set(self.settings.get("extensions", ""))
        self._zip_name_auto = True
        self._reset_zip_name()
        start_page = "unzip" if zips else self.settings.get("page", "rename")
        if start_page not in self.pages:
            start_page = "rename"
        self.show_page(start_page)
        self.refresh_undo_hint()
        self._update_empty_states()

    def _startup_scan(self) -> None:
        if self.input_paths:
            self.scan_now()
        else:
            self._update_empty_states()

    def _set_input(self, paths: list[Path], scan: bool = True) -> None:
        cleaned: list[Path] = []
        for path in paths:
            try:
                path = Path(path).expanduser().resolve()
            except OSError:
                path = Path(path).expanduser()
            if path not in cleaned:
                cleaned.append(path)
        self.input_paths = cleaned
        if not cleaned:
            self._input_display = ""
        elif len(cleaned) == 1:
            self._input_display = str(cleaned[0])
        else:
            self._input_display = f"{cleaned[0]}   (+{len(cleaned) - 1} more)"
        self.input_var.set(self._input_display)
        self._files_dirty = True
        if self._zip_name_auto:
            self._reset_zip_name()
        if scan:
            self.schedule_scan(40)

    def _set_output(self, path: Path | None) -> None:
        self.output_dir = Path(path) if path else None
        self._output_display = str(self.output_dir) if self.output_dir else ""
        self.output_var.set(self._output_display)
        self._update_zip_hint()

    def _commit_input_entry(self, silent: bool = False) -> None:
        text = self.input_var.get().strip()
        if text == getattr(self, "_input_display", ""):
            return
        if not text:
            self._set_input([])
            return
        path = Path(text).expanduser()
        if path.exists():
            self._set_input([path])
        elif not silent:
            self.toast(f"Not found: {text}", "error")

    def _commit_output_entry(self, silent: bool = False) -> None:
        text = self.output_var.get().strip()
        if text == getattr(self, "_output_display", ""):
            return
        if not text:
            self._set_output(None)
            return
        path = Path(text).expanduser()
        if path.exists() and not path.is_dir():
            if not silent:
                self.toast("The output path must be a folder", "error")
            return
        self._set_output(path)

    def browse_input(self) -> None:
        initial = str(self.input_paths[0]) if self.input_paths else str(Path.home())
        if Path(initial).is_file():
            initial = str(Path(initial).parent)
        chosen = filedialog.askdirectory(title="Choose the input folder", initialdir=initial)
        if chosen:
            self._set_input([Path(chosen)])

    def add_files(self) -> None:
        initial = str(self.input_paths[0].parent) if self.input_paths else str(Path.home())
        if Path(initial).is_file():
            initial = str(Path(initial).parent)
        chosen = filedialog.askopenfilenames(title="Add files to work on", initialdir=initial)
        if chosen:
            self._set_input(self.input_paths + [Path(p) for p in chosen])

    def browse_output(self) -> None:
        initial = str(self.output_dir or (self.input_paths[0] if self.input_paths else Path.home()))
        if Path(initial).is_file():
            initial = str(Path(initial).parent)
        chosen = filedialog.askdirectory(title="Choose the output folder", initialdir=initial)
        if chosen:
            self._set_output(Path(chosen))

    def ensure_output(self, title: str = "Choose the output folder") -> Path | None:
        if self.output_dir:
            try:
                self.output_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                messagebox.showerror("Output folder", f"Cannot create the output folder:\n{exc}")
                return None
            return self.output_dir
        initial = str(self.effective_output())
        chosen = filedialog.askdirectory(title=title, initialdir=initial, mustexist=False)
        if not chosen:
            return None
        self._set_output(Path(chosen))
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
        except OSError as exc:
            messagebox.showerror("Output folder", f"Cannot create the output folder:\n{exc}")
            return None
        return self.output_dir

    def effective_output(self) -> Path:
        """Best guess for a destination when the output box is empty."""
        if self.output_dir:
            return self.output_dir
        if self.input_paths:
            first = self.input_paths[0]
            return first.parent if first.is_dir() else first.parent
        return Path.home()

    def _reset_zip_name(self) -> None:
        self.zip_name.set(self._default_zip_name())

    def _default_zip_name(self) -> str:
        base = "archive"
        if len(self.input_paths) == 1:
            first = self.input_paths[0]
            base = first.name if first.is_dir() else (first.stem or base)
        elif self.input_paths:
            base = "collection"
        return f"{base}-{time.strftime('%Y%m%d')}.zip"

    # ------------------------------------------------------------------ scanning
    def schedule_scan(self, delay: int = 240) -> None:
        if self._scan_timer is not None:
            try:
                self.after_cancel(self._scan_timer)
            except tk.TclError:
                pass
        self._scan_timer = self.after(delay, self.scan_now)

    def scan_now(self) -> None:
        self._scan_timer = None
        if self._busy:
            self.schedule_scan(400)
            return
        roots = [p for p in self.input_paths if p.exists()]
        if not roots:
            self.files = []
            self.zips = []
            self._files_dirty = True
            self._on_scan_done(([], []))
            return
        wanted = core.parse_extensions(self.ext_var.get())
        union = (wanted | {".zip"}) if wanted else None
        recursive = self.recursive_var.get()

        def worker(progress, should_cancel):
            collected: list[core.FileInfo] = []
            for root in roots:
                if should_cancel():
                    raise core.CancelledError("Cancelled")
                if root.is_file():
                    info = self._info_for(root)
                    if info is not None:
                        collected.append(info)
                elif root.is_dir():
                    collected.extend(
                        core.scan_files(
                            root, recursive=recursive, extensions=union,
                            progress=lambda done, _total, message: progress(done, 0, message),
                        )
                    )
            collected.sort(key=lambda info: core.natural_key(info.name))
            files = [f for f in collected if (wanted is None or f.ext in wanted)]
            zips = [f for f in collected if f.ext == ".zip"]
            return files, zips

        self.run_job("Scanning files...", worker, self._on_scan_done)

    def _info_for(self, path: Path) -> core.FileInfo | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return core.FileInfo(path=path, size=stat.st_size, mtime=stat.st_mtime,
                             ext=path.suffix.casefold())

    def _on_scan_done(self, payload) -> None:
        files, zips = payload
        self.files = files
        self.zips = zips
        self._files_dirty = True
        self._plan = None
        self._replace_plan = None
        self._update_empty_states()
        self._refresh_visible_page()
        self._update_counts()
        if files:
            self.toast(f"{len(files):,} files ready", "info")
        elif self.input_paths:
            self.toast("No files matched - check the folder or extension filter", "warn")

    # ------------------------------------------------------------------- pages
    def show_page(self, key: str) -> None:
        if key not in self.pages:
            return
        self._page = key
        for name, page in self.pages.items():
            if name == key:
                page.grid()
            else:
                page.grid_remove()
        for name, item in self.nav.items():
            item.set_active(name == key)
        meta = {k: (label, subtitle) for k, label, subtitle in PAGES}
        label, subtitle = meta.get(key, ("", ""))
        self.page_title.configure(text=label)
        self.page_subtitle.configure(text=subtitle)
        self._refresh_visible_page()
        self._update_counts()

    def _refresh_visible_page(self) -> None:
        if self._page == "rename":
            self.refresh_rename_preview()
        elif self._page == "zip":
            self._render_zip_list()
        elif self._page == "unzip":
            self._render_unzip_list()
        elif self._page == "find":
            self._render_find_plan()
        self._update_empty_states()

    def _update_empty_states(self) -> None:
        has_files = bool(self.files)
        for tree, overlay in (
            (self.rename_tree, self.rename_empty),
            (self.zip_tree, self.zip_empty),
        ):
            if tree.get_children():
                overlay.place_forget()
            else:
                overlay.place(relx=0.5, rely=0.45, anchor="center")
        if self.find_tree.get_children():
            self.find_empty.place_forget()
        else:
            self.find_empty.place(relx=0.5, rely=0.45, anchor="center")
        if self.unzip_tree.get_children():
            self.unzip_empty.place_forget()
        else:
            self.unzip_empty.place(relx=0.5, rely=0.45, anchor="center")
        if not has_files and not self.input_paths:
            self.rename_empty.configure(text="Choose an input folder to get started")

    def _render_rows(self, tree: ttk.Treeview, rows: list[tuple[tuple, tuple]]) -> None:
        """Chunked tree population so the window stays responsive."""
        token = getattr(tree, "_gantz_token", 0) + 1
        tree._gantz_token = token  # type: ignore[attr-defined]
        tree.delete(*tree.get_children())

        def insert(start: int) -> None:
            if not self._alive or getattr(tree, "_gantz_token", -1) != token:
                return
            for values, tags in rows[start:start + 400]:
                tree.insert("", "end", values=values, tags=tags)
            if start + 400 < len(rows):
                self.after(1, insert, start + 400)

        insert(0)

    def _update_counts(self) -> None:
        if self._page == "unzip":
            infos = self._zip_infos()
            total = sum(info.size for info in infos)
            text = f"{len(infos):,} archives · {core.human_size(total)}"
        else:
            total = sum(info.size for info in self.files)
            text = f"{len(self.files):,} files · {core.human_size(total)}"
        self.count_label.configure(text=text)

    # --------------------------------------------------------------- rename page
    def _numbering_key(self) -> str:
        return self._numbering_labels.get(self.rename_numbering.get(), "after_first")

    def _int_value(self, var: tk.IntVar, fallback: int) -> int:
        try:
            return int(var.get())
        except (tk.TclError, ValueError):
            return fallback

    def schedule_rename_preview(self, delay: int = 220) -> None:
        if self._preview_timer is not None:
            try:
                self.after_cancel(self._preview_timer)
            except tk.TclError:
                pass
        self._preview_timer = self.after(delay, self.refresh_rename_preview)

    def _make_rename_plan(self) -> core.RenamePlan | None:
        if not self.files:
            return None
        return core.build_rename_plan(
            self.files,
            self.rename_prefix.get(),
            numbering=self._numbering_key(),
            start=self._int_value(self.rename_start, 1),
            padding=self._int_value(self.rename_padding, 0),
            separator=self.rename_separator.get(),
            sort_by="name",
        )

    def refresh_rename_preview(self, force: bool = False) -> None:
        self._preview_timer = None
        if self._page != "rename" and not force:
            return
        prefix = self.rename_prefix.get()
        plan: core.RenamePlan | None = None
        if self.files and prefix.strip():
            try:
                plan = self._make_rename_plan()
            except ValueError as exc:
                self.rename_summary.configure(text=str(exc))
        self._plan = plan

        rows: list[tuple[tuple, tuple]] = []
        if plan is not None:
            for item in plan.items:
                status_text = {
                    "ready": "Ready",
                    "exists": "Name taken",
                    "duplicate": "Conflict",
                    "unchanged": "Unchanged",
                    "invalid": "Invalid",
                }.get(item.status, item.status)
                rows.append(
                    (
                        (
                            item.source.name,
                            item.target.name,
                            str(item.source.parent),
                            self._source_size(item.source),
                            status_text,
                        ),
                        (item.status,),
                    )
                )
        elif self.files:
            for info in self.files:
                rows.append(
                    (
                        (info.name, "-", str(info.path.parent), core.human_size(info.size), "Waiting"),
                        ("unchanged",),
                    )
                )
        self._render_rows(self.rename_tree, rows)

        if plan is not None:
            bits = [f"{len(plan.items):,} files", f"{plan.ready_count:,} ready"]
            if plan.conflict_count:
                bits.append(f"{plan.conflict_count:,} blocked")
            if plan.unchanged_count:
                bits.append(f"{plan.unchanged_count:,} unchanged")
            self.rename_summary.configure(text="   ·   ".join(bits), fg=MUTED)
        elif not self.files:
            self.rename_summary.configure(text="No files loaded", fg=MUTED)
        else:
            self.rename_summary.configure(text="Enter a base name", fg=WARN)
        self._update_empty_states()
        self._update_counts()

    def _clear_rename_plan(self) -> None:
        self._plan = None
        self.schedule_rename_preview(60)

    def apply_rename(self) -> None:
        if self._busy:
            self.toast("A task is already running", "warn")
            return
        if not self.files:
            self.toast("Choose an input folder first", "warn")
            return
        try:
            plan = self._make_rename_plan()
        except ValueError as exc:
            messagebox.showinfo("Rename", str(exc))
            return
        if plan is None or not plan.items:
            return
        if plan.ready_count == 0:
            messagebox.showinfo(
                "Nothing to rename",
                "No files can be renamed with the current settings.\n\n"
                "Check the base name and the conflicts in the preview.",
            )
            return

        mode = self.rename_mode.get()
        destination: Path | None = None
        if mode in ("copy", "move"):
            destination = self.ensure_output("Choose where the renamed files should go")
            if destination is None:
                return

        action = {"rename": "Rename", "copy": "Copy", "move": "Move"}[mode]
        message = f"{action} {plan.ready_count:,} file(s)?"
        if mode == "rename":
            folders = {str(item.source.parent) for item in plan.items}
            where = next(iter(folders)) if len(folders) == 1 else f"{len(folders)} folders"
            message += f"\n\nThey will be renamed in:\n{where}"
        else:
            message += f"\n\nDestination:\n{destination}"
        if plan.conflict_count:
            message += (
                f"\n\n{plan.conflict_count:,} file(s) will be skipped because of "
                "name conflicts."
            )
        message += "\n\nYou can undo this with Ctrl+Z."
        if not messagebox.askyesno(f"{action} files", message):
            return

        self._plan = plan
        self._last_rename_destination = destination

        def worker(progress, should_cancel):
            return core.apply_rename_plan(
                plan, mode=mode, destination=destination, overwrite=False,
                progress=progress, should_cancel=should_cancel,
            )

        self.run_job(f"{action} {plan.ready_count:,} files...", worker, self._on_rename_done)

    def _on_rename_done(self, result: core.OperationResult) -> None:
        self._after_change(result)
        if result.ok:
            destination = self._last_rename_destination
            where = f"  ·  {destination}" if destination else ""
            self.toast(f"{result.summary()}{where}", "ok")

    # ---------------------------------------------------------------- find page
    def _search_params(self) -> tuple[str, str, bool, bool, bool] | None:
        find = self.find_var.get()
        if not find:
            self.toast("Type something to search for", "warn")
            self.find_entry.focus_set()
            return None
        replace = self.replace_var.get()
        return find, replace, self.case_var.get(), self.regex_var.get(), self.word_var.get()

    def _run_find(self, on_done) -> None:
        params = self._search_params()
        if params is None:
            return
        find, replace, case_sensitive, regex, whole_word = params
        try:
            core.compile_search(find, regex, case_sensitive, whole_word)
        except re.error as exc:
            messagebox.showerror("Invalid regular expression", str(exc))
            return
        if not self.files:
            self.toast("Choose an input folder first", "warn")
            return
        files = list(self.files)

        def worker(progress, should_cancel):
            return core.build_replace_plan(
                files, find, replace,
                case_sensitive=case_sensitive, regex=regex, whole_word=whole_word,
                progress=progress, should_cancel=should_cancel,
            )

        self.run_job("Searching files...", worker, on_done)

    def find_matches(self) -> None:
        self._run_find(self._on_find_done)

    def _on_find_done(self, plan: core.ReplacePlan) -> None:
        self._replace_plan = plan
        self._render_find_plan()
        if plan.total_matches:
            self.toast(
                f"{plan.total_matches:,} matches in {plan.matching_files:,} files", "ok"
            )
        else:
            self.toast("No matches found", "info")
            self.find_summary.configure(text="No matches", fg=MUTED)

    def _render_find_plan(self) -> None:
        plan = self._replace_plan
        rows: list[tuple[tuple, tuple]] = []
        self._find_rows: list[core.ReplaceItem] = []
        if plan is not None:
            for item in plan.items:
                if not item.matches:
                    continue
                preview = ""
                if item.previews:
                    preview = item.previews[0].after or "(deleted)"
                rows.append(
                    (
                        (
                            item.file.name,
                            str(item.file.path.parent),
                            f"{item.matches:,}",
                            preview[:110],
                        ),
                        (),
                    )
                )
                self._find_rows.append(item)
        self._render_rows(self.find_tree, rows)
        if plan is not None:
            skipped = sum(1 for item in plan.items if item.error)
            summary = f"{plan.total_matches:,} matches   ·   {plan.matching_files:,} files"
            if skipped:
                summary += f"   ·   {skipped:,} skipped"
            self.find_summary.configure(text=summary, fg=MUTED)
        else:
            self.find_summary.configure(text="", fg=MUTED)
        self._update_empty_states()
        self._update_counts()

    def _show_match_detail(self, _event=None) -> None:
        selection = self.find_tree.selection()
        if not selection:
            return
        index = self.find_tree.index(selection[0])
        if index >= len(getattr(self, "_find_rows", [])):
            return
        item = self._find_rows[index]
        widget = self.match_detail
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", f"{item.file.path}\n", ("muted",))
        widget.insert("end", f"{item.matches:,} match(es)\n\n", ("muted",))
        for preview in item.previews:
            widget.insert("end", f"line {preview.line_number}\n", ("muted",))
            widget.insert("end", f"  − {preview.before}\n", ("old",))
            widget.insert("end", f"  + {preview.after}\n\n", ("new",))
        widget.configure(state="disabled")

    def apply_replace(self) -> None:
        if self._busy:
            self.toast("A task is already running", "warn")
            return
        self._run_find(self._confirm_replace)

    def _confirm_replace(self, plan: core.ReplacePlan) -> None:
        self._on_find_done(plan)
        if not plan.total_matches:
            return
        backup = self.backup_var.get()
        message = (
            f"Replace {plan.total_matches:,} occurrence(s) "
            f"across {plan.matching_files:,} file(s)?"
        )
        if plan.replace:
            message += f"\n\nReplace with:\n{plan.replace}"
        else:
            message += "\n\nMatches will be deleted (no replacement text)."
        if backup:
            message += "\n\nOriginal files are backed up first (undo with Ctrl+Z)."
        else:
            message += "\n\nBackups are OFF - this cannot be undone."
        if not messagebox.askyesno("Replace text", message):
            return

        def worker(progress, should_cancel):
            return core.apply_replace_plan(
                plan, backup=backup, progress=progress, should_cancel=should_cancel
            )

        self.run_job("Replacing text...", worker, self._on_replace_done)

    def _on_replace_done(self, result: core.OperationResult) -> None:
        self._after_change(result)
        if result.ok:
            self.toast(f"{result.ok:,} file(s) updated  ·  {result.summary()}", "ok")
        self._replace_plan = None
        self._render_find_plan()

    # ------------------------------------------------------------------ zip page
    def _compression_key(self) -> str:
        return {
            "No compression (fastest)": "store",
            "Fast": "fast",
            "Balanced": "balanced",
            "Maximum (smallest)": "max",
        }.get(self.zip_level.get(), "balanced")

    def _target_zip_path(self) -> Path:
        name = self.zip_name.get().strip() or self._default_zip_name()
        if not name.casefold().endswith(".zip"):
            name += ".zip"
        return self.effective_output() / name

    def _update_zip_hint(self) -> None:
        target = self._target_zip_path()
        total = sum(info.size for info in self.files)
        hint = (
            f"→  {target}     ·     {len(self.files):,} files · {core.human_size(total)}"
        )
        try:
            if target.exists():
                hint += "     ·     existing archive will be replaced"
        except OSError:
            pass
        self.zip_hint.configure(text=hint)

    def _render_zip_list(self) -> None:
        rows = [
            (
                (info.name, str(info.path.parent), core.human_size(info.size), info.modified),
                (),
            )
            for info in self.files
        ]
        self._render_rows(self.zip_tree, rows)
        total = sum(info.size for info in self.files)
        self.zip_summary.configure(
            text=f"{len(self.files):,} files   ·   {core.human_size(total)} total",
            fg=MUTED,
        )
        self._update_zip_hint()
        self._update_empty_states()

    def _tree_path(self, tree: ttk.Treeview, iid: str) -> str:
        try:
            return str(Path(tree.set(iid, "location")) / tree.set(iid, "name"))
        except tk.TclError:
            return ""

    def _source_size(self, path: Path) -> str:
        try:
            return core.human_size(path.stat().st_size)
        except OSError:
            return "-"

    def _selected_files(self, tree: ttk.Treeview) -> list[Path]:
        paths: list[Path] = []
        for iid in tree.selection():
            path = Path(self._tree_path(tree, iid))
            if path.exists():
                paths.append(path)
        return paths

    def create_zip_clicked(self) -> None:
        if not self.input_paths:
            self.toast("Choose a folder to compress", "warn")
            return
        sources = [p for p in self.input_paths if p.exists()]
        if not sources:
            self.toast("The input folder no longer exists", "error")
            return
        self._start_zip(sources)

    def zip_selected_clicked(self) -> None:
        paths = self._selected_files(self.zip_tree)
        if not paths:
            self.toast("Select one or more files in the list first", "warn")
            return
        self._start_zip(paths)

    def _start_zip(self, sources: list[Path]) -> None:
        if self._busy:
            self.toast("A task is already running", "warn")
            return
        target = self._target_zip_path()
        overwrite = False
        if target.exists():
            if not messagebox.askyesno(
                "Archive already exists",
                f"{target.name} already exists in:\n{target.parent}\n\nReplace it?",
            ):
                return
            overwrite = True
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Output folder", str(exc))
            return
        self._last_zip_target = target
        level = self._compression_key()

        def worker(progress, should_cancel):
            return core.create_zip(
                sources, target, compression=level,
                progress=progress, should_cancel=should_cancel, overwrite=overwrite,
            )

        self.run_job(f"Creating {target.name}...", worker, self._on_zip_done)

    def _on_zip_done(self, result: core.OperationResult) -> None:
        self._after_change(result)
        target = getattr(self, "_last_zip_target", None)
        if result.ok and target is not None and target.exists():
            self.toast(
                f"Created {target.name}  ·  {core.human_size(target.stat().st_size)}  "
                f"from {result.ok:,} files",
                "ok",
            )

    # ---------------------------------------------------------------- unzip page
    def _zip_infos(self) -> list[core.FileInfo]:
        infos: list[core.FileInfo] = []
        seen: set[str] = set()
        for info in self.zips:
            key = str(info.path.resolve()).casefold()
            if key not in seen:
                seen.add(key)
                infos.append(info)
        for path in self.extra_zips:
            key = str(path.resolve()).casefold() if path.exists() else str(path).casefold()
            if key in seen or not path.exists():
                continue
            info = self._info_for(path)
            if info is not None:
                seen.add(key)
                infos.append(info)
        infos.sort(key=lambda info: core.natural_key(info.name))
        return infos

    def _render_unzip_list(self, keep_selection: bool = False) -> None:
        infos = self._zip_infos()
        self._zip_rows = infos
        rows = [
            ((info.name, str(info.path.parent), core.human_size(info.size), "", ""), ())
            for info in infos
        ]
        self._render_rows(self.unzip_tree, rows)
        total = sum(info.size for info in infos)
        self.unzip_summary.configure(
            text=f"{len(infos):,} archives   ·   {core.human_size(total)}", fg=MUTED
        )
        self._update_empty_states()

    def _preview_archive(self, _event=None) -> None:
        selection = self.unzip_tree.selection()
        if not selection:
            return
        index = self.unzip_tree.index(selection[0])
        infos = getattr(self, "_zip_rows", [])
        if index >= len(infos):
            return
        info = infos[index]
        summary = core.read_zip(info.path)
        rows: list[tuple[tuple, tuple]] = []
        limit = 4000
        for entry in summary.entries[:limit]:
            rows.append(
                (
                    (
                        entry.name,
                        "-" if entry.is_dir else core.human_size(entry.size),
                        entry.date,
                        "Folder" if entry.is_dir else ("Locked" if entry.encrypted else "File"),
                    ),
                    ("locked" if entry.encrypted else (),),
                )
            )
        self._render_rows(self.contents_tree, rows)
        if summary.error:
            self.contents_summary.configure(text=f"Cannot read archive: {summary.error}", fg=DANGER)
            return
        text = (
            f"{summary.file_count:,} files   ·   "
            f"{core.human_size(summary.total_size)} unpacked"
        )
        if summary.encrypted:
            text += "   ·   password protected"
        if len(summary.entries) > limit:
            text += f"   ·   showing first {limit:,}"
        self.contents_summary.configure(text=text, fg=MUTED)
        self.unzip_tree.set(selection[0], "files", f"{summary.file_count:,}")
        self.unzip_tree.set(selection[0], "contents", core.human_size(summary.total_size))

    def add_archives(self) -> None:
        initial = str(self.effective_output())
        chosen = filedialog.askopenfilenames(
            title="Add zip archives",
            initialdir=initial,
            filetypes=[("Zip archives", "*.zip"), ("All files", "*.*")],
        )
        for path in chosen:
            candidate = Path(path)
            if candidate not in self.extra_zips:
                self.extra_zips.append(candidate)
        if chosen:
            self.show_page("unzip")
            self._render_unzip_list()

    def extract_all(self) -> None:
        infos = self._zip_infos()
        if not infos:
            self.toast("No archives found - add one first", "warn")
            return
        self._extract([info.path for info in infos])

    def extract_selected(self) -> None:
        selection = self.unzip_tree.selection()
        infos = getattr(self, "_zip_rows", [])
        if not selection:
            self.toast("Select one or more archives first", "warn")
            return
        paths = [infos[self.unzip_tree.index(iid)].path for iid in selection]
        self._extract(paths)

    def _extract(self, paths: list[Path]) -> None:
        if self._busy:
            self.toast("A task is already running", "warn")
            return
        if not paths:
            return
        destination = self.ensure_output("Choose where to extract the archives")
        if destination is None:
            return
        message = f"Extract {len(paths):,} archive(s) into:\n{destination}"
        if self.unzip_subfolder.get():
            message += "\n\nEach archive gets its own subfolder."
        if self.unzip_overwrite.get():
            message += "\n\nExisting files with the same name will be overwritten."
        if self.unzip_password.get():
            message += "\n\nThe password you entered will be used."
        if not messagebox.askyesno("Extract archives", message):
            return

        subfolder = self.unzip_subfolder.get()
        overwrite = self.unzip_overwrite.get()
        skip_junk = self.unzip_skipjunk.get()
        password = self.unzip_password.get()

        def worker(progress, should_cancel):
            return core.extract_zips(
                paths, destination, subfolder=subfolder, overwrite=overwrite,
                skip_junk=skip_junk, password=password,
                progress=progress, should_cancel=should_cancel,
            )

        self.run_job(f"Extracting {len(paths):,} archive(s)...", worker, self._on_extract_done)

    def _on_extract_done(self, result: core.OperationResult) -> None:
        self._after_change(result)
        if result.ok:
            self.toast(
                f"Extracted {result.ok:,} files  ·  {core.human_size(result.bytes_written)}",
                "ok",
            )
        self._render_unzip_list(keep_selection=True)

    # ------------------------------------------------------------------ jobs
    def _progress_cb(self, done: int, total: int, message: str = "") -> None:
        self._jobs.put(("progress", (done, total, message)))

    def run_job(self, label: str, worker, on_done, cancellable: bool = True) -> None:
        if not self._alive:
            return
        if self._busy:
            self.toast("Please wait - another task is running", "warn")
            return
        self._busy = True
        self._cancel.clear()
        self.status_label.configure(text=label, fg=INK_SOFT)
        self.cancel_button.configure(state="normal")
        self.cancel_button.grid(row=0, column=2, padx=(0, 10), sticky="e")
        self.progress.configure(mode="indeterminate", value=0)
        self.progress.grid(row=0, column=3, sticky="e")
        self.progress.start(14)
        self._set_buttons_enabled(False)

        def target() -> None:
            try:
                result = worker(self._progress_cb, self._cancel.is_set)
            except core.CancelledError:
                self._jobs.put(("cancelled", None))
                return
            except Exception as exc:
                self._jobs.put(("error", (label, exc, traceback.format_exc())))
                return
            self._jobs.put(("done", (on_done, result)))

        self._worker = threading.Thread(target=target, name="gantz-job", daemon=True)
        self._worker.start()

    def _drain_jobs(self) -> None:
        if not self._alive:
            return
        try:
            while True:
                kind, payload = self._jobs.get_nowait()
                if kind == "progress":
                    self._apply_progress(*payload)
                elif kind == "done":
                    on_done, result = payload
                    self._finish_busy()
                    try:
                        on_done(result)
                    except Exception:
                        self.report_callback_exception(*sys.exc_info())
                elif kind == "cancelled":
                    self._finish_busy()
                    self.toast("Cancelled - nothing else was changed", "warn")
                elif kind == "error":
                    label, exc, tb = payload
                    self._finish_busy()
                    self._log_error(label, tb)
                    messagebox.showerror(
                        "Something went wrong",
                        f"{label}\n\n{exc}\n\nDetails were written to the Gantz data folder.",
                    )
                    self.toast(f"{label.rstrip('.')} failed", "error")
        except queue.Empty:
            pass
        self.after(50, self._drain_jobs)

    def _apply_progress(self, done: int, total: int, message: str = "") -> None:
        if total and total > 0:
            if str(self.progress["mode"]) != "determinate":
                self.progress.stop()
                self.progress.configure(mode="determinate", maximum=total)
            self.progress["value"] = min(done, total)
        else:
            if str(self.progress["mode"]) != "indeterminate":
                self.progress.configure(mode="indeterminate", value=0)
                self.progress.start(14)
        if message:
            self.status_label.configure(text=message)

    def _finish_busy(self) -> None:
        self._busy = False
        try:
            self.progress.stop()
        except tk.TclError:
            pass
        self.progress.configure(mode="determinate", value=0)
        self.progress.grid_remove()
        self.cancel_button.grid_remove()
        self.cancel_button.configure(state="normal")
        self._set_buttons_enabled(True)

    def cancel_job(self) -> None:
        if self._busy:
            self._cancel.set()
            self.cancel_button.configure(state="disabled")
            self.status_label.configure(text="Cancelling...", fg=WARN)

    def _iter_flat_buttons(self, widget):
        for child in widget.winfo_children():
            if isinstance(child, FlatButton):
                yield child
            yield from self._iter_flat_buttons(child)

    def _set_buttons_enabled(self, enabled: bool) -> None:
        for button in self._iter_flat_buttons(self):
            if button is self.cancel_button or button is self.undo_button:
                continue
            if enabled:
                button.configure(state="normal", bg=button._bg, fg=button._fg)
            else:
                button.configure(state="disabled", bg=button._bg, fg="#9ca3af")

    # ------------------------------------------------------------------ misc UI
    def toast(self, message: str, kind: str = "info") -> None:
        colours = {"info": MUTED, "ok": OK, "warn": WARN, "error": DANGER}
        if not self._alive:
            return
        self.status_label.configure(text=message, fg=colours.get(kind, MUTED))

    def open_output(self) -> None:
        target = self.output_dir or self.effective_output()
        if target is None:
            self.toast("Choose an output folder first", "warn")
            return
        if target.exists():
            open_path(target)
        else:
            self.toast(f"Folder does not exist yet: {target}", "warn")

    def refresh_undo_hint(self) -> None:
        manifests = core.list_manifests()
        if not manifests:
            self.undo_hint.configure(text="Nothing to undo yet")
            self.undo_button.configure(state="disabled", fg="#64748b")
            return
        last = manifests[0]
        label = last["label"]
        if len(label) > 38:
            label = label[:37] + "…"
        self.undo_hint.configure(text=f"Last: {label}\n{last['created_text']}")
        self.undo_button.configure(state="normal", fg="#e2e8f0")

    def undo_last(self) -> None:
        if self._busy:
            self.toast("Wait for the current task to finish", "warn")
            return
        manifests = core.list_manifests()
        if not manifests:
            self.toast("There is nothing to undo", "info")
            return
        last = manifests[0]
        message = (
            f"Undo \"{last['label']}\" from {last['created_text']}?\n\n"
            f"{last['count']:,} change(s) will be reversed."
        )
        if not messagebox.askyesno("Undo last change", message):
            return

        def worker(progress, should_cancel):
            return core.undo_last(progress=progress, should_cancel=should_cancel)

        self.run_job("Undoing last change...", worker, self._on_undo_done)

    def _on_undo_done(self, result: core.OperationResult | None) -> None:
        self.refresh_undo_hint()
        self.schedule_scan(60)
        if result is None:
            self.toast("There was nothing to undo", "info")
            return
        if result.failed:
            self._show_failures(result)
        self.toast(f"Undo complete  ·  {result.summary()}", "ok" if not result.failed else "warn")

    def clear_undo_history(self) -> None:
        if not core.list_manifests():
            self.toast("Undo history is already empty", "info")
            return
        if not messagebox.askyesno(
            "Clear undo history",
            "Delete the saved undo history and all replace backups?\n\n"
            "This cannot be undone.",
        ):
            return
        removed = core.clear_undo_history()
        self.refresh_undo_hint()
        self.toast(f"Cleared {removed:,} history entr(y/ies)", "info")

    def _show_failures(self, result: core.OperationResult) -> None:
        shown = result.errors[:10]
        extra = len(result.errors) - len(shown)
        details = "\n".join(f"• {line}" for line in shown)
        if extra > 0:
            details += f"\n…and {extra:,} more"
        messagebox.showwarning(
            result.label or "Operation finished with errors",
            f"{result.failed:,} item(s) could not be processed.\n\n{details}",
        )

    def _after_change(self, result: core.OperationResult) -> None:
        self.refresh_undo_hint()
        if result.failed:
            self._show_failures(result)
        self._files_dirty = True
        self.schedule_scan(80)

    def _log_error(self, label: str, tb: str) -> None:
        try:
            log = core.data_dir() / "error.log"
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with log.open("a", encoding="utf-8") as handle:
                handle.write(f"\n[{stamp}] {label}\n{tb}\n")
        except OSError:
            pass

    def report_callback_exception(self, exc_type, exc_value, exc_traceback) -> None:
        tb = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        self._log_error("UI callback", tb)
        try:
            messagebox.showerror(
                "Unexpected error",
                f"{exc_value}\n\nThe details were written to:\n"
                f"{core.data_dir() / 'error.log'}",
            )
        except Exception:
            pass

    # ------------------------------------------------------------------ menu/keys
    def _build_menu(self) -> None:
        menubar = tk.Menu(self, tearoff=0)
        file_menu = tk.Menu(menubar, tearoff=0, font=(UI_FONT, 9))
        file_menu.add_command(label="Choose input folder…", accelerator="Ctrl+O",
                              command=self.browse_input)
        file_menu.add_command(label="Add files…", accelerator="Ctrl+Shift+A",
                              command=self.add_files)
        file_menu.add_command(label="Choose output folder…", accelerator="Ctrl+Shift+O",
                              command=self.browse_output)
        file_menu.add_command(label="Open output folder", command=self.open_output)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=0, font=(UI_FONT, 9))
        edit_menu.add_command(label="Undo last change", accelerator="Ctrl+Z",
                              command=self.undo_last)
        edit_menu.add_command(label="Clear undo history", command=self.clear_undo_history)
        edit_menu.add_separator()
        edit_menu.add_command(label="Refresh file list", accelerator="F5",
                              command=self.scan_now)
        menubar.add_cascade(label="Edit", menu=edit_menu)

        help_menu = tk.Menu(menubar, tearoff=0, font=(UI_FONT, 9))
        help_menu.add_command(label="About Gantz", command=self._about)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.configure(menu=menubar)

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-o>", self._shortcut(self.browse_input))
        self.bind("<Control-O>", self._shortcut(self.browse_input))
        self.bind("<Control-Shift-a>", self._shortcut(self.add_files))
        self.bind("<Control-Shift-A>", self._shortcut(self.add_files))
        self.bind("<Control-Shift-o>", self._shortcut(self.browse_output))
        self.bind("<Control-Shift-O>", self._shortcut(self.browse_output))
        self.bind("<Control-z>", self._shortcut(self.undo_last))
        self.bind("<Control-Z>", self._shortcut(self.undo_last))
        self.bind("<F5>", self._shortcut(self.scan_now))
        self.bind("<Control-f>", self._shortcut(self._focus_find))
        self.bind("<Control-F>", self._shortcut(self._focus_find))
        self.bind("<Control-Return>", self._shortcut(self._primary_action))

    @staticmethod
    def _shortcut(callback):
        def handler(_event=None):
            callback()
            return "break"

        return handler

    def _focus_find(self) -> None:
        self.show_page("find")
        self.find_entry.focus_set()
        self.find_entry.selection_range(0, "end")

    def _primary_action(self) -> None:
        {
            "rename": self.apply_rename,
            "find": self.apply_replace,
            "zip": self.create_zip_clicked,
            "unzip": self.extract_all,
        }.get(self._page, lambda: None)()

    def _about(self) -> None:
        messagebox.showinfo(
            "About Gantz",
            f"Gantz {core.APP_VERSION}\n{core.APP_TAGLINE}\n\n"
            "Rename, find & replace, zip and unzip - all on this computer.\n"
            "No internet connection is used, ever.\n\n"
            f"Undo history: {core.data_dir()}",
        )

    # ------------------------------------------------------------------ lifecycle
    def _save_state(self) -> None:
        try:
            data = {
                "input": str(self.input_paths[0]) if self.input_paths else "",
                "output": str(self.output_dir) if self.output_dir else "",
                "recursive": self.recursive_var.get(),
                "extensions": self.ext_var.get(),
                "page": self._page,
                "geometry": self.geometry(),
                "rename_prefix": self.rename_prefix.get(),
                "rename_numbering": self._numbering_key(),
                "rename_start": self._int_value(self.rename_start, 1),
                "rename_padding": self._int_value(self.rename_padding, 0),
                "rename_separator": self.rename_separator.get(),
                "find": self.find_var.get(),
                "replace": self.replace_var.get(),
                "case_sensitive": self.case_var.get(),
                "regex": self.regex_var.get(),
                "whole_word": self.word_var.get(),
            }
            save_settings(data)
        except (tk.TclError, OSError):
            pass

    def _on_close(self) -> None:
        if self._busy:
            if not messagebox.askyesno(
                "Quit Gantz",
                "A task is still running. Quitting now may leave that task unfinished.\n\n"
                "Quit anyway?",
            ):
                return
            self._cancel.set()
        self._save_state()
        self._alive = False
        self.destroy()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--version" in args or "-V" in args:
        print(f"Gantz {core.APP_VERSION}")
        return 0
    if "--help" in args or "-h" in args:
        print(
            "Gantz - offline file toolkit\n\n"
            "Usage: Gantz.exe [FOLDER | FILE.zip ...]\n\n"
            "  FOLDER      open with this input folder\n"
            "  FILE.zip    open the Unzip page with this archive\n"
            "  --smoke-test  start the GUI, verify it builds, then exit\n"
            "  --version     print the version and exit"
        )
        return 0

    enable_windows_dpi_awareness()
    paths = [Path(a) for a in args if not a.startswith("-")]
    app = GantzApp([p for p in paths if p.exists()])

    if "--smoke-test" in args:
        app.update_idletasks()
        app.update()
        app.destroy()
        print("GUI smoke test passed")
        return 0

    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
