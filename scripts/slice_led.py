# -*- coding: utf-8 -*-
"""从 res_420 LED 数字条切出 0-9 模板到 perception/templates/counter/。

条带 13x276 = 12 精灵 × 23px，内容自 23k+11 起、环绕到下一单元（偏移 11-22 + 0-8），
目检+7 段码验证（2026-09-09）：cell0=空格(暗8底) / cell1-10 = 数字 9..0 降序 / cell11=空尾巴。
初级盘雷数 10、bot 只落确定旗，计数器不会为负，'-' 精灵缺失不影响 m3。

模板=全彩 glyph（亮段+暗段背景），与运行时渲染同源（同 res_420 blit）。
"""
import sys
from pathlib import Path

import cv2

ROOT = Path(r"D:\workbuddy\projects\minesweeper-bot")
SRC = ROOT / "perception/templates/raw/res_420.png"
OUT = ROOT / "perception/templates/counter"

PITCH = 23
START = 11  # 精灵内容在 23px 单元内的起始偏移
GLYPH_H = 21


def main() -> int:
    strip = cv2.imread(str(SRC), cv2.IMREAD_UNCHANGED)
    if strip is None:
        print("SRC load failed")
        return 1
    if strip.ndim == 2 or strip.shape[2] == 1:
        strip = cv2.cvtColor(strip, cv2.COLOR_GRAY2BGR)
    h = strip.shape[0]
    OUT.mkdir(exist_ok=True)
    n = 0
    for digit in range(10):
        cell = 10 - digit  # cell1=9 ... cell10=0（7 段码+目检验证）
        base = cell * PITCH + START
        idx = [(base + i) % h for i in range(GLYPH_H)]
        glyph = strip[idx]  # 13x21 BGR
        if glyph.shape[0] != GLYPH_H or glyph.shape[1] != 13:
            print(f"led_{digit}: bad shape {glyph.shape}")
            return 1
        cv2.imwrite(str(OUT / f"led_{digit}.png"), glyph)
        n += 1
    print(f"saved {n} LED templates -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
