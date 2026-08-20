"""VEGA's hands. The voice loop knows exactly one tool: delegate(task).

Everything Vega can *do* lives behind `claude -p` - a Claude Code session with
its own context, its own tools, its own skills. Adding an ability means dropping
a markdown file in .claude/skills/, not touching the voice loop or its prompt.
The hot path's token cost is therefore constant no matter how many abilities exist.
"""
import json, subprocess, threading, uuid

SESSION, _started = str(uuid.uuid4()), False
CWD = "/Users/marcuschan/Documents/vega"

# The entire tool surface the voice model ever sees. ~60 tokens, forever.
TOOLS = [{"type": "function", "function": {
    "name": "delegate",
    "description": "Do anything requiring the computer: files, web, email, terminal, "
                   "code, calculations, memory, reminders. Pass the request in full.",
    "parameters": {"type": "object", "required": ["task"], "properties": {
        "task": {"type": "string", "description": "What to do, in one sentence."}}},
}}]

def _run(task, timeout):
    global _started
    # First call names the session, later ones resume it - that's how the hands
    # remember what they did five minutes ago without the voice loop carrying it.
    session = ["--session-id", SESSION] if not _started else ["--resume", SESSION]
    _started = True
    try:
        p = subprocess.run(
            ["claude", "-p", task, "--output-format", "json", *session,
             # ponytail: dontAsk skips anything needing approval rather than granting it.
             # Swap to bypassPermissions once you trust the skills; it's a real security call.
             "--permission-mode", "dontAsk",
             "--append-system-prompt",
             "Your output is read aloud by a voice assistant. Reply in one or two "
             "plain spoken sentences, no markdown, numbers as words."],
            cwd=CWD, capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            return f"That didn't work, sir. {p.stderr.strip()[:200]}"
        return json.loads(p.stdout)["result"]
    except subprocess.TimeoutExpired:
        return "I'm still working on that, sir."
    except Exception as e:
        return f"Something went wrong, sir. {e}"

def delegate(task, on_done, timeout=180):
    """Fire and forget. on_done(text) is called from a worker thread when it lands -
    Jarvis acknowledges immediately and reports back, he doesn't go silent."""
    threading.Thread(target=lambda: on_done(_run(task, timeout)), daemon=True).start()

if __name__ == "__main__":
    done = threading.Event()
    delegate("How many .ipynb files are in this directory? Just say the number.",
             lambda t: (print("hands:", t), done.set()))
    assert done.wait(180), "delegate never returned"
