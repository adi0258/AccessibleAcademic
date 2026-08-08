"""Pick the clearest head-to-head moments and mark every word against the reference.

For each sampled window we align Panopto and Accessible Academic to the same
reference word list, so a word is marked wrong only when it actually differs
from what the third engine heard — not merely because the two systems disagree.
"""
import io
import json
import re
import unicodedata

BASE = ("/private/tmp/claude-501/-Users-aditapiero-PycharmProjects-"
        "AccessibleAcademicBackend/29a8d99f-8540-4f2e-8f38-7da18385251d/scratchpad/panopto")

PUNCT = re.compile(r'[^\w֐-׿]+', re.UNICODE)
NIQQUD = re.compile(r'[֑-ׇ]')


# Hebrew attaches prefix particles with a maqaf ("ב-E1"). Our engine writes them,
# Panopto never does. Splitting on the hyphen puts both on the same footing.
HYPH = re.compile(r'[-\u05be\u2010-\u2015]')


def toks(text):
    """Return [(display, normalised)] so we can show original spelling but match cleanly."""
    out = []
    for raw in HYPH.sub(' ', text).split():
        n = NIQQUD.sub('', unicodedata.normalize('NFKC', raw))
        n = PUNCT.sub('', n).lower()
        if n:
            out.append((raw, n))
    return out


def align(ref, hyp):
    """Levenshtein backtrace -> list of (op, ref_tok, hyp_tok)."""
    n, m = len(ref), len(hyp)
    d = [[0]*(m+1) for _ in range(n+1)]
    bt = [[None]*(m+1) for _ in range(n+1)]
    for i in range(1, n+1):
        d[i][0], bt[i][0] = i, 'D'
    for j in range(1, m+1):
        d[0][j], bt[0][j] = j, 'I'
    for i in range(1, n+1):
        for j in range(1, m+1):
            if ref[i-1][1] == hyp[j-1][1]:
                d[i][j], bt[i][j] = d[i-1][j-1], 'C'
            else:
                s, dl, ins = d[i-1][j-1]+1, d[i-1][j]+1, d[i][j-1]+1
                best = min(s, dl, ins)
                d[i][j] = best
                bt[i][j] = 'S' if best == s else ('D' if best == dl else 'I')
    i, j, ops = n, m, []
    while i > 0 or j > 0:
        o = bt[i][j]
        if o in ('C', 'S'):
            ops.append((o, ref[i-1][0], hyp[j-1][0])); i, j = i-1, j-1
        elif o == 'D':
            ops.append(('D', ref[i-1][0], None)); i -= 1
        else:
            ops.append(('I', None, hyp[j-1][0])); j -= 1
    return list(reversed(ops))


def marked(ops):
    """Hypothesis words tagged ok/bad, with deletions shown as gaps."""
    out = []
    for o, r, h in ops:
        if o == 'C':
            out.append({'w': h, 'k': 'ok'})
        elif o == 'S':
            out.append({'w': h, 'k': 'bad', 'exp': r})
        elif o == 'I':
            out.append({'w': h, 'k': 'bad'})
        else:
            out.append({'w': '—', 'k': 'miss', 'exp': r})
    return out


pan = json.load(io.open(f'{BASE}/panopto.json', encoding='utf-8'))
ours = json.load(io.open(f'{BASE}/ours_raw.json', encoding='utf-8'))
ref = json.load(io.open(f'{BASE}/reference.json', encoding='utf-8'))
W = ours['words']

results = []
for w in ref:
    a, b = w['start'], w['end']
    R = toks(w['text'])
    P = toks(' '.join(s['text'] for s in pan if a <= s['start'] < b))
    O = toks(' '.join(x['text'] for x in W if a*1000 <= x['start'] < b*1000))

    pops, oops = align(R, P), align(R, O)
    perr = sum(1 for o, _, _ in pops if o != 'C')
    oerr = sum(1 for o, _, _ in oops if o != 'C')

    results.append({
        'start': a, 'end': b, 'ref_words': len(R),
        'panopto_err': perr, 'ours_err': oerr,
        'panopto_wer': perr/len(R) if R else None,
        'ours_wer': oerr/len(R) if R else None,
        'gap': (perr - oerr)/len(R) if R else 0,
        'ref_text': w['text'],
        'panopto_marked': marked(pops),
        'ours_marked': marked(oops),
    })

json.dump(results, io.open(f'{BASE}/marked.json', 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)

TR = sum(r['ref_words'] for r in results)
TP = sum(r['panopto_err'] for r in results)
TO = sum(r['ours_err'] for r in results)
print(f'reference words : {TR}')
print(f'Panopto  errors : {TP:5d}   WER {TP/TR*100:5.1f}%   accuracy {100-TP/TR*100:5.1f}%')
print(f'Ours     errors : {TO:5d}   WER {TO/TR*100:5.1f}%   accuracy {100-TO/TR*100:5.1f}%')
print(f'relative error reduction : {(TP-TO)/TP*100:.1f}%')
print()
print(f'{"window":>16} {"ref":>5} {"Panopto":>9} {"Ours":>9}')
for r in sorted(results, key=lambda x: x['start']):
    print(f'{r["start"]//60:>6}:{r["start"]%60:02d}-{r["end"]//60:>3}:{r["end"]%60:02d}'
          f' {r["ref_words"]:>5} {r["panopto_wer"]*100:8.1f}% {r["ours_wer"]*100:8.1f}%')
