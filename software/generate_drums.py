"""
Drum kit sample generator -- HAcK 2026, Team 13
================================================
Run ONCE, off-device, to produce 8 short .wav files. These get shipped
as data files and loaded by main.py at boot (fast: no synthesis at
runtime), NOT regenerated on the Pico. See main.py's drum-kit section
for the loader and one-shot playback engine.

All at SAMPLE_RATE=16000, 16-bit mono PCM, matching main.py's I2S config
exactly -- a mismatch here would play back at the wrong pitch/speed.
"""

import math
import random
import struct
import wave

SAMPLE_RATE = 16000
AMP = 32000


def write_wav(path, samples):
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(struct.pack("<%dh" % len(samples), *samples))


def one_pole_lowpass(samples, cutoff_hz):
    k = 1.0 - math.exp(-2 * math.pi * cutoff_hz / SAMPLE_RATE)
    y = 0.0
    out = []
    for x in samples:
        y += (x - y) * k
        out.append(y)
    return out


def one_pole_highpass(samples, cutoff_hz):
    lp = one_pole_lowpass(samples, cutoff_hz)
    return [s - l for s, l in zip(samples, lp)]


def white_noise(n, seed):
    rng = random.Random(seed)
    return [(rng.random() * 2 - 1) for _ in range(n)]


def normalize(samples, target_peak=0.92):
    peak = max(abs(x) for x in samples) or 1.0
    scale = (AMP * target_peak) / peak
    return [int(x * scale) for x in samples]


def fade_tail(samples, fade_samples=32):
    """Force the last few samples to zero with a short linear fade, so a
    one-shot never ends on a nonzero sample -- that would click the
    instant playback stops or the sound gets re-triggered."""
    n = len(samples)
    fade_samples = min(fade_samples, n)
    out = list(samples)
    for i in range(fade_samples):
        frac = i / fade_samples
        out[n - fade_samples + i] = int(out[n - fade_samples + i] * (1.0 - frac))
    return out


# ---------------- Kick ----------------
# Pitched sine sweep, high to low, fast exponential decay -- the
# standard drum-machine kick mechanism. The pitch sweep is what reads as
# "thump" rather than just a low sine tone.
def make_kick():
    duration_s = 0.28
    n = int(duration_s * SAMPLE_RATE)
    f_start, f_end = 155.0, 48.0
    decay = 7.0
    phase = 0.0
    out = []
    for i in range(n):
        frac = i / n
        freq = f_start + (f_end - f_start) * frac
        phase += freq / SAMPLE_RATE
        env = math.exp(-decay * frac)
        # slight saturation (tanh-ish via clipping the sine sum) for punch
        s = math.sin(2 * math.pi * phase) * env
        out.append(s)
    return fade_tail(normalize(out))


# ---------------- Snare ----------------
# Noise (highpassed for "snap") blended with a low tone (the drum shell
# resonance), fast decay.
def make_snare():
    duration_s = 0.18
    n = int(duration_s * SAMPLE_RATE)
    noise = one_pole_highpass(white_noise(n, seed=2), 900)
    decay = 9.0
    out = []
    for i in range(n):
        frac = i / n
        env = math.exp(-decay * frac)
        tone = math.sin(2 * math.pi * 195 * i / SAMPLE_RATE) * 0.35
        out.append((noise[i] * 0.75 + tone * 0.25) * env)
    return fade_tail(normalize(out))


# ---------------- Hi-hats ----------------
# Highpassed noise. Closed = very short + tight; open = longer decay,
# slightly less aggressive highpass so it has a bit more body.
def make_hihat(duration_s, decay, cutoff, seed):
    n = int(duration_s * SAMPLE_RATE)
    noise = one_pole_highpass(white_noise(n, seed=seed), cutoff)
    out = []
    for i in range(n):
        frac = i / n
        env = math.exp(-decay * frac)
        out.append(noise[i] * env)
    return fade_tail(normalize(out))


# ---------------- Clap ----------------
# Real hand claps are 3-4 near-simultaneous noise bursts (a "flam"), not
# one clean transient, followed by a longer decay tail. This is what
# makes a synthesized clap read as a clap instead of a hiss.
def make_clap():
    duration_s = 0.14
    n = int(duration_s * SAMPLE_RATE)
    out = [0.0] * n
    burst_len = int(0.007 * SAMPLE_RATE)
    for k, start_ms in enumerate((0, 10, 20)):
        start = int(start_ms / 1000 * SAMPLE_RATE)
        burst = one_pole_highpass(white_noise(burst_len, seed=10 + k), 1000)
        for j, s in enumerate(burst):
            idx = start + j
            if idx < n:
                env = math.exp(-28 * (j / burst_len))
                out[idx] += s * env
    tail_start = int(0.028 * SAMPLE_RATE)
    tail = one_pole_highpass(white_noise(n - tail_start, seed=20), 1100)
    for j, s in enumerate(tail):
        frac = j / len(tail)
        env = math.exp(-9 * frac)
        out[tail_start + j] += s * env * 0.5
    return fade_tail(normalize(out))


# ---------------- Toms ----------------
# Same sine-sweep mechanism as the kick, higher pitched and with a
# longer, more resonant (slower) decay -- toms ring out more than kicks.
def make_tom(f_start, f_end, duration_s, decay, seed):
    n = int(duration_s * SAMPLE_RATE)
    phase = 0.0
    out = []
    for i in range(n):
        frac = i / n
        freq = f_start + (f_end - f_start) * frac
        phase += freq / SAMPLE_RATE
        env = math.exp(-decay * frac)
        out.append(math.sin(2 * math.pi * phase) * env)
    return fade_tail(normalize(out))


# ---------------- Crash ----------------
# Dense, bright, slow-decaying noise -- the least filtered, longest
# sound in the kit, matching a real crash cymbal's wash of overtones.
def make_crash():
    duration_s = 0.42
    n = int(duration_s * SAMPLE_RATE)
    noise = one_pole_highpass(white_noise(n, seed=99), 3500)
    decay = 3.2
    out = []
    for i in range(n):
        frac = i / n
        env = math.exp(-decay * frac)
        out.append(noise[i] * env)
    return fade_tail(normalize(out))


KIT = {
    "kick.wav":  make_kick(),
    "snare.wav": make_snare(),
    "hat_closed.wav": make_hihat(0.055, 26, 7000, seed=30),
    "hat_open.wav":   make_hihat(0.28, 5.5, 6000, seed=31),
    "clap.wav":  make_clap(),
    "tom_low.wav": make_tom(140, 90, 0.24, 4.5, seed=40),
    "tom_mid.wav": make_tom(210, 140, 0.20, 5.0, seed=41),
    "crash.wav": make_crash(),
}

import os
os.makedirs("/home/claude/work/drums/output", exist_ok=True)
for name, samples in KIT.items():
    path = f"/home/claude/work/drums/output/{name}"
    write_wav(path, samples)
    print(f"{name:16s} {len(samples):6d} samples  {len(samples)*2:6d} bytes  {len(samples)/SAMPLE_RATE*1000:.0f}ms")
