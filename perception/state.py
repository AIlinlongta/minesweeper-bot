# -*- coding: utf-8 -*-
"""状态聚合 → FrameState + LED 计数器交叉校验（m3，设计 §3.8 / §4.4b）。

read_mines_counter: 左上 LED 计数器模板读数（同源模板 counter/led_0..9.png，
切片规则与验证见 scripts/slice_led.py）。定位不依赖固定偏移：
红色像素按 x 向聚类（左簇=雷计数器，右簇=计时器），右缘对齐切 3 个
13x21 数字槽（任意数字的亮段必达最右列，右缘恒稳定），竖向 ±3px 搜索对齐。
读数失败（遮挡/负号/渲染异常）返回 None，交上层重采样自愈（§5.4 同路径）。

reconcile: mines_total - flag数 - 已可见雷数 == counter_read；不一致 → 感知漂移嫌疑
（上层整盘重感知一次，再不一致 → calibrate_robust）。
"""
from pathlib import Path

import cv2
import numpy as np

_TPL_DIR = Path(__file__).parent / "templates" / "counter"

SLOT_W, GLYPH_H = 13, 21
LED_ACCEPT_DIFF = 25.0   # 槽位搜索下逐像素 mean|diff|；同源渲染实测 ~0-3
_DY_SEARCH = range(-3, 4)

_digit_tpls: dict[int, np.ndarray] | None = None


def _templates() -> dict[int, np.ndarray]:
    global _digit_tpls
    if _digit_tpls is None:
        _digit_tpls = {}
        for d in range(10):
            img = cv2.imread(str(_TPL_DIR / f"led_{d}.png"), cv2.IMREAD_COLOR)
            if img is None:
                raise FileNotFoundError(f"led template missing: {d}")
            _digit_tpls[d] = img
    return _digit_tpls


def _red_mask(frame: np.ndarray) -> np.ndarray:
    """LED 亮红段掩码（BGR）。"""
    b = frame[:, :, 0].astype(int)
    g = frame[:, :, 1].astype(int)
    r = frame[:, :, 2].astype(int)
    return (r > 100) & (r > g * 1.5) & (r > b * 1.5)


def _counter_bbox(frame: np.ndarray) -> tuple[int, int, int, int] | None:
    """左计数器内容 bbox（x0, y0, x1, y1），None=无红像素。

    先按 y 取最顶部 ≤26px 高的红色条带（计数器/计时器同处顶板，
    旗子等棋盘红像素都在其下），带内再按 x 聚类（间隙>10px 分簇），
    最左簇=雷计数器。
    """
    mask = _red_mask(frame)
    ys_all = np.where(mask.any(axis=1))[0]
    if len(ys_all) == 0:
        return None
    band = mask[ys_all[0]:ys_all[0] + 24]
    xs = np.unique(np.where(band)[1])
    if len(xs) == 0:
        return None
    groups = [[xs[0]]]
    for x in xs[1:]:
        if x - groups[-1][-1] > 10:
            groups.append([])
        groups[-1].append(x)
    g = groups[0]  # 最左簇
    sel = band[:, g[0]:g[-1] + 1]
    ys = np.where(sel.any(axis=1))[0]
    return int(g[0]), int(ys[0] + ys_all[0]), int(g[-1]), int(ys[-1] + ys_all[0])


def _slot_diff(frame: np.ndarray, x: int, y: int) -> tuple[int, float]:
    """(x,y) 处 13x21 槽与 10 个数字模板的最小 mean|diff| → (digit, diff)。"""
    crop = frame[y:y + GLYPH_H, x:x + SLOT_W].astype(np.int16)
    best_d, best_diff = -1, 1e9
    for d, tpl in _templates().items():
        diff = float(np.abs(crop - tpl.astype(np.int16)).mean())
        if diff < best_diff:
            best_d, best_diff = d, diff
    return best_d, best_diff


