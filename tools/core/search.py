"""internet_lookup - claude CLI does the searching.

Slow: the brain runs every tool off the main thread and delivers the answer late
through speak_q, so run() simply blocks until it knows."""
import json, subprocess, threading

SPEC = {"type": "function", "function": {
    "name": "internet_lookup",
    "description": "Look something up on the live internet - news, weather, prices, "
                   "anything you don't know or that may have changed. Slow: the answer "
                   "arrives later on its own, so acknowledge briefly and stop.",
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description": "what to find out, as one full question"}},
        "required": ["query"]}}}


def run(args, ui):
    ask = args.get("query") or json.dumps(args)

    def dev(s):
        ui and ui.dev(s)

    found = ""
    # stream-json instead of plain print: same final answer, but every event
    # arrives as its own line, so -dev can watch the terminal live.
    p = subprocess.Popen(
        ["claude", "-p", "--allowedTools", "WebSearch,WebFetch",
         "--output-format", "stream-json", "--verbose",
         f"Look this up on the internet and answer in under eighty words of "
         f"plain text: {ask}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    dev(f"-- CLI   claude[{p.pid}]  {ask}")
    killer = threading.Timer(180, p.kill)   # Popen has no timeout= of its own
    killer.start()
    quiet = 0                               # updates in a row showing no activity
    for line in p.stdout:
        try:
            j = json.loads(line)
        except ValueError:                  # stderr noise on the merged pipe
            quiet += 1
            if quiet <= 3:                  # three of those in a row: mute the rest
                dev(f"   cli   {line.rstrip()[:150]}")
            continue
        for b in (j.get("message") or {}).get("content") or []:
            if b.get("type") == "tool_use":
                dev(f"   cli   {b['name']}  {json.dumps(b.get('input', {}))[:120]}")
                quiet = 0
            elif b.get("type") == "text" and b.get("text"):
                dev(f"   cli   {b['text'][:150]}")
                quiet = 0
        if j.get("type") == "result":
            found = (j.get("result") or "").strip()
            dev(f"-- CLI   done in {j.get('duration_ms', 0) // 1000}s")
    killer.cancel()
    return found
