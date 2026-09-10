# -*- coding: utf-8 -*-
"""真实致死格探针 v2：逐格点击，每次存点击格与整盘图，打印红占比与中心色。
防卡：连续 CLICK_STALL 次无「新增翻面」即视为死亡 → 停止。
"""
import os
import sys
import time
from collections import Counter

import cv2
import numpy as np

ROOT = r"D:\workbuddy\projects\minesweeper-bot"
sys.path.insert(0, ROOT)
OUT = ROOT + r"\scripts\_dbg"
os.makedirs(OUT, exist_ok=True)

from perception import calibrate, capture, windowing  # noqa: E402
from action import action  # noqa: E402
import win32con, win32gui  # noqa: E402
import yaml  # noqa: E402

CLICK_STALL = 3


def ensure_visible(win):
    """强制窗口可见且在屏幕内（规避最小化/-32000 导致抓帧失败）。"""
    hwnd = win["hwnd"]
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    time.sleep(0.3)
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    if r - l <= 0 or b - t <= 0 or l < -1000 or t < -1000:
        win32gui.SetWindowPos(hwnd, 0, 80, 60, 0, 0, 0x0001 | 0x0004)  # move,keep size
        time.sleep(0.3)
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.3)


def load_cfg():
    with open(ROOT + r"\config\window.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def red_frac(cell, thr=150):
    region = cell[2:-2, 2:-2]
    b = region[:, :, 0].astype(int)
    g = region[:, :, 1].astype(int)
    r = region[:, :, 2].astype(int)
    red = (r > thr) & (r > g * 1.5) & (r > b * 1.5)
    return float(red.mean())


def center_mode(cell):
    region = cell[4:12, 4:12].reshape(-1, 3)
    cnt = Counter()
    for p in region:
        cnt[tuple(int(v) for v in p)] += 1
    return cnt.most_common(3)


def main():
    windowing.enable_dpi_aware()
    cfg = load_cfg()
    win = windowing.find_game_window(cfg)
    if not win:
        print("NO WINDOW"); return 1
    windowing.bring_to_foreground(win["hwnd"])
    ensure_visible(win)
    frame = capture.grab_client(win)
    calib = calibrate.detect_board(frame)
    print("calib:", calib["rows"], "x", calib["cols"], flush=True)
    if calib["rows"] == 0:
        print("board not detected"); return 1
    rows, cols = calib["rows"], calib["cols"]
    ox, oy, cs = calib["board_origin"]["x"], calib["board_origin"]["y"], calib["cell_size"]

    # 重置新局
    f = calibrate.detect_anchor(frame).get("face_rect")
    if f and f["w"] < 40:
        action.restart((win["client_rect"]["x"] + f["x"] + f["w"] // 2,
                        win["client_rect"]["y"] + f["y"] + f["h"] // 2))
        time.sleep(0.7)
        frame = capture.grab_client(win)

    def opened_count(fr):
        got = 0
        for r in range(rows):
            for c in range(cols):
                if int(fr[oy + r * cs, ox + c * cs].max()) < 240:
                    got += 1
        return got

    prev_open = opened_count(frame)
    rng = np.random.default_rng()
    stall = 0
    for i in range(60):
        picked = None
        for _t in range(100):
            c = int(rng.integers(0, cols))
            r = int(rng.integers(0, rows))
            if int(frame[oy + r * cs, ox + c * cs].max()) >= 240:
                picked = (c, r)
                break
        if picked is None:
            print(f"[{i}] 无可翻开格，停"); break
        action.reveal(picked[0], picked[1], win, calib)
        time.sleep(0.5)
        frame = capture.grab_client(win)
        cur_open = opened_count(frame)
        delta = cur_open - prev_open
        prev_open = cur_open
        c, r = picked
        cell = frame[oy + r * cs:oy + (r + 1) * cs, ox + c * cs:ox + (c + 1) * cs]
        rf = red_frac(cell)
        cm = center_mode(cell)
        cv2.imwrite(f"{OUT}\\cell_{i:02d}.png", cell)
        cv2.imwrite(f"{OUT}\\board_{i:02d}.png", frame)
        opened = int(cell[0, 0].max()) < 240
        print(f"[{i}] click({c},{r}) newopen={delta:+3d} open={opened} "
              f"red_frac={rf:.3f} mode={cm}", flush=True)
        if delta == 0:
            stall += 1
            if stall >= CLICK_STALL:
                print(f"==> 连续 {CLICK_STALL} 次无新翻面 → 判定已死/卡，停。last=({c},{r})")
                break
        else:
            stall = 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())