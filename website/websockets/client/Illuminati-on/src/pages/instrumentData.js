// Ported directly from the Pico firmware's instruments.py, so the
// visualizer's waveform shapes and press/release timing match the real
// synth rather than approximating it. Keep these two files in sync if
// the instrument table on the Pico ever changes.
//
// partials: [(harmonic multiple, amplitude), ...] -- same numbers as
// the *_P tuples and the physics comments that justify them in
// instruments.py.
// envelope: {attack, decay, sustain, release} in ms/0-1, same order as
// ENVELOPES's (attack_ms, decay_ms, sustain_level, release_ms) tuples.

function harmonicPartials(pairs, norm) {
  const n = norm ?? pairs.reduce((sum, [, amp]) => sum + amp, 0)
  return { partials: pairs, norm: n }
}

const ORGAN_P = [[1, 1.0], [2, 0.75], [3, 0.5], [4, 0.4], [5, 0.25], [6, 0.2], [8, 0.15], [9, 0.1], [10, 0.08], [12, 0.05]]
const BELL_P = [[1, 1.0], [2.71, 0.55], [4.07, 0.32], [5.83, 0.22], [7.91, 0.12]]
const PLUCK_P = [[1, 1.0], [2, 0.92], [3, 0.81], [4, 0.65], [5, 0.48], [6, 0.31]]
const GUITAR_P = [[1, 1.0], [2, 0.81], [3, 0.54], [4, 0.25], [6, 0.17]]
const PIANO_P = [[1, 1.0], [2.01, 0.62], [3.02, 0.38], [4.04, 0.24], [5.08, 0.16], [6.13, 0.1], [7.2, 0.06]]
const BASS_P = [[1, 1.0], [2, 0.48], [3, 0.22], [4, 0.1]]
const FLUTE_P = [[1, 1.0], [2, 0.15], [3, 0.07], [4, 0.03], [5, 0.015], [6, 0.008]]
const CLARINET_P = [[1, 1.0], [2, 0.08], [3, 0.55], [4, 0.1], [5, 0.3], [7, 0.14], [9, 0.08]]
const TRUMPET_P = [[1, 1.0], [2, 0.85], [3, 0.72], [4, 0.58], [5, 0.46], [6, 0.36], [7, 0.27], [8, 0.2], [9, 0.14], [10, 0.09]]
const STRINGS_P = [[1, 1.0], [2, 0.72], [3, 0.5], [4, 0.35], [5, 0.24], [6, 0.16], [7, 0.1], [8, 0.06]]

// Non-harmonic (geometric) waveform shapes, sampled the same way
// make_table() does on the Pico: fn(t) for t in [0,1).
const WAVE_FN = {
  square: (t) => (t < 0.5 ? 1 : -1),
  saw: (t) => 2 * t - 1,
  triangle: (t) => 4 * Math.abs(t - 0.5) - 1,
  pulse25: (t) => (t < 0.25 ? 1 : -1),
}

function sampleWave(fn, n = 128) {
  const out = new Float32Array(n)
  for (let i = 0; i < n; i++) out[i] = fn(i / n)
  return out
}

function sampleHarmonic({ partials, norm }, n = 128) {
  const out = new Float32Array(n)
  for (let i = 0; i < n; i++) {
    const t = i / n
    let v = 0
    for (const [mult, amp] of partials) {
      v += amp * Math.sin(2 * Math.PI * mult * t)
    }
    out[i] = v / norm
  }
  return out
}

// envelope tuple order matches main.py: (attack_ms, decay_ms, sustain, release_ms)
function env(attack, decay, sustain, release) {
  return { attack, decay, sustain, release }
}

