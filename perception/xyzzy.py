# -*- coding: utf-8 -*-
"""xyzzy 调试通道（可选调试加速器，不参与验收/决策）。

经典 winmine 逆向实现（SetPixel → 屏幕 DC (0,0)）：
  - 激活：切英文输入（关中文输入法）→ 输入 "xyzzy" → 按一次 Shift 开启作弊；
  - 指示：鼠标悬停棋盘格时，屏幕左上角 (0,0) 像素被重绘：
       雷 → 黑 RGB(0,0,0)；安全 → 白 RGB(255,255,255)；
  - 前提：屏幕 (0,0) 处必须是桌面（无窗口遮挡），否则像素画在被遮挡层下不可见。

封装：
  activate(hwnd)        —— 激活后门（脚本须先保证游戏窗口在前台且不遮 (0,0)）；
  calibrate(win, sx, sy)—— 悬停已知安全格，验证读到纯白（安全色），确认后门生效；
  cell_is_mine(sx, sy)  —— 悬停任意格判定：白=安全 / 黑=雷 / 其他=不可判。
"""
import time

import mss
import numpy as np
import pydirectinput

from perception import windowing

_IND_POS = (0, 0)  # 屏幕左上角（逆向确认）

# 指示语义（逆向确认）：雷=黑，安全=白
_MINE = "mine"
_SAFE = "safe"
_NONE = "none"


def _read_pixel(pos: tuple[int, int]) -> tuple[int, int, int] | None:
    """读屏幕 (x,y) 单像素，返回 (R,G,B)。"""
    x, y = pos
    with mss.mss() as s:
        shot = s.grab({"left": int(x), "top": int(y), "width": 1, "height": 1})
        b, g, r = [int(v) for v in np.asarray(shot)[0, 0][:3]]
        return (r, g, b)


def _classify(rgb: tuple[int, int, int] | None) -> str:
    """白=安全，黑=雷，其余=不可判。"""
    if rgb is None:
        return _NONE
    total = sum(rgb)
    if total >= 700:   # 近白 → 安全
        return _SAFE
    if total <= 240:   # 近黑 → 雷
        return _MINE
    return _NONE


def activate(hwnd: int) -> None:
    """激活后门：切英文输入 → 输入 xyzzy → 按一次 Shift 开启。"""
    windowing.ensure_english_input(hwnd)
    time.sleep(0.2)
    pydirectinput.write("xyzzy", interval=0.05)
    time.sleep(0.2)
    pydirectinput.keyDown("shift")
    time.sleep(0.1)
    pydirectinput.keyUp("shift")
    time.sleep(0.3)


def calibrate(sx_safe: int, sy_safe: int) -> bool:
    """悬停已知安全格，验证指示像素读到纯白（安全=白），确认后门生效。

    激活失败（IME 拦截 / 该版本无后门 / (0,0) 被遮挡）时返回 False。
    """
    pydirectinput.keyDown("shift")
    try:
        pydirectinput.moveTo(int(sx_safe), int(sy_safe))
        time.sleep(0.2)
        rgb = _read_pixel(_IND_POS)
    finally:
        pydirectinput.keyUp("shift")
    return _classify(rgb) == _SAFE


def cell_is_mine(sx: int, sy: int) -> bool | None:
    """悬停 (sx,sy) 判定是否雷。True=雷 / False=安全 / None=不可判。"""
    pydirectinput.keyDown("shift")
    try:
        pydirectinput.moveTo(int(sx), int(sy))
        time.sleep(0.15)
        rgb = _read_pixel(_IND_POS)
    finally:
        pydirectinput.keyUp("shift")
    c = _classify(rgb)
    if c == _MINE:
        return True
    if c == _SAFE:
        return False
    return None
