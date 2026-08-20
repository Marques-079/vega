"""VEGA's face. One always-on-top window that is either a VEGA tag or a chat panel.

Runs in the same process as the brain, so it calls its functions directly - no IPC,
no socket, no second runtime. Worker threads reach the UI only through say()/call(),
which are queue-backed because Tk isn't thread safe.
"""
import queue, tkinter as tk
from tkinter import font as tkfont

PANEL, MARGIN, TOP = (330, 280), 16, 40
DEV_PANEL = (560, 620)      # -dev: room for the wire feed under the conversation
TAG_FONT, PAD = ("SF Mono", 9, "bold"), 6   # macOS rounds the window corners and clips
MAX_INPUT = 3               # whatever they cover, so the pill keeps a clear margin

DARK = dict(bg="#0a0a0a", line="#1c1c1c", fg="#e6e6e6", dim="#6b6b6b",
            accent="#ff7a1a", ember="#7a3b0d", field="#141414", focus="#e8e8e8")
LIGHT = dict(bg="#fbfaf8", line="#e4e0da", fg="#141414", dim="#8b857d",
             accent="#c2560a", ember="#e6c4a0", field="#efece7", focus="#141414")


def _round_rect(c, x1, y1, x2, y2, r, **kw):
    return c.create_polygon(
        [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
         x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1],
        smooth=True, **kw)


def _rows(w):
    """Wrapped display lines in a Text - not the same as newline count."""
    n = w.count("1.0", "end-1c", "displaylines")
    return max(1, (n[0] if isinstance(n, tuple) else n) or 1)


class _Switch:
    """Canvas toggle. There are two of them, which is what earns it a class."""

    def __init__(self, parent, ui, w, h, cmd):
        self.ui, self.w, self.h = ui, w, h
        self.pad = 3 if h < 14 else 4
        self.c = tk.Canvas(parent, width=w, height=h, highlightthickness=0, cursor="hand2")
        self.track = _round_rect(self.c, 1, 1, w - 1, h - 1, (h - 2) / 2, outline="")
        d = h - 2 * self.pad
        self.knob = self.c.create_oval(self.pad, self.pad, self.pad + d, self.pad + d,
                                       outline="")
        self.c.bind("<Button-1>", lambda e: cmd())

    def paint(self, on):
        p, d = self.ui.pal, self.h - 2 * self.pad
        self.c.config(bg=p["bg"])
        self.c.itemconfig(self.track, fill=p["accent"] if on else p["field"],
                          outline=p["accent"] if on else p["line"])
        x = self.w - self.pad - d if on else self.pad
        self.c.coords(self.knob, x, self.pad, x + d, self.pad + d)
        self.c.itemconfig(self.knob, fill=p["bg"] if on else p["dim"])

    def pack(self, **kw):
        self.c.pack(**kw)
        return self


