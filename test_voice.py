"""Feeds synthesized speech with a pause in the middle through the live socket
and asserts the pause splits the transcript into two lines. Needs network + API key."""
import os, threading, time
from dotenv import load_dotenv
from deepgram import DeepgramClient

load_dotenv()
dg = DeepgramClient(api_key=os.environ["DEEPGRAM_API_KEY"])
RATE = 24000

def tts(text):
    return b"".join(dg.speak.v1.audio.generate(
        text=text, model="aura-2-thalia-en", encoding="linear16",
        container="none", sample_rate=RATE))

def test_pause_splits_lines():
    pcm = tts("Hello there. This is the first sentence.")
    pcm += b"\x00" * (RATE * 3)          # 1.5s silence == conversational break
    pcm += tts("And here is a completely separate second thought.")

    with dg.listen.v1.connect(
        model="nova-3", encoding="linear16", sample_rate=RATE, channels=1,
        interim_results=True, punctuate=True, smart_format=True,
        endpointing=800, utterance_end_ms=1000,
    ) as sock:
        def feed():
            for i in range(0, len(pcm), RATE // 5):
                sock.send_media(pcm[i:i + RATE // 5])
                time.sleep(0.1)          # roughly realtime
            sock.send_close_stream()
        threading.Thread(target=feed, daemon=True).start()

        lines, line = [], []
        for m in sock:
            if m.type != "Results":
                continue
            text = m.channel.alternatives[0].transcript
            if m.is_final:
                if text:
                    line.append(text)
                if m.speech_final and line:
                    lines.append(" ".join(line))
                    line = []

    print(lines)
    assert len(lines) == 2, f"expected 2 lines, got {lines}"
    assert "first sentence" in lines[0].lower()
    assert "second thought" in lines[1].lower()

def test_tts_streams_immediately():
    """First audio must arrive long before the whole clip is generated, and the
    stream must be raw PCM (no wav header) in whole 16-bit frames."""
    text = ("Hey Marcus, this is a long enough sentence that waiting for the whole "
            "thing would be obvious to anyone listening to it.")
    next(iter(dg.speak.v1.audio.generate(text="warm up", model="aura-2-thalia-en",
        encoding="linear16", container="none", sample_rate=RATE)))   # pay TLS setup first

    t0 = time.time()
    first_at, first_chunk, total = None, None, 0
    for chunk in dg.speak.v1.audio.generate(text=text, model="aura-2-thalia-en",
            encoding="linear16", container="none", sample_rate=RATE):
        if first_at is None:
            first_at, first_chunk = time.time() - t0, chunk
        total += len(chunk)

    audio_secs = total / 2 / RATE
    print(f"first audio {first_at:.2f}s for {audio_secs:.1f}s of speech")
    assert first_at < 1.0, f"first audio took {first_at:.2f}s"
    assert first_at < audio_secs / 4, "not streaming - waited for most of the clip"
    assert not first_chunk.startswith(b"RIFF"), "wav header leaked into raw PCM"


def test_play_keeps_frames_aligned():
    """play() must never hand sounddevice a partial 16-bit frame."""
    written, tail = [], b""
    def play(chunk):
        nonlocal tail
        buf = tail + chunk
        cut = len(buf) - len(buf) % 2
        written.append(buf[:cut])
        tail = buf[cut:]

    for chunk in [b"\x01", b"\x02\x03\x04", b"\x05\x06", b"\x07"]:
        play(chunk)
    assert all(len(w) % 2 == 0 for w in written), written
    assert b"".join(written) + tail == b"\x01\x02\x03\x04\x05\x06\x07"


def test_fast_commit_breaks_lines_on_real_pause():
    """STT 3's pattern: phrases print as soon as they are final, but the line only
    breaks on UtteranceEnd, so a short gap does not start a new line."""
    speech = tts("Marcus could you forward the Kubernetes migration doc to Priya before the standup.")
    speech += b"\x00" * (RATE * 3)          # 1.5s - a real conversational break
    speech += tts("She mentioned the Postgres schema changes are blocking her.")
    speech += b"\x00" * (RATE * 4)

    lines, cur = [], []
    with dg.listen.v1.connect(
        model="nova-3", encoding="linear16", sample_rate=RATE, channels=1,
        interim_results=True, punctuate=True, smart_format=True,
        endpointing=150, utterance_end_ms=1000,
        keyterm=["Kubernetes", "Priya", "Postgres", "standup"],
    ) as sock:
        def feed():
            for i in range(0, len(speech), RATE // 10):
                sock.send_media(speech[i:i + RATE // 10])
                time.sleep(0.1)
            sock.send_close_stream()
        threading.Thread(target=feed, daemon=True).start()

        for m in sock:
            if getattr(m, "type", None) == "UtteranceEnd":
                if cur:
                    lines.append(" ".join(cur)); cur = []
            elif getattr(m, "type", None) == "Results" and m.is_final:
                t = m.channel.alternatives[0].transcript
                if t:
                    cur.append(t)
    if cur:
        lines.append(" ".join(cur))

    print(lines)
    assert len(lines) == 2, f"expected 2 lines, got {lines}"
    assert "kubernetes" in lines[0].lower() and "priya" in lines[0].lower()
    assert "postgres" in lines[1].lower()


if __name__ == "__main__":
    test_play_keeps_frames_aligned()
    test_tts_streams_immediately()
    test_pause_splits_lines()
    test_fast_commit_breaks_lines_on_real_pause()
    print("ok")
