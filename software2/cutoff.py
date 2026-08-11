"""Resonant lowpass filter -- HAcK 2026, Team 13.

Replaces the single one-pole that used to live in main.py. Three things
changed and all three are audible:

1. TAPER. The old map was 60 Hz -> 5400 Hz exponentially, which put the
   midpoint of the knob at 569 Hz. Anything below about 3/4 travel was a
   mud switch, not a filter. Musical sweeps live between roughly 200 Hz
   and Nyquist, so that is the range the knob now covers, and the curve is
   shaped so the middle of the travel lands near 1.4 kHz.

2. SLOPE. One pole is 6 dB/octave -- so gentle that closing it removes
   loudness roughly as fast as it removes brightness, which is why it read
   as "everything got quiet and muddy" instead of "filter". Two cascaded
   poles give 12 dB/octave: the fundamental survives while the harmonics
   above the corner actually go away.

3. RESONANCE. A little feedback around the pair puts a bump at the corner.
   This is the entire reason a filter sweep sounds like anything, and it
   also buys back the perceived brightness that the second pole costs.

Integer throughout, Q10 fixed point, no allocation in the audio path.

Usage from main.py:

    import cutoff
    cutoff.configure(SAMPLE_RATE)       # once, before the loop
    cutoff.process(mix_buf, n, pct)     # once per block, pct is 0-100

Verify it on the bench with cutoff.self_test() -- see the bottom of the
file. If it prints a flat response, the filter is not running.
"""

import math

# ---------------- Fixed point ----------------
# Q10: 1.0 == 1024. Chosen over Q12 for headroom, see the clamps below.

_Q = 10
_ONE = 1024

# Hard bounds on the internal states. These exist for overflow safety, not
# tone. MicroPython small ints are 31-bit (+-1.07e9); the widest product in
# the loop is (input + state) * k, so with input clamped to 300000 and the
# states to 350000 the worst case is 650000 * 1024 = 665M, comfortably
# inside the limit. Without these, a resonant sweep on a loud chord
# promotes to a big int, which allocates -- inside the audio loop -- and
# you get a dropout, not a wrong number.
_IN_CLAMP = 300000
_STATE_CLAMP = 350000

# ---------------- Frequency mapping ----------------

CUTOFF_MIN_HZ = 200
CUTOFF_MAX_HZ = 5600        # must stay under Nyquist; move if SAMPLE_RATE does

# Below this percentage the filter is bypassed entirely (k == 1.0 exactly,
# so y tracks x sample for sample and costs nothing but the compare).
OPEN_PCT = 99

# Resonance, Q10. 0 = none, 768 = self-oscillating-ish and unstable on
# loud material. 256 is a clear vocal bump that still behaves with 8 voices
# and drums running. Raise it for a more dramatic demo sweep.
RESONANCE = 256

# Resonance is tapered off as the filter opens: a bump at 5 kHz on a
# 12 kHz system is just harshness, and it is where clipping risk is worst.
RES_FADE_START_PCT = 75

_rate = 12000
_k_table = None
_res_table = None
_mk_table = None

# Two pole states, carried across blocks. A per-block reset would put a
# step discontinuity at every block boundary -- that is a 47 Hz buzz.
_y1 = 0
_y2 = 0


def _corner_hz(pct):
    """Knob percent to corner frequency.

    Exponential in frequency (linear in pitch), which is the only mapping
    that feels even under the finger. The old code used the same shape but
    anchored at 60 Hz, and anchoring is everything: two thirds of the
    travel was spent below 1 kHz.
    """
    if pct <= 0:
        return CUTOFF_MIN_HZ
    ratio = CUTOFF_MAX_HZ / CUTOFF_MIN_HZ
    return CUTOFF_MIN_HZ * (ratio ** (pct / 100.0))


def _k_for(pct, rate):
    """One-pole coefficient, k = 1 - exp(-2*pi*fc/fs), as Q10."""
    if pct >= OPEN_PCT:
        return _ONE
    fc = _corner_hz(pct)
    k = 1.0 - math.exp(-2.0 * math.pi * fc / rate)
    return max(1, min(_ONE, int(k * _ONE)))


def _res_for(pct):
    """Resonance amount, Q10, faded out as the filter opens."""
    if RESONANCE <= 0 or pct >= OPEN_PCT:
        return 0
    if pct <= RES_FADE_START_PCT:
        return RESONANCE
    span = OPEN_PCT - RES_FADE_START_PCT
    return int(RESONANCE * (OPEN_PCT - pct) / span)


def _makeup_for(pct):
    """Passband makeup gain, Q10.

    The resonance feedback is negative, so at DC the loop settles at
    y = x / (1 + res), i.e. turning resonance up quietly turns the whole
    instrument down -- 1.9 dB at res=256. That is a loudness bug wearing a
    tone-control costume, so it gets compensated exactly rather than left
    for the volume knob to chase.
    """
    res = _res_for(pct)
    return ((_ONE + res) * _ONE) // _ONE


