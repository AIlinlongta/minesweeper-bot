# -*- coding: utf-8 -*-
"""把 res_410 切成 16 块，拼一张带索引的放大对照图 + 每块中心色清单，供人工命名。"""
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(r"D:\workbuddy\projects\minesweeper-bot")
SRC = ROOT / "perception/templates/raw/res_410.png"
OUT = ROOT / "scripts"

strip = cv2.imread(str(SRC), cv2.IMREAD_UNCHANGED)
if strip.ndim == 2 or strip.shape[2] == 1:
    strip = cv2.cvtColor(strip, cv2.COLOR_GRAY2BGR)
h, w = strip.shape[:2]
ts = 16
tiles = []
if h >= w:
    for i in range(h // ts):
        tiles.append(strip[i * ts:(i + 1) * ts, 0:ts])
else:
    for i in range(w // ts):
        tiles.append(strip[0:ts, i * ts:(i + 1) * ts])

log = open(OUT / "_tile_colors.txt", "w", encoding="utf-8")
big = cv2.resize(strip, None, fx=10, fy=10, interpolation=cv2.INTER_NEAREST)
cv2.imwrite(str(OUT / "_strip_big.png"), big)
for i, t in enumerate(tiles):
    region = t[6:10, 6:10].reshape(-1, 3)
    cnt: Counter = Counter(tuple(int(v) for v in p) for p in region)
    top = ", ".join(f"{c}@{n}" for c, n in cnt.most_common(4))
    print(f"tile {i:02d}: {top}", file=log)
log.close()
print("done")
