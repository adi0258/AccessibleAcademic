"""Independent reference transcript, produced locally with Whisper large-v3-turbo.

Why a third engine: neither Panopto's transcript nor ours is ground truth, so
scoring one against the other proves nothing. Whisper is architecturally
independent of AssemblyAI (the engine inside Accessible Academic) and of
Panopto's engine, so it has no stake in either result.

The reference is itself imperfect. That inflates both measured error rates by a
similar amount, but it does not favour either system — the *comparison* stays
valid. Runs entirely on this machine; no audio leaves it.
"""
import io
import json
import os
import subprocess

BASE = ("/private/tmp/claude-501/-Users-aditapiero-PycharmProjects-"
        "AccessibleAcademicBackend/29a8d99f-8540-4f2e-8f38-7da18385251d/scratchpad/panopto")
SRC = f"{BASE}/lecture_boosted.mp3"
MODEL = f"{BASE}/ggml-large-v3-turbo.bin"

windows = json.load(io.open(f"{BASE}/windows.json"))
out = []

for w in windows:
    a, b = w["start"], w["end"]
    wav = f"{BASE}/ref_{a}.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(a), "-to", str(b), "-i", SRC,
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-y", wav],
        check=True,
    )
    stem = f"{BASE}/ref_{a}"
    subprocess.run(
        ["whisper-cli", "-m", MODEL, "-f", wav, "-l", "he",
         "-otxt", "-of", stem, "-nt", "-t", "8"],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    text = io.open(f"{stem}.txt", encoding="utf-8").read().strip()
    out.append({"start": a, "end": b, "text": text})
    print(f"[{a//60:>3d}:{a%60:02d}-{b//60:>3d}:{b%60:02d}] {len(text.split()):4d} words", flush=True)
    os.remove(wav)
    os.remove(f"{stem}.txt")

json.dump(out, io.open(f"{BASE}/reference.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("\nmodel: whisper large-v3-turbo (local)")
print("reference words:", sum(len(w["text"].split()) for w in out))
