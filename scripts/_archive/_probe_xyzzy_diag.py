# -*- coding: utf-8 -*-
"""临时诊断：确定 xyzzy 指示像素的真实位置与黑白语义。

在存活棋盘上，对「已翻开的已知安全格」与「未翻格」分别悬停+Shift 读候选像素：
  - 候选位置：屏幕 (0,0)、窗口左上角、客户区左上角
  - 若某位置对已知安全格返回纯黑/纯白 → 即指示位置；安全色 = 该读值。
"""
import sys
import time
from collections import Counter

import mss
import numpy as np
import pydirectinput
import win32gui
import yaml

ROOT = r"D:\workbuddy\projects\minesweeper-bot"
sys.path.insert(0, ROOT)

from perception import calibrate, capture, grid_perception, windowing, xyzzy  # noqa: E402


def read_px(x, y):
    with mss.mss() as s:
        shot = s.grab({"left": int(x), "top": int(y), "width": 1, "height": 1})
        b, g, r = [int(v) for v in np.asarray(shot)[0, 0][:3]]
        return (r, g, b)


def hover_read(sx, sy, cands):
    pydirectinput.moveTo(int(sx), int(sy))
    time.sleep(0.05)
    pydirectinput.keyDown("shift")
    time.sleep(0.12)
    vals = {name: read_px(x, y) for name, (x, y) in cands.items()}
    pydirectinput.keyUp("shift")
    return vals


def main():
    pydirectinput.FAILSAFE = False
    windowing.enable_dpi_aware()
    with open(ROOT + r"\config\window.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    win = windowing.find_game_window(cfg)
    if not win:
        print("NO WINDOW"); return 1
    windowing.bring_to_foreground(win["hwnd"])
    time.sleep(0.5)
    frame = capture.grab_client(win)
    calib = calibrate.detect_board(frame)
    if calib["rows"] == 0:
        print("NO BOARD"); return 1
    print("board:", calib["rows"], "x", calib["cols"], flush=True)

    cands = {
        "screen00": (0, 0),
        "win_tl": (win["window_rect"]["x"], win["window_rect"]["y"]),
        "client_tl": (win["client_rect"]["x"], win["client_rect"]["y"]),
    }
    # 基线（未悬停任何格、不按 Shift）
    base = {name: read_px(x, y) for name, (x, y) in cands.items()}
    print("baseline:", base, flush=True)

    xyzzy.activate()

    opened = []   # 已翻开（已知安全）
    covered = []  # 未翻开（未知）
    for r in range(calib["rows"]):
        for c in range(calib["cols"]):
            cell = grid_perception.slice_cell(frame, calib, c, r)
            sx, sy = windowing.resolve_screen_cell(win, calib, c, r)
            (opened if int(cell[0, 0].max()) < 240 else covered).append((c, r, sx, sy))
    print(f"opened={len(opened)} covered={len(covered)}", flush=True)

    safe_cnt = Counter()
    cov_cnt = Counter()
    # 已知安全格：全部翻开格取前 15 个
    for c, r, sx, sy in opened[:15]:
        vals = hover_read(sx, sy, cands)
        for name, v in vals.items():
            safe_cnt[(name, v)] += 1
        print(f"SAFE cell ({c},{r}): {vals}", flush=True)
    # 未翻格：取 15 个
    for c, r, sx, sy in covered[:15]:
        vals = hover_read(sx, sy, cands)
        for name, v in vals.items():
            cov_cnt[(name, v)] += 1
        print(f"COVERED cell ({c},{r}): {vals}", flush=True)

    print("\nSAFE histogram:", flush=True)
    for (name, v), n in safe_cnt.most_common():
        print(f"  {name} {v} x{n}", flush=True)
    print("COVERED histogram:", flush=True)
    for (name, v), n in cov_cnt.most_common():
        print(f"  {name} {v} x{n}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
