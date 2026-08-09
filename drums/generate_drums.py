"""
DRUM SAMPLE GENERATOR -- HAcK 2026, Team 13
===========================================

Runs on a LAPTOP, not on the Pico. Writes eight 16-bit mono PCM .wav
files that main.py loads at boot into DRUM_SAMPLES.

    python3 generate_drums.py

Why offline: synthesising this much noise and swept-sine DSP in
interpreted MicroPython would cost several seconds of unpredictable boot
time every power-on, and boot happens on stage. Generating once and
loading flat files is effectively instant.

MUST MATCH main.py. _load_wav_samples() validates the fmt chunk and
raises at boot on any mismatch, deliberately -- a 44100 Hz or stereo file
does not error at load time, it just plays at the wrong pitch and half
speed, which is a miserable thing to debug during sound-check. So:

    SAMPLE_RATE here == SAMPLE_RATE in main.py     (12000)
    16-bit, mono, PCM format 1

If SAMPLE_RATE in main.py ever moves again, re-run this and re-copy all
eight files. That coupling is the price of doing no resampling on the
Pico.

SYNTHESIS APPROACH
------------------
Each drum is built from two ingredients, mixed in different proportions:

  a TONAL body   -- a sine whose pitch falls exponentially. Real drum
                    heads drop in pitch as the strike detensions them,
                    and that downward sweep is most of what makes a
                    sound read as "drum" rather than "beep".
  a NOISE body   -- white noise, usually high-passed. Snare wires, hi-hat
                    cymbals and hand claps are broadband; the filter
                    corner is what separates a hat from a snare.

Both are shaped by an exponential amplitude decay, because that is how
struck resonators actually lose energy.

BAND LIMITING: everything is generated AT 12000 Hz rather than generated
high and downsampled, so no partial above the 6000 Hz Nyquist is ever
created in the first place. The one exception is white noise, which is
flat to Nyquist by construction -- fine, since noise has no harmonic
structure to alias into.

Output is written next to this script. Copy all eight to the ROOT of the
Pico's filesystem (main.py's DRUM_KIT_DIR = "/"), alongside main.py.
"""

import math
import random
import struct
import wave

# MUST equal SAMPLE_RATE in main.py. See the header note.
SAMPLE_RATE = 12000

# Fixed seed so every regeneration produces byte-identical files. Without
# this, re-running the script after a tweak silently changes the drums
# you rehearsed with.
random.seed(1337)

# Peak amplitude per sample. Deliberately below the int16 ceiling: drum
# hits mix into the SAME int32 accumulator as up to 8 melodic voices, and
# MIX_HEADROOM in main.py is sized around 32000-peak sources.
AMP = 28000


# ------------------------------------------------------------------
# Building blocks
# ------------------------------------------------------------------

def exp_decay(n, tau_s):
    """Exponential amplitude envelope, 1.0 falling toward 0 with time
    constant tau_s seconds. Struck resonators lose energy at a rate
    proportional to the energy they still have, which integrates to
    exactly this -- so it is the physically right shape, not just a
    convenient curve."""
    return [math.exp(-i / (tau_s * SAMPLE_RATE)) for i in range(n)]


def pitch_sweep(n, f_start, f_end, tau_s):
    """Sine whose frequency falls exponentially from f_start to f_end.

    Phase is ACCUMULATED (phase += 2*pi*f/rate) rather than computed as
    sin(2*pi*f(t)*t). The closed form is wrong for a changing f: it
    treats the current frequency as if it had applied for the whole
    elapsed time, which produces a discontinuous, warbling sweep. Only
    integrating instantaneous frequency gives continuous phase."""
    out = []
    phase = 0.0
    for i in range(n):
        f = f_end + (f_start - f_end) * math.exp(-i / (tau_s * SAMPLE_RATE))
        phase += 2 * math.pi * f / SAMPLE_RATE
        out.append(math.sin(phase))
    return out


def noise(n):
    return [random.uniform(-1.0, 1.0) for _ in range(n)]


def highpass(x, fc):
    """One-pole high-pass: y[n] = a * (y[n-1] + x[n] - x[n-1]).

    Same filter topology as main.py's lowpass, rearranged. Used to set
    each noise source's character: a hi-hat is noise with everything
    below ~4 kHz removed, a snare keeps far more low end. One pole is
    only 6 dB/octave, which is gentle -- but these are percussive
    sounds heard for a few hundred milliseconds, not sustained tones, so
    a steeper filter would not be audible for the extra cost."""
    dt = 1.0 / SAMPLE_RATE
    rc = 1.0 / (2 * math.pi * fc)
    a = rc / (rc + dt)
    y = [0.0] * len(x)
    for i in range(1, len(x)):
        y[i] = a * (y[i - 1] + x[i] - x[i - 1])
    return y


