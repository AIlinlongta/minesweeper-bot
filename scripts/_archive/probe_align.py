# -*- coding: utf-8 -*-
"""离线对齐探针：用保存的实盘帧（scripts/_custom_frame.png）逐数字格在精灵条
全行范围搜索最优切片起点 y*，输出 per-digit y* 直方图与对应 diff。

用途：修正 slice_sprites.py 的 START_ROW（当前所有数字模板整体下移 3 行）。
"""
import sys
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np
import yaml

ROOT = Path(r"D:\workbuddy\projects\minesweeper-bot")
sys.path.insert(0, str(ROOT))

from perception import calibrate, grid_perception  # noqa: E402

FRAME = ROOT / "scripts/_custom_frame.png"
STRIP = ROOT / "perception/templates/raw/res_410.png"

ORACLE_RGB = {
    (0, 0, 255): 1, (0, 128, 0): 2, (255, 0, 0): 3, (0, 0, 128): 4,
    (128, 0, 0): 5, (0, 128, 128): 6, (0, 0, 0): 7, (128, 128, 128): 8,
}


def oracle_digit(cell):
    """中心 10x10 主色匹配（BGR → RGB 查表）。"""
    region = cell[3:13, 3:13].reshape(-1, 3)
    cnt = Counter(tuple(int(v) for v in p)[::-1] for p in region)
    for rgb, _ in cnt.most_common():
        if rgb in ORACLE_RGB:
            return ORACLE_RGB[rgb]
    return None


def main():
    frame = cv2.imread(str(FRAME), cv2.IMREAD_COLOR)
    if frame is None:
        print("frame load failed")
        return 1
    calib = calibrate.detect_board(frame)
    print(f"calib: {calib['rows']}x{calib['cols']} cell={calib['cell_size']} "
          f"origin={calib['board_origin']} conf={calib['confidence']}")
    if calib["rows"] == 0:
        print("detect_board failed on saved frame")
        return 1

    strip = cv2.imread(str(STRIP), cv2.IMREAD_UNCHANGED)
    if strip is None:
        print("strip load failed")
        return 1
    if strip.ndim == 2 or strip.shape[2] == 1:
        strip = cv2.cvtColor(strip, cv2.COLOR_GRAY2BGR)
    H = strip.shape[0]
    strip16 = strip.astype(np.int16)
    print(f"strip: {strip.shape}")

    best_y_hist = defaultdict(Counter)   # digit -> Counter(best_y)
    diff_at = defaultdict(list)          # digit -> [diff at best_y]
    per_digit_y = defaultdict(list)

    n = 0
    for r in range(calib["rows"]):
        for c in range(calib["cols"]):
            cell = grid_perception.slice_cell(frame, calib, c, r)
            if int(cell[0, 0].max()) >= 240:      # 覆盖格跳过
                continue
            od = oracle_digit(cell)
            if od is None:
                continue
            n += 1
            cell16 = cell.astype(np.int16)
            # 全行搜索最优 y*
            diffs = np.array([
                np.abs(cell16 - strip16[y:y + 16, 0:16]).mean()
                for y in range(0, H - 15)
            ])
            y_star = int(diffs.argmin())
            best_y_hist[od][y_star] += 1
            diff_at[od].append(float(diffs[y_star]))
            per_digit_y[od].append(y_star)

    print(f"\ndigit cells sampled: {n}")
    print("\nper-digit best strip-row y* histogram:")
    for d in sorted(best_y_hist):
        h = best_y_hist[d].most_common(5)
        v = sorted(diff_at[d])
        print(f"  digit_{d}: top_y*={h}  diff@y* min={v[0]:.1f} med={v[len(v)//2]:.1f} max={v[-1]:.1f}")

    # 一致性结论：若每个数字的 y* 高度集中 → 直接给出新 START_ROW
    print("\nsuggested START_ROW:")
    for d in sorted(per_digit_y):
        ys = per_digit_y[d]
        med = int(np.median(ys))
        agree = sum(1 for y in ys if abs(y - med) <= 1) / len(ys)
        print(f"  digit_{d}: median_y*={med} agree±1={agree:.2%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
