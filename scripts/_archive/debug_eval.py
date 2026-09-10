# -*- coding: utf-8 -*-
"""对照验证：精确点格 (cr,rr) → 切片 → 存图；整盘框出点击格 → 存图。
确认 cut 的格子 == 点击的格子。"""
import sys
import time

import cv2
import numpy as np

ROOT = r"D:\workbuddy\projects\minesweeper-bot"
sys.path.insert(0, ROOT)

from perception import calibrate, capture, windowing  # noqa: E402
from action import action  # noqa: E402
import yaml  # noqa: E402


def load_cfg():
    with open(ROOT + r"\config\window.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    windowing.enable_dpi_aware()
    cfg = load_cfg()
    win = windowing.find_game_window(cfg)
    windowing.bring_to_foreground(win["hwnd"])
    time.sleep(0.6)
    frame = capture.grab_client(win)
    calib = calibrate.detect_board(frame)
    print("calib:", calib["rows"], "x", calib["cols"], "origin", calib["board_origin"],
          "cell", calib["cell_size"], flush=True)
    if calib["rows"] == 0:
        print("board not detected; abort"); return 1

    rows, cols = calib["rows"], calib["cols"]
    # 重置到新局
    f = calibrate.detect_anchor(frame).get("face_rect")
    if f and f["w"] < 40:
        action.restart((win["client_rect"]["x"] + f["x"] + f["w"] // 2,
                        win["client_rect"]["y"] + f["y"] + f["h"] // 2))
        time.sleep(0.7)
        frame = capture.grab_client(win)

    cr, rr = 3, 3  # 目标格
    sy, sx = windowing.resolve_screen_cell(win, calib, cr, rr)
    print(f"click cell ({cr},{rr}) -> screen ({sx},{sy})", flush=True)
    action.reveal(cr, rr, win, calib)
    time.sleep(0.6)
    frame = capture.grab_client(win)

    # 全盘，框出点击格（红框）标注坐标
    marked = frame.copy()
    ox, oy, cs = calib["board_origin"]["x"], calib["board_origin"]["y"], calib["cell_size"]
    cv2.rectangle(marked, (ox + cr * cs, oy + rr * cs), (ox + (cr + 1) * cs, oy + (rr + 1) * cs),
                  (0, 0, 255), 2)

    # 逐个格子切片打印顶边框 + 是否已翻开，并在本格图上标 (c,r)
    print("\n9x9 附近区域逐格顶边框亮度(已翻开=<128)：")
    for r in range(max(0, rr - 1), min(rows, rr + 2)):
        line = []
        for c in range(max(0, cr - 2), min(cols, cr + 3)):
            cell = frame[oy + r * cs:oy + (r + 1) * cs, ox + c * cs:ox + (c + 1) * cs]
            line.append(f"{c},{r}@{int(cell[0, 0].max())}")
        print("  " + "  ".join(line))

    # 存图
    cv2.imwrite(ROOT + r"\scripts\_probe_whole.png", marked)
    target = frame[oy + rr * cs:oy + (rr + 1) * cs, ox + cr * cs:ox + (cr + 1) * cs]
    cv2.imwrite(ROOT + r"\scripts\_probe_target_cell.png", target)
    # 目标格放大便于看
    big = cv2.resize(target, None, fx=12, fy=12, interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(ROOT + r"\scripts\_probe_target_big.png", big)
    print("\n已存图：")
    print("  _probe_whole.png        (红框=点击格，全盘)")
    print("  _probe_target_cell.png  (原尺寸目标格切片)")
    print("  _probe_target_big.png   (目标格 12x 放大)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())