const RAW_INSTRUMENTS = {
  Sine: { wave: () => sampleHarmonic(harmonicPartials([[1, 1.0]])), envelope: env(10, 80, 0.85, 150) },
  Square: { wave: () => sampleWave(WAVE_FN.square), envelope: env(5, 60, 0.8, 100) },
  Sawtooth: { wave: () => sampleWave(WAVE_FN.saw), envelope: env(15, 120, 0.75, 200) },
  Triangle: { wave: () => sampleWave(WAVE_FN.triangle), envelope: env(10, 100, 0.85, 180) },
  Pulse: { wave: () => sampleWave(WAVE_FN.pulse25), envelope: env(5, 60, 0.75, 120) },

  Organ: { wave: () => sampleHarmonic(harmonicPartials(ORGAN_P)), envelope: env(12, 40, 0.97, 400) },
  Bell: { wave: () => sampleHarmonic(harmonicPartials(BELL_P)), envelope: env(1, 400, 0.22, 1900) },
  Pluck: { wave: () => sampleHarmonic(harmonicPartials(PLUCK_P)), envelope: env(2, 200, 0.09, 90) },
  // norm=3.15 is deliberate (hand-trimmed), not the raw amplitude sum --
  // see the comment in instruments.py.
  Piano: { wave: () => sampleHarmonic(harmonicPartials(PIANO_P, 3.15)), envelope: env(2, 450, 0.12, 450) },
  Guitar: { wave: () => sampleHarmonic(harmonicPartials(GUITAR_P)), envelope: env(3, 100, 0.25, 800) },
  Bass: { wave: () => sampleHarmonic(harmonicPartials(BASS_P)), envelope: env(3, 190, 0.15, 90) },
  Flute: { wave: () => sampleHarmonic(harmonicPartials(FLUTE_P)), envelope: env(90, 100, 0.92, 240) },
  Clarinet: { wave: () => sampleHarmonic(harmonicPartials(CLARINET_P)), envelope: env(20, 45, 0.94, 150) },
  Trumpet: { wave: () => sampleHarmonic(harmonicPartials(TRUMPET_P)), envelope: env(25, 50, 0.85, 160) },
  Strings: { wave: () => sampleHarmonic(harmonicPartials(STRINGS_P)), envelope: env(140, 100, 0.92, 550) },

  // Drums have no tuned waveform/envelope on the Pico (one-shot sample
  // playback, see main.py's DRUM KIT section) -- give the visualizer a
  // plausible percussive shape rather than crashing on a lookup miss.
  Drums: { wave: () => sampleWave(WAVE_FN.pulse25), envelope: env(1, 30, 0.0, 80) },
}

// Precompute each instrument's sampled waveform once (the harmonic sums
// don't change at runtime) and expose {samples, attack, decay, sustain,
// release} per name, keyed exactly like WAVETABLES/ENVELOPES on the Pico.
export const INSTRUMENTS = Object.fromEntries(
  Object.entries(RAW_INSTRUMENTS).map(([name, { wave, envelope }]) => [
    name,
    { name, samples: wave(), ...envelope },
  ])
)

// Warm/cool split for the 9 real modes (see MODE_LIST in scale.py),
// used to bias the background's hue rotation -- major-family modes
// (Major, Lydian, Mixolydian) lean warm, minor-family modes lean cool,
// consistent with how those modes are generally heard.
export const MODE_COLOR = {
  Major: { rgb: "251,146,60", text: "#fb923c" },       // warm orange
  Lydian: { rgb: "250,204,21", text: "#facc15" },      // bright yellow
  Mixolydian: { rgb: "251,113,133", text: "#fb7185" }, // warm pink
  "Natural Minor": { rgb: "96,165,250", text: "#60a5fa" },  // cool blue
  "Melodic Minor": { rgb: "129,140,248", text: "#818cf8" }, // indigo
  "Harmonic Minor": { rgb: "192,132,252", text: "#c084fc" }, // violet
  Dorian: { rgb: "45,212,191", text: "#2dd4bf" },      // teal
  Phrygian: { rgb: "232,121,249", text: "#e879f9" },   // magenta
  Locrian: { rgb: "148,163,184", text: "#94a3b8" },    // slate (unstable mode, desaturated)
}