def lowpass(x, fc):
    """One-pole low-pass, for taking the edge off noise sources."""
    dt = 1.0 / SAMPLE_RATE
    rc = 1.0 / (2 * math.pi * fc)
    a = dt / (rc + dt)
    y = [0.0] * len(x)
    prev = 0.0
    for i in range(len(x)):
        prev = prev + a * (x[i] - prev)
        y[i] = prev
    return y


def mix(*parts):
    """Sum equal-length signals element-wise."""
    n = max(len(p) for p in parts)
    out = [0.0] * n
    for p in parts:
        for i in range(len(p)):
            out[i] += p[i]
    return out


def apply_env(x, env):
    return [x[i] * env[i] for i in range(min(len(x), len(env)))]


def normalise(x, peak=1.0):
    """Scale to a known peak. Every drum is normalised individually so
    the kit is level-matched -- otherwise the crash, which has the most
    energy, would be twice the volume of the kick on the same button
    row."""
    m = max(abs(v) for v in x) or 1.0
    return [v * peak / m for v in x]


def soft_clip(x):
    """tanh-shaped limiter. Reaching the rails is what makes a kick sound
    punchy on a small speaker, but HARD clipping generates broadband
    harmonics that alias at 12000 Hz. tanh saturates smoothly instead,
    adding mostly low-order harmonics that stay under Nyquist."""
    return [math.tanh(v) for v in x]


def seconds(t):
    return int(t * SAMPLE_RATE)


# ------------------------------------------------------------------
# The kit -- order matches DRUM_FILES / button index in main.py
# ------------------------------------------------------------------

def kick():
    """Button 1. Deep pitch sweep 120 -> 45 Hz: the drop is fast (25 ms
    time constant) because a kick drum head detensions almost
    immediately. Short noise burst on top supplies the beater click, the
    transient that lets a kick cut through a mix on a small speaker."""
    n = seconds(0.30)
    body = apply_env(pitch_sweep(n, 120, 45, 0.025), exp_decay(n, 0.10))
    click = apply_env(highpass(noise(n), 1200), exp_decay(n, 0.004))
    return soft_clip(normalise(mix(body, [c * 0.30 for c in click]), 1.15))


def snare():
    """Button 2. Two detuned tonal bodies (180 and 330 Hz) for the drum
    shell, plus a bright noise band for the wires underneath. Snare wires
    ring longer than the shell does, so the noise envelope has the longer
    time constant of the two."""
    n = seconds(0.22)
    shell = mix(apply_env(pitch_sweep(n, 210, 180, 0.02), exp_decay(n, 0.045)),
                [0.6 * v for v in apply_env(pitch_sweep(n, 380, 330, 0.02),
                                            exp_decay(n, 0.035))])
    wires = apply_env(highpass(noise(n), 900), exp_decay(n, 0.075))
    return normalise(mix([0.7 * v for v in shell], [0.9 * v for v in wires]))


def hat(open_hat):
    """Buttons 3 and 4. Hi-hats are two cymbals clamped together: closed
    is a 55 ms tick, open rings for ~320 ms. Identical source, different
    decay -- which is exactly the physical difference, so generating them
    from one recipe is honest rather than lazy.

    High-passed hard at 5 kHz. That is close to the 6000 Hz Nyquist, so
    a hat is mostly the top octave of the available spectrum, which is
    why hats are the drum most affected by the sample rate choice."""
    t = 0.32 if open_hat else 0.055
    n = seconds(t)
    src = highpass(noise(n), 5000)
    env = exp_decay(n, t / 3.2)
    return normalise(apply_env(src, env))


def clap():
    """Button 5. A hand clap is not one event -- it is three or four
    slightly offset impacts (both hands, multiple contact points) that
    the ear fuses into one sound with a characteristic 'spread'. Modelled
    literally: three short bursts a few milliseconds apart, then a longer
    tail for the room reflection."""
    n = seconds(0.28)
    src = highpass(noise(n), 1100)
    env = [0.0] * n
    for offset_ms, gain in ((0, 1.0), (9, 0.8), (19, 0.65)):
        start = seconds(offset_ms / 1000.0)
        burst = exp_decay(n - start, 0.006)
        for i in range(len(burst)):
            env[start + i] += gain * burst[i]
    tail = exp_decay(n, 0.075)
    for i in range(n):
        env[i] += 0.28 * tail[i]
    return normalise(apply_env(src, env))


def tom(f_start, f_end, t):
    """Buttons 6 and 7. Toms are pitched drums: a much smaller pitch drop
    than a kick (the head is tuned, not slack) over a longer decay. Mild
    noise for the stick attack only."""
    n = seconds(t)
    body = apply_env(pitch_sweep(n, f_start, f_end, 0.05), exp_decay(n, t / 3.0))
    stick = apply_env(highpass(noise(n), 2000), exp_decay(n, 0.006))
    return normalise(mix(body, [0.18 * v for v in stick]))


