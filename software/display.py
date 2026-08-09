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


def displayState(st, looper):
    """Partial, CHANGE-DETECTED redraw: draws into display.buffer exactly
    like a full redraw would, but pushes only the pages whose CONTENT
    actually changed since the last call, as individual 128-byte page
    writes -- never display.show(), which pushes all 1024 bytes every
    time.

    Pushing all 5 text pages unconditionally costs ~15 ms of I2C
    transfer against a 16 ms block budget -- too tight to trust. The
    common case (e.g. only the volume pot moved) needs one page
    repainted, not five, so tracking each page's last-drawn string and
    skipping unchanged ones keeps the typical call cheap.

    ACCEPTED RISK: an action that changes all 5 pages at once (preset
    cycle changes key/octave/sample/mode together) still costs ~15 ms
    against the 16 ms budget and could overrun. Not spread across
    multiple loop passes (would need a pending-pages queue) since it's a
    rare, self-limiting case -- revisit if bench testing shows a click
    specifically on preset switch.

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

        _push_page(page)
        _page_last[page] = content
