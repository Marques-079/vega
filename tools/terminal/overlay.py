"""open_overlay - floating cards in VEGA's own style: text, or an image with caption.

Each overlay is its own little process running this same file as a script. That is
what makes closing one safe - destroying a Toplevel inside VEGA's Tk made macOS
reshuffle the whole app's windows and minimise it - and it is what makes many-at-once
trivial: the parent just holds Popen handles in OPEN, prunes the ones whose × was
clicked, and close_overlay terminates by id. The palette rides in the payload, so
the child needs nothing but tkinter (and AppKit to float above fullscreen apps).
"""
import atexit, itertools, json, os, subprocess, sys, time

POSITIONS = ("top-left", "top-center", "top-right", "center",
             "bottom-left", "bottom-right")

SPEC = {"type": "function", "function": {
    "name": "open_overlay",
    "description": "Open a small floating overlay window showing text - and optionally "
                   "an image file - that stays on top of everything. Every call opens "
                   "a NEW overlay; the ones on screen are listed in your system prompt "
                   "and close_overlay removes them. Instant.",
    "parameters": {"type": "object", "properties": {
        "text": {"type": "string", "description": "what the overlay should display"},
        "image": {"type": "string", "description":
                  "optional absolute path to a PNG or GIF to show above the text; "
                  "only paths you were given, never invented ones"},
        "position": {"type": "string", "enum": list(POSITIONS),
                     "description": "where on screen; default top-center"}},
        "required": ["text"]}}}

OPEN = {}               # id -> {"proc", "desc", "pos", "since"}; VEGA's open overlays
_ids = itertools.count(1)
# Fallback palette for headless callers; live calls carry ui.pal instead.
_PAL = dict(bg="#0a0a0a", line="#1c1c1c", fg="#e6e6e6", dim="#6b6b6b", accent="#ff7a1a")
atexit.register(lambda: [o["proc"].terminate() for o in OPEN.values()])


def _prune():
    """Drop overlays whose process is gone - the user clicked their ×."""
    for i in [i for i, o in OPEN.items() if o["proc"].poll() is not None]:
        OPEN.pop(i)


def run(args, ui, job):
    _prune()
    text, image = args.get("text", ""), args.get("image")
    pos = args.get("position") if args.get("position") in POSITIONS else "top-center"
    payload = {"text": text, "image": image, "pos": pos, "nth": len(OPEN),
               "pal": dict(ui.pal) if ui else _PAL}
    p = subprocess.Popen([sys.executable, __file__, json.dumps(payload)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    oid = next(_ids)
    OPEN[oid] = {"proc": p, "pos": pos, "since": time.time(),
                 "desc": f"image {os.path.basename(image)}" if image else text[:80]}
    return f"Overlay {oid} is up, sir."


def status():
    """The open overlays rendered for the system prompt; empty string when none."""
    _prune()
    if not OPEN:
        return ""
    lines = [f"  [{i}] at {o['pos']} - {o['desc']}" for i, o in OPEN.items()]
    return ("\nOverlays on screen right now (close one or all with close_overlay):\n"
            + "\n".join(lines))


# ---------------------------------------------------------------- child process
def _child(payload):
    import contextlib, tkinter as tk
    p = payload["pal"]
    root = tk.Tk()
    root.overrideredirect(True)         # no chrome, and it can never take focus
    root.wm_attributes("-topmost", True)
    root.config(bg=p["line"])
    body = tk.Frame(root, bg=p["bg"])   # 1px border is the gap showing through
    body.pack(fill="both", expand=True, padx=1, pady=1)

    bar = tk.Frame(body, bg=p["bg"])
    bar.pack(fill="x")
    title = tk.Label(bar, text="VEGA", font=("SF Mono", 9, "bold"),
                     bg=p["bg"], fg=p["accent"])
    title.pack(side="left", padx=10, pady=4)
    x = tk.Label(bar, text="×", font=("SF Mono", 12), bg=p["bg"], fg=p["dim"],
                 cursor="hand2")
    x.pack(side="right", padx=8)
    x.bind("<Button-1>", lambda e: root.destroy())

    drag, wrap = [bar, title, body], 300
    image = payload.get("image")
    if image and os.path.exists(image):
        with contextlib.suppress(tk.TclError):  # not a format Tk reads: text only
            img = tk.PhotoImage(file=image)
            # Integer shrink only, but qlmanage renders at 640 so it lands near that.
            shrink = max(1, (img.width() + 679) // 680, (img.height() + 519) // 520)
            if shrink > 1:
                img = img.subsample(shrink)
            root.img = img                      # Tk blanks the label if this ref dies
            pic = tk.Label(body, image=img, bg=p["bg"], bd=0)
            pic.pack(padx=12, pady=(2, 0))
            drag.append(pic)
            wrap = max(wrap, img.width())
    if payload.get("text"):
        lbl = tk.Label(body, text=payload["text"], font=("SF Pro Text", 12),
                       bg=p["bg"], fg=p["fg"], wraplength=wrap, justify="left")
        lbl.pack(padx=12, pady=(2, 12), anchor="w")
        drag.append(lbl)
    else:
        tk.Frame(body, height=10, bg=p["bg"]).pack()    # image-only bottom margin

    off = {}
    def press(e):
        off["xy"] = (e.x_root - root.winfo_x(), e.y_root - root.winfo_y())
    def move(e):
        root.geometry("+%d+%d" % (e.x_root - off["xy"][0], e.y_root - off["xy"][1]))
    for w in drag:
        w.bind("<ButtonPress-1>", press)
        w.bind("<B1-Motion>", move)

    # Anchor to the asked-for corner, cascading later overlays toward the middle
    # so several at the same anchor stay visible.
    root.update_idletasks()
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    vert, _, horz = payload["pos"].partition("-")
    horz = horz or "center"
    n = payload.get("nth", 0) % 8 * 34
    xpos = {"left": 16 + n, "center": (sw - w) // 2 + n, "right": sw - w - 16 - n}[horz]
    ypos = {"top": 40 + n, "center": (sh - h) // 2 + n, "bottom": sh - h - 16 - n}[vert]
    root.geometry(f"+{xpos}+{ypos}")

    def lift():
        """This process's own window flags: accessory (no Dock icon), above
        fullscreen Spaces. Same trick as ui._float, one process over."""
        try:
            from AppKit import NSApp, NSApplicationActivationPolicyAccessory
        except ImportError:
            return                      # -topmost alone still floats over normal apps
        root.update_idletasks()
        app = NSApp()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        for win in app.windows():
            if type(win).__name__ == "TKWindow":
                win.setLevel_(1000)                     # NSScreenSaverWindowLevel
                win.setCollectionBehavior_(1 | 1 << 4 | 1 << 6 | 1 << 8)
                # CanJoinAllSpaces | Stationary | IgnoresCycle | FullScreenAuxiliary
    root.after(0, lift)                 # the NSWindow exists once mainloop runs
    root.mainloop()


if __name__ == "__main__":
    _child(json.loads(sys.argv[1]))