def crash():
    """Button 8. Long, bright, and the biggest file in the kit by far --
    1.1 s is 26 KB, which is why main.py loads the bank largest-first
    (see the LOADED BIGGEST FIRST note there).

    Band-passed rather than just high-passed: a real crash has a shimmer
    peak rather than rising forever toward Nyquist, and an unfiltered top
    end reads as hiss. Two decay rates summed, because the high partials
    of a cymbal die away faster than the low ones -- a single envelope
    makes it sound like a noise gate closing."""
    n = seconds(1.10)
    src = lowpass(highpass(noise(n), 2500), 5500)
    env = [0.65 * a + 0.35 * b
           for a, b in zip(exp_decay(n, 0.10), exp_decay(n, 0.55))]
    return normalise(apply_env(src, env))


KIT = (
    ("kick.wav",       kick),
    ("snare.wav",      snare),
    ("hat_closed.wav", lambda: hat(False)),
    ("hat_open.wav",   lambda: hat(True)),
    ("clap.wav",       clap),
    ("tom_low.wav",    lambda: tom(105, 78, 0.38)),
    ("tom_mid.wav",    lambda: tom(155, 115, 0.32)),
    ("crash.wav",      crash),
)


# ------------------------------------------------------------------
# Write + verify
# ------------------------------------------------------------------

def write_wav(path, samples):
    """Write 16-bit mono PCM. Clamped, not wrapped: a sample that
    overflows int16 and wraps becomes a full-scale sign flip, i.e. the
    loudest possible click, on a file that is supposed to be percussion."""
    frames = bytearray()
    for v in samples:
        s = int(v * AMP)
        if s > 32767:
            s = 32767
        elif s < -32768:
            s = -32768
        frames += struct.pack("<h", s)

    # Even byte count: main.py rejects an odd data chunk, since an odd
    # size cannot be a whole number of 16-bit samples.
    if len(frames) & 1:
        frames += b"\x00\x00"

    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(bytes(frames))
    return len(frames)


def verify(path):
    """Re-read the file with the SAME chunk-walking logic main.py uses,
    so a file that would fail at boot fails here on the laptop instead --
    where there is a keyboard and no audience."""
    with open(path, "rb") as f:
        header = f.read(12)
        if header[0:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise RuntimeError(path + ": not RIFF/WAVE")
        fmt_seen = False
        while True:
            chunk_id = f.read(4)
            if len(chunk_id) < 4:
                raise RuntimeError(path + ": no data chunk")
            chunk_size = int.from_bytes(f.read(4), "little")
            pad = chunk_size & 1
            if chunk_id == b"fmt ":
                fmt = f.read(chunk_size)
                afmt = int.from_bytes(fmt[0:2], "little")
                ch = int.from_bytes(fmt[2:4], "little")
                rate = int.from_bytes(fmt[4:8], "little")
                bits = int.from_bytes(fmt[14:16], "little")
                if afmt != 1 or bits != 16 or ch != 1:
                    raise RuntimeError(path + ": need 16-bit mono PCM")
                if rate != SAMPLE_RATE:
                    raise RuntimeError(path + ": rate %d != %d" % (rate, SAMPLE_RATE))
                fmt_seen = True
                if pad:
                    f.read(1)
            elif chunk_id == b"data":
                if chunk_size & 1:
                    raise RuntimeError(path + ": odd data chunk")
                raw = f.read(chunk_size)
                if len(raw) != chunk_size:
                    raise RuntimeError(path + ": data truncated")
                break
            else:
                f.read(chunk_size + pad)
    if not fmt_seen:
        raise RuntimeError(path + ": no fmt chunk before data")
    return chunk_size


if __name__ == "__main__":
    print("Generating drum kit at %d Hz\n" % SAMPLE_RATE)
    sizes = []
    for name, fn in KIT:
        n_bytes = write_wav(name, fn())
        checked = verify(name)
        assert checked == n_bytes, name
        sizes.append((n_bytes, name))
        print("  %-15s %6d bytes  %5.0f ms  OK"
              % (name, n_bytes, n_bytes / 2 / SAMPLE_RATE * 1000))

    total = sum(b for b, _ in sizes)
    largest = max(sizes)[0]

    # Mirrors main.py's preflight: loading a wav costs 2x its size for a
    # moment (raw bytes plus the array copy), so the bank fits iff
    # total + largest fits, and that peak lands on the largest file
    # because main.py loads biggest-first.
    print("\n  resident %d bytes | peak %d bytes during load"
          % (total, total + largest))
    print("  loop buffers at 4 s: %d bytes" % (4 * SAMPLE_RATE * 2 * 2))
    print("\nCopy all eight to the ROOT of the Pico, next to main.py.")