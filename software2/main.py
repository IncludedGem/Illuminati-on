"""
Pico 2 W music controller -- HAcK 2026, Team 13.

Signal chain:
    voices -> mix -> lowpass -> [loop tap] -> master volume -> clip -> I2S

Pins:
    Keypad rows    GP0, GP1, GP2, GP3    (high-Z except while scanning)
    Keypad cols    GP6, GP7, GP19        (internal pull-ups)
    Note buttons   GP15, 14, 13, 12, 11, 10, 9, 8   (active-low, pull-ups)
    Volume pot     GP26 (ADC0)
    Cutoff pot     GP27 (ADC1)
    OLED I2C       SDA=GP4, SCL=GP5
    I2S DAC        sck=GP16, ws=GP17, sd=GP18

Keypad:
    1 loop reset    2 (unmapped)    3 cycle preset
    4 play/pause    5 loop record   6 cycle mode
    7 sample -      8 octave -      9 key -
    * sample +      0 octave +      # key +

Serial: every state change prints '#' + JSON, coalesced to one line per
JSON_MIN_INTERVAL_MS. The website reads only lines starting with '#'.
"""

import gc
import os
import sys
import time
import json
from array import array
from micropython import const
from machine import Pin, I2C, ADC, I2S
import loop
from keypad import scan_keypad
from scale import shift_key, shift_octave, shift_mode, build_scale_freqs

DEBUG = False


# ---------------- Non-blocking serial ----------------
# print() to USB CDC blocks when the host holds the port open but does not
# drain it (Thonny does exactly this), stalling the audio loop. poll(0)
# checks for TX room and returns immediately either way.

try:
    import uselect
    _tx_poll = uselect.poll()
    _tx_poll.register(sys.stdout, uselect.POLLOUT)
    _TX_POLLABLE = True
except (ImportError, OSError, ValueError, AttributeError):
    _tx_poll = None
    _TX_POLLABLE = False

TX_SKIP_LIMIT = 32      # write anyway after this many skips, in case poll lies
tx_skips = 0


def serial_ready():
    """True if a line can be written without blocking."""
    if not _TX_POLLABLE:
        return True
    if _tx_poll.poll(0):
        return True
    return tx_skips >= TX_SKIP_LIMIT


# ---------------- Audio constants + looper ----------------
# The loop buffers are the largest allocation in the project and must be
# contiguous, so the Looper is built while the heap is still one clean
# block, before wavetables/OLED/I2S carve it up. Do not move this down.

SAMPLE_RATE = 12000     # 21.3 ms per block; Nyquist 6000 Hz
BUF_SAMPLES = const(256)

# Latency is (1 + IBUF_BLOCKS) * BUF_SAMPLES / SAMPLE_RATE = 85 ms.
# Raise to 4 if you hear clicks, drop to 2 for less latency.
IBUF_BLOCKS = 3

LOOP_SECONDS = 4        # 188 KB for the two int16 buffers at this rate

looper = loop.Looper(SAMPLE_RATE, BUF_SAMPLES, seconds=LOOP_SECONDS)

# Must be imported after the Looper -- this builds the wavetable bank.
from instruments import (TABLE_LEN, WAVETABLES, ENVELOPES, DRUM_KIT_NAME,
                         shift_sample, pick_table, bank_bytes)
import cutoff


# ---------------- Note buttons ----------------

BUTTON_PINS = (15, 14, 13, 12, 11, 10, 9, 8)
buttons = [Pin(p, Pin.IN, Pin.PULL_UP) for p in BUTTON_PINS]
NUM_VOICES = len(BUTTON_PINS)

# bytearrays rather than lists, so the main loop never allocates.
key_bits = bytearray(8)
prev_key_bits = bytearray(8)

# Previous note's button index, for melodic minor's direction-dependent
# 6th/7th. None means "no direction yet", treated as ascending. Reset on
# any edit that changes what a button index means.
last_degree_index = None


# ---------------- Drum kit ----------------
# One-shot sample playback: no pitch, no sustain, plays out regardless of
# button state. Button index is the drum index, ignoring key/octave/mode.
# Samples are precomputed by generate_drums.py and loaded as .wav files.

DRUM_FILES = (
    "kick.wav", "snare.wav", "hat_closed.wav", "hat_open.wav",
    "clap.wav", "tom_low.wav", "tom_mid.wav", "crash.wav",
)

