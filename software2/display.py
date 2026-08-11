"""OLED status display: partial, change-detected, deferred redraw.

A full display.show() pushes 1024 bytes and takes ~23 ms at 400 kHz --
more than a whole audio block's budget. So this module never calls show().
Instead displayState() draws into the framebuffer and queues the pages it
changed, and push_one() sends at most one 128-byte page (~3 ms) per call.

Caller supplies init(i2c) once at startup, then displayState(st, looper)
to redraw and push_one() once per main-loop pass.
"""

import ssd1306
from scale import MODE_DISPLAY_LABEL

OLED_MIN_INTERVAL_MS = 100
BAR_INTERVAL_MS = 50        # loop progress bar, one page only

_SET_COL_ADDR = 0x21
_SET_PAGE_ADDR = 0x22
PAGE_HEIGHT = 8

display = None      # set by init()


def init(i2c):
    """Build the SSD1306 driver. Call once before anything else here."""
    global display
    display = ssd1306.SSD1306_I2C(128, 64, i2c)
    return display


def _push_page(page):
    """Write one 128-byte page from display.buffer, instead of show()'s
    full 1024-byte frame."""
    display.write_cmd(_SET_COL_ADDR)
    display.write_cmd(0)
    display.write_cmd(127)
    display.write_cmd(_SET_PAGE_ADDR)
    display.write_cmd(page)
    display.write_cmd(page)
    display.write_data(display.buffer[page * 128:(page + 1) * 128])


# Last-drawn content per page. None, not "", so the first call treats every
# page as changed and does a full initial paint.
_page_last = {0: None, 1: None, 2: None, 3: None, 5: None}

# Pages drawn into the framebuffer but not yet sent. Deduplicated on
# insert: a second draw has already overwritten the first in the buffer.
_pending = []


def push_one():
    """Send at most one queued page (~3 ms). Returns whether it sent
    anything. Call once per main-loop pass."""
    if not _pending:
        return False
    _push_page(_pending.pop(0))
    return True


def flush():
    """Drain the whole queue at once. Startup only -- this is the ~15 ms
    spike push_one() exists to avoid."""
    while _pending:
        _push_page(_pending.pop(0))


def displayState(st, looper):
    """Redraw changed rows into the framebuffer and queue their pages.

    Layout. Every row must sit on an 8px page boundary, or a partial-page
    push moves only the top half of the text and leaves the rest stale:

        row 0   (page 0)  P1 Key:C     Oct:4
        row 8   (page 1)  Sample: Sawtooth
        row 16  (page 2)  Mode: HarMin        (abbreviated -- the full name
                                               is wider than the screen)
        row 24  (page 3)  Vol: 75      LP: 100
        row 40  (page 5)  Loop: EMPTY
        row 56  (page 7)  owned by loop.py's progress bar, never touched here

    Adding a row means adding it to new_content AND to _page_last, or it
    draws correctly but never reaches the screen.
    """
    page0 = "P" + str(st["preset"]) + " Key:" + st["key"]
    page0b = "Oct:" + str(st["octave"])
    page1 = "Sample: " + st["sample"]
    mode_label = MODE_DISPLAY_LABEL.get(st["mode"], st["mode"])
    page2 = "Mode: " + mode_label
    page3 = "Vol: " + str(st["volume"])
    page3b = "LP: " + str(st["cutoff"])
    page5 = "Loop: " + looper.status_text()

    # One key per page, so a change in either string on a shared page
    # (Vol vs LP) is still detected.
    new_content = {
        0: page0 + "|" + page0b,
        1: page1,
        2: page2,
        3: page3 + "|" + page3b,
        5: page5,
    }

    for page, content in new_content.items():
        if _page_last[page] == content:
            continue

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

        if page not in _pending:
            _pending.append(page)
        _page_last[page] = content