"""
Shared ttk theme configuration for ByteBack.

Applies a modern zinc-inspired palette matching the DevServer Commander look.
"""

import tkinter as tk
from tkinter import ttk
from typing import Dict


def configure_ui_style(root: tk.Tk) -> Dict[str, str]:
    """
    Apply a modern ttk theme with subtle spacing improvements.

    Args:
        root: Root Tk window to style.

    Returns:
        Color token dictionary for styling non-ttk widgets (e.g. Listbox).
    """
    style = ttk.Style(root)
    available_themes = set(style.theme_names())
    if "clam" in available_themes:
        style.theme_use("clam")

    colors = {
        "bg": "#f4f4f5",
        "panel_bg": "#ffffff",
        "fg": "#18181b",
        "muted_fg": "#71717a",
        "border": "#e4e4e7",
        "accent": "#3f3f46",
        "accent_hover": "#27272a",
        "accent_pressed": "#18181b",
        "selection": "#e4e4e7",
        "focus": "#a1a1aa",
        "success": "#22c55e",
        "success_hover": "#16a34a",
        "on_accent": "#fafafa",
    }

    bg = colors["bg"]
    panel_bg = colors["panel_bg"]
    fg = colors["fg"]
    muted_fg = colors["muted_fg"]
    border = colors["border"]
    accent = colors["accent"]
    accent_hover = colors["accent_hover"]
    accent_pressed = colors["accent_pressed"]
    selection = colors["selection"]
    focus = colors["focus"]
    success = colors["success"]
    success_hover = colors["success_hover"]

    root.configure(background=bg)
    root.option_add("*Background", bg)
    root.option_add("*Foreground", fg)
    root.option_add("*Font", "TkDefaultFont 10")
    root.option_add("*Menu.Background", panel_bg)
    root.option_add("*Menu.Foreground", fg)
    root.option_add("*Menu.ActiveBackground", accent)
    root.option_add("*Menu.ActiveForeground", colors["on_accent"])

    style.configure(".", background=bg, foreground=fg)
    style.configure("TFrame", background=bg)
    style.configure("TPanedwindow", background=bg)
    style.configure("TLabelframe", background=bg, bordercolor=border, relief="flat")
    style.configure("TLabelframe.Label", background=bg, foreground=muted_fg)
    style.configure("TLabel", background=bg, foreground=fg)
    style.configure("Muted.TLabel", background=bg, foreground=muted_fg)
    style.configure(
        "TButton",
        padding=(12, 7),
        background=panel_bg,
        foreground=fg,
        borderwidth=1,
        bordercolor=border,
        focusthickness=1,
        focuscolor=focus,
        relief="flat",
    )
    style.map(
        "TButton",
        background=[("active", colors["on_accent"]), ("pressed", bg), ("disabled", bg)],
        foreground=[("disabled", focus)],
        bordercolor=[("active", "#d4d4d8"), ("disabled", border)],
    )
    style.configure(
        "Primary.TButton",
        padding=(12, 7),
        background=accent,
        foreground=colors["on_accent"],
        borderwidth=0,
        focusthickness=0,
        relief="flat",
    )
    style.map(
        "Primary.TButton",
        background=[
            ("active", accent_hover),
            ("pressed", accent_pressed),
            ("disabled", focus),
        ],
        foreground=[("disabled", bg)],
    )
    style.configure(
        "Success.TButton",
        padding=(12, 7),
        background=success,
        foreground="#ffffff",
        borderwidth=0,
        focusthickness=0,
        relief="flat",
    )
    style.map(
        "Success.TButton",
        background=[
            ("active", success_hover),
            ("pressed", "#15803d"),
            ("disabled", focus),
        ],
        foreground=[("disabled", bg)],
    )
    style.configure(
        "TEntry",
        padding=(8, 6),
        fieldbackground=panel_bg,
        foreground=fg,
        bordercolor=border,
        lightcolor=border,
        darkcolor=border,
        relief="flat",
    )
    style.map(
        "TEntry",
        bordercolor=[("focus", focus)],
        lightcolor=[("focus", focus)],
        darkcolor=[("focus", focus)],
    )
    style.configure(
        "TCheckbutton",
        background=bg,
        foreground=fg,
        focuscolor=focus,
    )
    style.map(
        "TCheckbutton",
        background=[("active", bg)],
        foreground=[("disabled", muted_fg)],
    )
    style.configure(
        "Horizontal.TProgressbar",
        troughcolor=border,
        background=success,
        bordercolor=border,
        lightcolor=success,
        darkcolor=success,
        thickness=8,
    )
    style.configure(
        "Treeview",
        rowheight=26,
        background=panel_bg,
        fieldbackground=panel_bg,
        foreground=fg,
        bordercolor=border,
        lightcolor=border,
        darkcolor=border,
    )
    style.map(
        "Treeview",
        background=[("selected", selection)],
        foreground=[("selected", fg)],
    )
    style.configure(
        "Treeview.Heading",
        padding=(10, 7),
        background=bg,
        foreground=muted_fg,
        bordercolor=border,
        relief="flat",
    )
    style.map(
        "Treeview.Heading",
        background=[("active", colors["on_accent"])],
        foreground=[("active", fg)],
    )
    style.configure(
        "Vertical.TScrollbar",
        background=bg,
        troughcolor=bg,
        bordercolor=border,
        arrowcolor=muted_fg,
    )
    style.configure(
        "Horizontal.TScrollbar",
        background=bg,
        troughcolor=bg,
        bordercolor=border,
        arrowcolor=muted_fg,
    )
    style.configure("TSeparator", background=border)

    return colors


def style_listbox(listbox: tk.Listbox, colors: Dict[str, str]) -> None:
    """
    Apply theme colors to a tk.Listbox widget.

    Args:
        listbox: Listbox instance to style.
        colors: Color tokens returned by configure_ui_style().
    """
    listbox.configure(
        background=colors["panel_bg"],
        foreground=colors["fg"],
        selectbackground=colors["selection"],
        selectforeground=colors["fg"],
        highlightbackground=colors["border"],
        highlightcolor=colors["focus"],
        highlightthickness=1,
        relief="flat",
        borderwidth=1,
        activestyle="none",
        font="TkDefaultFont 10",
    )
