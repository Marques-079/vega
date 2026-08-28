"""show_file - find a file from a rough description and preview it on the overlay.

Spotlight does the finding (mdfind already indexes names, kinds, dates, and file
CONTENT, so "the doc about the standup" works without us reading anything), a
name-token-then-recency sort does the choosing, and Quick Look (qlmanage) renders
the preview PNG - every format macOS can preview, no per-format code.

Confidence rule: show the top hit only when it clearly beats the runner-up;
otherwise speak the top few and let the user pick - the reply is the picker UI,
and the follow-up ("the second one") lands in history like any other turn.
"""
import os, re, subprocess, tempfile, time

HOME = os.path.expanduser("~")


def _shots_dir():
    with_default = f"{HOME}/Desktop"
    try:
        d = subprocess.run(["defaults", "read", "com.apple.screencapture", "location"],
                           capture_output=True, text=True, timeout=5).stdout.strip()
        return os.path.expanduser(d) if d else with_default
    except Exception:
        return with_default


# Local-only, scoped: file access never leaves these folders. Trailing slash so
# prefix matching can't bleed into sibling folders with the same stem.
ROOTS = tuple({f"{HOME}/{d}" for d in ("Desktop", "Documents", "Downloads", "Pictures")}
              | {_shots_dir().rstrip("/")})
ROOTS = tuple(r + "/" for r in ROOTS)

KINDS = {"screenshot": 'kMDItemIsScreenCapture == 1',
         "image": 'kMDItemContentTypeTree == "public.image"',
         "pdf": 'kMDItemContentTypeTree == "com.adobe.pdf"',
         "document": 'kMDItemContentTypeTree == "public.composite-content"'}

SPEC = {"type": "function", "function": {
    "name": "show_file",
    "description": "Find a file on this Mac from a rough description and preview it "
                   "on the floating overlay - screenshots, documents, downloads. Also "
                   "answers 'where is that file': it speaks the location and puts the "
                   "path on the clipboard. The result arrives on its own, so "
                   "acknowledge briefly and stop.",
    "parameters": {"type": "object", "properties": {
        "words": {"type": "string", "description":
                  "words likely in the file's name or contents, straight from the "
                  "user's request - never a path, never invented"},
        "kind": {"type": "string", "enum": ["screenshot", "image", "pdf", "document", "any"]},
        "minutes": {"type": "integer", "description":
                    "only files touched this recently - 15 covers 'just now'; omit "
                    "when the user gave no time hint"}},
        "required": []}}}


def _mtime(path):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0


def _ago(ts):
    m = (time.time() - ts) / 60
    if m < 1.5:
        return "moments ago"
    if m < 90:
        return f"{round(m)} minutes ago"
    if m < 36 * 60:
        return f"{round(m / 60)} hours ago"
    return f"{round(m / 60 / 24)} days ago"


def _thumb(path):
    """Quick Look's preview PNG, or None - the overlay falls back to text."""
    out = tempfile.mkdtemp(prefix="vega_ql_")
    try:
        subprocess.run(["qlmanage", "-t", "-s", "640", "-o", out, path],
                       capture_output=True, timeout=20)
    except Exception:
        return None
    png = os.path.join(out, os.path.basename(path) + ".png")
    return png if os.path.exists(png) else None


def run(args, ui, job):
    import tools as toolbox     # deferred: the package imports this module
    toks = [t for t in re.findall(r"\w+", (args.get("words") or "").lower()) if len(t) > 2]
    kind = args.get("kind") or "any"

    base = [KINDS[kind]] if kind in KINDS else []
    if args.get("minutes"):
        base.append(f"kMDItemFSContentChangeDate >= $time.now(-{int(args['minutes']) * 60})")
    if not (base or toks):
        return "What should I look for, sir - a name, a kind, or how recent it is?"

    # Strict to loose: every word in the name, any word in the name, then the words
    # inside the file (Spotlight's content index). First pass with hits wins.
    name = [f'kMDItemFSName == "*{t}*"cd' for t in toks]
    body = [f'kMDItemTextContent == "{t}*"cd' for t in toks]
    passes = [p for p in (" && ".join(name),
                          " || ".join(name) if len(name) > 1 else "",
                          " && ".join(body)) if p] or [""]
    hits = []
    for extra in passes:
        q = " && ".join(base + ([extra] if extra else []))
        out = subprocess.run(["mdfind", q], capture_output=True, text=True,
                             timeout=15).stdout
        hits = [ln for ln in out.splitlines()
                if ln.startswith(ROOTS) and not os.path.basename(ln).startswith(".")]
        if hits:
            break
    if not hits:
        return "I couldn't find a matching file, sir."

    def hit_count(p):
        nm = os.path.basename(p).lower()
        return sum(t in nm for t in toks)

    def say_name(p):
        if kind == "screenshot":
            return "the screenshot"
        return re.sub(r"[-_.]+", " ",
                      os.path.splitext(os.path.basename(p))[0]).strip() or "the file"

    cands = sorted(hits[:400], key=lambda p: (-hit_count(p), -_mtime(p)))[:3]
    top, now = cands[0], time.time()
    # Sure when the name matches better than the runner-up, or when the top is
    # decisively fresher on a name tie. ponytail: screenshots taken seconds apart
    # come back as a question - loosen the 3x if that grates.
    sure = (len(cands) == 1 or hit_count(top) > hit_count(cands[1])
            or (now - _mtime(top)) * 3 < (now - _mtime(cands[1])))
    if not sure:
        opts = ", ".join(f"{say_name(p)} from {_ago(_mtime(p))}" for p in cands)
        return f"I found a few, sir: {opts}. Which one?"

    subprocess.run(["pbcopy"], input=top, text=True)    # never read a path aloud
    where = os.path.dirname(top).replace(HOME, "").lstrip("/") or "your home folder"
    toolbox.run("open_overlay",
                {"text": f"{os.path.basename(top)}\n{top}", "image": _thumb(top)}, ui)
    return (f"Showing {say_name(top)} from {_ago(_mtime(top))} - it's in {where}, "
            f"and the path is on your clipboard, sir.")
