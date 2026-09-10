# -*- coding: utf-8 -*-
"""临时探针：验证 xyzzy 指示像素能否经 GDI GetPixel(屏幕DC) 读到。
对比：mss 合成画面 vs GetPixel 屏幕表面。步骤：激活后门 → 中心首击 → 悬停+Shift 读 (0,0)。
"""
import ctypes
import sys
import time

import mss
import numpy as np
import pydirectinput
import win32gui
import yaml

ROOT = r"D:\workbuddy\projects\minesweeper-bot"
sys.path.insert(0, ROOT)

from perception import calibrate, capture, windowing  # noqa: E402
from action import action  # noqa: E402


def getpixel_00():
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    hdc = user32.GetDC(0)
    try:
        cr = gdi32.GetPixel(hdc, 0, 0)
    finally:
        user32.ReleaseDC(0, hdc)
    return (cr & 0xFF, (cr >> 8) & 0xFF, (cr >> 16) & 0xFF)


def mss_px(x=0, y=0):
    with mss.mss() as s:
        shot = s.grab({"left": x, "top": y, "width": 1, "height": 1})
        b, g, r = [int(v) for v in np.asarray(shot)[0, 0][:3]]
        return (r, g, b)


def main():
    pydirectinput.FAILSAFE = False
    windowing.enable_dpi_aware()
    with open(ROOT + r"\config\window.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    win = windowing.find_game_window(cfg)
    if not win:
        print("NO WINDOW"); return 1
    windowing.bring_to_foreground(win["hwnd"])
    win32gui.SetWindowPos(win["hwnd"], 0, 80, 60, 0, 0, 0x0001 | 0x0004)
    time.sleep(0.4)

    print("before: mss(0,0)=", mss_px(), "getpixel(0,0)=", getpixel_00(), flush=True)

    # 激活
    windowing.ensure_english_input(win["hwnd"])
    time.sleep(0.2)
    pydirectinput.write("xyzzy", interval=0.05)
    time.sleep(0.2)
    pydirectinput.keyDown("shift"); time.sleep(0.1); pydirectinput.keyUp("shift")
    time.sleep(0.3)

    frame = capture.grab_client(win)
    calib = calibrate.detect_board(frame)
    if calib["rows"] == 0:
        print("NO BOARD"); return 1
    print("board:", calib["rows"], "x", calib["cols"], flush=True)

    cc, cr = calib["cols"] // 2, calib["rows"] // 2
    action.reveal(cc, cr, win, calib)
    time.sleep(0.5)
    sx, sy = windowing.resolve_screen_cell(win, calib, cc, cr)

    pydirectinput.keyDown("shift")
    try:
        pydirectinput.moveTo(int(sx), int(sy))
        time.sleep(0.3)
        print("hover SAFE center: mss(0,0)=", mss_px(), "getpixel(0,0)=", getpixel_00(), flush=True)
        # 也读几个未翻格
        for i in range(3):
            x = win["client_rect"]["x"] + calib["board_origin"]["x"] + 2 * calib["cell_size"] + calib["cell_size"] // 2
            y = win["client_rect"]["y"] + calib["board_origin"]["y"] + (2 + i) * calib["cell_size"] + calib["cell_size"] // 2
            pydirectinput.moveTo(int(x), int(y)); time.sleep(0.3)
            print(f"hover covered({i}): mss(0,0)=", mss_px(), "getpixel(0,0)=", getpixel_00(), flush=True)
    finally:
        pydirectinput.keyUp("shift")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
