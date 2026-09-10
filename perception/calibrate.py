# -*- coding: utf-8 -*-
"""自校准定位：网格自动检测 + 单格自动测量 + 难度判定。

开发设计 §三.3.4。核心思想（弃固定偏移/16px）：
  - 未挖开格子的「左上高光（白 255）/ 右下阴影」在每个格子边界形成 1px 高光线；
  - 对高光像素做行/列投影，得到等间距的网格分割线；
  - 从分割线间距反推 cell_size，从分割线数量反推 rows×cols，进而判定难度。

输出 CalibrationResult（见开发设计 §2.4）。
"""
from collections import Counter

import cv2
import numpy as np

# 难度规格：(rows, cols) -> (difficulty, mines_total)
DIFFICULTY = {
    (9, 9): ("beginner", 10),
    (16, 16): ("intermediate", 40),
    (16, 30): ("expert", 99),
}
_VALID_SHAPES = set(DIFFICULTY.keys())


class CalibrateError(RuntimeError):
    """§5.3 兜底链全失败（m3 主循环捕获 → 致命退出）。"""


def preprocess_grid(client_bgr: np.ndarray) -> np.ndarray:
    """灰度化。"""
    return cv2.cvtColor(client_bgr, cv2.COLOR_BGR2GRAY)


def _mode_diff(diffs: list[int]) -> int | None:
    """相邻分割线间距的众数（容忍 ±1 像素抖动）。"""
    c = Counter(diffs)
    merged: dict[int, int] = {}
    for k in sorted(c):
        placed = False
        for m in merged:
            if abs(m - k) <= 1:
                merged[m] += c[k]
                placed = True
                break
        if not placed:
            merged[k] = c[k]
    if not merged:
        return None
    return max(merged, key=merged.get)


def _grid_lines(proj: np.ndarray) -> tuple[int | None, list[int]]:
    """从高光投影提取网格线。

    返回 (cell_size, 网格线位置列表)。cell_size 为 None 表示未检出。
    """
    peak = int(proj.max())
    if peak <= 2:
        return None, []
    thr = max(2, int(0.5 * peak))

    runs: list[int] = []
    active = None
    for i, v in enumerate(proj):
        if int(v) >= thr:
            if active is None:
                active = i
        elif active is not None:
            runs.append(active)
            active = None
    if active is not None:
        runs.append(active)

    if len(runs) < 3:
        return None, runs

    diffs = [b - a for a, b in zip(runs, runs[1:])]
    cell = _mode_diff(diffs)
    if cell is None or cell < 8 or cell > 60:
        return None, runs

    # 找最长「等间距链」（间距≈cell），链的起点即网格起点
    best_start, best_len = 0, 0
    i = 0
    while i < len(runs):
        j = i
        while j + 1 < len(runs) and abs((runs[j + 1] - runs[j]) - cell) <= 1:
            j += 1
        if j - i + 1 > best_len:
            best_start, best_len = i, j - i + 1
        i = j + 1 if j > i else i + 1

    if best_len < 3:
        return None, runs
    lines = runs[best_start:best_start + best_len]
    return cell, lines


def detect_board(client_bgr: np.ndarray) -> dict:
    """主入口：单帧客户区截图 → CalibrationResult。"""
    gray = preprocess_grid(client_bgr)
    bright = (gray > 200).astype(np.uint8)

    vproj = bright.sum(axis=0).astype(int)
    hproj = bright.sum(axis=1).astype(int)

    cell_x, vlines = _grid_lines(vproj)
    cell_y, hlines = _grid_lines(hproj)

    result = {
        "board_origin": {"x": 0, "y": 0},
        "cell_size": 0,
        "rows": 0,
        "cols": 0,
        "difficulty": "unknown",
        "mines_total": None,
        "confidence": 0.0,
        "method": "grid_projection",
        "board_rect": {"x": 0, "y": 0, "w": 0, "h": 0},
    }

    if cell_x is None or cell_y is None:
        return result

    if abs(cell_x - cell_y) > 1:
        # 行列测得的 cell_size 不一致 → 不可信
        return result

    cell = int(round((cell_x + cell_y) / 2))
    ox, oy = int(vlines[0]), int(hlines[0])
    cols = len(vlines) - 1
    rows = len(hlines) - 1

    result["cell_size"] = cell
    result["board_origin"] = {"x": ox, "y": oy}
    result["cols"] = cols
    result["rows"] = rows
    result["board_rect"] = {"x": ox, "y": oy, "w": cols * cell, "h": rows * cell}

    difficulty, mines = detect_difficulty(rows, cols)
    result["difficulty"] = difficulty
    result["mines_total"] = mines

    valid = validate(result)
    result["confidence"] = _confidence(vproj, hproj, cell)
    if not valid:
        result["confidence"] = min(result["confidence"], 0.3)
    return result


