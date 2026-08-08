"""Time-align the Panopto transcript against Accessible Academic's output.

Panopto gives one text block per start-timestamp. Our engine gives word-level
timings, so we can rebuild exactly the same time windows and compare like for
like — no guessing about which sentence corresponds to which.
"""
import io
import json
import re

BASE = "/private/tmp/claude-501/-Users-aditapiero-PycharmProjects-AccessibleAcademicBackend/29a8d99f-8540-4f2e-8f38-7da18385251d/scratchpad/panopto"

pan = json.load(io.open(f"{BASE}/panopto.json", encoding="utf-8"))
ours = json.load(io.open(f"{BASE}/ours_raw.json", encoding="utf-8"))
words = ours["words"]          # [{text, start(ms), end(ms), confidence}, ...]

END = 7964  # media duration, seconds


def hhmmss(sec):
    return f"{int(sec)//3600:d}:{(int(sec)%3600)//60:02d}:{int(sec)%60:02d}"


rows = []
for i, seg in enumerate(pan):
    a = seg["start"]
    b = pan[i + 1]["start"] if i + 1 < len(pan) else END
    ours_txt = " ".join(
        w["text"] for w in words if a * 1000 <= w["start"] < b * 1000
    )
    conf = [w.get("confidence", 0) for w in words if a * 1000 <= w["start"] < b * 1000]
    rows.append({
        "i": i,
        "start": a,
        "end": b,
        "ts": hhmmss(a),
        "panopto": seg["text"].strip(),
        "ours": ours_txt.strip(),
        "conf": round(sum(conf) / len(conf), 4) if conf else None,
    })

json.dump(rows, io.open(f"{BASE}/aligned.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

pw = sum(len(r["panopto"].split()) for r in rows)
ow = sum(len(r["ours"].split()) for r in rows)
print(f"aligned windows : {len(rows)}")
print(f"panopto words   : {pw}")
print(f"ours words      : {ow}")
print(f"ours mean conf  : {sum(w.get('confidence',0) for w in words)/len(words):.4f}")
print()
for r in rows[:6]:
    print(f"[{r['ts']}]")
    print(f"  PAN : {r['panopto']}")
    print(f"  OURS: {r['ours']}")
