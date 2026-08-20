"""VEGA. Overlay on the main thread, Cerebras for thinking, Deepgram for talking.

Text and voice share one `history`, so what you typed is still there when you
switch to voice and back. The voice loop is lifted straight from R&D/conversation.ipynb.
Everything VEGA can do beyond talking lives in tools/ - see tools/__init__.py.
"""
import os, re, json, sys, time, queue, threading, contextlib, httpx
from dotenv import load_dotenv
import ui as _ui
import tools as toolbox

load_dotenv()
LLM = httpx.Client(base_url="https://api.cerebras.ai/v1", timeout=30,
                   headers={"Authorization": f"Bearer {os.environ['CEREBUS']}"})

MODEL = "gpt-oss-120b"
VOICE, SPEED = "aura-2-jupiter-en", 1.2
TTS_RATES = (8000, 16000, 24000, 32000, 48000)   # linear16 rates Deepgram can emit
KEYTERMS = ["Vega", "Kubernetes", "Priya", "Postgres", "standup"]
WAKE = re.compile(r"\bvega\b", re.I)   # says it mid-answer and it cuts itself off
IDLE_TIMEOUT = 120                      # seconds of no speech before the socket hangs up
MUTE_TAIL = 0.25                        # mic stays deaf this long after it stops talking

TYPED = ("You are Vega, Marcus's assistant. Answer in a few short sentences. "
         "Plain text, no headings or bullets.")
SPOKEN = ("Respond shortly. You are a voice assistant and your reply is read aloud: "
          "answer in one or two plain spoken sentences. Never use markdown, lists, "
          "bullets, asterisks, headings, or emoji. Write numbers and units as words.")

# Rides along on every Cerebras call, like a second system prompt. The tools
# themselves live in tools/ - one module each, see tools/__init__.py.
TOOLS = toolbox.SPECS

history = [{"role": "system", "content": TYPED}]
speak_q = queue.Queue()               # late answers; delivered only when the floor is free
_speak = {"fn": None, "free": None}   # the live voice session registers its speaker here
APP = None                            # set at startup; the tools' window into the UI


def after_name(txt):
    """The user's side of a barge turn: the last 'vega' and everything after it - what
    came before is Vega's own voice off the speaker. Empty when the name is all there
    is, because the question is still coming and its own clean turn will carry it."""
    hits = list(WAKE.finditer(txt))
    txt = txt[hits[-1].start():] if hits else txt
    return txt if WAKE.sub("", txt, 1).strip(" ,.") else ""


def stream(msg, spoken):
    """Yield Cerebras tokens for `msg`. Same history either way, only the system
    prompt swaps - typed answers may use punctuation a voice would trip over."""
    history[0]["content"] = SPOKEN if spoken else TYPED
    history.append({"role": "user", "content": msg})
    answer, why, tool = "", None, {"id": "", "name": "", "args": ""}
    with LLM.stream("POST", "/chat/completions", json={
            "model": MODEL, "messages": history, "stream": True, "tools": TOOLS,
            # gpt-oss reasons before answering out of the same budget - at effort
            # "high" that can eat all of it and return nothing.
            "reasoning_effort": "low", "max_completion_tokens": 400,
            "temperature": 0.6}) as r:
        if r.status_code != 200:
            history.pop()
            yield f"[cerebras {r.status_code}] {r.read().decode()[:160]}"   # 429 = per-minute cap
            return
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            body = line[6:]
            if body == "[DONE]":
                break
            ch = json.loads(body)["choices"][0]
            why = ch.get("finish_reason") or why
            for tc in ch["delta"].get("tool_calls") or []:   # streamed in fragments
                tool["id"] = tc.get("id") or tool["id"]
                tool["name"] = tc.get("function", {}).get("name") or tool["name"]
                tool["args"] += tc.get("function", {}).get("arguments") or ""
            tok = ch["delta"].get("content") or ""
            if tok:
                answer += tok
                yield tok
    if why == "tool_calls" and tool["name"]:
        try:
            args = json.loads(tool["args"])
        except ValueError:
            args = {}
        if not isinstance(args, dict):
            args = {"query": str(args)}
        if not answer:
            answer = "One moment, sir."
            yield answer
        # The tool runs on a snapshot: shared history never carries tool messages,
        # so a question asked while one is in flight can't wedge into its turn order.
        snap = [{**history[0]}, *history[1:],
                {"role": "assistant", "content": answer, "tool_calls": [
                    {"id": tool["id"] or "call_0", "type": "function",
                     "function": {"name": tool["name"], "arguments": tool["args"]}}]}]
        threading.Thread(target=_run_tool,
                         args=(tool["name"], args, tool["id"] or "call_0", snap),
                         daemon=True).start()
    if answer:
        history.append({"role": "assistant", "content": answer})
    elif why == "length":
        # Only a real truncation. Empty content with finish_reason "stop" means the
        # model chose to say nothing - "don't reply", "just listen" - so let it.
        yield "[cut off - raise max_completion_tokens]"


