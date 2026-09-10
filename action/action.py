# -*- coding: utf-8 -*-
"""输入注入：pydirectinput 点击 + 坐标换算。

开发设计 §三.3.10。反馈回灌（点击结果 → 真值）由调用方配合 capture 完成
（m0 在 click_probe 中验证翻开；m1 在 grid_perception 重分类验证）。
"""
import time

import pydirectinput

from perception import windowing

pydirectinput.FAILSAFE = False
pydirectinput.PAUSE = 0.05


def _move_click(sx: int, sy: int, button: str) -> bool:
    pydirectinput.moveTo(sx, sy)
    time.sleep(0.05)
    if button == "right":
        pydirectinput.rightClick()
    else:
        pydirectinput.click()
    return True


def click(c: int, r: int, button: str, win: dict, calib: dict) -> tuple[int, int]:
    """点击格子 (c,r)，返回实际屏幕坐标。button: 'left' / 'right'。"""
    sx, sy = windowing.resolve_screen_cell(win, calib, c, r)
    _move_click(int(sx), int(sy), button)
    return int(sx), int(sy)


def reveal(c: int, r: int, win: dict, calib: dict) -> tuple[int, int]:
    """左键翻开（safe reveal）。"""
    return click(c, r, "left", win, calib)


def flag(c: int, r: int, win: dict, calib: dict) -> tuple[int, int]:
    """右键标旗。"""
    return click(c, r, "right", win, calib)


def restart(face_screen: tuple[int, int]) -> bool:
    """点击脸符重启。face_screen 为脸符中心的屏幕坐标（由 calibrate.detect_anchor 求得）。"""
    return _move_click(int(face_screen[0]), int(face_screen[1]), "left")