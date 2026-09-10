# -*- coding: utf-8 -*-
"""m1 离线验收：直接对一张截图做逐格 OCR，与 oracle 颜色对照，分数字统计。
用法: python scripts/eval_screenshot.py <图片路径>
"""
import sys
from collections import Counter

import cv2
import numpy as np

ROOT = r"D:\workbuddy\projects\minesweeper-bot"
sys.path.insert(0, ROOT)

from perception import calibrate, grid_perception  # noqa: E402

ORACLE = {
    (0, 0, 255): 1, (0, 128, 0): 2, (255, 0, 0): 3, (0, 0, 128): 4,
    (128, 0, 0): 5, (0, 128, 128): 6, (0, 0, 0): 7, (128, 128, 128): 8,
    (0, 0, 0): 7, (128, 128, 128): 8,
}
DIGITS = {str(i) for i in range(1, 9)}


def main():
    if len(sys.argv) < 2:
        print("usage: eval_screenshot.py <png_path>")
        return 2
    path = sys.argv[1]
    frame = cv2.imread(path)
    if frame is None:
        print("cannot read:", path)
        return 2

    calib = calibrate.detect_board(frame)
    if calib["rows"] == 0:
        print("board not detected on this image; maybe crop to client area only")
        return 1
    print(f"detected {calib['rows']}x{calib['cols']} cell={calib['cell_size']} "
          f"origin={calib['board_origin']}", flush=True)

    stats = {"n": 0, "correct": 0, "mism": []}
    hist_true: Counter = Counter()
    hist_hit: Counter = Counter()
    for r in range(calib["rows"]):
        for c in range(calib["cols"]):
            cell = grid_perception.slice_cell(frame, calib, c, r)
            if int(cell[0, 0].max()) >= 240:
                continue  # 未翻开
            # oracle：中心 8x8 众数数字色
            region = cell[4:12, 4:12].reshape(-1, 3)
            cnt = Counter(tuple(int(v) for v in p) for p in region)
            od = None
            for rgb, _ in cnt.most_common():
                b, g, rr = rgb
                if (rr, g, b) in ORACLE:
                    od = ORACLE[(rr, g, b)]
                    break
            state = grid_perception.classify_cell(cell)["state"]
            if od is None:
                if state in DIGITS:
                    stats["mism"].append((c, r, "blank", state))
                continue
            hist_true[od] += 1
            if state in DIGITS and int(state) == od:
                stats["correct"] += 1
                hist_hit[od] += 1
            else:
                stats["mism"].append((c, r, od, state))
            stats["n"] += 1

    print(f"\n数字样本 n={stats['n']} correct={stats['correct']} "
          f"acc={stats['correct']/stats['n']:.3f}" if stats["n"] else "\nno digit samples")
    print("按数字正确/总样本:", {d: f"{hist_hit.get(d,0)}/{hist_true.get(d,0)}"
                          for d in sorted(hist_true)})
    if stats["mism"]:
        print("mismatch(c,r,true,ocr):", stats["mism"][:40])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())