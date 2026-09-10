# -*- coding: utf-8 -*-
"""截屏：mss 静态画面 → BGR ndarray。

开发设计 §三.3.3。仅 mss 后端。
"""
import mss
import numpy as np


def grab(region: dict) -> np.ndarray:
    """抓取屏幕区域，返回 BGR (h, w, 3)。

    region: {"left": x, "top": y, "width": w, "height": h}（屏幕坐标）。
    """
    with mss.mss() as sct:
        shot = sct.grab(region)
        # mss 输出 BGRA；转 BGR
        img = np.asarray(shot, dtype=np.uint8)[:, :, :3]
    return img


def grab_client(win: dict) -> np.ndarray:
    """抓客户区。win 为 windowing 输出的 WindowInfo。"""
    r = win["client_rect"]
    return grab({"left": r["x"], "top": r["y"], "width": r["w"], "height": r["h"]})


def self_check(frame: np.ndarray) -> bool:
    """自检：非黑屏（均值 > 阈值）+ 尺寸合理。"""
    if frame is None or frame.size == 0 or frame.ndim != 3:
        return False
    if frame.shape[0] < 10 or frame.shape[1] < 10:
        return False
    mean = float(frame.mean())
    return mean > 8.0