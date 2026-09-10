# -*- coding: utf-8 -*-
"""离线精灵条结构分析：扫描 res_410.png 每行的深灰(≈128)占比，
精灵顶边框行应 ≈ 全行深灰。输出候选边界行与当前 START_ROW 的对照。"""
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(r"D:\workbuddy\projects\minesweeper-bot")
STRIP = ROOT / "perception/templates/raw/res_410.png"

CURRENT = {
    "flag": 3, "question": 19, "mine_red": 35, "mine_cross": 51, "mine": 67,
    "digit_8": 99, "digit_7": 115, "digit_6": 131, "digit_5": 147,
    "digit_4": 163, "digit_3": 179, "digit_2": 195, "digit_1": 211,
    "blank": 227,
}

strip = cv2.imread(str(STRIP), cv2.IMREAD_UNCHANGED)
if strip is None:
    print("strip load failed"); sys.exit(1)
if strip.ndim == 2 or strip.shape[2] == 1:
    strip = cv2.cvtColor(strip, cv2.COLOR_GRAY2BGR)
H = strip.shape[0]
print(f"strip shape={strip.shape}")

g = strip.astype(np.int16)
dark = (np.abs(g - 128).max(axis=2) <= 12)          # 接近 128 的灰
light = (np.abs(g - 192).max(axis=2) <= 12)         # 接近 192 的灰
white = (g.min(axis=2) >= 240)

print("\nrow | dark_frac light_frac white_frac | marker")
bounds = []
for y in range(H):
    d, l, w = dark[y].mean(), light[y].mean(), white[y].mean()
    mark = ""
    if d >= 0.85:
        mark = "<== sprite-top candidate"
        bounds.append(y)
    cur = [name for name, yy in CURRENT.items() if yy == y]
    tag = f"  CURRENT={cur}" if cur else ""
    if mark or cur or d > 0.3:
        print(f"{y:4d} | {d:.2f} {l:.2f} {w:.2f} | {mark}{tag}")

print(f"\nall dark-boundary rows: {bounds}")
diffs = [b - a for a, b in zip(bounds, bounds[1:])]
print(f"boundary gaps: {diffs}")