def read_mines_counter(frame: np.ndarray, anchor: dict | None = None) -> int | None:
    """读雷计数器（百十个位 3 槽，右缘对齐），失败返回 None。"""
    # anchor 提供可信 rect 时限定搜索窗（排除计时器干扰），否则全帧聚类
    if anchor and anchor.get("mines_counter_rect"):
        rc = anchor["mines_counter_rect"]
        x0, y0 = rc["x"], rc["y"]
        sub = frame[y0:y0 + rc["h"], x0:x0 + rc["w"]]
        bb = _counter_bbox(sub)
        if bb is None:
            return None
        bx0, by0, bx1, by1 = bb
        bx0, bx1 = bx0 + x0, bx1 + x0
        by0, by1 = by0 + y0, by1 + y0
    else:
        bb = _counter_bbox(frame)
        if bb is None:
            return None
        bx0, by0, bx1, by1 = bb

    # 右缘对齐：任意数字亮段必达 cell 内 col 11（col 12 恒空），
    # 故最后一个 cell 的右缘 = bx1 + 2；3 槽各 13px 向左排开。
    x_right = bx1 + 2
    x_slots = [x_right - 3 * SLOT_W, x_right - 2 * SLOT_W, x_right - SLOT_W]
    if x_slots[0] < 0:
        return None

    # 竖向对齐：底缘基准 ±3px 搜索（全"1"盘底缘上移 3px）
    best = None  # (total_diff, top, digits)
    for dy in _DY_SEARCH:
        top = by1 - GLYPH_H + 1 + dy
        if top < 0 or top + GLYPH_H > frame.shape[0]:
            continue
        total, digits = 0.0, []
        for x in x_slots:
            d, diff = _slot_diff(frame, x, top)
            digits.append(d)
            total += diff
        if best is None or total < best[0]:
            best = (total, top, digits)
    if best is None or best[0] / 3 > LED_ACCEPT_DIFF:
        return None
    d100, d10, d1 = best[2]
    return d100 * 100 + d10 * 10 + d1


def reconcile(board: list[dict], counter_read: int | None,
              mines_total: int) -> bool:
    """对账公式：mines_total - flag数 - mine_cross数 == counter_read（§4.4b）。

    LED 读数只随落旗动作递减（不分旗对错）；踩雷不改变读数。死盘上错旗
    被游戏渲染为 mine_cross，需一并扣减；对局中/胜局 mine_cross=0，
    公式退化为 mines_total - flag数 == counter_read。"""
    if counter_read is None:
        return False
    flags = sum(1 for c in board if c["state"] == "flag")
    cross = sum(1 for c in board if c["state"] == "mine_cross")
    return mines_total - flags - cross == counter_read


_LOST_STATES = {"mine", "mine_red", "mine_cross"}


def detect_scene(board: list[dict]) -> str:
    """由盘面判局幕（游戏自身渲染，非后门）：lost=出现雷态精灵；
    won=无 covered/question/unknown（余格全为翻开/旗）；其余 playing。"""
    states = [c["state"] for c in board]
    if any(s in _LOST_STATES for s in states):
        return "lost"
    if not any(s in ("covered", "question", "unknown") for s in states):
        return "won"
    return "playing"


def build_frame(frame_id: int, ts: float, game: dict, board: list[dict],
                scene: str, action: dict | None = None) -> dict:
    """聚合 FrameState（§2.7）。game: calib + mines_remaining/window_rect。"""
    return {
        "frame_id": frame_id,
        "timestamp": ts,
        "scene": scene,
        "game": {
            "difficulty": game.get("difficulty"),
            "rows": game.get("rows"),
            "cols": game.get("cols"),
            "mines_total": game.get("mines_total"),
            "mines_remaining": game.get("mines_remaining"),
            "window_rect": game.get("window_rect"),
            "board_origin": game.get("board_origin"),
            "cell_size": game.get("cell_size"),
        },
        "board": [{"c": c["c"], "r": c["r"], "state": c["state"],
                   "source": c.get("source"), "is_mine": c.get("is_mine")}
                  for c in board],
        "action": action or {},
    }
