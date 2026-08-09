"""OLED status display: partial, change-detected redraw.

Self-contained aside from two things the caller must supply:
  - init(i2c) once at startup, to build the SSD1306 driver
  - displayState(st, looper) each redraw, passing the live Looper so the
    loop-status row can read it

freq on the I2C bus is set explicitly: a full 1 KB frame at the 100 kHz
default takes ~100 ms, blowing the entire audio block budget on its own.
Even at 400 kHz a full display.show() is ~23-25 ms -- more than the
whole 16 ms block budget (see SAMPLE_RATE in main.py). Fixed by never
calling display.show() at all -- see PAGE_HEIGHT / _push_page below.

Pushing is also DEFERRED. displayState() draws into the framebuffer and
queues the page numbers it touched; the actual I2C traffic happens in
push_one(), which sends at most one 128-byte page (~3 ms) per call. The
caller runs push_one() once per audio block, so a change that dirties
all five text rows costs 3 ms on each of five passes instead of 15 ms on
one -- see the SPIKE BUDGET note in main.py.
"""

import ssd1306
from scale import MODE_DISPLAY_LABEL

OLED_MIN_INTERVAL_MS = 100   # partial redraw is cheap now; can run more often
BAR_INTERVAL_MS = 50         # loop progress bar, one page only (~3 ms)

# SSD1306 command bytes for addressing a single 8px-tall "page" (row),
# same technique loop.py already uses for the progress bar -- see
# Looper.update_bar(). Duplicated here rather than imported from loop.py
# because it is a display-driver detail, not a looper detail; the two
# modules happen to both need it.
_SET_COL_ADDR = 0x21
_SET_PAGE_ADDR = 0x22
PAGE_HEIGHT = 8

display = None  # set by init()


def init(i2c):
    """Build the SSD1306 driver on the given I2C bus. Call once at
    startup before any other function in this module."""
    global display
    display = ssd1306.SSD1306_I2C(128, 64, i2c)
    return display


def _push_page(page):
    """Write ONE 128-byte page from display.buffer to the OLED, instead
    of display.show()'s full 1024-byte frame. ~1/8th the I2C traffic,
    which is the difference between overrunning the audio block budget
    and not. Mirrors loop.py's update_bar() exactly, generalized to any
    page 0-7 instead of hardcoding page 7."""
    display.write_cmd(_SET_COL_ADDR)
    display.write_cmd(0)
    display.write_cmd(127)
    display.write_cmd(_SET_PAGE_ADDR)
    display.write_cmd(page)
    display.write_cmd(page)
    display.write_data(display.buffer[page * 128:(page + 1) * 128])


# Last-drawn content string per page, keyed by page number. displayState()
# skips redrawing/pushing a page whose content matches what is already on
# screen. None (not "") for every entry so the very FIRST call always
# treats every page as changed and does a full initial paint -- an empty
# string would look identical to a page that hasn't been drawn yet if any
# real content ever manages to be genuinely empty, but None can never
# collide with a str.
_page_last = {0: None, 1: None, 2: None, 3: None, 5: None}

# Page numbers drawn into the framebuffer but not yet sent over I2C.
# FIFO, and deliberately deduplicated on insert: if a page is dirtied
# twice before it drains, the second draw has already overwritten the
# first in the framebuffer, so queueing it twice would push identical
# bytes twice and burn 3 ms for nothing.
_pending = []


def push_one():
    """Send at most ONE queued page to the physical screen (~3 ms) and
    return whether anything was sent. Call once per main-loop pass.

    This is the whole point of the deferral: no single pass can spend
    more than one page of I2C time, so the OLED can never be the reason
    an audio block overruns. A five-page repaint takes five passes
    (~80 ms at a 16 ms block) to appear in full, which is far below what
    reads as lag on a status readout."""
    if not _pending:
        return False
    _push_page(_pending.pop(0))
    return True


def flush():
    """Drain the whole queue immediately, ignoring the one-per-pass
    rule. STARTUP ONLY -- this is exactly the ~15 ms spike push_one()
    exists to prevent, which is harmless before the audio loop starts
    and unacceptable once it has."""
    while _pending:
        _push_page(_pending.pop(0))


