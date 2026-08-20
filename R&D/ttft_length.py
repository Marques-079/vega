"""Does prompt length change time to first token? Same question, padded with N words
of junk, round-robin so network drift hits every size equally. Median of ROUNDS.

Padding is random words - identical padding would let any prompt cache answer the
second round instantly and we'd be timing the cache, not the prompt.
"""
import os, sys, time, json, random, statistics, httpx
from dotenv import load_dotenv

load_dotenv()
LLM = httpx.Client(base_url="https://api.cerebras.ai/v1", timeout=60,
                   headers={"Authorization": f"Bearer {os.environ['CEREBUS']}"})
MODEL = "gpt-oss-120b"
SIZES = [0, 1000, 4000, 12000, 24000]   # padding words, ~1.3 tokens each
ROUNDS = 5
WORDS = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike".split()


def ttft(pad_words):
    """Returns (first chunk, first content token, total) seconds. gpt-oss reasons before
    it answers, so first chunk is the real network+prefill latency and first content
    trails it by however long the reasoning ran."""
    pad = " ".join(random.choices(WORDS, k=pad_words))
    body = {"model": MODEL, "stream": True, "max_completion_tokens": 96,
            "reasoning_effort": "low", "temperature": 0.6,
            "messages": [{"role": "system", "content": "Respond shortly."},
                         {"role": "user", "content":
                          (f"Ignore this filler: {pad}\n\n" if pad_words else "")
                          + "How far away is the moon?"}]}
    t0 = time.perf_counter()
    chunk = content = None
    with LLM.stream("POST", "/chat/completions", json=body) as r:
        if r.status_code != 200:                       # 429 = per-minute token cap
            print(f"[cerebras {r.status_code}] {r.read().decode()[:120]} - waiting 30s")
            time.sleep(30)
            return ttft(pad_words)
        for line in r.iter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            d = json.loads(line[6:])["choices"][0]["delta"]
            chunk = chunk or time.perf_counter() - t0
            if d.get("content"):
                content = content or time.perf_counter() - t0
    return chunk, content, time.perf_counter() - t0


runs = {n: [] for n in SIZES}
for i in range(ROUNDS):
    for n in random.sample(SIZES, len(SIZES)):     # shuffled: no size always goes first
        runs[n].append(ttft(n))
        print(f"round {i+1} pad {n:>5}w  chunk {runs[n][-1][0]:.3f}s  "
              f"content {runs[n][-1][1]:.3f}s  total {runs[n][-1][2]:.3f}s", flush=True)

def med(vals):
    vals = [v for v in vals if v is not None]   # a run whose reasoning ate the whole
    return statistics.median(vals) if vals else float("nan")   # budget yields no content

print(f"\n{'pad words':>10} {'~tokens':>8} {'chunk med':>10} {'chunk min':>10} {'content med':>12}")
for n in SIZES:
    c = [x[0] for x in runs[n]]
    print(f"{n:>10} {int(n*1.33):>8} {med(c):>9.3f}s {min(c):>9.3f}s "
          f"{med([x[1] for x in runs[n]]):>11.3f}s")
