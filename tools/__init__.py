"""Every tool VEGA can call. One module per tool, exporting:

    SPEC               the OpenAI-style schema that rides along on every Cerebras call
    run(args, ui, job) do the thing, return a short string - spoken verbatim as the
                       answer, no second pass through the LLM; called off the main
                       thread, so touch Tk only through ui.call(). `job` is this
                       call's RUNNING entry: anything long-running sets job["cancel"]
                       to whatever aborts it (usually Popen.kill).

core/     built-ins (internet search, cancel)
terminal/ the terminal-themed set (overlay, file preview, clipboard)

Add a tool: drop a module in the right folder, list it in MODULES below.
"""
import itertools, json, time
from .core import cancel, search
from .terminal import clipboard, close_overlay, overlay, show_file

MODULES = (search, overlay, close_overlay, show_file, clipboard, cancel)
SPECS = [m.SPEC for m in MODULES]

RUNNING = {}        # id -> job dict: what VEGA is busy with right now
_ids = itertools.count(1)


def run(name, args, ui):
    for m in MODULES:
        if m.SPEC["function"]["name"] != name:
            continue
        job = {"id": next(_ids), "name": name, "since": time.time(), "cancel": None,
               "task": str(args.get("query") or args.get("_user") or json.dumps(args))}
        RUNNING[job["id"]] = job
        try:
            out = m.run(args, ui, job)
            # A cancelled job's return is noise - cancel_task already answered.
            return None if job.get("cancelled") else out
        finally:
            RUNNING.pop(job["id"], None)
    return f"no tool named {name}"


def status():
    """Live jobs and open overlays rendered for the system prompt; "" when idle."""
    jobs = list(RUNNING.values())
    part = ""
    if jobs:
        lines = [f"  [{j['id']}] {j['name']} - running for {int(time.time() - j['since'])}s"
                 f" - task: {j['task'][:200]}" for j in jobs]
        part = ("\nBackground tasks running right now - the user is waiting on these, "
                "and can ask you to stop one (call cancel_task with its id):\n"
                + "\n".join(lines))
    return part + overlay.status()
