# -*- coding: utf-8 -*-
"""按目检+中心色验证的显式起点，把 res_410 精灵条切成模板到 perception/templates/。

条带非均匀 pitch（flag/question 凸起白边框、34/35 双暗行错位），周期推算不可靠。
起点经中心主色逐一验证（2026-09-09）：
  3 flag / 19 question / 35 mine_red / 51 mine_cross / 67 mine / 83 question按下态(略)
  99 digit_8 / 115 digit_7 / 131 digit_6 / 147 digit_5 / 163 digit_4
  179 digit_3 / 195 digit_2 / 211 digit_1 / 227 blank
"""
import sys
from pathlib import Path

import cv2

ROOT = Path(r"D:\workbuddy\projects\minesweeper-bot")
SRC = ROOT / "perception/templates/raw/res_410.png"
OUT = ROOT / "perception/templates"

START_ROW = {
    "flag": 3, "question": 19, "mine_red": 35, "mine_cross": 51, "mine": 67,
    "digit_8": 99, "digit_7": 115, "digit_6": 131, "digit_5": 147,
    "digit_4": 163, "digit_3": 179, "digit_2": 195, "digit_1": 211,
    "blank": 227,
}


def main() -> int:
    strip = cv2.imread(str(SRC), cv2.IMREAD_UNCHANGED)
    if strip is None:
        print("SRC load failed"); return 1
    if strip.ndim == 2 or strip.shape[2] == 1:
        strip = cv2.cvtColor(strip, cv2.COLOR_GRAY2BGR)
    h = strip.shape[0]
    for p in OUT.glob("*.png"):
        if p.parent.name != "raw":
            p.unlink()
    for label, y in START_ROW.items():
        if y + 16 > h:
            print(f"{label}: range overflow"); return 1
        cv2.imwrite(str(OUT / f"{label}.png"), strip[y:y + 16, 0:16])
    print(f"saved {len(START_ROW)} templates")
    return 0


if __name__ == "__main__":
    sys.exit(main())
