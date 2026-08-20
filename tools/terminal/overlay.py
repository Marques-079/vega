"""open_overlay - a floating text card in VEGA's own style.

One overlay, singleton: calling the tool again swaps the text. Draggable anywhere
on its face, always on top, and - because overrideredirect strips the titled style
mask - it can never become the key window, so it steals focus from nothing.
"""
import tkinter as tk

SPEC = {"type": "function", "function": {
    "name": "open_overlay",
    "description": "Show text in a small floating overlay window that stays on top of "
                   "everything - notes, reminders, anything the user should keep seeing. "
                   "Calling it again replaces the text. Instant.",
    "parameters": {"type": "object", "properties": {
        "text": {"type": "string", "description": "what the overlay should display"}},
        "required": ["text"]}}}

_win = {}   # top, lbl of the live overlay, if any


def run(args, ui):
    if ui is None:
        return "no display to draw on"
    ui.call(lambda: _show(ui, args.get("text", "")))
    return "the overlay is up"


def _show(ui, text):
    """UI thread only. Build once, then re-calls just swap the text."""
    if _win.get("top") and _win["top"].winfo_exists():
        _win["lbl"].config(text=text)
        _win["top"].lift()
        return
    p = ui.pal
    top = tk.Toplevel(ui, bg=p["line"])
    top.overrideredirect(True)
    top.wm_attributes("-topmost", True)
    body = tk.Frame(top, bg=p["bg"])            # 1px border is the gap showing through
    body.pack(fill="both", expand=True, padx=1, pady=1)

    bar = tk.Frame(body, bg=p["bg"])
    bar.pack(fill="x")
    title = tk.Label(bar, text="VEGA", font=("SF Mono", 9, "bold"),
                     bg=p["bg"], fg=p["accent"])
    title.pack(side="left", padx=10, pady=4)
    x = tk.Label(bar, text="×", font=("SF Mono", 12), bg=p["bg"], fg=p["dim"],
                 cursor="hand2")
    x.pack(side="right", padx=8)
    x.bind("<Button-1>", lambda e: top.destroy())

    lbl = tk.Label(body, text=text, font=("SF Pro Text", 12), bg=p["bg"], fg=p["fg"],
                   wraplength=300, justify="left")
    lbl.pack(padx=12, pady=(2, 12))

    off = {}
    def press(e):
        off["xy"] = (e.x_root - top.winfo_x(), e.y_root - top.winfo_y())
    def move(e):
        top.geometry("+%d+%d" % (e.x_root - off["xy"][0], e.y_root - off["xy"][1]))
    for w in (bar, title, body, lbl):
        w.bind("<ButtonPress-1>", press)
        w.bind("<B1-Motion>", move)

    top.geometry("+%d+%d" % (ui.winfo_screenwidth() // 2 - 160, 60))
    top.update_idletasks()
    ui._float()     # same NSWindow level as VEGA itself, so fullscreen apps don't cover it
    _win.update(top=top, lbl=lbl)
