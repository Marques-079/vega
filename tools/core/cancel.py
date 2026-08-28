"""cancel_task - stop a background task that is still running.

Cancellation is cooperative: whatever module started the job registered
job["cancel"] (usually Popen.kill on its subprocess), and the wrapper in
tools/__init__.py swallows the dead job's return so nothing half-finished
gets spoken. A job that never set a cancel hook can only be disowned.
"""

SPEC = {"type": "function", "function": {
    "name": "cancel_task",
    "description": "Stop a running background task. The tasks running right now and "
                   "their ids are listed in your system prompt. Instant.",
    "parameters": {"type": "object", "properties": {
        "id": {"type": "integer", "description":
               "id of the task to stop, from the running-tasks list; may be omitted "
               "when only one task is running"}},
        "required": []}}}


def run(args, ui, job):
    import tools as toolbox     # deferred: the package imports this module
    live = {i: j for i, j in toolbox.RUNNING.items() if i != job["id"]}
    if not live:
        return "Nothing is running in the background, sir."
    target = live.get(args.get("id")) or (len(live) == 1 and next(iter(live.values())))
    if not target:
        return ("I couldn't tell which task you meant, sir. Running now: "
                + ", ".join(f"{i} is {j['name']}" for i, j in live.items()))
    target["cancelled"] = True
    if target["cancel"]:
        target["cancel"]()
    return f"Cancelled the {target['name']} task, sir."