def _run_tool(name, args, call_id, msgs):
    """One tool call, off the main thread. What it returns goes back through
    Cerebras with the conversation attached, and the answer waits in speak_q
    for a free floor instead of cutting in."""
    try:
        found = str(toolbox.run(name, args, APP)).strip()[:4000] or "nothing came back"
    except Exception as e:
        found = f"{name} failed: {e}"
    msgs.append({"role": "tool", "tool_call_id": call_id, "content": found})
    try:            # no tools on this pass: one tool call per question, no loops
        r = LLM.post("/chat/completions", json={
            "model": MODEL, "messages": msgs, "reasoning_effort": "low",
            "max_completion_tokens": 400, "temperature": 0.6})
        answer = (r.json()["choices"][0]["message"]["content"] or "").strip()
    except Exception as e:
        answer = f"The tool finished but I couldn't read it, sir. {e}"
    if answer:
        history.append({"role": "assistant", "content": answer})
        speak_q.put(answer)


def _speaker(ui):
    """Drain speak_q, forever. A late answer waits until the floor is free - Vega
    not mid-sentence, you not mid-sentence - then goes out through the live voice
    session, or straight to the log when the mic is off."""
    while True:
        text = speak_q.get()
        while _speak["free"] and not _speak["free"]():
            time.sleep(0.3)
        try:
            (_speak["fn"] or ui.say)(text)
        except Exception:
            ui.say(text)                # voice session died mid-handoff


