# -*- coding: utf-8 -*-
"""逐精灵输出亮段 7 段码状态 + ASCII 图，确定 12 个 LED 精灵与字符的映射。"""
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(r"D:\workbuddy\projects\minesweeper-bot")
SRC = ROOT / "perception/templates/raw/res_420.png"

img = cv2.imread(str(SRC), cv2.IMREAD_UNCHANGED)
bgr = img[:, :, :3] if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
H = bgr.shape[0]


def lit_matrix(k: int, off: int = 11) -> np.ndarray:
    idx = [(23 * k + off + i) % H for i in range(21)]
    c = bgr[idx]
    b, g, r = [c[:, :, i].astype(int) for i in range(3)]
    return (r > 180) & (g < 120) & (b < 120)


def seg_state(m: np.ndarray) -> dict:
    return {
        "top": m[0:3, 2:11].mean() > 0.5,
        "ul": m[3:9, 0:3].mean() > 0.5,
        "ur": m[3:9, 10:13].mean() > 0.5,
        "mid": m[9:12, 2:11].mean() > 0.5,
        "ll": m[12:18, 0:3].mean() > 0.5,
        "lr": m[12:18, 10:13].mean() > 0.5,
        "bot": m[18:21, 2:11].mean() > 0.5,
    }


SEG2DIGIT = {
    frozenset("top ul ur ll lr bot".split()): "0",
    frozenset("ur lr".split()): "1",
    frozenset("top ur mid ll bot".split()): "2",
    frozenset("top ur mid lr bot".split()): "3",
    frozenset("ul mid ur lr".split()): "4",
    frozenset("top ul mid lr bot".split()): "5",
    frozenset("top ul mid ll lr bot".split()): "6",
    frozenset("top ur lr".split()): "7",
    frozenset(*["top ul ur mid ll lr bot".split()],): "8",
    frozenset("top ul ur mid lr bot".split()): "9",
    frozenset("mid".split()): "-",
    frozenset(): " ",
}

for k in range(12):
    m = lit_matrix(k)
    st = seg_state(m)
    on = [n for n, v in st.items() if v]
    ch = SEG2DIGIT.get(frozenset(on), "?")
    print(f"cell {k:2d}: segs={sorted(on)} -> '{ch}'")
    if ch == "?":
        for row in m:
            print("   " + "".join("#" if v else "." for v in row))
