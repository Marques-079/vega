"""VEGA's face. One always-on-top window that is either a dot or a chat panel.

Runs in the same process as the brain, so it calls its functions directly - no IPC,
no socket, no second runtime. Worker threads reach the UI only through say()/call(),
which are queue-backed because Tk isn't thread safe.
"""
import queue, tkinter as tk

DOT, PANEL = 64, (360, 440)
BG, FG, DIM, ACCENT, LIVE, NEW = "#0d1117", "#c9d1d9", "#6e7681", "#58a6ff", "#f85149", "#3fb950"


class Vega(tk.Tk):
    def __init__(self, on_text=lambda s: None, on_talk=lambda on: None):
        super().__init__()
        self.on_text, self.on_talk = on_text, on_talk
        self.inbox, self.talking, self.mode = queue.Queue(), False, "dot"
        self._styled = False

        # No overrideredirect: it strips NSWindowStyleMaskTitled, and a window without
        # that bit can never become key, so the entry would never receive a keystroke.
        # The chrome comes off in _float() instead, from the AppKit side.
        self.wm_attributes("-topmost", True)
        try:
            # macOS only: lets the dot render as an actual circle, not a gray square.
            self.wm_attributes("-transparent", True)
            self.config(bg="systemTransparent")
            self._clear = "systemTransparent"
        except tk.TclError:
            self._clear = BG
            self.config(bg=BG)
        self.geometry("+%d+%d" % (self.winfo_screenwidth() - 160, 140))

        self._build_dot()
        self._build_panel()
        self.collapse()
        self.after(50, self._drain)
        self.after(0, self._float)   # inside mainloop: before that the NSWindow may not exist

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
                w.setHasShadow_(False)  # a shadow would trace the square, not the circle
                self._styled = True
            # Re-asserted on every call: Tk resets the level when it remaps the window.
            w.setLevel_(1000)                        # NSScreenSaverWindowLevel
            w.setCollectionBehavior_(1 | 1 << 4 | 1 << 6 | 1 << 8)
            # CanJoinAllSpaces | Stationary | IgnoresCycle | FullScreenAuxiliary

    def _focus(self):
        """Accessory apps do not get focus by being clicked into alone."""
        try:
            from AppKit import NSApp
            NSApp().activateIgnoringOtherApps_(True)
        except ImportError:
            pass
        self.focus_force()

    # ---------- widgets ----------
    def _build_dot(self):
        self.dot = tk.Canvas(self, width=DOT, height=DOT, bg=self._clear, highlightthickness=0)
        self.blob = self.dot.create_oval(8, 8, DOT - 8, DOT - 8, fill=ACCENT, outline="")
        self._draggable(self.dot, on_click=self.expand)

    def _build_panel(self):
        self.panel = tk.Frame(self, bg=BG, width=PANEL[0], height=PANEL[1])

        bar = tk.Frame(self.panel, bg=BG, height=28)
        bar.pack(fill="x")
        tk.Label(bar, text="VEGA", bg=BG, fg=DIM, font=("SF Mono", 11)).pack(side="left", padx=10)
        for txt, cmd in (("x", self.destroy), ("-", self.collapse)):
            b = tk.Label(bar, text=txt, bg=BG, fg=DIM, font=("SF Mono", 13), cursor="hand2")
            b.pack(side="right", padx=6)
            b.bind("<Button-1>", lambda e, c=cmd: c())
        self._draggable(bar)

        self.log = tk.Text(self.panel, bg=BG, fg=FG, bd=0, highlightthickness=0, wrap="word",
                           font=("SF Pro Text", 12), padx=10, pady=6, state="disabled")
        self.log.tag_config("you", foreground=DIM)
        self.log.tag_config("vega", foreground=FG)
        self.log.pack(fill="both", expand=True)

        foot = tk.Frame(self.panel, bg=BG)
        foot.pack(fill="x", pady=(0, 8), padx=8)
        self.talk = tk.Label(foot, text="●  talk", bg=BG, fg=DIM, font=("SF Mono", 12), cursor="hand2")
        self.talk.pack(side="left", padx=(2, 8))
        self.talk.bind("<Button-1>", lambda e: self.toggle_talk())
        self.entry = tk.Entry(foot, bg="#161b22", fg=FG, bd=0, insertbackground=FG,
                              font=("SF Pro Text", 12), relief="flat")
        self.entry.pack(fill="x", ipady=6, padx=2)
        self.entry.bind("<Return>", self._submit)
        self.bind("<Escape>", lambda e: self.collapse())

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
    def collapse(self):
        self.panel.pack_forget()
        self.dot.pack()
        self.geometry("%dx%d" % (DOT, DOT))
        self.mode = "dot"
        self._float()               # re-assert: Tk remaps the window on resize

    def expand(self):
        self.dot.pack_forget()
        self.panel.pack(fill="both", expand=True)
        self.geometry("%dx%d" % PANEL)
        self.mode = "panel"
        self.dot.itemconfig(self.blob, fill=LIVE if self.talking else ACCENT)  # clear unread
        self._float()
        self._focus()
        self.entry.focus_set()

    def toggle_talk(self, on=None):
        self.talking = (not self.talking) if on is None else on
        self.talk.config(fg=LIVE if self.talking else DIM)
        self.dot.itemconfig(self.blob, fill=LIVE if self.talking else ACCENT)
        self.on_talk(self.talking)

    # ---------- io ----------
    def say(self, text, who="vega"):
        """Thread safe. Call from the voice loop, from a worker, from anywhere."""
        self.inbox.put((who, text))

    def call(self, fn):
        """Run fn on the UI thread. Thread safe."""
        self.inbox.put(fn)

    def _submit(self, _):
        text = self.entry.get().strip()
        if text:
            self.entry.delete(0, "end")
            self.say(text, "you")
            self.on_text(text)

    def _drain(self):
        while not self.inbox.empty():
            item = self.inbox.get()
            if callable(item):
                item()
                continue
            who, text = item
            self.log.config(state="normal")
            self.log.insert("end", f"{text}\n\n", who)
            self.log.see("end")
            self.log.config(state="disabled")
            if self.mode == "dot" and who == "vega":
                self.dot.itemconfig(self.blob, fill=NEW)      # unread nudge
        self.after(50, self._drain)


if __name__ == "__main__":
    ui = Vega()
    ui.on_text = lambda t: ui.after(400, lambda: ui.say(f"You said: {t}"))
    ui.on_talk = lambda on: ui.say(f"[mic {'open' if on else 'closed'}]")
    ui.say("Good evening, sir.")
    ui.expand()
    ui.mainloop()
