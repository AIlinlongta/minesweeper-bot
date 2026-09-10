# -*- coding: utf-8 -*-
"""临时探针（决定性）：死局下对比 雷格 vs 数字格 在屏幕(0,0)的读数，定 xyzzy 指示语义。
步骤：前台+移窗 → 重启新局 → 中心首击 → 强制翻雷致死 → 逐格读 (0,0)。"""
import ctypes
import sys
import time

import cv2
import mss
import numpy as np
import pydirectinput
import win32gui
import yaml

ROOT = r"D:\workbuddy\projects\minesweeper-bot"
sys.path.insert(0, ROOT)

from perception import calibrate, capture, grid_perception, windowing  # noqa: E402
from action import action  # noqa: E402


def getpixel00():
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    hdc = user32.GetDC(0)
    try:
        cr = gdi32.GetPixel(hdc, 0, 0)
    finally:
        user32.ReleaseDC(0, hdc)
    return (cr & 0xFF, (cr >> 8) & 0xFF, (cr >> 16) & 0xFF)


def hover_read(sx, sy):
    pydirectinput.keyDown("shift")
    try:
        pydirectinput.moveTo(int(sx), int(sy))
        time.sleep(0.25)
        return getpixel00()
    finally:
        pydirectinput.keyUp("shift")


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

    # 存 (0,0) 角落环境图
    with mss.mss() as s:
        region = s.grab({"left": 0, "top": 0, "width": 120, "height": 80})
        img = np.asarray(region, dtype=np.uint8)[:, :, :3]
        cv2.imwrite(ROOT + r"\scripts\_corner00.png", img)

    # 重启新局
    frame = capture.grab_client(win)
    anchor = calibrate.detect_anchor(frame)
    face = anchor.get("face_rect")
    if face:
        fx = win["client_rect"]["x"] + face["x"] + face["w"] // 2
        fy = win["client_rect"]["y"] + face["y"] + face["h"] // 2
        action.restart((fx, fy))
        time.sleep(0.6)
    frame = capture.grab_client(win)
    calib = calibrate.detect_board(frame)
    if calib["rows"] == 0:
        print("NO BOARD"); return 1
    rows, cols = calib["rows"], calib["cols"]
    print("board:", rows, "x", cols, flush=True)

    # 激活
    windowing.ensure_english_input(win["hwnd"])
    time.sleep(0.2)
    pydirectinput.write("xyzzy", interval=0.05)
    time.sleep(0.2)
    pydirectinput.keyDown("shift"); time.sleep(0.1); pydirectinput.keyUp("shift")
    time.sleep(0.3)
    print("activated", flush=True)

    # 中心首击
    cc, cr = cols // 2, rows // 2
    action.reveal(cc, cr, win, calib)
    time.sleep(0.5)
    frame = capture.grab_client(win)
    sx, sy = windowing.resolve_screen_cell(win, calib, cc, cr)
    print("SAFE center read:", hover_read(sx, sy), flush=True)

    # 强制翻雷致死
    rng = np.random.default_rng()
    dead = False
    prev_open = sum(1 for r in range(rows) for c in range(cols)
                    if int(grid_perception.slice_cell(frame, calib, c, r)[0, 0].max()) < 240)
    for _ in range(300):
        frame = capture.grab_client(win)
        covered = [(c, r) for r in range(rows) for c in range(cols)
                   if int(grid_perception.slice_cell(frame, calib, c, r)[0, 0].max()) >= 240]
        if not covered:
            break
        c, r = covered[int(rng.integers(0, len(covered)))]
        action.reveal(c, r, win, calib)
        time.sleep(0.4)
        frame = capture.grab_client(win)
        cur = sum(1 for rr in range(rows) for cc in range(cols)
                  if int(grid_perception.slice_cell(frame, calib, cc, rr)[0, 0].max()) < 240)
        cell = grid_perception.slice_cell(frame, calib, c, r)
        region = cell[3:-3, 3:-3].astype(int)
        b, g, rr2 = region[:, :, 0], region[:, :, 1], region[:, :, 2]
        red = float(((rr2 > 150) & (rr2 > g * 1.5) & (rr2 > b * 1.5)).mean())
        if red > 0.25 or cur - prev_open >= 15:
            print("DEAD at", (c, r), "red_frac=%.3f" % red, flush=True)
            dead = True
            break
        prev_open = cur
    if not dead:
        print("NOT DEAD (board finished?); continuing read anyway", flush=True)

    # 死局：雷 vs 数字 读数
    mine_reads, digit_reads = [], []
    for r in range(rows):
        for c in range(cols):
            cell = grid_perception.slice_cell(frame, calib, c, r)
            if int(cell[0, 0].max()) >= 240:
                continue
            region = cell[3:-3, 3:-3].astype(int)
            b, g, rr2 = region[:, :, 0], region[:, :, 1], region[:, :, 2]
            red = float(((rr2 > 150) & (rr2 > g * 1.5) & (rr2 > b * 1.5)).mean())
            sx, sy = windowing.resolve_screen_cell(win, calib, c, r)
            v = hover_read(sx, sy)
            if red > 0.25:
                mine_reads.append(v)
            else:
                digit_reads.append(v)
    print("MINE reads:", dict(__import__("collections").Counter(mine_reads)), flush=True)
    print("DIGIT reads:", dict(__import__("collections").Counter(digit_reads)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
