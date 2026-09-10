# -*- coding: utf-8 -*-
"""程序化合成格子训练集（m1.5，v1.1 §3.11）。

真值同源性：基准精灵 = winmine.exe 位图切片模板（m1 已验证与实盘渲染 diff=0.0），
covered 精灵取自实盘新局帧（条带内无该精灵，256 格实测逐格一致）。

label_id 口径（§2.6 表，实际 13 类；mine/mine_red/mine_cross 合并到 11）：
  0=blank  1..8=digit_1..8  9=covered  10=flag  11=mine族  12=question

增强仅做光度扰动（噪声/亮度/对比度/轻模糊/椒盐），不做几何位移——
主通道像素级对齐，位移会制造实盘不存在的输入并损害双通道一致性。
输出 data/m15/dataset.npz：x 为灰度 float32 /255 (N,16,16)，y 为 label_id。
"""
import random
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(r"D:\workbuddy\projects\minesweeper-bot")
TPL_DIR = ROOT / "perception" / "templates"
OUT = ROOT / "data" / "m15" / "dataset.npz"

DIGITS = {f"digit_{i}": i for i in range(1, 9)}
# label_id → 生成该类的基准精灵（mine 族 3 精灵均匀混采）
CLASS_SPRITES: dict[int, list[str]] = {
    0: ["blank"], 1: ["digit_1"], 2: ["digit_2"], 3: ["digit_3"],
    4: ["digit_4"], 5: ["digit_5"], 6: ["digit_6"], 7: ["digit_7"],
    8: ["digit_8"], 9: ["covered"], 10: ["flag"],
    11: ["mine", "mine_red", "mine_cross"], 12: ["question"],
}
NUM_CLASSES = len(CLASS_SPRITES)          # 13
CELL = 16                                 # 实测格子 16×16（设计文档旧值 33 已废）


def load_base_sprites() -> dict[str, np.ndarray]:
    sprites = {}
    for p in sorted(TPL_DIR.glob("*.png")):
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(p)
        assert img.shape == (CELL, CELL, 3), f"{p.name} 尺寸异常 {img.shape}"
        sprites[p.stem] = img
    missing = {s for lst in CLASS_SPRITES.values() for s in lst} - set(sprites)
    if missing:
        raise FileNotFoundError(f"缺基准精灵: {missing}")
    return sprites


def augment(cell_bgr: np.ndarray, rng: random.Random) -> np.ndarray:
    """光度增强：保持调色板可辨识范围内的实拍风格扰动。"""
    out = cell_bgr.astype(np.float32)
    if rng.random() < 0.8:                       # 高斯噪声
        out += rng.gauss(0.0, rng.uniform(0.5, 6.0))
    if rng.random() < 0.6:                       # 亮度平移
        out += rng.uniform(-8.0, 8.0)
    if rng.random() < 0.4:                       # 对比度（围绕中灰 128）
        out = (out - 128.0) * rng.uniform(0.92, 1.08) + 128.0
    if rng.random() < 0.15:                      # 轻模糊（截获软化）
        out = cv2.GaussianBlur(out, (3, 3), 0.5)
    out = np.clip(out, 0, 255).astype(np.uint8)
    if rng.random() < 0.1:                       # 椒盐（采集毛刺）
        mask = np.random.default_rng(rng.randrange(2**31)).random((CELL, CELL)) < 0.003
        out[mask] = rng.choice([0, 255])
    return out


def make_dataset(per_class: int = 4000, val_per_class: int = 400,
                 seed: int = 20260909) -> dict:
    rng = random.Random(seed)
    sprites = load_base_sprites()
    xs, ys, xvs, yvs = [], [], [], []
    for label, names in CLASS_SPRITES.items():
        n_val = val_per_class
        n_train = per_class
        for i in range(n_train + n_val):
            base = sprites[rng.choice(names)]
            sample = augment(base, rng)
            gray = cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
            if i < n_val:
                xvs.append(gray); yvs.append(label)
            else:
                xs.append(gray); ys.append(label)
    data = {
        "x_train": np.asarray(xs, np.float32),
        "y_train": np.asarray(ys, np.int64),
        "x_val": np.asarray(xvs, np.float32),
        "y_val": np.asarray(yvs, np.int64),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(OUT, **data)
    return data


if __name__ == "__main__":
    d = make_dataset()
    print(f"saved {OUT}  train={d['x_train'].shape} val={d['x_val'].shape} "
          f"classes={NUM_CLASSES}")