class Vega(tk.Tk):
    def __init__(self, on_text=lambda s: None, on_talk=lambda on: None, dev=False):
        super().__init__()
        self.on_text, self.on_talk, self.dev_on = on_text, on_talk, dev
        self.inbox, self.talking, self.mode = queue.Queue(), False, "tag"
        self._styled, self._size, self._lit = False, DEV_PANEL if dev else PANEL, False
        self.light, self.pal, self._skinned = False, DARK, []

        # No overrideredirect: it strips NSWindowStyleMaskTitled, and a window without
        # that bit can never become key, so the input would never receive a keystroke.
        # The chrome comes off in _float() instead, from the AppKit side.
        self.wm_attributes("-topmost", True)
        try:
            # macOS only: lets the tag sit on the desktop without a box behind it.
            self.wm_attributes("-transparent", True)
            self.config(bg="systemTransparent")
            self._clear = "systemTransparent"
        except tk.TclError:
            self._clear = self.pal["bg"]
            self.config(bg=self._clear)

        self._build_tag()
        self._build_panel()
        self.collapse()
        self._home = "%dx%d+%d+%d" % (*self._tag,
                                      self.winfo_screenwidth() - self._tag[0] - MARGIN, TOP)
        self.geometry(self._home)
        self.bind("<Configure>", self._remember)
        self.after(50, self._drain)
        # Both deferred into mainloop: before that the NSWindow doesn't exist, and Tk
        # adds a titlebar offset to the first geometry call that a re-apply cancels.
        self.after(0, lambda: (self._float(), self.geometry(self._home)))

    # ---------- theming ----------
    def _skin(self, w, **opts):
        """Register Tk options that follow the palette, keyed by palette entry."""
        self._skinned.append((w, opts))
        w.config(**{k: self.pal[v] for k, v in opts.items()})
        return w

    def theme(self, light=None):
        self.light = (not self.light) if light is None else light
        self.pal = LIGHT if self.light else DARK
        p = self.pal
        for w, opts in self._skinned:
            w.config(**{k: p[v] for k, v in opts.items()})
        self.log.tag_config("you", foreground=p["dim"])
        self.log.tag_config("vega", foreground=p["fg"])
        self.theme_sw.paint(self.light)
        self.voice_sw.paint(self.talking)
        self.voice_lbl.config(fg=p["accent"] if self.talking else p["dim"])
        self._paint_box()
        self._paint_tag()

    # ---------- chrome off, float above everything including fullscreen ----------
    def _float(self):
        """Strip the titlebar and raise the window above fullscreen Spaces.

        Tk's -topmost only reaches level 19, which loses to a fullscreen app. The window
        is a real NSWindow in this process, so we set the level ourselves. Needs no
        entitlement and no admin - it is all just window flags.
        """
        try:
            from AppKit import NSApp, NSApplicationActivationPolicyAccessory
        except ImportError:
            return                      # pip install pyobjc-framework-Cocoa
        self.update_idletasks()
        app = NSApp()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # no Dock icon
        for w in app.windows():
            if type(w).__name__ != "TKWindow":
                continue                # Tk keeps a private TUINSWindow around; leave it
            if not self._styled:
                w.setStyleMask_(w.styleMask() | 1 << 15)   # FullSizeContentView
                w.setTitlebarAppearsTransparent_(True)
                w.setTitleVisibility_(1)                   # NSWindowTitleHidden
                for b in (0, 1, 2):                        # close / minimise / zoom
                    if w.standardWindowButton_(b):
                        w.standardWindowButton_(b).setHidden_(True)
                w.setHasShadow_(False)  # a shadow would trace the frame, not the tag
                self._styled = True
            # Re-asserted on every call: Tk resets the level when it remaps the window.
            w.setLevel_(1000)                        # NSScreenSaverWindowLevel
            w.setCollectionBehavior_(1 | 1 << 4 | 1 << 6 | 1 << 8)
            # CanJoinAllSpaces | Stationary | IgnoresCycle | FullScreenAuxiliary

    def _focus(self):
        """Accessory apps do not take focus from being clicked into alone."""
        try:
            from AppKit import NSApp
            NSApp().activateIgnoringOtherApps_(True)
        except ImportError:
            pass
        self.focus_force()

    # ---------- minimised tag ----------
    def _build_tag(self):
        # Sized from the text rather than fixed, so the pill still fits if macOS
        # substitutes a wider face for SF Mono.
        f = tkfont.Font(font=TAG_FONT)
        pw, ph = f.measure("VEGA") + 12, f.metrics("linespace") + 5
        self._tag = (pw + 2 * PAD, ph + 2 * PAD)
        self.tag = tk.Canvas(self, width=self._tag[0], height=self._tag[1],
                             bg=self._clear, highlightthickness=0)
        self.plate = _round_rect(self.tag, PAD, PAD, PAD + pw, PAD + ph, ph / 2)
        self.word = self.tag.create_text(self._tag[0] / 2, self._tag[1] / 2, text="VEGA",
                                         font=TAG_FONT)
        self._draggable(self.tag, on_click=self.expand)

    def _paint_tag(self, unread=False):
        p, live = self.pal, self.talking
        self.tag.itemconfig(self.plate, fill=p["accent"] if live else p["bg"],
                            outline=p["accent"] if (live or unread) else p["ember"])
        self.tag.itemconfig(self.word, fill=p["bg"] if live else p["accent"])

    # ---------- panel ----------
    def _build_panel(self):
        self.panel = self._skin(tk.Frame(self), bg="bg")

        # top bar: drag to move, click to minimise
        bar = self._skin(tk.Frame(self.panel, height=26), bg="bg")
        bar.pack(fill="x")
        self.title_lbl = self._skin(tk.Label(bar, text="VEGA", font=("SF Mono", 10, "bold"),
                                             cursor="hand2"), bg="bg", fg="accent")
        self.title_lbl.pack(side="right", padx=12)
        close = self._skin(tk.Label(bar, text="×", font=("SF Mono", 13), cursor="hand2"),
                           bg="bg", fg="dim")
        close.pack(side="left", padx=(10, 0))
        close.bind("<Button-1>", lambda e: self.destroy())
        close.bind("<Enter>", lambda e: close.config(fg=self.pal["accent"]))
        close.bind("<Leave>", lambda e: close.config(fg=self.pal["dim"]))
        for w in (bar, self.title_lbl):
            self._draggable(w, on_click=self.collapse)
        self._skin(tk.Frame(self.panel, height=1), bg="line").pack(fill="x")

        # voice switch, directly under the top bar
        row = self._skin(tk.Frame(self.panel), bg="bg")
        row.pack(fill="x", pady=7, padx=12)
        self.voice_sw = _Switch(row, self, 30, 16, self.toggle_talk).pack(side="right")
        self.voice_lbl = self._skin(tk.Label(row, text="VOICE", font=("SF Mono", 9),
                                             cursor="hand2"), bg="bg", fg="dim")
        self.voice_lbl.pack(side="right", padx=8)
        self.voice_lbl.bind("<Button-1>", lambda e: self.toggle_talk())
        self.theme_sw = _Switch(row, self, 22, 12, self.theme).pack(side="left")

        # Input across the bottom. The border is the wrapper's background showing through
        # a 1px gap - Tk's own focus ring is a rounded rect that macOS's corner mask clips.
        # The 6px inset keeps the border's corners clear of that mask too.
        self.boxwrap = self._skin(tk.Frame(self.panel), bg="line")
        self.boxwrap.pack(side="bottom", fill="x", padx=6, pady=6)
        self.box = self._skin(
            tk.Text(self.boxwrap, height=1, bd=0, highlightthickness=0, wrap="word",
                    font=("SF Pro Text", 12), padx=10, pady=6, undo=True),
            bg="bg", fg="fg", insertbackground="accent")
        self.box.pack(fill="x", padx=1, pady=1)
        self.box.bind("<Return>", self._submit)
        self.box.bind("<Shift-Return>", lambda e: self.box.insert("insert", "\n") or "break")
        self.box.bind("<KeyRelease>", self._fit_box)

        if self.dev_on:
            # The wire feed: every Deepgram message the instant it lands, newest at the
            # bottom, wedged between the conversation and the input.
            self._skin(tk.Frame(self.panel, height=1), bg="line").pack(side="bottom", fill="x")
            self.devlog = self._skin(
                tk.Text(self.panel, height=12, bd=0, highlightthickness=0, wrap="word",
                        font=("SF Mono", 9), padx=10, pady=5, state="disabled"),
                bg="field", fg="dim")
            self.devlog.pack(side="bottom", fill="x")

        # log hugs the bottom of its box, so the first line sits just above the input
        # and the conversation grows upwards from there.
        self.logbox = self._skin(tk.Frame(self.panel), bg="bg")
        self.logbox.pack(fill="both", expand=True)
        self.log = self._skin(
            tk.Text(self.logbox, height=1, bd=0, highlightthickness=0, wrap="word",
                    font=("SF Pro Text", 12), padx=12, pady=0, spacing1=6,
                    state="disabled"), bg="bg", fg="fg")
        self.log.pack(side="bottom", fill="x")
        self.logbox.bind("<Configure>", lambda e: self._fit_log())
        self._lh = tkfont.Font(font=self.log["font"]).metrics("linespace") + 6

        self.bind("<Escape>", lambda e: self.collapse())
        self.theme(False)               # paints the canvas items the registry can't reach

    def _paint_box(self):
        self.boxwrap.config(bg=self.pal["focus" if self._lit else "line"])

    def _relight(self):
        """Lit whenever Vega itself is the focused app, not just when the box holds Tk
        focus - clicking the log or the switch shouldn't dim it. Polled on the drain
        tick because focus_displayof() answers directly and Tk sends the toplevel no
        event when focus moves between its own children."""
        lit = not self.talking and self.focus_displayof() is not None
        if lit != self._lit:
            self._lit = lit
            self._paint_box()

    def _fit_box(self, _=None):
        self.update_idletasks()         # else the wrap count is a frame stale
        self.box.config(height=min(_rows(self.box), MAX_INPUT))
        self.box.see("insert")          # past MAX_INPUT the earlier lines scroll out of view

    def _fit_log(self):
        self.update_idletasks()
        cap = max(1, self.logbox.winfo_height() // self._lh)
        self.log.config(height=min(_rows(self.log), cap))
        self.log.see("end")

    # ---------- drag / click ----------
    def _draggable(self, w, on_click=None):
        w.bind("<ButtonPress-1>", self._press)
        w.bind("<B1-Motion>", self._move)
        if on_click:
            w.bind("<ButtonRelease-1>", lambda e: self._moved or on_click())

    def _press(self, e):
        self._off, self._moved = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y()), False

    def _move(self, e):
        self._moved = True
        self.geometry("+%d+%d" % (e.x_root - self._off[0], e.y_root - self._off[1]))

    # ---------- states ----------
    def _remember(self, e):
        if self.mode == "panel" and e.widget is self:
            self._size = (e.width, e.height)   # keep whatever size you dragged it to

    def _reshape(self, size):
        """Pin the right edge. The window hangs off the top-right corner, so Tk's default
        of holding the left edge fixed would walk it away from the screen edge on every
        collapse. Read from the live geometry, so it still works after you drag it."""
        right, y = self.winfo_x() + self.winfo_width(), self.winfo_y()
        self.geometry("%dx%d+%d+%d" % (*size, right - size[0], y))

    def collapse(self):
        self.mode = "tag"                      # set first, so _remember ignores this resize
        self.panel.pack_forget()
        self.tag.pack()
        self._reshape(self._tag)
        self._float()                          # re-assert: Tk remaps the window on resize

    def expand(self):
        self.mode = "panel"
        self.tag.pack_forget()
        self.panel.pack(fill="both", expand=True)
        self._reshape(self._size)
        self._paint_tag()                      # clear the unread mark
        self._float()
        self._focus()
        self._fit_log()
        if not self.talking:
            self.box.focus_set()
        self._relight()

    def toggle_talk(self, on=None):
        self.talking = (not self.talking) if on is None else on
        self.voice_sw.paint(self.talking)
        self.voice_lbl.config(fg=self.pal["accent" if self.talking else "dim"])
        # Typing while the mic is live would race the voice loop for the same history.
        self.box.config(state="normal")
        self.box.delete("1.0", "end")
        self._fit_box()
        self.box.config(state="disabled" if self.talking else "normal")
        # A disabled Text keeps Tk focus, so FocusOut never fires - repaint by hand.
        if not self.talking and self.mode == "panel":
            self.box.focus_set()
        self._relight()
        self._paint_tag()
        self.on_talk(self.talking)

    # ---------- io ----------
    def say(self, text, who="vega"):
        """Thread safe. Call from the voice loop, from a worker, from anywhere."""
        self.inbox.put((who, text))

    def call(self, fn):
        """Run fn on the UI thread. Thread safe."""
        self.inbox.put(fn)

    def dev(self, text):
        """One line into the -dev wire feed; a no-op in normal mode. Thread safe."""
        if self.dev_on:
            self.call(lambda: self._dev_line(text))

    def _dev_line(self, text):
        self.devlog.config(state="normal")
        self.devlog.insert("end", text + "\n")
        self.devlog.delete("1.0", "end-300l")   # the feed never grows without bound
        self.devlog.see("end")
        self.devlog.config(state="disabled")

    def _submit(self, _):
        text = self.box.get("1.0", "end-1c").strip()
        if text:
            self.box.delete("1.0", "end")
            self._fit_box()
            self.say(text, "you")
            self.on_text(text)
        return "break"                  # never let Return leave a newline behind

    def _drain(self):
        while not self.inbox.empty():
            item = self.inbox.get()
            if callable(item):
                item()
                continue
            who, text = item
            self.log.config(state="normal")
            self.log.insert("end", ("\n" if self.log.index("end-1c") != "1.0" else "") + text, who)
            self.log.config(state="disabled")
            self._fit_log()
            if self.mode == "tag" and who == "vega":
                self._paint_tag(unread=True)
        self._relight()
        self.after(50, self._drain)


if __name__ == "__main__":
    ui = Vega()
    ui.on_text = lambda t: ui.after(400, lambda: ui.say(f"You said: {t}"))
    ui.on_talk = lambda on: ui.say(f"[mic {'open' if on else 'closed'}]")
    ui.say("Good evening, sir.")
    ui.mainloop()
