"""Key, octave, and scale/mode theory.

Pure functions and lookup tables -- no hardware, no shared mutable state
with the rest of the project. Melodic Minor is direction-sensitive (its
6th and 7th degrees differ ascending vs. descending); see
scale_step_for_degree() and build_scale_freqs().
"""

CHROMATIC_SCALE = (
    "C", "C#", "D", "D#", "E", "F",
    "F#", "G", "G#", "A", "A#", "B"
)

MIN_OCTAVE = 2
MAX_OCTAVE = 6

# Semitone offsets, including the octave on top, so the 8 buttons play
# root-to-root (e.g. C4..C5). Tuple values, not lists -- these are read
# constantly and never mutated.
#
# The 7 diatonic modes (Ionian..Locrian) plus harmonic minor. Each is a
# fixed rotation/alteration of the same 8-degree shape.
MODE_STEPS = {
    "Major":          (0, 2, 4, 5, 7, 9, 11, 12),   # Ionian
    "Dorian":         (0, 2, 3, 5, 7, 9, 10, 12),
    "Phrygian":       (0, 1, 3, 5, 7, 8, 10, 12),
    "Lydian":         (0, 2, 4, 6, 7, 9, 11, 12),
    "Mixolydian":     (0, 2, 4, 5, 7, 9, 10, 12),
    "Natural Minor":  (0, 2, 3, 5, 7, 8, 10, 12),   # Aeolian
    "Locrian":        (0, 1, 3, 5, 6, 8, 10, 12),
    "Harmonic Minor": (0, 2, 3, 5, 7, 8, 11, 12),
}

# Real melodic minor is not one fixed scale -- the 6th and 7th degrees
# depend on melodic direction:
#   ascending:  raised 6th & 7th (strong leading tone into the octave)
#   descending: natural 6th & 7th (same as natural minor)
# Handled separately from MODE_STEPS for that reason; see
# scale_step_for_degree() and the MELODIC MINOR note in main.py's
# module docstring.
MELODIC_MINOR_ASCENDING_STEPS = (0, 2, 3, 5, 7, 9, 11, 12)
MELODIC_MINOR_DESCENDING_STEPS = MODE_STEPS["Natural Minor"]

# Cycling order for keypad key '6'. Tuple, not a dict -- MicroPython
# does not preserve dict insertion order, and deriving cycle order from
# one would scramble it and make the mode key unrehearsable on stage.
MODE_LIST = (
    "Major",
    "Natural Minor",
    "Melodic Minor",
    "Harmonic Minor",
    "Dorian",
    "Phrygian",
    "Lydian",
    "Mixolydian",
    "Locrian",
)

# OLED labels ARE shown (see displayState()'s "Mode: " prefix), but the
# mode VALUE still needs to be short: "Mode: Harmonic Minor" is 160px,
# wider than the 128px screen, so the full mode name literally cannot
# render even with the whole row to itself. 8 chars is the practical
# ceiling for the value at 8px-wide default font, once "Mode: " (6 chars)
# is accounted for.
MODE_DISPLAY_LABEL = {
    "Major":          "Major",
    "Natural Minor":  "NatMin",
    "Melodic Minor":  "MelMin",
    "Harmonic Minor": "HarMin",
    "Dorian":         "Dorian",
    "Phrygian":       "Phryg",
    "Lydian":         "Lydian",
    "Mixolydian":     "Mixo",
    "Locrian":        "Locr",
}


def shift_key(current_key, step):
    """Transpose by `step` half steps, wrapping around the octave."""
    idx = CHROMATIC_SCALE.index(current_key)
    return CHROMATIC_SCALE[(idx + step) % 12]


def shift_octave(current_octave, step):
    """Shift octave by `step`, clamped (no wrap -- wrapping mid-song
    would jump the instrument three octaves on a single keypress)."""
    return max(MIN_OCTAVE, min(MAX_OCTAVE, current_octave + step))


def shift_mode(current_mode, step):
    """Step through MODE_LIST, wrapping. `step` is always +-1 from the
    keypad today, but this takes a step count (not just "next") to
    match shift_key/shift_octave/shift_sample's shape."""
    idx = MODE_LIST.index(current_mode)
    return MODE_LIST[(idx + step) % len(MODE_LIST)]


def scale_step_for_degree(mode, degree_index, ascending):
    """Which semitone step to use for one button/degree (0-7),
    accounting for melodic minor's direction-dependent 6th/7th."""
    if mode == "Melodic Minor":
        return (MELODIC_MINOR_ASCENDING_STEPS if ascending
                else MELODIC_MINOR_DESCENDING_STEPS)[degree_index]
    return MODE_STEPS.get(mode, MODE_STEPS["Major"])[degree_index]


def build_scale_freqs(key, octave, mode, degree_index, ascending):
    """One frequency (Hz) for a single note button, in `key`/`octave`,
    using `mode` (direction-aware for Melodic Minor via `ascending`).
    Equal temperament, A4 = 440 Hz.

    Takes ONE degree, not all 8 -- Melodic Minor needs a per-note
    ascending/descending direction that can differ button to button
    within the same keypress batch, so the note-on loop calls this once
    per newly-pressed button instead of computing the whole scale up
    front."""
    root_idx = CHROMATIC_SCALE.index(key)
    step = scale_step_for_degree(mode, degree_index, ascending)
    total = root_idx + step
    note_octave = octave + total // 12
    note_idx = total % 12
    semitones_from_a4 = (note_octave - 4) * 12 + (note_idx - 9)
    return 440.0 * (2 ** (semitones_from_a4 / 12))