def configure(sample_rate):
    """Build the coefficient tables. Call once, before the audio loop.

    Tables rather than live math: _corner_hz uses exp() and pow(), and a
    float call per block is a heap allocation per block.
    """
    global _rate, _k_table, _res_table, _mk_table
    _rate = sample_rate
    _k_table = tuple(_k_for(p, sample_rate) for p in range(101))
    _res_table = tuple(_res_for(p) for p in range(101))
    _mk_table = tuple(_makeup_for(p) for p in range(101))
    reset()
    return _k_table


def reset():
    """Zero the states. Integer floor-shift is asymmetric, so a small
    negative state can converge to -1 and sit there as a DC offset --
    call this when the mix goes fully silent."""
    global _y1, _y2
    _y1 = 0
    _y2 = 0


def is_open(pct):
    """True when the filter is a pass-through, so the caller can skip it."""
    return pct >= OPEN_PCT


def tail_active():
    """True while the filter still has energy in it. main.py's silence
    fast path must consult this, or the tail of every phrase is cut off
    into a click."""
    return _y1 > 16 or _y1 < -16 or _y2 > 16 or _y2 < -16


def corner_hz(pct):
    """The actual corner frequency for a knob position. Useful on the OLED
    and for proving to a judge that the knob does something."""
    if pct >= OPEN_PCT:
        return _rate // 2
    return int(_corner_hz(pct))


@micropython.native
def process(mix, n, pct):
    """Filter one block in place. mix is the int32 accumulator.

    Two one-poles in series with a feedback path around the pair:

        u  = x - res * y2          resonance: feed the output back, inverted
        y1 = y1 + k * (u  - y1)    pole 1
        y2 = y2 + k * (y1 - y2)    pole 2

    Everything is pulled into locals first -- module-global lookups are
    among the slowest operations in MicroPython and this runs 256 times a
    block.
    """
    global _y1, _y2

    k = _k_table[pct]
    res = _res_table[pct]
    mk = _mk_table[pct]

    # Fully open: nothing to do but keep the states tracking, so that
    # closing the knob mid-note does not start from a stale value.
    if k >= _ONE and res == 0:
        last = mix[n - 1]
        _y1 = last if -_STATE_CLAMP < last < _STATE_CLAMP else 0
        _y2 = _y1
        return

    y1 = _y1
    y2 = _y2

    i = 0
    while i < n:
        x = mix[i]
        if x > _IN_CLAMP:
            x = _IN_CLAMP
        elif x < -_IN_CLAMP:
            x = -_IN_CLAMP

        if res:
            u = ((x * mk) >> _Q) - ((res * y2) >> _Q)
        else:
            u = x

        y1 += ((u - y1) * k) >> _Q
        if y1 > _STATE_CLAMP:
            y1 = _STATE_CLAMP
        elif y1 < -_STATE_CLAMP:
            y1 = -_STATE_CLAMP

        y2 += ((y1 - y2) * k) >> _Q
        if y2 > _STATE_CLAMP:
            y2 = _STATE_CLAMP
        elif y2 < -_STATE_CLAMP:
            y2 = -_STATE_CLAMP

        mix[i] = y2
        i += 1

    _y1 = y1
    _y2 = y2


# ---------------- Bench verification ----------------

def self_test(rate=12000, verbose=True):
    """Measure the actual response at a few frequencies and knob settings.

    Runs on the Pico or on a laptop under CPython -- it touches no
    hardware. If the numbers do not drop as the knob closes, the filter is
    not in the signal path, which is a wiring-up problem in main.py, not a
    filter problem.

    Returns a dict of {pct: {freq_hz: gain_db}}.
    """
    from array import array

    configure(rate)
    n = 512
    buf = array("l", [0] * n)
    results = {}

    for pct in (0, 25, 50, 75, 100):
        row = {}
        for f in (100, 250, 500, 1000, 2000, 4000):
            reset()
            amp = 20000
            # Two passes: the first lets the filter settle, the second is
            # measured, so we report steady state rather than the attack.
            for _ in range(2):
                for i in range(n):
                    buf[i] = int(amp * math.sin(2 * math.pi * f * i / rate))
                process(buf, n, pct)
            # RMS, not peak. At 4 kHz on a 12 kHz clock there are three
            # samples per cycle, and peak-detecting that under-reads the
            # true amplitude by over a dB -- which would show up here as
            # filter rolloff that is not actually there.
            acc = 0
            for i in range(n):
                v = buf[i]
                acc += v * v
            rms = math.sqrt(acc / n)
            ref = amp / math.sqrt(2)
            row[f] = round(20 * math.log10(max(rms, 1e-9) / ref), 1)
        results[pct] = row

    if verbose:
        freqs = (100, 250, 500, 1000, 2000, 4000)
        print("cutoff self-test, fs =", rate, "Hz -- gain in dB")
        print("  knob | corner |" + "".join("%7d" % f for f in freqs))
        for pct in sorted(results):
            print("  %4d | %5d  |" % (pct, corner_hz(pct))
                  + "".join("%7.1f" % results[pct][f] for f in freqs))

    return results


if __name__ == "__main__":
    self_test()
