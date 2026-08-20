"""VEGA. Overlay on the main thread, Cerebras for thinking, Deepgram for talking.

Text and voice share one `history`, so what you typed is still there when you
switch to voice and back. The voice loop is lifted straight from R&D/conversation.ipynb.
"""
import os, json, time, queue, threading, contextlib, httpx
from dotenv import load_dotenv
import ui as _ui

load_dotenv()
LLM = httpx.Client(base_url="https://api.cerebras.ai/v1", timeout=30,
                   headers={"Authorization": f"Bearer {os.environ['CEREBUS']}"})

MODEL = "gpt-oss-120b"
VOICE, SPEED, TTS_RATE = "aura-2-jupiter-en", 1.2, 24000
KEYTERMS = ["Kubernetes", "Priya", "Postgres", "standup"]
IDLE_TIMEOUT = 120                      # seconds of no speech before the socket hangs up

TYPED = ("You are Vega, Marcus's assistant. Answer in a few short sentences. "
         "Plain text, no headings or bullets.")
SPOKEN = ("Respond shortly. You are a voice assistant and your reply is read aloud: "
          "answer in one or two plain spoken sentences. Never use markdown, lists, "
          "bullets, asterisks, headings, or emoji. Write numbers and units as words.")

history = [{"role": "system", "content": TYPED}]


def stream(msg, spoken):
    """Yield Cerebras tokens for `msg`. Same history either way, only the system
    prompt swaps - typed answers may use punctuation a voice would trip over."""
    history[0]["content"] = SPOKEN if spoken else TYPED
    history.append({"role": "user", "content": msg})
    answer = ""
    with LLM.stream("POST", "/chat/completions", json={
            "model": MODEL, "messages": history, "stream": True,
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
            tok = json.loads(body)["choices"][0]["delta"].get("content") or ""
            if tok:
                answer += tok
                yield tok
    history.append({"role": "assistant", "content": answer})


# ---------------------------------------------------------------- text mode
def on_text(ui, msg):
    def work():
        ui.say("".join(stream(msg, spoken=False)).strip() or "[empty reply]")
    threading.Thread(target=work, daemon=True).start()


# --------------------------------------------------------------- voice mode
_stop, _voice_running = threading.Event(), threading.Event()

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
    rate = int(sd.query_devices(kind="input")["default_samplerate"])
    st = {"speaking": False, "flushes": 0, "tail": b"", "last": time.time()}

    try:
        with contextlib.ExitStack() as stack:
            out = sd.RawOutputStream(samplerate=TTS_RATE, channels=1, dtype="int16",
                                     latency="low")
            out.start()
            stack.callback(out.close)
            stack.callback(out.abort)

            tts = stack.enter_context(dg.speak.v1.connect(
                model=VOICE, encoding="linear16", sample_rate=TTS_RATE, speed=SPEED))

            def play(chunk):
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
                            st["flushes"] += 1  # every chunk before it is already out
            threading.Thread(target=pump, daemon=True).start()

            def reply(heard):
                """Pipe tokens straight into the open TTS socket, flushing at each
                sentence end so it starts talking on sentence one, not on the answer."""
                st["speaking"] = True
                before, sent, answer = st["flushes"], 0, ""
                for tok in stream(heard, spoken=True):
                    answer += tok
                    tts.send_text(SpeakV1Text(type="Speak", text=tok))
                    if tok.rstrip()[-1:] in ".!?":
                        tts.send_flush(); sent += 1
                tts.send_flush(); sent += 1     # trailing text, plus a done marker
                ui.say(answer.strip() or "[empty - reasoning used the whole budget]")

                deadline = time.time() + 30     # let the audio actually finish
                while st["flushes"] - before < sent and time.time() < deadline:
                    time.sleep(0.02)
                time.sleep(0.25)                # device buffer tail
                st["speaking"] = False

            q = queue.Queue()
            stt = stack.enter_context(dg.listen.v2.connect(
                model="flux-general-en", encoding="linear16", sample_rate=rate,
                eager_eot_threshold=0.3, eot_threshold=0.5, eot_timeout_ms=2000,
                keyterm=KEYTERMS))
            threading.Thread(target=lambda: [stt.send_media(b) for b in iter(q.get, None)],
                             daemon=True).start()

            def watchdog():
                while not _stop.wait(1):
                    if time.time() - st["last"] > IDLE_TIMEOUT and not st["speaking"]:
                        ui.say("Going quiet, sir.")
                        break
                _stop.set()
                with contextlib.suppress(Exception):
                    stt.send_close_stream()     # unblocks the `for m in stt` below
            threading.Thread(target=watchdog, daemon=True).start()

            with sd.RawInputStream(samplerate=rate, channels=1, dtype="int16",
                                   blocksize=rate // 20,
                                   callback=lambda d, *_: q.put(
                                       bytes(len(d)) if st["speaking"] else bytes(d))):
                try:                            # mic feeds silence while it talks,
                    for m in stt:               # or it hears itself
                        if getattr(m, "event", None) == "EndOfTurn" and m.transcript.strip():
                            st["last"] = time.time()
                            ui.say(m.transcript, "you")
                            reply(m.transcript)
                            st["last"] = time.time()
                except Exception:
                    pass                        # socket closed under us; that's the exit
            q.put(None)
    except Exception as e:
        ui.say(f"Voice failed, sir. {e}")
    finally:
        _stop.set()
        _voice_running.clear()
        ui.call(lambda: ui.toggle_talk(False))  # put the dot back to blue


if __name__ == "__main__":
    app = _ui.Vega()
    app.on_text = lambda t: on_text(app, t)
    app.on_talk = lambda on: on_talk(app, on)
    app.say("Good evening, sir.")
    app.expand()
    app.mainloop()
