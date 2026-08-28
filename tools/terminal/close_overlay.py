# CODE CHANGKDAJWIJODPK:AWJNDHIJOKAW:JDIOAKW:DL


"""close_overlay - take overlays off the screen, by id or all at once.

Each overlay is its own process, so closing is just terminating it; the entry
leaves OPEN immediately rather than waiting for the corpse to be noticed.
"""

SPEC = {"type": "function", "function": {
    "name": "close_overlay",
    "description": "Close overlay windows. The overlays on screen and their ids are "
                   "listed in your system prompt. Instant.",
    "parameters": {"type": "object", "properties": {
        "id": {"type": "integer", "description":
               "id of the overlay to close, from the list; may be omitted when only "
               "one is open"},
        "all": {"type": "boolean", "description": "close every overlay"}},
        "required": []}}}


def run(args, ui, job):
    from . import overlay
    overlay._prune()
    live = overlay.OPEN
    if not live:
        return "There are no overlays open, sir."
    try:
        want = int(args.get("id"))
    except (TypeError, ValueError):
        want = None
    ids = (list(live) if args.get("all")
           else [want] if want in live
           else list(live) if len(live) == 1
           else [])
    if not ids:
        return ("Which one, sir? On screen: "
                + ", ".join(f"{i} is {o['desc'] or o['pos']}" for i, o in live.items()))
    for i in ids:
        live.pop(i)["proc"].terminate()
    return "Closed it, sir." if len(ids) == 1 else f"Closed {len(ids)} overlays, sir."
