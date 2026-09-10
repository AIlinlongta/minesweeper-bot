# -*- coding: utf-8 -*-
"""探查 raw 精灵条（411/420/421/430/431）：尺寸 + 按深灰边界行粗切保存放大图，供目检 LED 数字。"""
from pathlib import Path

import cv2
import numpy as np

RAW = Path(r"D:\workbuddy\projects\minesweeper-bot\perception\templates\raw")
DBG = Path(r"D:\workbuddy\projects\minesweeper-bot\scripts\_m3_dbg")
DBG.mkdir(exist_ok=True)

for rid in (411, 420, 421, 430, 431):
    p = RAW / f"res_{rid}.png"
    img = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
    if img is None:
        print(f"{rid}: load fail")
        continue
    if img.ndim == 3 and img.shape[2] == 4:
        alpha = img[:, :, 3]
        bgr = img[:, :, :3].copy()
        bgr[alpha == 0] = (255, 0, 255)  # 透明区品红标记
    elif img.ndim == 2:
        bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    else:
        bgr = img
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    # 每行暗像素占比（深灰 <128）
    dark = (gray < 128).mean(axis=1)
    bounds = [y for y in range(h) if dark[y] >= 0.85]
    print(f"res_{rid}: {w}x{h} border_rows={bounds}")
    big = cv2.resize(bgr, (w * 8, h * 8), interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(DBG / f"strip_{rid}_x8.png"), big)
    # 逐 16 列块也存一份（若是 LED 条，单字宽度可能非 16）
    cv2.imwrite(str(DBG / f"strip_{rid}.png"), bgr)
print("done ->", DBG)
