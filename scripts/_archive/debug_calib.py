# -*- coding: utf-8 -*-
"""把当前 fresh 客户区存 PNG + 打印 detect_board 内部（hproj/vproj 高亮结构）。"""
import sys, subprocess, time
import numpy as np
import cv2
import yaml

ROOT = r"D:\workbuddy\projects\minesweeper-bot"
sys.path.insert(0, ROOT)
from perception import calibrate, capture, windowing  # noqa: E402


def main():
    windowing.enable_dpi_aware()
    with open(ROOT + r"\config\window.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    win = windowing.find_game_window(cfg)
    if not win:
        print("NO WINDOW"); return 1
    windowing.bring_to_foreground(win["hwnd"])
    time.sleep(0.5)
    frame = capture.grab_client(win)
    cv2.imwrite(ROOT + r"\scripts\_fresh.png", frame)
    print("saved _fresh.png", frame.shape)

    calib = calibrate.detect_board(frame)
    print("detect rows/cols:", calib["rows"], "x", calib["cols"])

    gray = calibrate.preprocess_grid(frame)
    bright = (gray > 200).astype(np.uint8)
    hproj = bright.sum(axis=1).astype(int)
    thr = max(2, int(0.5 * hproj.max()))
    print("hproj peak", hproj.max(), "thr", thr)
    runs = []
    active = None
    for i, v in enumerate(hproj):
        if int(v) >= thr and active is None:
            active = i
        elif int(v) < thr and active is not None:
            runs.append(active); active = None
    if active is not None:
        runs.append(active)
    print("h bright runs(rows):", runs)
    print("n_runs", len(runs), "diffs", [b-a for a,b in zip(runs,runs[1:])])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())