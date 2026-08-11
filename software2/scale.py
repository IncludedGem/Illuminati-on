"""Key, octave, and scale/mode theory. Pure functions, no hardware."""

CHROMATIC_SCALE = (
    "C", "C#", "D", "D#", "E", "F",
    "F#", "G", "G#", "A", "A#", "B"
)

MIN_OCTAVE = 2
MAX_OCTAVE = 6

# Semitone offsets including the octave on top, so the 8 buttons play
# root to root (C4..C5). The 7 diatonic modes plus harmonic minor.
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

# Melodic minor is direction-sensitive: raised 6th and 7th ascending,
# natural descending. Handled separately from MODE_STEPS for that reason.
MELODIC_MINOR_ASCENDING_STEPS = (0, 2, 3, 5, 7, 9, 11, 12)
MELODIC_MINOR_DESCENDING_STEPS = MODE_STEPS["Natural Minor"]

# Cycling order for keypad key 6. A tuple, not a dict -- MicroPython does
# not preserve dict insertion order, which would scramble the cycle.
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

# Abbreviated for the OLED: "Mode: Harmonic Minor" is 160px on a 128px
# screen, so the value has a practical ceiling of 8 characters.
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
    """Shift octave, clamped -- wrapping would jump three octaves mid-song."""
    return max(MIN_OCTAVE, min(MAX_OCTAVE, current_octave + step))


def shift_mode(current_mode, step):
    """Step through MODE_LIST, wrapping."""
    idx = MODE_LIST.index(current_mode)
    return MODE_LIST[(idx + step) % len(MODE_LIST)]


def scale_step_for_degree(mode, degree_index, ascending):
    """Semitone step for one button/degree (0-7), handling melodic minor's
    direction-dependent 6th and 7th."""
    if mode == "Melodic Minor":
        return (MELODIC_MINOR_ASCENDING_STEPS if ascending
                else MELODIC_MINOR_DESCENDING_STEPS)[degree_index]
    return MODE_STEPS.get(mode, MODE_STEPS["Major"])[degree_index]


def build_scale_freqs(key, octave, mode, degree_index, ascending):
    """Frequency (Hz) for a single note button. Equal temperament, A4=440.

    Takes one degree rather than all 8, because melodic minor's direction
    can differ button to button within the same keypress batch.
    """
    root_idx = CHROMATIC_SCALE.index(key)
    step = scale_step_for_degree(mode, degree_index, ascending)
    total = root_idx + step
    note_octave = octave + total // 12
    note_idx = total % 12
    semitones_from_a4 = (note_octave - 4) * 12 + (note_idx - 9)
    return 440.0 * (2 ** (semitones_from_a4 / 12))