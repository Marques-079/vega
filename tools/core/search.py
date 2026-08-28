"""SINGLE_SEARCH_HAIKU - one internet task per call, claude CLI on Haiku.

Slow: the brain runs every tool off the main thread and delivers the answer late
through speak_q, so run() simply blocks until it knows."""
import json, os, subprocess, threading

SPEC = {"type": "function", "function": {
    "name": "SINGLE_SEARCH_HAIKU",
    "description": "Run one internet task - look up news, weather, prices, anything "
                   "you don't know or that may have changed. Slow: the answer arrives "
                   "later on its own, so acknowledge briefly and stop.",
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description":
                  "ALL of the user's instructions, complete and word for word - "
                  "what to find out AND what to do with the result (show it on the "
                  "overlay, etc.). Pass everything through verbatim; never summarise, "
                  "shorten, or drop any part."}},
        "required": ["query"]}}}


def run(args, ui, job):
    import tools as toolbox     # deferred: the package imports this module
    ask = args.get("query") or json.dumps(args)
    # The user's words outrank the model's paraphrase of them; the paraphrase stays
    # as a hint because it resolves references ("those ten words") from conversation
    # context the subprocess never sees.
    raw = args.get("_user") or ask
    ctx = f"\nThe assistant's reading of it, given the conversation: {ask}" \
          if raw != ask else ""

    def dev(s):
        ui and ui.dev(s)

    def vega_call(line):
        """A `VEGA {...}` line in claude's output is it driving one of our tools -
        the subprocess can't reach this Tk process any other way."""
        try:
            c = json.loads(line[5:])
            dev(f"   cli   -> {c['name']}")
            toolbox.run(c["name"], c.get("args") or {}, ui)
        except Exception as e:
            dev(f"   cli   -> bad VEGA call: {e}")

    others = [s["function"] for s in toolbox.SPECS
              if s["function"]["name"] != SPEC["function"]["name"]]
    briefing = ('You can also drive VEGA\'s own tools: print a line that is exactly '
                'VEGA {"name":"<tool>","args":{...}} - nothing else on that line, '
                'and it runs immediately. Tools: ' + json.dumps(others))

    found = ""
    # stream-json instead of plain print: same final answer, but every event
    # arrives as its own line, so -dev can watch the terminal live.
    p = subprocess.Popen(
        ["claude", "-p", "--model", "haiku",
         "--allowedTools", "WebSearch,WebFetch",
         "--append-system-prompt", briefing,
         "--output-format", "stream-json", "--verbose",
         f"Handle this request, looking things up on the internet as needed: {raw}{ctx}\n"
         f"If it asks to display or overlay something, drive the VEGA tool for it. "
         f"Your final reply is read aloud by a voice assistant - under eighty words "
         f"of plain spoken prose, no markdown, no links, no source lists."],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        # thinking: low - Claude Code reads its budget from this env var
        env={**os.environ, "MAX_THINKING_TOKENS": "4096"})
    job["cancel"] = p.kill      # cancel_task reaches the subprocess through this
    dev(f"-- CLI   claude[{p.pid}]  {raw}")   # what claude was given, not the paraphrase
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
                for tl in b["text"].splitlines():
                    if tl.startswith("VEGA "):
                        vega_call(tl)
        if j.get("type") == "result":
            # Sentinel lines already ran off the message events above; what's left
            # of the final text is the part meant to be spoken.
            found = "\n".join(tl for tl in (j.get("result") or "").splitlines()
                              if not tl.startswith("VEGA ")).strip()
            dev(f"-- CLI   done in {j.get('duration_ms', 0) // 1000}s")
    killer.cancel()
    return found
