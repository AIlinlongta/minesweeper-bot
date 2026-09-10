# -*- coding: utf-8 -*-
"""轻 CNN 训练（m1.5，v1.1 §3.11）。

极浅网络：Conv×2 + FC，输入 16×16 灰度（实测格子尺寸，设计旧值 33 已废），
输出 13 类（§2.6 label_id 表；mine 族合并为 11）。
训练数据来自 synth_data.make_dataset()（npz 缓存）。
产出 models/cnn_cell.pt（state_dict + 元数据）。
"""
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

ROOT = Path(r"D:\workbuddy\projects\minesweeper-bot")
sys.path.insert(0, str(ROOT))

from labeling.synth_data import NUM_CLASSES, OUT as DATASET_NPZ  # noqa: E402

PT_PATH = ROOT / "models" / "cnn_cell.pt"
EPOCHS = 8
BATCH = 256
LR = 1e-3


class CellCNN(nn.Module):
    def __init__(self, num_classes: int = NUM_CLASSES):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv2d(1, 8, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),   # 16→8
            nn.Conv2d(8, 16, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),  # 8→4
            nn.Flatten(),
            nn.Linear(16 * 4 * 4, 64), nn.ReLU(),
            nn.Linear(64, num_classes),
        )

    def forward(self, x):
        return self.net(x)


def build_model() -> CellCNN:
    return CellCNN()


def _batches(x, y, bs, shuffle, rng=None):
    idx = np.arange(len(x))
    if shuffle:
        (rng or np.random.default_rng(0)).shuffle(idx)
    for i in range(0, len(x), bs):
        j = idx[i:i + bs]
        yield (torch.from_numpy(x[j]).unsqueeze(1),      # (B,1,16,16)
               torch.from_numpy(y[j]))


def train(model=None, epochs: int = EPOCHS) -> dict:
    d = np.load(DATASET_NPZ)
    xt, yt = d["x_train"], d["y_train"]
    xv, yv = d["x_val"], d["y_val"]
    model = model or build_model()
    opt = torch.optim.Adam(model.parameters(), lr=LR)
    lossf = nn.CrossEntropyLoss()
    rng = np.random.default_rng(0)
    best_acc, best_state = 0.0, None
    for ep in range(1, epochs + 1):
        model.train()
        for xb, yb in _batches(xt, yt, BATCH, True, rng):
            opt.zero_grad()
            loss = lossf(model(xb), yb)
            loss.backward()
            opt.step()
        model.eval()
        correct = 0
        with torch.no_grad():
            for xb, yb in _batches(xv, yv, 1024, False):
                correct += int((model(xb).argmax(1) == yb).sum())
        acc = correct / len(xv)
        print(f"epoch {ep}: val_acc={acc:.4f}")
        if acc > best_acc:
            best_acc, best_state = acc, {k: v.clone() for k, v in model.state_dict().items()}
    model.load_state_dict(best_state)
    PT_PATH.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": best_state, "num_classes": NUM_CLASSES,
                "input": [1, 16, 16]}, PT_PATH)
    print(f"saved {PT_PATH}  best_val_acc={best_acc:.4f}")
    return {"val_acc": best_acc}


if __name__ == "__main__":
    result = train()
    sys.exit(0 if result["val_acc"] >= 0.99 else 1)
