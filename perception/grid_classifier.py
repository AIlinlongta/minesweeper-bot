# -*- coding: utf-8 -*-
"""轻 CNN 推理（m1.5，v1.1 §3.6）——模板主通道的校验通道。

onnxruntime CPU 推理：16×16 灰度格 → 13 类概率（§2.6 label_id 表，
mine/mine_red/mine_cross 合并为 label 11）。默认惰性加载 models/cnn_cell.onnx。
"""
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ONNX = ROOT / "models" / "cnn_cell.onnx"

# label_id → 规范化 state（§2.6；label 11 反向映射为规范态 "mine"）
LABEL_STATE = {
    0: "0", 1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8",
    9: "covered", 10: "flag", 11: "mine", 12: "question",
}


@lru_cache(maxsize=None)
def _session(onnx_path: str) -> ort.InferenceSession:
    return ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])


def load_model(onnx_path: str | Path = DEFAULT_ONNX) -> ort.InferenceSession:
    """加载 ONNX 模型（显式加载；classify 亦会惰性加载默认模型）。"""
    return _session(str(onnx_path))


def _preprocess(cell_bgr: np.ndarray) -> np.ndarray:
    if cell_bgr.shape[:2] != (16, 16):
        raise ValueError(f"格子尺寸应为 16×16，实得 {cell_bgr.shape[:2]}")
    gray = cv2.cvtColor(cell_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    return gray.reshape(1, 1, 16, 16)


def _softmax(logits: np.ndarray) -> np.ndarray:
    e = np.exp(logits - logits.max())
    return e / e.sum()


def classify(cell_bgr: np.ndarray,
             session: ort.InferenceSession | None = None) -> dict:
    """单格分类 → {"label_id": int, "state": str, "prob": float}（softmax argmax）。"""
    sess = session or _session(str(DEFAULT_ONNX))
    logits = sess.run(None, {"cell": _preprocess(cell_bgr)})[0][0]
    probs = _softmax(logits)
    label_id = int(probs.argmax())
    return {"label_id": label_id, "state": LABEL_STATE[label_id],
            "prob": float(probs[label_id])}
