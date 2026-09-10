# -*- coding: utf-8 -*-
"""导出 ONNX（m1.5，v1.1 §3.11）。

models/cnn_cell.pt → models/cnn_cell.onnx，
并用 onnxruntime 做数值对齐验证（logits max|Δ|<1e-4，argmax 全一致）。
"""
import sys
from pathlib import Path

import numpy as np
import torch

ROOT = Path(r"D:\workbuddy\projects\minesweeper-bot")
sys.path.insert(0, str(ROOT))

from labeling.synth_data import OUT as DATASET_NPZ  # noqa: E402
from labeling.train_cnn import PT_PATH, build_model  # noqa: E402

ONNX_PATH = ROOT / "models" / "cnn_cell.onnx"


def export_onnx(model=None, onnx_path: Path = ONNX_PATH) -> Path:
    model = model or build_model()
    ckpt = torch.load(PT_PATH, weights_only=True)
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    dummy = torch.zeros(1, 1, 16, 16)
    torch.onnx.export(model, dummy, str(onnx_path),
                      input_names=["cell"], output_names=["logits"],
                      dynamic_axes={"cell": {0: "batch"}, "logits": {0: "batch"}})
    return onnx_path


def verify(onnx_path: Path = ONNX_PATH, n: int = 512) -> float:
    import onnxruntime as ort
    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    model = build_model()
    ckpt = torch.load(PT_PATH, weights_only=True)
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    d = np.load(DATASET_NPZ)
    x = d["x_val"][:n]
    with torch.no_grad():
        ref = model(torch.from_numpy(x).unsqueeze(1)).numpy()
    got = sess.run(None, {"cell": x.reshape(-1, 1, 16, 16).astype(np.float32)})[0]
    max_diff = float(np.abs(ref - got).max())
    agree = float((ref.argmax(1) == got.argmax(1)).mean())
    print(f"onnxruntime parity: max|Δlogits|={max_diff:.2e} argmax一致率={agree:.4f}")
    if max_diff >= 1e-4 or agree < 1.0:
        raise RuntimeError("ONNX 与 torch 输出不一致")
    return max_diff


if __name__ == "__main__":
    p = export_onnx()
    print(f"saved {p}")
    verify()
    sys.exit(0)
