"""show_clipboard - whatever is on the clipboard, onto the overlay.

Text comes off with pbpaste; a copied image has no pbpaste face, so AppleScript
writes the clipboard's PNG to a temp file for the overlay to show.
"""
import os, subprocess, tempfile

SPEC = {"type": "function", "function": {
    "name": "show_clipboard",
    "description": "Show whatever is currently on the clipboard - copied text or a "
                   "copied image - on the floating overlay. Instant.",
    "parameters": {"type": "object", "properties": {}, "required": []}}}


def run(args, ui, job):
    import tools as toolbox     # deferred: the package imports this module
    txt = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5).stdout
    if txt.strip():
        clip = txt.strip()
        toolbox.run("open_overlay",
                    {"text": clip[:1500] + (" …" if len(clip) > 1500 else "")}, ui)
        return "Your clipboard text is on the overlay, sir."

    png = os.path.join(tempfile.mkdtemp(prefix="vega_clip_"), "clip.png")
    script = (f'set f to open for access POSIX file "{png}" with write permission\n'
              'write (the clipboard as «class PNGf») to f\nclose access f')
    r = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
    if r.returncode == 0 and os.path.exists(png) and os.path.getsize(png):
        toolbox.run("open_overlay", {"text": "", "image": png}, ui)
        return "Your clipboard image is on the overlay, sir."
    return "The clipboard looks empty, sir."
