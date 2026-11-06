"""Text-only WeChat Moments post via RPA. Runs INSIDE the wechat-ai container.

Env: MOMENTS_TEXT_B64 (utf-8 text, base64), MOMENTS_MODE ("test" fills then cancels, "publish" posts).
Prints one JSON line: {"status": "...", "message": "..."}.
"""

import base64
import json
import os
import subprocess as sp
import time

import pyperclip
from PIL import ImageGrab

DISPLAY = os.environ.get("DISPLAY", ":99")
# Offsets inside the Moments window (WeChat Linux 4.x layout).
CAMERA, TEXT, CLOSE = (79, 33), (80, 122), (422, 32)
DIALOG_CORNERS = ((60, 90), (400, 90), (60, 470), (400, 470))
SIDEBAR_MOMENTS = (28, 290)
WINDOW_SIZE = (460, 560)
# Enlarged after the dialog opens (it stays top-anchored) so long drafts keep 发表/取消 on screen.
TALL_SIZE = (460, 1100)
# 取消 position once the draft is empty (the dialog shrinks back to its minimum height).
CANCEL_EMPTY = (289, 432)


def xdo(*args):
    return sp.run(["xdotool", *map(str, args)], capture_output=True, text=True)


def click(x, y, hold=0.0):
    xdo("mousemove", "--sync", x, y)
    time.sleep(0.2)
    xdo("mousedown", 1)
    time.sleep(hold or 0.1)
    xdo("mouseup", 1)


def window(name):
    ids = xdo("search", "--onlyvisible", "--name", f"^{name}$").stdout.split()
    if not ids:
        return None
    shell = dict(line.split("=", 1) for line in xdo("getwindowgeometry", "--shell", ids[0]).stdout.split())
    return ids[0], int(shell["X"]), int(shell["Y"])


def dialog_open(gx, gy):
    im = ImageGrab.grab(xdisplay=DISPLAY)
    return all(min(im.getpixel((gx + dx, gy + dy))[:3]) >= 240 for dx, dy in DIALOG_CORNERS)


def find_publish(gx, gy):
    """Centre of the green 发表 button inside the dialog, or None."""
    left, top = gx + 60, gy + 150
    im = ImageGrab.grab(xdisplay=DISPLAY, bbox=(left, top, gx + 400, gy + TALL_SIZE[1] - 10))
    px = im.load()
    rows = []
    for y in range(im.height):
        xs = [x for x in range(im.width) if _is_button_green(px[x, y])]
        # The button is a ~110px-wide bar of WeChat green; its white label only thins the row out.
        if len(xs) >= 40 and 80 <= xs[-1] - xs[0] <= 220:
            if rows and y - rows[-1][0] > 6:
                break
            rows.append((y, xs[0], xs[-1]))
    if len(rows) < 15:
        return None
    x0 = min(r[1] for r in rows)
    x1 = max(r[2] for r in rows)
    return left + (x0 + x1) // 2, top + (rows[0][0] + rows[-1][0]) // 2


def _is_button_green(p):
    r, g, b = p[:3]
    return r <= 25 and abs(g - 195) <= 15 and abs(b - 117) <= 15


def wait(cond, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        result = cond()
        if result:
            return result
        time.sleep(0.4)
    return None


def editor_text():
    pyperclip.copy("")
    xdo("key", "ctrl+a")
    xdo("key", "ctrl+c")
    time.sleep(0.4)
    return pyperclip.paste().replace("\r\n", "\n")


def run(text, mode):
    main = window("WeChat")
    if not main:
        return "error", "WeChat main window not found (logged out?)"
    xdo("windowactivate", "--sync", main[0])
    moments = window("朋友圈")
    if not moments:
        click(*SIDEBAR_MOMENTS)
        moments = wait(lambda: window("朋友圈"))
    if not moments:
        return "error", "Moments window did not open"
    wid, gx, gy = moments
    xdo("windowsize", wid, *WINDOW_SIZE)
    time.sleep(1.0)
    xdo("windowactivate", "--sync", wid)
    try:
        click(gx + CAMERA[0], gy + CAMERA[1], hold=2.0)
        if not wait(lambda: dialog_open(gx, gy)):
            return "error", "text-only compose dialog did not appear"
        xdo("windowsize", wid, *TALL_SIZE)
        time.sleep(1.0)
        click(gx + TEXT[0], gy + TEXT[1])
        xdo("key", "ctrl+a")
        xdo("key", "Delete")
        pyperclip.copy(text)
        time.sleep(0.2)
        xdo("key", "ctrl+v")
        time.sleep(0.6)
        publish = find_publish(gx, gy)
        if not publish:
            return "error", "发表 button not found; draft left open for manual check"

        def discard():
            xdo("key", "ctrl+a")
            xdo("key", "Delete")
            time.sleep(0.8)
            click(gx + CANCEL_EMPTY[0], gy + CANCEL_EMPTY[1])
            time.sleep(1.0)

        if editor_text().strip() != text.strip():
            discard()
            return "error", "compose box did not contain the intended text; cancelled"
        xdo("key", "End")
        if mode != "publish":
            discard()
            return "tested", "filled and cancelled; nothing published"
        click(*publish)
        closed = wait(lambda: not find_publish(gx, gy), timeout=10)
        return ("published", "dialog closed after 发表") if closed else ("unconfirmed", "dialog still open after 发表")
    finally:
        time.sleep(1.0)
        if window("朋友圈") and not dialog_open(gx, gy):
            xdo("windowsize", wid, *WINDOW_SIZE)
            time.sleep(0.5)
            click(gx + CLOSE[0], gy + CLOSE[1])


if __name__ == "__main__":
    text = base64.b64decode(os.environ["MOMENTS_TEXT_B64"]).decode("utf-8")
    try:
        status, message = run(text, os.environ.get("MOMENTS_MODE", "test"))
    except Exception as exc:  # noqa: BLE001 - report any RPA failure as JSON
        status, message = "error", f"{type(exc).__name__}: {exc}"
    print(json.dumps({"status": status, "message": message}, ensure_ascii=False))
