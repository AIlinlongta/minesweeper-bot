# -*- coding: utf-8 -*-
"""逐格分类（模板匹配主通道）。

判定规则（实测 2026-09-08）：
  - 格子左上角像素（其自身 1px 边框）：
      * 白色斜面(min>=200) → 凸起态：flag / question / mine_red（模板）或 covered
      * 暗(128)            → 已挖开：模板匹配数字 1-8 / 空白 / 问号 / 三种雷态
  - 模板全部来自 winmine.exe 位图资源原样提取（与运行时渲染同源，像素级一致），
    覆盖全部 15 类；covered 无精灵（运行时 3D 斜面绘制）→ 边框规则判定。
  - 模板未命中 → 返回 unknown（不硬猜），上层重采样；OCR 已移除（错误来源）。

输出 CellClassify（见开发设计 §2.6）。
"""
import numpy as np

from perception import template_match

# label_id：0-8 数字 / 9 covered / 10 flag / 11 mine / 12 question
LABEL_DIGIT_BASE = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8}

# 模板标签 → (state, label_id, is_mine)
_TMPL_STATE = {
    **{f"digit_{i}": (str(i), i, False) for i in range(1, 9)},
    "blank": ("0", 0, False),
    "question": ("question", 12, False),
    "flag": ("flag", 10, True),
    "mine": ("mine", 11, True),
    "mine_red": ("mine_red", 11, True),
    "mine_cross": ("mine_cross", 11, True),
}


def slice_cell(frame: np.ndarray, calib: dict, c: int, r: int) -> np.ndarray:
    """按自校准结果切单格（含自身 1px 边框）。"""
    ox = calib["board_origin"]["x"]
    oy = calib["board_origin"]["y"]
    cs = calib["cell_size"]
    return frame[oy + r * cs:oy + (r + 1) * cs, ox + c * cs:ox + (c + 1) * cs]


def _center_rgb(cell: np.ndarray) -> tuple[int, int, int]:
    """中心 3×3 中值色（RGB 元组）。"""
    h, w = cell.shape[:2]
    cy, cx = h // 2, w // 2
    patch = cell[cy - 1:cy + 2, cx - 1:cx + 2].reshape(-1, 3)
    b, g, r = [int(np.median(patch[:, i])) for i in range(3)]
    return (r, g, b)


def _is_flag(cell: np.ndarray) -> bool:
    """中心区域红色像素占比（旗为红色）。"""
    h, w = cell.shape[:2]
    region = cell[4:h - 4, 4:w - 4]
    if region.size == 0:
        return False
    b = region[:, :, 0].astype(int)
    g = region[:, :, 1].astype(int)
    r = region[:, :, 2].astype(int)
    red = (r > 120) & (r > g * 1.6) & (r > b * 1.6)
    return float(red.mean()) > 0.25


def _has_sprite(cell: np.ndarray) -> bool:
    """中心 8x8 是否含暗色精灵像素（旗杆/问号/雷体）。

    覆盖格=纯白斜面+浅灰内芯，中心无暗像素（实测 0）；
    flag/question/mine_red 模板中心暗像素 14/18/56。用于阻止
    纯覆盖格被凸起态模板（diff 恰好压线）误吸。
    """
    return int((cell[4:12, 4:12].max(axis=2) < 100).sum()) >= 3


def _rgb_hex(rgb: tuple[int, int, int]) -> str:
    return "%02X%02X%02X" % rgb


def _is_blank_region(cell: np.ndarray) -> bool:
    """中心区域是否纯空（无色差）。空白格=均匀浅灰；数字格=含彩色/深色像素。

    颜色无关：不依赖固定 RGB，仅判中心区域 RGB 通道扩散度。
    """
    h, w = cell.shape[:2]
    region = cell[3:h - 3, 3:w - 3]
    if region.size == 0:
        return True
    b = region[:, :, 0].astype(int)
    g = region[:, :, 1].astype(int)
    r = region[:, :, 2].astype(int)
    spread = (r.max() - r.min()) + (g.max() - g.min()) + (b.max() - b.min())
    return spread < 60


def classify_cell(cell: np.ndarray) -> dict:
    """单格分类（模板主通道）。

    模板未命中 → state="unknown"（confidence 0），由上层重采样，不硬猜。
    """
    rgb = _center_rgb(cell)
    hexc = _rgb_hex(rgb)

    # 白色斜面边框 → covered / flag / question / mine_red(凸起态红底雷)
    # 用 min 判白：白色斜面 tl=(255,255,255)；普通数字格 tl=(128,128,128)
    if int(cell[0, 0].min()) >= 200:
        lb, diff = template_match.best(cell, ["flag", "question", "mine_red"])
        if diff <= template_match.ACCEPT_DIFF and _has_sprite(cell):
            state, lid, im = _TMPL_STATE[lb]
            return {"state": state, "source": "template", "confidence": 1.0,
                    "is_mine": im, "label_id": lid, "color": hexc}
        if _is_flag(cell):
            return {"state": "flag", "source": "template", "confidence": 1.0,
                    "is_mine": True, "label_id": 10, "color": hexc}
        return {"state": "covered", "source": "template", "confidence": 1.0,
                "is_mine": False, "label_id": 9, "color": hexc}

    # 已挖开 → 模板匹配（数字/空白/问号/雷）主通道
    lb, diff = template_match.best(cell)
    if diff <= template_match.ACCEPT_DIFF:
        state, lid, im = _TMPL_STATE[lb]
        conf = round(max(0.0, 1.0 - diff / template_match.ACCEPT_DIFF), 3)
        return {"state": state, "source": "template", "confidence": conf,
                "is_mine": im, "label_id": lid, "color": hexc}

    # 兜底：空白快速路径（中心无色差，廉价）
    if _is_blank_region(cell):
        return {"state": "0", "source": "template", "confidence": 0.95,
                "is_mine": False, "label_id": 0, "color": hexc}

    # 未知渲染态：不硬猜，交上层重采样
    return {"state": "unknown", "source": "none", "confidence": 0.0,
            "is_mine": False, "label_id": -1, "color": hexc}


def classify_board(frame: np.ndarray, calib: dict) -> list[dict]:
    """整盘逐格分类。"""
    results = []
    for r in range(calib["rows"]):
        for c in range(calib["cols"]):
            cell = slice_cell(frame, calib, c, r)
            item = classify_cell(cell)
            item["c"], item["r"] = c, r
            results.append(item)
    return results