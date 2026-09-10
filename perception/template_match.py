# -*- coding: utf-8 -*-
"""模板匹配分类（主通道，源自 winmine.exe 位图资源原样提取）。

模板目录 perception/templates/：digit_1..8 / blank / question / flag /
mine / mine_red / mine_cross（16x16 BGR，与运行时渲染同源，像素级一致）。

匹配：逐标签 mean(|cell - tmpl|)，取最小；阈值内判定为该标签。
covered 无精灵（运行时为 3D 斜面绘制）→ 仍由边框高光规则判定。
"""
from pathlib import Path

import cv2
import numpy as np

_DIR = Path(__file__).parent / "templates"

# 打开态模板集合（covered 不在其中）
_OPEN_LABELS = ["digit_1", "digit_2", "digit_3", "digit_4", "digit_5",
                "digit_6", "digit_7", "digit_8", "blank", "question",
                "mine", "mine_red", "mine_cross"]
_FLAG_LABEL = "flag"

# mean abs diff 接受阈值（同源渲染实测 ~0-3；阈值放宽抗截屏抖动）
ACCEPT_DIFF = 20.0

_cache: dict[str, np.ndarray] | None = None


def load() -> dict[str, np.ndarray]:
    """懒加载模板 {label: BGR uint8 16x16}。"""
    global _cache
    if _cache is None:
        _cache = {}
        for name in [*_OPEN_LABELS, _FLAG_LABEL]:
            p = _DIR / f"{name}.png"
            img = cv2.imread(str(p), cv2.IMREAD_COLOR)
            if img is None:
                raise FileNotFoundError(f"template missing: {p}")
            _cache[name] = img
    return _cache


def best(cell: np.ndarray, labels: list[str] | None = None) -> tuple[str, float]:
    """返回 (最佳标签, mean abs diff)。labels 限定候选集（None=全部打开态）。"""
    tpls = load()
    if labels is None:
        labels = _OPEN_LABELS
    best_label, best_diff = "", 1e9
    cell16 = cell.astype(np.int16)
    for lb in labels:
        d = float(np.abs(cell16 - tpls[lb].astype(np.int16)).mean())
        if d < best_diff:
            best_label, best_diff = lb, d
    return best_label, best_diff