# Q10 gain per drum, applied at mix time. The wav bank is already near full
# scale (-1.4 dBFS peak on every file except the kick), so the problem was
# never the source level -- it is that a drum is a transient. Measured RMS:
# clap 2510, crash 3457, snare 4350, against a sustained note's ~20000. At
# equal PEAK a transient reads far quieter, so the short bright hits get
# lifted and the long crash gets pulled down. Tune by ear; the limiter
# downstream will catch anything that gets greedy.
DRUM_GAIN = (1300, 1150, 1024, 1024, 1400, 1150, 1150, 900)

DRUM_KIT_DIR = "/"      # absolute, so it resolves the same however main.py is run


def _load_wav_samples(path):
    """Read a 16-bit mono PCM .wav into an array('h').

    Walks chunks to find 'data' rather than assuming a 44-byte header.
    The fmt chunk is validated because playback does no rate conversion:
    a 44.1 kHz or stereo file would not error, just play wrong.
    """
    with open(path, "rb") as f:
        header = f.read(12)
        if header[0:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise RuntimeError(path + ": not a RIFF/WAVE file")

        raw = None
        fmt_seen = False

        while True:
            chunk_id = f.read(4)
            if len(chunk_id) < 4:
                raise RuntimeError(path + ": no data chunk found")
            size_bytes = f.read(4)
            if len(size_bytes) < 4:
                raise RuntimeError(path + ": truncated chunk header")
            chunk_size = int.from_bytes(size_bytes, "little")
            pad = chunk_size & 1      # odd chunks are followed by a pad byte

            if chunk_id == b"fmt ":
                fmt = f.read(chunk_size)
                audio_format = int.from_bytes(fmt[0:2], "little")
                channels = int.from_bytes(fmt[2:4], "little")
                rate = int.from_bytes(fmt[4:8], "little")
                bits = int.from_bytes(fmt[14:16], "little")
                if audio_format != 1 or bits != 16 or channels != 1:
                    raise RuntimeError(
                        path + ": need 16-bit mono PCM, got format "
                        + str(audio_format) + "/" + str(bits) + "-bit/"
                        + str(channels) + "ch")
                if rate != SAMPLE_RATE:
                    raise RuntimeError(
                        path + ": sample rate " + str(rate) + " != "
                        + str(SAMPLE_RATE) + " (no resampling on the Pico)")
                fmt_seen = True
                if pad:
                    f.read(1)

            elif chunk_id == b"data":
                if chunk_size & 1:
                    raise RuntimeError(
                        path + ": data chunk size not a multiple of 2 bytes")
                raw = f.read(chunk_size)
                if len(raw) != chunk_size:
                    raise RuntimeError(
                        path + ": data chunk truncated (" + str(len(raw))
                        + " of " + str(chunk_size) + " bytes)")
                break

            else:
                f.read(chunk_size + pad)

    if not fmt_seen:
        raise RuntimeError(path + ": no fmt chunk before data")

    return array("h", raw)


# Loading a wav costs 2x its size momentarily (raw bytes + array copy), so
# the bank fits iff total + largest fits. Sizes are checked before anything
# is allocated, and files are loaded biggest-first so the transient peak
# lands on a clean heap rather than one seven samples have carved up.

_sizes = []
for _i in range(len(DRUM_FILES)):
    _path = DRUM_KIT_DIR + DRUM_FILES[_i]
    try:
        _sizes.append((os.stat(_path)[6], _i))
    except OSError as e:
        raise OSError("cannot open " + _path + " (errno " + str(e.args[0])
                      + ") -- is " + DRUM_KIT_DIR
                      + " a directory with all 8 wavs in it?")

_sizes.sort()
_sizes.reverse()

gc.collect()
_need = 0
for _sz, _i in _sizes:
    _need += _sz
_need += _sizes[0][0]
_free = gc.mem_free()

if _need > _free:
    # 0.5 s of LOOP_SECONDS is 24000 bytes at this rate.
    _short = _need - _free
    raise MemoryError(
        "drum bank needs ~" + str(_need) + " bytes (" + str(_need - _sizes[0][0])
        + " resident + " + str(_sizes[0][0]) + " peak on "
        + DRUM_FILES[_sizes[0][1]] + "), only " + str(_free)
        + " free -- short by " + str(_short) + ". Trim the longest samples, or"
        + " drop LOOP_SECONDS by " + str((_short + 23999) // 24000 * 0.5) + " s")

_slots = [None] * len(DRUM_FILES)
for _sz, _i in _sizes:
    _path = DRUM_KIT_DIR + DRUM_FILES[_i]
    try:
        _slots[_i] = _load_wav_samples(_path)
    except OSError as e:
        raise OSError("cannot open " + _path + " (errno " + str(e.args[0])
                      + ") -- is " + DRUM_KIT_DIR
                      + " a directory with all 8 wavs in it?")
    except MemoryError:
        raise MemoryError(
            "no contiguous run for " + _path + " (" + str(_sz) + " bytes, "
            + "needs " + str(2 * _sz) + " to load) -- free=" + str(gc.mem_free())
            + "; heap is fragmented, lower LOOP_SECONDS")

DRUM_SAMPLES = tuple(_slots)      # back in button order: 0=Kick .. 7=Crash
_sizes = None
_slots = None
_path = None
gc.collect()


class DrumVoice:
    """One-shot sample playback. Goes idle when the sample runs out."""
    __slots__ = ("active", "sample", "pos", "gain")

    def __init__(self):
        self.active = False
        self.sample = None
        self.pos = 0
        self.gain = 1024

    def note_on(self, drum_index):
        """Retriggering a sounding drum restarts it rather than layering."""
        self.sample = DRUM_SAMPLES[drum_index]
        self.pos = 0
        self.gain = DRUM_GAIN[drum_index]
        self.active = True

    def note_off(self):
        # No-op by design: a drum plays out however long the button is held.
        # Present so the main loop needs no drum-specific branch on release.
        pass


drum_voices = [DrumVoice() for _ in range(NUM_VOICES)]


@micropython.native
def render_drum_voice(v, mix_buf, n_samples):
    """Mix one drum voice into mix_buf. No phase, envelope or interpolation
    -- these are recordings already at SAMPLE_RATE.

    The Q10 gain is the only processing: the samples arrive near full scale
    already, but a drum is a transient and a transient at the same PEAK as
    a sustained note is much quieter to the ear. See DRUM_GAIN.
    """
    sample = v.sample
    pos = v.pos
    gain = v.gain
    sample_len = len(sample)

    n = 0
    while n < n_samples and pos < sample_len:
        mix_buf[n] += (sample[pos] * gain) >> 10
        pos += 1
        n += 1

    v.pos = pos
    if pos >= sample_len:
        v.active = False


# ---------------- Presets and reported state ----------------
# A preset stores only what the keypad edits. Volume and cutoff are
# deliberately excluded -- they are physical pots, and the knob always wins.

PRESET_FIELDS = ("octave", "key", "sample", "mode")

presets = [
    {"octave": 4, "key": "C", "sample": "Sine", "mode": "Major"},
    {"octave": 5, "key": "F", "sample": "Sawtooth", "mode": "Major"},
]

active_preset = 0

state = {
    "preset": 1,
    "octave": 4,
    "key": "C",
    "sample": "Sine",
    "mode": "Major",
    "volume": 75,
    "cutoff": 100,          # 100 = filter fully open
    "keys": [False] * 8,
    "loop": "empty",
    "loop_pos": 0,
}


def sync_state():
    """Mirror the active preset into the reported state. Loops over
    PRESET_FIELDS so a new field cannot silently miss its sync line."""
    p = presets[active_preset]
    state["preset"] = active_preset + 1
    for field in PRESET_FIELDS:
        state[field] = p[field]


# ---------------- Pots (self-calibrating) ----------------

POT_VOLUME = const(0)
POT_CUTOFF = const(1)

adc_channels = (ADC(26), ADC(27))

# Learned endpoints, one entry per channel. Pots rarely swing the full
# 0-65535, so hardcoding the ends gives "halfway already reads 100".
adc_min = [65535] * len(adc_channels)
adc_max = [0] * len(adc_channels)

MIN_ADC_SPAN = 8000     # below this the "range" is just ADC noise

# Reported before a channel has been swept. Cutoff defaults to fully open:
# guessing from the raw reading put the filter at ~569 Hz on an untouched
# pot, which is muffled and quiet with nothing obviously wrong on screen.
UNCAL_DEFAULT = (75, 100)

VOLUME_DEADBAND = 2
CUTOFF_DEADBAND = 2

previous_volume = -1
previous_cutoff = -1


def read_pot(ch, uncal=None):
    """Return 0-100 for ADC channel `ch` using its own learned calibration.
    Averages 4 reads so a single noise spike cannot widen the range
    permanently.

    `uncal` overrides what is reported before the channel has been swept
    far enough to trust. The call site for cutoff passes 100 so an unwired
    or untouched knob leaves the filter open rather than muffling the set.
    (This parameter was being passed already but did not exist -- the
    cutoff read raised TypeError on the first pass of the main loop.)
    """
    a = adc_channels[ch]
    raw = (a.read_u16() + a.read_u16() + a.read_u16() + a.read_u16()) >> 2

    if raw < adc_min[ch]:
        adc_min[ch] = raw
    if raw > adc_max[ch]:
        adc_max[ch] = raw

    span = adc_max[ch] - adc_min[ch]
    if span < MIN_ADC_SPAN:
        return UNCAL_DEFAULT[ch] if uncal is None else uncal

    pct = round((raw - adc_min[ch]) / span * 100)
    return max(0, min(100, pct))


def pot_calibrated(ch):
    """Whether this channel has seen enough travel to be trusted. The
    startup banner reports it, because "the cutoff knob does nothing" and
    "the cutoff knob has not been swept yet" look identical on stage."""
    return (adc_max[ch] - adc_min[ch]) >= MIN_ADC_SPAN


# ---------------- OLED ----------------

import display as oled
i2c = I2C(0, sda=Pin(4), scl=Pin(5), freq=400000)
oled.init(i2c)


# ---------------- I2S output ----------------

audio = I2S(
    0,
    sck=Pin(16),
    ws=Pin(17),
    sd=Pin(18),
    mode=I2S.TX,
    bits=16,
    format=I2S.MONO,
    rate=SAMPLE_RATE,
    ibuf=BUF_SAMPLES * 2 * IBUF_BLOCKS,
)


# ---------------- Synth voices ----------------

_IDLE = const(0)
_ATTACK = const(1)
_DECAY = const(2)
_SUSTAIN = const(3)
_RELEASE = const(4)

# Fixed-point scales for render_voice. Everything in the inner loop is
# integer: MicroPython boxes floats on the heap, so float arithmetic there
# allocates on every sample. All products stay inside the 31-bit small-int
# range (+-1.07e9) so nothing promotes to a heap-allocated big int.
#
#   phase  Q16.16
#   frac   Q8, not Q16 -- (s1-s0)*65535 would reach 4.2e9 and overflow
#   level  Q23 accumulator, used at Q15 for the multiply. Q15 alone is too
#          coarse to accumulate: Bell's 1900 ms release would round to 600 ms.
_PHASE_BITS = const(16)
# Literal, not const(TABLE_LEN << 16): const() folds at compile time and its
# argument must be a literal or a const from this module, but TABLE_LEN is
# imported. The assert keeps the two in step.
_PHASE_WRAP = const(16777216)
assert TABLE_LEN == 256, "_PHASE_WRAP hardcodes TABLE_LEN=256"

_FRAC_BITS = const(8)
_FRAC_MASK = const(255)

_LVL_ONE = const(8388608)         # 1 << 23
_LVL_TO_Q15 = const(8)


class Voice:
    __slots__ = (
        "active", "table", "phase", "phase_inc", "stage", "level",
        "attack_step", "decay_step", "sustain_level", "release_step",
        "sustain_decay_step",
    )

    def __init__(self):
        self.active = False
        self.table = None
        self.phase = 0
        self.phase_inc = 0
        self.stage = _IDLE
        self.level = 0
        self.attack_step = 0
        self.decay_step = 0
        self.sustain_level = 0
        self.release_step = 0
        self.sustain_decay_step = 0

    def note_on(self, freq, instrument):
        # Float maths is fine here -- this runs once per keypress, and
        # everything stored is an integer.
        #
        # The table is chosen by FREQUENCY, not by instrument alone: each
        # instrument has several band-limited versions and this picks the
        # richest one that will not alias at this pitch. See instruments.py.
        self.table = pick_table(instrument, freq)
        self.phase = 0
        self.phase_inc = int(freq * TABLE_LEN * 65536 / SAMPLE_RATE)

        a_ms, d_ms, s_lvl, r_ms, sd_ms = ENVELOPES.get(instrument,
                                                       ENVELOPES["Sine"])
        a_samples = max(1, int(a_ms * SAMPLE_RATE / 1000))
        d_samples = max(1, int(d_ms * SAMPLE_RATE / 1000))
        r_samples = max(1, int(r_ms * SAMPLE_RATE / 1000))

        s_level = int(s_lvl * _LVL_ONE)

        # max(1, ...) everywhere: a step of 0 is a note that never leaves
        # its stage, i.e. stuck on forever.
        self.attack_step = max(1, _LVL_ONE // a_samples)
        self.decay_step = max(1, (_LVL_ONE - s_level) // d_samples)
        self.sustain_level = s_level
        self.release_step = max(1, s_level // r_samples)

        # 0 ms means hold forever (organ, flute, strings). Anything else is
        # a plucked or struck instrument, which keeps decaying while held
        # instead of parking at a sustain floor.
        if sd_ms:
            sd_samples = max(1, int(sd_ms * SAMPLE_RATE / 1000))
            self.sustain_decay_step = max(1, s_level // sd_samples)
        else:
            self.sustain_decay_step = 0

        self.stage = _ATTACK
        self.level = 0
        self.active = True

    def note_off(self):
        if self.active and self.stage != _RELEASE:
            self.stage = _RELEASE


voices = [Voice() for _ in range(NUM_VOICES)]

# int32 accumulator so all voices sum before a single clip at the end.
# Everything preallocated: no allocation in the audio path.
mix_buf = array("l", [0] * BUF_SAMPLES)
out_buf = array("h", [0] * BUF_SAMPLES)
zero_buf = array("l", [0] * BUF_SAMPLES)      # for a C-level clear of mix_buf
silence_buf = array("h", [0] * BUF_SAMPLES)   # written directly when idle

# ---------------- Master volume ----------------
# VOL_TABLE was referenced by generate_block but never defined, which is a
# NameError on the first block -- no audio at all. Restored here, with a
# gentler curve than the square law the comment described.
#
# Square law is the textbook approximation of perceived loudness, but it is
# brutal at the top of a pot that self-calibrates: the knob only reads 100
# once it has been swept to its true mechanical end, and the default before
# that is 75, which under a square law is 0.56 -- 5 dB down before anything
# else in the chain. x^1.6 keeps the taper musical while putting unity at
# the top instead of the old 0.83.

VOL_CURVE = 1.6


def _vol_q(pct):
    """Knob percent to Q10 master gain. 1024 == unity."""
    if pct <= 0:
        return 0
    return int(round(((pct / 100.0) ** VOL_CURVE) * 1024))


VOL_TABLE = tuple(_vol_q(p) for p in range(101))


# ---------------- Output limiter ----------------
# The old stage hard-clipped at +-32000. With eight voices summing to
# +-256000 that meant a three-note chord was squared off into a buzz, while
# a single sustained note sat far below full scale -- quiet AND garbled at
# the same time, from the same missing gain stage.
#
# Soft knee instead: linear up to LIMIT_KNEE, then compress everything
# above it into the remaining headroom. Single notes pass through
# untouched at full level; chords lean on the knee and stay recognisable.

# The knee sits just above instruments.TABLE_AMP (28000) on purpose: one
# voice at full envelope is below it, so a solo note passes through with
# unity gain and no distortion at all. Only chords reach the compressed
# region, which is exactly where compression belongs.
LIMIT_KNEE = 28500
LIMIT_CEIL = 32700          # true 16-bit full scale is 32767
LIMIT_MAX_IN = 260000       # 8 voices plus drums, i.e. the realistic worst case

# Q10 slope of the compressed region. Plain int, not const() -- const()
# folds at compile time and wants a literal, not a derived expression.
LIMIT_SLOPE = ((LIMIT_CEIL - LIMIT_KNEE) << 10) // (LIMIT_MAX_IN - LIMIT_KNEE)



@micropython.native
def render_voice(v, mix_buf, n_samples):
    """Add one voice to mix_buf, advancing phase and envelope.

    All per-sample state is pulled into locals first: attribute lookups are
    among the slowest operations in MicroPython and this runs 256 times per
    voice per block. Integer only -- see the fixed-point scales above.

    Linearly interpolates between adjacent table entries; notes step 10-70+
    entries per sample, so nearest-neighbour lookup sounds harsh.
    """
    table = v.table
    phase = v.phase
    phase_inc = v.phase_inc
    stage = v.stage
    level = v.level
    attack_step = v.attack_step
    decay_step = v.decay_step
    sustain_level = v.sustain_level
    release_step = v.release_step
    sustain_decay_step = v.sustain_decay_step

    n = 0
    while n < n_samples:
        idx0 = phase >> _PHASE_BITS
        s0 = table[idx0]
        idx1 = idx0 + 1
        if idx1 >= TABLE_LEN:
            idx1 = 0
        raw = s0 + (((table[idx1] - s0) * ((phase >> _FRAC_BITS) & _FRAC_MASK))
                    >> _FRAC_BITS)

        # phase_inc stays below _PHASE_WRAP for every note in range, so one
        # subtract is enough and no modulo is needed.
        phase += phase_inc
        if phase >= _PHASE_WRAP:
            phase -= _PHASE_WRAP

        if stage == _ATTACK:
            level += attack_step
            if level >= _LVL_ONE:
                level = _LVL_ONE
                stage = _DECAY
        elif stage == _DECAY:
            level -= decay_step
            if level <= sustain_level:
                level = sustain_level
                stage = _SUSTAIN
        elif stage == _SUSTAIN:
            # sustain_decay_step is 0 for the instruments that genuinely
            # hold (organ, flute, strings), so this costs one compare for
            # them and nothing else.
            if sustain_decay_step:
                level -= sustain_decay_step
                if level <= 0:
                    level = 0
                    stage = _IDLE
        elif stage == _RELEASE:
            level -= release_step
            if level <= 0:
                level = 0
                stage = _IDLE

        mix_buf[n] += (raw * (level >> _LVL_TO_Q15)) >> 15
        n += 1

        if stage == _IDLE:
            break       # rest of the block gets 0 from this voice

    v.phase = phase
    v.stage = stage
    v.level = level
    if stage == _IDLE:
        v.active = False


@micropython.native
def generate_block(volume_pct, cutoff_pct):
    """Render one block: voices -> mix -> lowpass -> loop tap -> volume -> clip.

    The filter sits before volume so cutoff and loudness stay independent,
    and one filter runs on the summed mix rather than per voice. The loop
    tap sits after the filter so effects bake into a recording, and before
    volume so the knob still rides the loop.
    """
    # Bound to locals once; these would otherwise be dict lookups on every
    # one of the 256 iterations.
    mb = mix_buf
    ob = out_buf
    n = BUF_SAMPLES

    # Q10 master gain, table lookup. The two float lines that used to sit
    # here computed `vol` and then never used it -- and float arithmetic in
    # a native function allocates on the heap, every block, forever.
    vol_q = VOL_TABLE[volume_pct]

    knee = LIMIT_KNEE
    slope = LIMIT_SLOPE
    ceil = LIMIT_CEIL

    mb[:] = zero_buf

    for voice in voices:
        if voice.active:
            render_voice(voice, mb, n)

    # Drums mix into the same buffer at the same chain position, so a hit
    # gets filtered and looped like any melodic note.
    for dv in drum_voices:
        if dv.active:
            render_drum_voice(dv, mb, n)

    cutoff.process(mb, n, cutoff_pct)

    looper.process(mb, n)

    i = 0
    while i < n:
        total = (mb[i] * vol_q) >> 10

        if total > knee:
            total = knee + (((total - knee) * slope) >> 10)
            if total > ceil:
                total = ceil
        elif total < -knee:
            total = -knee + (((total + knee) * slope) >> 10)
            if total < -ceil:
                total = -ceil

        ob[i] = total
        i += 1


# ---------------- Startup ----------------

cutoff.configure(SAMPLE_RATE)

sync_state()
oled.displayState(state, looper)
oled.flush()        # displayState only queues; drain the first paint now
looper.update_bar(oled.display)

print("--- Pico 2 W Music Controller | Team 13 ---")
print("keypad: #/9 key +-  0/8 octave +-  */7 sample +-  6 mode")
print("        3 preset  5 rec  4 play/pause  1 loop reset")
print("loop: %.2f s | %d blocks | %d KB" % (
    looper.capacity / SAMPLE_RATE,
    looper.n_blocks,
    looper.capacity * 4 // 1024))
print("wavetables: %d KB | filter: %d-%d Hz, res %d" % (
    bank_bytes() // 1024, cutoff.corner_hz(0), cutoff.corner_hz(98),
    cutoff.RESONANCE))

# "The cutoff knob does nothing" and "the cutoff knob has not been swept
# far enough to calibrate yet" are the same symptom. Say which it is.
print("pots: volume %s, cutoff %s" % (
    "live" if pot_calibrated(POT_VOLUME) else "UNCALIBRATED (sweep it)",
    "live" if pot_calibrated(POT_CUTOFF) else "UNCALIBRATED (sweep it)"))
print("free heap after init:", gc.mem_free())
print("#" + json.dumps(state))

oled_dirty = False
json_dirty = False
oled_last_ms = time.ticks_ms()
bar_last_ms = time.ticks_ms()
json_last_ms = time.ticks_ms()

# Two serial clocks: a pending change goes out fast, a free-running loop
# heartbeat goes out slowly. 30 ms is under one frame at 30 fps, so the
# visualiser cannot tell, but a chord can no longer queue three blocking
# USB writes into three consecutive audio blocks.
JSON_MIN_INTERVAL_MS = 30
JSON_LOOP_INTERVAL_MS = 100

# Collect in the slack right after write() returns, rather than letting an
# allocation trigger one mid-block. Skipped on a pass that already spent
# time on serial or I2C; GC_MAX_BLOCKS stops that deferral running forever.
GC_EVERY_N_BLOCKS = 20
GC_MAX_BLOCKS = 40
block_count = 0


# ---------------- Main loop ----------------

while True:

    changed = False
    preset = presets[active_preset]

    # --- 8 note buttons -> voices ---
    for i in range(NUM_VOICES):
        key_bits[i] = 1 if buttons[i].value() == 0 else 0

    if key_bits != prev_key_bits:
        is_drums = (preset["sample"] == DRUM_KIT_NAME)

        # One frequency per newly-pressed button, not the whole scale up
        # front: melodic minor's 6th/7th is a per-note decision.
        for i in range(NUM_VOICES):
            if key_bits[i] and not prev_key_bits[i]:
                if is_drums:
                    drum_voices[i].note_on(i)
                else:
                    ascending = (last_degree_index is None
                                 or i > last_degree_index)
                    freq = build_scale_freqs(
                        preset["key"], preset["octave"], preset["mode"],
                        i, ascending)
                    voices[i].note_on(freq, preset["sample"])
                    last_degree_index = i
            elif prev_key_bits[i] and not key_bits[i]:
                # Both, unconditionally. is_drums reflects the sample
                # selected now, but the voice being released started under
                # whatever was selected then -- cycling sample or preset
                # while holding a note would otherwise strand it sounding.
                voices[i].note_off()
                drum_voices[i].note_off()

        state_keys = state["keys"]
        for i in range(NUM_VOICES):
            state_keys[i] = key_bits[i] == 1

        prev_key_bits[:] = key_bits
        changed = True

    # --- pots ---
    volume = read_pot(POT_VOLUME)
    if previous_volume == -1 or abs(volume - previous_volume) >= VOLUME_DEADBAND:
        previous_volume = volume
        state["volume"] = volume
        changed = True

    # Reports 100 (filter open) until GP27 has actually been swept -- see
    # read_pot. An unwired cutoff knob is now inaudible instead of
    # muffling the whole set.
    cutoff = read_pot(POT_CUTOFF, 100)
    if previous_cutoff == -1 or abs(cutoff - previous_cutoff) >= CUTOFF_DEADBAND:
        previous_cutoff = cutoff
        state["cutoff"] = cutoff
        changed = True

    # --- keypad ---
    pressed_key = scan_keypad()

    if pressed_key:
        if DEBUG:
            print("[debug] keypad:", pressed_key)

        # Key, octave, mode and preset all change what a button index means
        # musically, so each resets melodic minor's direction state.
        if pressed_key == "#":
            preset["key"] = shift_key(preset["key"], 1)
            last_degree_index = None
            changed = True

        elif pressed_key == "9":
            preset["key"] = shift_key(preset["key"], -1)
            last_degree_index = None
            changed = True

        elif pressed_key == "0":
            preset["octave"] = shift_octave(preset["octave"], 1)
            last_degree_index = None
            changed = True

        elif pressed_key == "8":
            preset["octave"] = shift_octave(preset["octave"], -1)
            last_degree_index = None
            changed = True

        elif pressed_key == "*":
            preset["sample"] = shift_sample(preset["sample"], 1)
            changed = True

        elif pressed_key == "7":
            preset["sample"] = shift_sample(preset["sample"], -1)
            changed = True

        elif pressed_key == "6":
            preset["mode"] = shift_mode(preset["mode"], 1)
            last_degree_index = None
            changed = True

        elif pressed_key == "3":
            # Cycles rather than direct-selects. Notes already sounding keep
            # the frequency they were triggered with.
            active_preset = (active_preset + 1) % len(presets)
            preset = presets[active_preset]
            last_degree_index = None
            changed = True

        elif pressed_key == "4":
            looper.play_toggle()
            changed = True

        elif pressed_key == "5":
            looper.record_toggle()
            changed = True

        elif pressed_key == "1":
            looper.reset()
            changed = True

        elif DEBUG:
            print("[debug] key", pressed_key, "unassigned")

    # --- serial and OLED ---
    now_ms = time.ticks_ms()

    loop_running = looper.is_sounding() or looper.state == loop.RECORDING

    if changed:
        # Cheap, and runs at the edge that caused the change -- the OLED
        # reads `state`, so deferring this could paint a stale mirror.
        sync_state()
        json_dirty = True
        oled_dirty = True

    if json_dirty:
        json_due = JSON_MIN_INTERVAL_MS
    elif loop_running:
        json_due = JSON_LOOP_INTERVAL_MS
    else:
        json_due = -1

    json_printed = False
    if json_due >= 0 and time.ticks_diff(now_ms, json_last_ms) >= json_due:
        if serial_ready():
            state["loop"] = looper.state_name()
            state["loop_pos"] = looper.progress()
            print("#" + json.dumps(state))
            json_last_ms = now_ms
            json_dirty = False
            json_printed = True
            tx_skips = 0
        else:
            # Host not draining: skip, stay dirty, retry next pass. Do not
            # touch json_last_ms or the retry waits out another interval.
            tx_skips += 1

    if oled_dirty and time.ticks_diff(now_ms, oled_last_ms) >= oled.OLED_MIN_INTERVAL_MS:
        oled.displayState(state, looper)    # queues pages, sends nothing
        oled_last_ms = now_ms
        oled_dirty = False

    bar_pushed = False
    if loop_running and time.ticks_diff(now_ms, bar_last_ms) >= oled.BAR_INTERVAL_MS:
        looper.update_bar(oled.display)
        bar_last_ms = now_ms
        bar_pushed = True

    # One page of I2C per pass, total -- the bar and the status rows share
    # the bus and the block budget.
    oled_pushed = False if bar_pushed else oled.push_one()

    # --- audio: must run every pass ---
    any_active = False
    for voice in voices:
        if voice.active:
            any_active = True
            break
    if not any_active:
        # Checked separately: a drum one-shot can be the only thing
        # sounding, and scanning only `voices` would write silence over it.
        for dv in drum_voices:
            if dv.active:
                any_active = True
                break

    # The looper and the filter tail can both be non-zero with no key held.
    # Cutting to silence while either is true is a click at the end of
    # every phrase.
    if (any_active or looper.state != loop.STOPPED
            or cutoff.tail_active()):
        generate_block(state["volume"], state["cutoff"])
        audio.write(out_buf)
    else:
        # Integer floor-shift is asymmetric, so a small negative filter
        # state can converge to -1 and stay there as a DC offset.
        cutoff.reset()
        audio.write(silence_buf)

    block_count += 1
    if block_count >= GC_EVERY_N_BLOCKS:
        if ((not json_printed and not bar_pushed and not oled_pushed)
                or block_count >= GC_MAX_BLOCKS):
            block_count = 0
            gc.collect()