def best_audio(mic=False):
    """Device name the voice session should use, or None for the system default.
    Output: bluetooth headphones, then wired externals, then the built-in speakers -
    the closer the sound sits to your ears, the less of it comes back through the mic.
    Input: the reverse, built-in mic first and bluetooth dead last - capturing from a
    bluetooth headset drops its whole link to phone-call audio, music included, and
    the Mac's own mic hears you better anyway. CoreAudio directly, because PortAudio
    never learned what a transport type is. Picked once per voice session - headphones
    that connect mid-session take effect on the next toggle."""
    import ctypes as ct
    from ctypes import util
    ca = ct.CDLL(util.find_library("CoreAudio"))

    class Addr(ct.Structure):
        _fields_ = [("sel", ct.c_uint32), ("scope", ct.c_uint32), ("elem", ct.c_uint32)]

    def four(code):
        return int.from_bytes(code.encode(), "big")

    def size_of(obj, sel, scope):
        n = ct.c_uint32(0)
        ca.AudioObjectGetPropertyDataSize(obj, ct.byref(Addr(four(sel), four(scope), 0)),
                                          0, None, ct.byref(n))
        return n.value

    def read(obj, sel, scope, buf):
        n = ct.c_uint32(ct.sizeof(buf))
        return not ca.AudioObjectGetPropertyData(obj, ct.byref(Addr(four(sel), four(scope), 0)),
                                                 0, None, ct.byref(n), ct.byref(buf))

    devs = (ct.c_uint32 * (size_of(1, "dev#", "glob") // 4 or 1))()   # 1 = system object
    read(1, "dev#", "glob", devs)
    ranked = []
    for d in devs:
        if not size_of(d, "stm#", "inpt" if mic else "outp"):
            continue                    # streams only in the direction we shop for
        kind, name = ct.c_uint32(0), ct.create_string_buffer(260)
        read(d, "tran", "glob", kind)
        read(d, "name", "glob", name)
        nm = name.value.decode(errors="replace")
        t = kind.value.to_bytes(4, "big").decode(errors="replace")
        if mic:
            ranked.append((0 if t == "bltn" else                       # the Mac's own
                           1 if t in ("usb ", "thun", "pci ") else     # wired external
                           3 if t in ("blue", "blea") else 2, nm))     # bt: never
        else:
            ranked.append((0 if t in ("blue", "blea") else             # bluetooth
                           1 if t in ("usb ", "hdmi", "dprt", "thun", "pci ")
                           or "headphone" in nm.lower() else           # plugged in
                           2 if t == "bltn" else                       # out loud
                           3,                                          # virtual et al.
                           nm))
    return min(ranked)[1] if ranked else None


# ---------------------------------------------------------------- text mode
def on_text(ui, msg):
    def work():
        # "…" rather than nothing: a silent reply is indistinguishable from a hang.
        ui.say("".join(stream(msg, spoken=False)).strip() or "…")
    threading.Thread(target=work, daemon=True).start()


# --------------------------------------------------------------- voice mode
_stop, _voice_running = threading.Event(), threading.Event()
# The output stream outlives the voice session: joining and leaving a bluetooth
# device makes its link re-buffer, which anything else playing feels as a glitch.
# Kept open, Vega is one permanent client that is simply silent between sessions.
_out = {"key": None, "s": None}

def _prewarm():
    """The imports and PortAudio's first device query cost about a second between
    them, and both are idempotent. Pay it while the window is opening rather than on
    the toggle - by then the re-import inside `_voice` is a dict lookup."""
    with contextlib.suppress(Exception):
        import sounddevice as sd, deepgram      # noqa: F401
        sd.query_devices(kind="input")
threading.Thread(target=_prewarm, daemon=True).start()

def on_talk(ui, on):
    # Only ever called on the UI thread, so these flags need no lock. A toggle-on
    # during the ~1s teardown is ignored; the dot resets and you click again.
    if not on:
        _stop.set()                     # watchdog notices within a second, closes the socket
    elif not _voice_running.is_set():
        _voice_running.set()
        _stop.clear()
        threading.Thread(target=_voice, args=(ui,), daemon=True).start()

def _voice(ui):
    """One STT socket and one TTS socket held open for the whole session, until you
    toggle off or go IDLE_TIMEOUT seconds without speaking."""
    import sounddevice as sd
    from deepgram import DeepgramClient
    from deepgram.speak.v1.types import SpeakV1Text

    dg = DeepgramClient(api_key=os.environ["DEEPGRAM_API_KEY"])

    def resolve(want, key):
        """CoreAudio name to PortAudio index; None falls through to the default."""
        return next((i for i, dv in enumerate(sd.query_devices())
                     if dv[key] and want and (want in dv["name"] or dv["name"] in want)),
                    None)

    mic_dev = None
    with contextlib.suppress(Exception):
        mic_dev = resolve(best_audio(mic=True), "max_input_channels")
    src = sd.query_devices(mic_dev) if mic_dev is not None else sd.query_devices(kind="input")
    rate = int(src["default_samplerate"])

    # Route the voice to the best sink around, and speak at the sink's own rate: any
    # other rate makes PortAudio switch the device's nominal rate under whatever else
    # is playing, and a bluetooth link renegotiates that audibly - music crackles,
    # fragments, cuts. 48k covers the odd sink whose rate Deepgram can't produce.
    spk = None
    with contextlib.suppress(Exception):
        spk = resolve(best_audio(), "max_output_channels")
    sink = sd.query_devices(spk) if spk is not None else sd.query_devices(kind="output")
    tts_rate = int(sink["default_samplerate"])
    if tts_rate not in TTS_RATES:
        tts_rate = 48000
    # `turn` is the whole cancellation mechanism: every barge-in bumps it, and the
    # reply thread that owns the older number bails at its next token.
    st = {"speaking": False, "flushes": 0, "tail": b"", "last": time.time(),
          "turn": 0, "cut": False, "saying": "", "mute": 0.0, "heard": 0.0,
          # `dirty` marks the in-flight turn as containing words transcribed while Vega
          # was talking - overheard, not addressed to it. Void at EndOfTurn.
          "dirty": False, "barged": False}

    q = queue.Queue()

    def mic(d, *_):
        # Deepgram hears the speaker as readily as it hears you. Real audio goes through
        # while Vega talks - that is the only way its name can interrupt - and silence
        # for a beat after, so the tail of an answer never lands as a turn of its own.
        q.put(bytes(len(d)) if time.time() < st["mute"] else bytes(d))

    def dial(cm, into, key):
        """__enter__ is where the handshake happens, so that is what has to run off the
        main path. The exception travels back as the value - raised where it makes sense."""
        try:
            into[key] = cm.__enter__()
        except Exception as e:
            into[key] = e

    try:
        with contextlib.ExitStack() as stack:
            # Mic first, before anything that touches the network: the queue holds
            # whatever you say while the sockets come up, so the opening words of a
            # session are late rather than lost.
            ui.dev(f"-- IN    {src['name']}")
            stack.enter_context(sd.RawInputStream(samplerate=rate, channels=1,
                                                  dtype="int16", blocksize=rate // 20,
                                                  callback=mic, device=mic_dev))

            # Two TLS handshakes, one after the other, is most of the wait before it
            # hears you. They have nothing to say to each other, so dial both at once.
            stt_cm = dg.listen.v2.connect(
                model="flux-general-en", encoding="linear16", sample_rate=rate,
                eager_eot_threshold=0.3, eot_threshold=0.5, eot_timeout_ms=2000,
                keyterm=KEYTERMS)
            tts_cm = dg.speak.v1.connect(
                model=VOICE, encoding="linear16", sample_rate=tts_rate, speed=SPEED)
            socks = {}
            ring = threading.Thread(target=dial, args=(tts_cm, socks, "tts"), daemon=True)
            ring.start()
            dial(stt_cm, socks, "stt")

            # latency="high": bluetooth delivers in bursts, and a low-latency
            # buffer underruns between Deepgram chunks - the crackle inside its own
            # speech. The cost is a beat more residual audio after a barge-in cut.
            ui.dev(f"-- OUT   {sink['name']} @ {tts_rate}")
            if _out["key"] != (spk, tts_rate):      # sink changed under us; swap streams
                with contextlib.suppress(Exception):
                    if _out["s"]:
                        _out["s"].close()
                o = sd.RawOutputStream(samplerate=tts_rate, channels=1, dtype="int16",
                                       latency="high", device=spk)
                o.start()
                _out.update(key=(spk, tts_rate), s=o)
            out = _out["s"]

            ring.join()                     # whichever failed, the other still has a
            for cm, key in ((stt_cm, "stt"), (tts_cm, "tts")):   # socket open to close
                if not isinstance(socks[key], Exception):
                    stack.push(cm)          # entered elsewhere, so push, not enter
            for key in ("stt", "tts"):
                if isinstance(socks[key], Exception):
                    raise socks[key]
            stt, tts = socks["stt"], socks["tts"]

            def play(chunk):
                if st["cut"]:
                    return                      # audio already on the wire when we cut
                buf = st["tail"] + chunk        # chunk edges don't respect 16-bit frames
                cut = len(buf) - len(buf) % 2
                with contextlib.suppress(Exception):
                    out.write(buf[:cut])        # device swapped mid-utterance; keep pumping
                st["tail"] = buf[cut:]

            def pump():
                with contextlib.suppress(Exception):
                    for m in tts:
                        if isinstance(m, bytes):
                            play(m)             # blocking, so by the time Flushed lands
                        elif getattr(m, "type", None) == "Flushed":
                            if not st["cut"]:   # stale ones would fake the next reply's
                                st["flushes"] += 1   # tail-wait into finishing early
                                                # every chunk before it is already out
            threading.Thread(target=pump, daemon=True).start()

            def say_aloud(text):
                """A queued lookup answer. Claims the floor through the same flags
                as reply(), so saying its name cuts this off like any other answer."""
                st["turn"] += 1
                turn = st["turn"]
                st["speaking"], st["cut"], st["saying"] = True, False, text
                before = st["flushes"]
                tts.send_text(SpeakV1Text(type="Speak", text=text))
                tts.send_flush()
                ui.say(text)
                deadline = time.time() + 30
                while (st["flushes"] == before and time.time() < deadline
                       and st["turn"] == turn):
                    time.sleep(0.02)
                time.sleep(0.25)                # device buffer tail
                if st["turn"] == turn:
                    st["speaking"] = False
                    st["mute"] = time.time() + MUTE_TAIL
            _speak["fn"] = say_aloud
            # Free floor: Vega quiet and nothing heard off the mic for a beat.
            _speak["free"] = lambda: (not st["speaking"]
                                      and time.time() - st["heard"] > 1.5)

            def reply(heard, turn):
                """Pipe tokens straight into the open TTS socket, flushing at each
                sentence end so it starts talking on sentence one, not on the answer.
                Bails wherever it notices `turn` went stale - something barged in."""
                def done():
                    if st["turn"] == turn:      # a newer reply owns the mic now; leaving
                        st["speaking"] = False  # its flag alone is the whole handoff
                        st["mute"] = time.time() + MUTE_TAIL
                # `saying` deliberately keeps the last answer until the next one starts
                # overwriting it - the echo of it arrives seconds after it stops talking.
                st["speaking"], st["cut"] = True, False
                before, sent, answer = st["flushes"], 0, ""
                for tok in stream(heard, spoken=True):
                    if st["turn"] != turn:
                        return              # cut off: nothing of the dead answer
                                            # reaches the log or the history
                    answer += tok
                    st["saying"] = answer       # what it's mid-way through saying, so a
                    tts.send_text(SpeakV1Text(type="Speak", text=tok))   # "vega" of its
                    if tok.rstrip()[-1:] in ".!?":                       # own is ignored
                        tts.send_flush(); sent += 1
                if not answer.strip():          # asked for silence, or got silence
                    return done()
                tts.send_flush(); sent += 1     # trailing text, plus a done marker
                ui.say(answer.strip())

                deadline = time.time() + 30     # let the audio actually finish
                while (st["flushes"] - before < sent and time.time() < deadline
                       and st["turn"] == turn):
                    time.sleep(0.02)
                time.sleep(0.25)                # device buffer tail
                done()

            def feed():
                """Teardown closes the socket while this thread is mid-send, so both the
                flag and the raise are exits - whichever gets here first."""
                try:
                    for chunk in iter(q.get, None):
                        if _stop.is_set():
                            break
                        stt.send_media(chunk)
                except Exception:
                    pass                        # socket closed under us
            threading.Thread(target=feed, daemon=True).start()

            def watchdog():
                while not _stop.wait(1):
                    if time.time() - st["last"] > IDLE_TIMEOUT and not st["speaking"]:
                        ui.say("Going quiet, sir.")
                        break
                _stop.set()
                with contextlib.suppress(Exception):
                    stt.send_close_stream()     # unblocks the `for m in stt` below
            threading.Thread(target=watchdog, daemon=True).start()

            try:
                upd_n, upd_txt = 0, None        # run length of unchanged Update events
                for m in stt:
                    txt = (getattr(m, "transcript", "") or "").strip()
                    ev = getattr(m, "event", None) or getattr(m, "type", "") or ""
                    if ev == "Update":          # a quiet mic Updates every second; three
                        upd_n = upd_n + 1 if txt == upd_txt else 1   # in a row is enough
                        upd_txt = txt           # to see it's alive - new words reset it
                    else:
                        upd_n = 0
                    if upd_n <= 3:
                        ui.dev(f"{ev:<12} {txt}".rstrip())
                    if txt:
                        st["heard"] = time.time()   # someone's talking; queued answers hold
                    if st["speaking"]:
                        # Mid-answer its name is the only thing that means anything -
                        # everything else arriving now is its own voice off the speaker,
                        # or you talking to someone who is not it. Either way the turn
                        # carrying it is tainted. (Not when it just said the name itself,
                        # in which case that is what came back - no interruption.)
                        if txt:
                            st["dirty"] = True
                        if not WAKE.search(txt) or WAKE.search(st["saying"]):
                            if getattr(m, "event", None) == "EndOfTurn":
                                st["dirty"] = False   # died entirely mid-answer; the
                            continue                  # next turn starts clean
                        st["turn"] += 1                 # strands the running reply
                        st["cut"], st["tail"] = True, b""   # kill local playback
                        st["speaking"], st["saying"] = False, ""
                        st["barged"] = True
                        st["mute"] = 0                  # you have the floor; stay listening
                        ui.dev("-- CUT   name heard mid-answer")
                        with contextlib.suppress(Exception):
                            tts.send_clear()            # drop what Deepgram queued
                        # then fall through: this same turn may carry the question
                    if getattr(m, "event", None) == "EndOfTurn":
                        barged, st["barged"] = st["barged"], False
                        dirty, st["dirty"] = st["dirty"], False
                        if not txt:
                            continue
                        if dirty and not barged:
                            # Words in this turn were transcribed while Vega was talking
                            # - overheard speech, never addressed to it. Only a turn
                            # that cut Vega off by name survives, below.
                            ui.dev("-- VOID  overlapped playback")
                            continue
                        if barged:
                            # Log and history carry "vega" plus your query, nothing of
                            # the answer it interrupted.
                            txt = after_name(txt)
                            if not txt:
                                continue    # the name alone: you have its attention
                        ui.dev(f"-- ASK   {txt}")
                        st["last"] = time.time()
                        ui.say(txt, "you")
                        st["turn"] += 1
                        # Claim the floor here, not inside the thread: between the two
                        # is a gap Deepgram can land another EndOfTurn in, and the same
                        # question gets asked and answered twice.
                        st["speaking"] = True
                        threading.Thread(target=reply, args=(txt, st["turn"]),
                                         daemon=True).start()
            except Exception:
                pass                            # socket closed under us; that's the exit
            q.put(None)
    except Exception as e:
        ui.say(f"Voice failed, sir. {e}")
    finally:
        _speak["fn"] = _speak["free"] = None    # queued answers fall back to the log
        _stop.set()
        _voice_running.clear()
        ui.call(lambda: ui.toggle_talk(False))  # put the dot back to blue


if __name__ == "__main__":
    app = APP = _ui.Vega(dev="-dev" in sys.argv)
    app.on_text = lambda t: on_text(app, t)
    app.on_talk = lambda on: on_talk(app, on)
    threading.Thread(target=_speaker, args=(app,), daemon=True).start()
    app.say("Good evening, sir.")
    app.mainloop()
