"""Render the real before/after audio envelopes as a vector SVG for the poster."""
import array
import sys

W, H = 1600, 132          # per-panel viewBox units
PAD = 4
BINS = 900


def envelope(path):
    a = array.array('h')
    with open(path, 'rb') as f:
        a.frombytes(f.read())
    n = len(a)
    step = n / BINS
    out = []
    for i in range(BINS):
        lo, hi = int(i * step), int((i + 1) * step)
        chunk = a[lo:hi] or a[lo:lo + 1]
        peak = max(abs(min(chunk)), abs(max(chunk)))
        out.append(peak / 32768.0)
    return out


def panel(vals, color, y_off):
    """Mirrored peak envelope drawn as one filled path."""
    mid = y_off + H / 2
    half = (H - 2 * PAD) / 2
    top = []
    bot = []
    for i, v in enumerate(vals):
        x = i * (W / (BINS - 1))
        d = v * half
        top.append(f'{x:.1f},{mid - d:.1f}')
        bot.append(f'{x:.1f},{mid + d:.1f}')
    return (
        f'<path d="M{top[0]} L' + ' L'.join(top[1:]) +
        ' L' + ' L'.join(reversed(bot)) + ' Z" '
        f'fill="{color}" fill-opacity="0.92"/>'
        f'<line x1="0" y1="{mid:.1f}" x2="{W}" y2="{mid:.1f}" '
        f'stroke="{color}" stroke-opacity="0.35" stroke-width="1"/>'
    )


before = envelope('raw_before.pcm')
after = envelope('raw_after.pcm')

GAP = 26
total_h = H * 2 + GAP

parts = [
    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {total_h}" '
    f'width="{W}" height="{total_h}" font-family="Inter, Helvetica, Arial, sans-serif">'
]

# Panel backgrounds
parts.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#F4F5F7" rx="4"/>')
parts.append(f'<rect x="0" y="{H + GAP}" width="{W}" height="{H}" fill="#EEF5F4" rx="4"/>')

parts.append(panel(before, '#8A93A0', 0))
parts.append(panel(after, '#0F766E', H + GAP))

# Labels
parts.append(
    f'<text x="12" y="19" font-size="15" font-weight="700" fill="#5A6470" '
    f'letter-spacing="1.0">BEFORE &#183; raw hall recording</text>'
)
parts.append(
    f'<text x="12" y="{H + GAP + 19}" font-size="15" font-weight="700" fill="#0F766E" '
    f'letter-spacing="1.0">AFTER &#183; normalised for transcription</text>'
)

# Level readouts: true RMS over the raw PCM, not the binned envelope.
import math


def rms_dbfs(path):
    a = array.array('h')
    with open(path, 'rb') as f:
        a.frombytes(f.read())
    r = math.sqrt(sum(float(x) * x for x in a) / len(a)) / 32768.0
    return 20 * math.log10(r)


pb, pa = rms_dbfs('raw_before.pcm'), rms_dbfs('raw_after.pcm')
parts.append(
    f'<text x="{W - 12}" y="19" text-anchor="end" font-size="15" fill="#8A93A0">'
    f'RMS {pb:.1f} dBFS</text>'
)
parts.append(
    f'<text x="{W - 12}" y="{H + GAP + 19}" text-anchor="end" font-size="15" fill="#0F766E">'
    f'RMS {pa:.1f} dBFS</text>'
)

parts.append('</svg>')

open('waveform.svg', 'w').write('\n'.join(parts))
print(f'wrote waveform.svg  before={pb:.1f} dBFS  after={pa:.1f} dBFS  gain=+{pa - pb:.1f} dB')
