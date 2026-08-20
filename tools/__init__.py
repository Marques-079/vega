"""Every tool VEGA can call. One module per tool, exporting:

    SPEC          the OpenAI-style schema that rides along on every Cerebras call
    run(args, ui) do the thing, return a short string for the follow-up pass;
                  called off the main thread, so touch Tk only through ui.call()

core/     built-ins (internet search)
terminal/ the terminal-themed set (overlay, ...)

Add a tool: drop a module in the right folder, list it in MODULES below.
"""
from .core import search
from .terminal import overlay

MODULES = (search, overlay)
SPECS = [m.SPEC for m in MODULES]


def run(name, args, ui):
    for m in MODULES:
        if m.SPEC["function"]["name"] == name:
            return m.run(args, ui)
    return f"no tool named {name}"