def displayState(st, looper):
    """Partial, CHANGE-DETECTED redraw: draws into display.buffer exactly
    like a full redraw would, then QUEUES the pages whose CONTENT
    actually changed since the last call. Sends nothing itself -- see
    push_one() for the I2C side.

    Two independent savings, and both are needed. Change detection means
    the common case (only the volume pot moved) queues one page instead
    of five. Deferral means that even the worst case -- a preset cycle,
    which changes key, octave, sample and mode together and so dirties
    all five text rows -- cannot spend 15 ms of I2C inside a single 16 ms
    audio block; it drains one page per pass instead.

    This call is therefore cheap and UNCONDITIONAL in cost terms: a
    handful of string builds plus some framebuffer writes, no bus
    traffic. It is still rate-limited by the caller (OLED_MIN_INTERVAL_MS)
    purely to avoid rebuilding those strings pointlessly.

    Rows 56-63 (page 7) belong to the looper's progress bar, pushed
    separately by loop.py -- never touched here.

    LAYOUT  (128px wide / 16 chars per row at the default 8px font;
    every row below MUST stay on an 8px page boundary -- y a multiple
    of 8 -- or a partial-page push will only ever move the top half of
    that row's text and leave the bottom half stale on screen. This is
    why the loop-status row moved from y=44, which straddled pages 5/6,
    to y=40, which does not.):

        row 0   (page 0)  P1 Key:C          Oct:4     preset/key/octave
        row 8   (page 1)  Sample: Sawtooth            sample name
        row 16  (page 2)  Mode: HarMin                mode (abbreviated
                                                        value -- see
                                                        MODE_DISPLAY_LABEL,
                                                        "Mode: Harmonic
                                                        Minor" is 160px,
                                                        wider than the
                                                        128px screen)
        row 24  (page 3)  Vol: 75           LP: 100   volume, cutoff
        row 32  (page 4)  -- blank, dropped key-bitmap rows --
        row 40  (page 5)  Loop: EMPTY                 loop status
        row 48  (page 6)  -- blank --
        row 56  (page 7)  -- owned by loop.py's progress bar --

    Adding a new row means adding it to new_content below AND to its
    page's entry in _page_last (both near the top of this module) --
    forgetting either draws the new text correctly into the buffer but
    never pushes it to the physical screen, which is a silent, confusing
    bug (the OLED would just never show the new field)."""
    page0 = "P" + str(st["preset"]) + " Key:" + st["key"]
    page0b = "Oct:" + str(st["octave"])
    page1 = "Sample: " + st["sample"]
    mode_label = MODE_DISPLAY_LABEL.get(st["mode"], st["mode"])
    page2 = "Mode: " + mode_label
    page3 = "Vol: " + str(st["volume"])
    page3b = "LP: " + str(st["cutoff"])
    page5 = "Loop: " + looper.status_text()

    # One combined key per page so a change in EITHER string on a shared
    # page (e.g. Vol vs LP, both on page 3) is still detected -- comparing
    # them separately would need two dirty-flags per page for no benefit.
    new_content = {
        0: page0 + "|" + page0b,
        1: page1,
        2: page2,
        3: page3 + "|" + page3b,
        5: page5,
    }

    for page, content in new_content.items():
        if _page_last[page] == content:
            continue   # unchanged since last call -- skip blank/draw/push entirely

        display.fill_rect(0, page * PAGE_HEIGHT, 128, PAGE_HEIGHT, 0)

        if page == 0:
            display.text(page0, 0, 0)
            display.text(page0b, 80, 0)
        elif page == 1:
            display.text(page1, 0, 8)
        elif page == 2:
            display.text(page2, 0, 16)
        elif page == 3:
            display.text(page3, 0, 24)
            display.text(page3b, 64, 24)
        elif page == 5:
            display.text(page5, 0, 40)

        # Queue rather than push. _page_last is updated HERE, not when
        # the page actually drains, because the framebuffer now holds
        # this content -- a later call comparing against it is asking
        # "is the buffer already correct?", not "is the screen already
        # correct?", and re-drawing identical bytes would be wasted work
        # either way.
        if page not in _pending:
            _pending.append(page)
        _page_last[page] = content