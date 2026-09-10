# -*- coding: utf-8 -*-
"""xyzzy 后门探针 v2：单一校准（新盘取一次，死局复用），聚合对比雷/安全格拐角像素。
"""
import sys
import time
from collections import Counter

import mss
import numpy as np
import pydirectinput
import win32con, win32gui
import yaml

ROOT = r"D:\workbuddy\projects\minesweeper-bot"
sys.path.insert(0, ROOT)

from perception import calibrate, capture, grid_perception, windowing  # noqa: E402
from action import action  # noqa: E402


def load_cfg():
    with open(ROOT + r"\config\window.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def ensure_visible(win):
    hwnd = win["hwnd"]
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    time.sleep(0.2)


def read_pixel_00():
    with mss.mss() as s:
        shot = s.grab({"left": 0, "top": 0, "width": 1, "height": 1})
        return tuple(int(v) for v in np.asarray(shot)[0, 0][:3])


def main():
    pydirectinput.FAILSAFE = False
    windowing.enable_dpi_aware()
    cfg = load_cfg()
    win = windowing.find_game_window(cfg)
    if not win:
        print("NO WINDOW"); return 1
    windowing.bring_to_foreground(win["hwnd"])
    ensure_visible(win)
    time.sleep(0.5)

    pydirectinput.write("xyzzy", interval=0.05)
    time.sleep(0.4)
    print("typed xyzzy", flush=True)

    # 新盘一次校准
    frame = capture.grab_client(win)
    calib = calibrate.detect_board(frame)
    if calib["rows"] == 0:
        print("board not detected"); return 1
    rows, cols = calib["rows"], calib["cols"]
    print("fresh board:", rows, "x", cols, flush=True)

    # 打至死局，复用 calib
    action.reveal(cols // 2, rows // 2, win, calib)
    time.sleep(0.5)
    rng = np.random.default_rng()
    for _ in range(200):
        frame = capture.grab_client(win)
        cur = sum(1 for r in range(rows) for c in range(cols)
                  if int(grid_perception.slice_cell(frame, calib, c, r)[0, 0].max()) < 240)
        picked = None
        for _t in range(100):
            c = int(rng.integers(0, cols)); r = int(rng.integers(0, rows))
            if int(grid_perception.slice_cell(frame, calib, c, r)[0, 0].max()) >= 240:
                picked = (c, r); break
        if picked is None:
            break
        action.reveal(picked[0], picked[1], win, calib)
        time.sleep(0.5)
        frame = capture.grab_client(win)
        nxt = sum(1 for r in range(rows) for c in range(cols)
                  if int(grid_perception.slice_cell(frame, calib, c, r)[0, 0].max()) < 240)
        if nxt - cur >= 15:
            print("died at", picked, flush=True)
            break

    # 死局分类每格类型
    mine, safe = [], []
    for r in range(rows):
        for c in range(cols):
            cell = grid_perception.slice_cell(frame, calib, c, r)
            if int(cell[0, 0].max()) >= 240:
                continue
            region = cell[3:-3, 3:-3].astype(int)
            b, g, rr = region[:, :, 0], region[:, :, 1], region[:, :, 2]
            if float(((rr > 150) & (rr > g * 1.5) & (rr > b * 1.5)).mean()) > 0.25:
                mine.append((c, r))
            else:
                safe.append((c, r))
    print("n_mine", len(mine), "n_safe", len(safe), flush=True)

    def probe(cells, label):
        colors = Counter()
        moved = 0
        for (c, r) in cells:
            sx = win["client_rect"]["x"] + calib["board_origin"]["x"] + c * calib["cell_size"] + calib["cell_size"] // 2
            sy = win["client_rect"]["y"] + calib["board_origin"]["y"] + r * calib["cell_size"] + calib["cell_size"] // 2
            pydirectinput.moveTo(int(sx), int(sy))
            time.sleep(0.25)
            if read_pixel_00() is not None:
                moved += 1
        # 最后几个，持 shift 读
        for (c, r) in cells[-12:]:
            sx = win["client_rect"]["x"] + calib["board_origin"]["x"] + c * calib["cell_size"] + calib["cell_size"] // 2
            sy = win["client_rect"]["y"] + calib["board_origin"]["y"] + r * calib["cell_size"] + calib["cell_size"] // 2
            pydirectinput.moveTo(int(sx), int(sy))
            time.sleep(0.25)
            pydirectinput.keyDown("shift")
            time.sleep(0.2)
            colors[read_pixel_00()] += 1
            pydirectinput.keyUp("shift")
        print(f"{label} shift_colors:", dict(colors), flush=True)

    probe(safe, "SAFE")
    probe(mine, "MINE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())