def _confidence(vproj: np.ndarray, hproj: np.ndarray, cell: int) -> float:
    """粗略置信度：基于网格线投影峰值与噪声的对比。"""
    try:
        # 平均峰值（网格线亮度）相对整体均值
        peak = float(max(vproj.max(), hproj.max()))
        return round(min(1.0, max(0.0, peak / 255.0)), 3)
    except Exception:
        return 0.0


def detect_difficulty(rows: int, cols: int) -> tuple[str, int | None]:
    """由行列数反推难度与雷数。"""
    return DIFFICULTY.get((rows, cols), ("unknown", None))


def detect_anchor(client_bgr: np.ndarray) -> dict:
    """锚点辅助（可选）：定位脸符（黄圆）与左上计数器（红字），作雷区上界校验。

    经典 winmine：脸符为黄色圆（HSV H∈[20,40] 区域），计数器为红色七段字。
    """
    hsv = cv2.cvtColor(client_bgr, cv2.COLOR_BGR2HSV)
    # 黄：H~20-35, S 高, V 高
    yellow = cv2.inRange(hsv, (18, 80, 80), (40, 255, 255))
    # 红：H~0-10 或 170-180
    red1 = cv2.inRange(hsv, (0, 80, 40), (10, 255, 255))
    red2 = cv2.inRange(hsv, (170, 80, 40), (180, 255, 255))
    red = cv2.bitwise_or(red1, red2)

    def bbox(mask: np.ndarray) -> dict | None:
        ys, xs = np.where(mask > 0)
        if len(xs) < 5 or len(ys) < 5:
            return None
        return {"x": int(xs.min()), "y": int(ys.min()),
                "w": int(xs.max() - xs.min()) + 1, "h": int(ys.max() - ys.min()) + 1}

    return {
        "face_rect": bbox(yellow),
        "mines_counter_rect": bbox(red),
    }


def validate(calib: dict) -> bool:
    """尺寸/行列数合理性自检。"""
    rows, cols = calib.get("rows", 0), calib.get("cols", 0)
    cell = calib.get("cell_size", 0)
    if cell <= 0 or rows <= 0 or cols <= 0:
        return False
    return (rows, cols) in _VALID_SHAPES


# --------------------------------------------------------------------------- #
# §4.3b 鲁棒校准（m1 交付；m3 主循环一行调用）
# --------------------------------------------------------------------------- #
def calibrate_robust(win: dict, capture_fn=None, *,
                     recapture_rounds: int = 3,
                     restart_rounds: int = 3,
                     settle_s: float = 0.5) -> dict:
    """§5.3 兜底链的函数化封装。

    链条：detect_board → validate 通过即接受（confidence<0.9 时需 anchor 几何自洽，
    method 标记 anchor_assisted）→ 前台化清遮挡/IME 后重抓 ×recapture_rounds →
    脸符 restart 重开 → 再走重抓循环 ×restart_rounds → 全链失败 raise CalibrateError。

    接受门 = validate(calib)（rows/cols 为合法盘型）。confidence 不作硬门：
    实测其值为投影峰值/255，随盘型浮动（9×9≈0.56、16×16≈1.0），仅作日志参考。
    """
    import time as _time

    if capture_fn is None:
        from perception import capture as _capture
        capture_fn = _capture.grab_client

    def anchor_consistent(frame, calib: dict) -> bool:
        """anchor_assisted 先验：脸符须位于雷区上方（几何自洽）。"""
        face = detect_anchor(frame).get("face_rect")
        return bool(face and face["y"] + face["h"] <= calib["board_origin"]["y"] + 2)

    def restart_game():
        from action import action as _action  # 延迟导入，避免 perception→action 顶层依赖
        face = detect_anchor(capture_fn(win)).get("face_rect")
        if not face:
            return False
        _action.restart((win["client_rect"]["x"] + face["x"] + face["w"] // 2,
                         win["client_rect"]["y"] + face["y"] + face["h"] // 2))
        _time.sleep(0.6)
        return True

    for _ in range(restart_rounds):
        for _attempt in range(recapture_rounds):
            frame = capture_fn(win)
            calib = detect_board(frame)
            if validate(calib):
                if calib["confidence"] >= 0.9:
                    return calib
                if anchor_consistent(frame, calib):
                    calib["method"] = "anchor_assisted"
                    return calib
            # 未接受 → 前台化清除遮挡/IME 浮窗后重抓
            from perception import windowing as _windowing
            _windowing.bring_to_foreground(win["hwnd"])
            _time.sleep(settle_s)
        restart_game()
    raise CalibrateError("calibrate_robust: restart 后仍无法定位雷区")