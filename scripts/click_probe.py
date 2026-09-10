# -*- coding: utf-8 -*-
"""m0 点击回环探针。

流程：启动 → 找窗 → 截屏自检 → 自校准(board_origin/cell_size) → 点击(0,0) → 反馈验证翻开。
产物：stdout 结构化结果（board_origin/cell_size/点击前后差异/是否翻开）。
"""
import json
import subprocess
import sys
import time

import numpy as np
import yaml

ROOT = r"D:\workbuddy\projects\minesweeper-bot"
sys.path.insert(0, ROOT)

from perception import capture, calibrate, windowing  # noqa: E402
from action import action  # noqa: E402


def load_cfg() -> dict:
    with open(ROOT + r"\config\window.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def cell_img(frame: np.ndarray, calib: dict, c: int, r: int) -> np.ndarray:
    ox, oy = calib["board_origin"]["x"], calib["board_origin"]["y"]
    cs = calib["cell_size"]
    return frame[oy + r * cs:oy + (r + 1) * cs, ox + c * cs:ox + (c + 1) * cs]


def main() -> int:
    windowing.enable_dpi_aware()
    cfg = load_cfg()

    win = windowing.find_game_window(cfg)
    if not win:
        proc = subprocess.Popen([cfg["exe_path"]])
        time.sleep(2.5)
        win = windowing.find_game_window(cfg, pid=proc.pid)
    if not win:
        print(json.dumps({"result": "NOT_FOUND"}, ensure_ascii=False))
        return 1

    windowing.bring_to_foreground(win["hwnd"])
    time.sleep(0.4)

    frame = capture.grab_client(win)
    if not capture.self_check(frame):
        print(json.dumps({"result": "CAPTURE_FAIL"}, ensure_ascii=False))
        return 1

    calib = calibrate.detect_board(frame)
    if not calibrate.validate(calib):
        print(json.dumps({"result": "CALIB_FAIL", "calib": calib}, ensure_ascii=False))
        return 1

    before = cell_img(frame, calib, 0, 0).astype(np.float32)
    sx, sy = action.reveal(0, 0, win, calib)
    time.sleep(0.4)
    after = cell_img(capture.grab_client(win), calib, 0, 0).astype(np.float32)

    diff = float(np.abs(before - after).mean())
    changed = diff > 5.0

    out = {
        "result": "OK" if changed else "NO_CHANGE",
        "bind_method": win["bind_method"],
        "client_rect": win["client_rect"],
        "difficulty": calib["difficulty"],
        "board_origin": calib["board_origin"],
        "cell_size": calib["cell_size"],
        "rows": calib["rows"],
        "cols": calib["cols"],
        "clicked_screen": [sx, sy],
        "cell_diff_mean": round(diff, 2),
        "opened": changed,
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0 if changed else 1


if __name__ == "__main__":
    raise SystemExit(main())