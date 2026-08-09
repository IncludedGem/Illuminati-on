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

def make_kick():
    duration_s = 0.32
    n = int(duration_s * SAMPLE_RATE)

    out = []

    phase = 0.0

    for i in range(n):
        t = i / SAMPLE_RATE

        # Fast pitch drop: ~170 Hz -> ~48 Hz
        freq = 48 + (170 - 48) * math.exp(-t * 28)

        phase += freq / SAMPLE_RATE

        # Main body
        body_env = math.exp(-t * 10)
        body = math.sin(2 * math.pi * phase) * body_env

        # Shorter sub component
        sub_env = math.exp(-t * 16)
        sub = math.sin(2 * math.pi * 52 * t) * sub_env * 0.35

        # Very short attack/click
        click_env = math.exp(-t * 180)
        click = (
            math.sin(2 * math.pi * 2500 * t)
            + 0.5 * math.sin(2 * math.pi * 4200 * t)
        ) * click_env * 0.12

        # Slight nonlinear saturation
        s = body * 0.85 + sub + click
        s = math.tanh(s * 1.8)

        out.append(s)

    return fade_tail(normalize(out))


# ---------------- Snare ----------------

def make_snare():
    duration_s = 0.24
    n = int(duration_s * SAMPLE_RATE)

    noise = white_noise(n, seed=2)
    noise_bright = one_pole_highpass(noise, 1400)

    out = []

    for i in range(n):
        t = i / SAMPLE_RATE

        # ---------------- Body ----------------
        # Two resonances make the drum feel larger/thicker.
        body_env = math.exp(-t * 15)

        body1 = math.sin(2 * math.pi * 185 * t)
        body2 = math.sin(2 * math.pi * 230 * t)

        body = (body1 * 0.65 + body2 * 0.35) * body_env

        # ---------------- Snare wires ----------------
        wire_env = math.exp(-t * 18)
        wires = noise_bright[i] * wire_env

        # ---------------- Initial crack ----------------
        crack_env = math.exp(-t * 140)

        crack = (
            noise[i] * 0.5
            + math.sin(2 * math.pi * 3200 * t) * 0.35
            + math.sin(2 * math.pi * 4800 * t) * 0.15
        ) * crack_env

        # ---------------- Combine ----------------
        s = (
            body * 0.65
            + wires * 0.75
            + crack * 0.45
        )

        # Mild saturation for density
        s = math.tanh(s * 1.7)

        out.append(s)

    return fade_tail(normalize(out))


# ---------------- Hi-hats ----------------

def make_hihat(duration_s, decay, cutoff, seed):
    n = int(duration_s * SAMPLE_RATE)

    noise = white_noise(n, seed=seed)
    bright = one_pole_highpass(noise, cutoff)
    very_bright = one_pole_highpass(noise, min(cutoff * 1.8, 7500))

    out = []

    for i in range(n):
        t = i / SAMPLE_RATE

        env = math.exp(-decay * (i / n))

        # Sharp metallic attack
        attack_env = math.exp(-t * 300)

        attack = (
            bright[i] * 0.8
            + very_bright[i] * 0.35
        ) * attack_env

        body = (
            bright[i] * 0.75
            + very_bright[i] * 0.25
        ) * env

        s = body + attack * 0.5

        # Slight saturation
        s = math.tanh(s * 1.4)

        out.append(s)

    return fade_tail(normalize(out))

# ---------------- Clap ----------------

def make_clap():
    duration_s = 0.20
    n = int(duration_s * SAMPLE_RATE)

    out = [0.0] * n

    # Several tightly spaced hand-clap bursts
    burst_starts = (0, 7, 14, 22)

    for k, start_ms in enumerate(burst_starts):
        start = int(start_ms / 1000 * SAMPLE_RATE)

        burst_len = int(0.010 * SAMPLE_RATE)

        noise = white_noise(burst_len, seed=50 + k)
        noise = one_pole_highpass(noise, 900)

        for j, sample in enumerate(noise):
            idx = start + j

            if idx < n:
                env = math.exp(-j / (burst_len * 0.30))
                out[idx] += sample * env * 0.8

    # Diffuse tail
    tail_start = int(0.025 * SAMPLE_RATE)

    tail = white_noise(n - tail_start, seed=80)
    tail = one_pole_highpass(tail, 1100)

    for j, sample in enumerate(tail):
        frac = j / len(tail)
        env = math.exp(-frac * 8)

        out[tail_start + j] += sample * env * 0.55

    # Saturation
    out = [math.tanh(x * 1.5) for x in out]

    return fade_tail(normalize(out))

# ---------------- Toms ----------------

def make_tom(f_start, f_end, duration_s, decay, seed):
    n = int(duration_s * SAMPLE_RATE)

    phase = 0.0
    out = []

    for i in range(n):
        t = i / SAMPLE_RATE
        frac = i / n

        # Nonlinear pitch drop
        freq = f_end + (f_start - f_end) * math.exp(-t * 12)

        phase += freq / SAMPLE_RATE

        # Main resonance
        env = math.exp(-decay * frac)

        fundamental = math.sin(2 * math.pi * phase)

        # Second harmonic gives the drum some character
        harmonic = math.sin(4 * math.pi * phase) * 0.22

        # Very short attack
        attack_env = math.exp(-t * 100)
        attack = math.sin(2 * math.pi * 900 * t) * attack_env * 0.08

        s = (
            fundamental * 0.9
            + harmonic
            + attack
        ) * env

        s = math.tanh(s * 1.5)

        out.append(s)

    return fade_tail(normalize(out))

# ---------------- Crash ----------------

def make_crash():
    duration_s = 0.50
    n = int(duration_s * SAMPLE_RATE)

    noise = white_noise(n, seed=99)

    bright = one_pole_highpass(noise, 3000)
    mid = one_pole_highpass(noise, 1200)

    out = []

    for i in range(n):
        t = i / SAMPLE_RATE
        frac = i / n

        # Fast initial attack
        attack_env = math.exp(-t * 35)

        # Long cymbal decay
        body_env = math.exp(-frac * 3.5)

        attack = bright[i] * attack_env
        body = (
            bright[i] * 0.7
            + mid[i] * 0.3
        ) * body_env

        s = attack * 0.7 + body

        # Gentle saturation
        s = math.tanh(s * 1.25)

        out.append(s)

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
os.makedirs("drums", exist_ok=True)

for name, samples in KIT.items():
    path = f"drums/{name}"
    write_wav(path, samples)
    print(f"{name:16s} {len(samples):6d} samples  {len(samples)*2:6d} bytes  {len(samples)/SAMPLE_RATE*1000:.0f}ms")