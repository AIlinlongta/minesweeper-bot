# -*- coding: utf-8 -*-
"""窗口路由：DPI 声明 + 多路找窗 + 客户区矩形 + 坐标换算。

开发设计 §三.3.2。输出 WindowInfo（见 docs/阶段0-扫雷轻量预演-开发设计-v1.md §2.3）。
"""
import ctypes
import time

import win32con
import win32gui
import win32process


# --------------------------------------------------------------------------- #
# DPI
# --------------------------------------------------------------------------- #
def enable_dpi_aware() -> bool:
    """取句柄前必调。优先 PER_MONITOR_AWARE_V2，回退 V1 / SetProcessDPIAware。"""
    for fn, args in (
        (_shcore_set_awareness, (2,)),   # PER_MONITOR_AWARE_V2
        (_shcore_set_awareness, (1,)),   # PER_MONITOR_AWARE
        (_user32_set_dpi_aware, ()),
    ):
        try:
            if fn(*args):
                return True
        except Exception:
            continue
    return False


def _shcore_set_awareness(value: int) -> bool:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(value)
        return True
    except Exception:
        return False


def _user32_set_dpi_aware() -> bool:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# 找窗
# --------------------------------------------------------------------------- #
def _build_window_info(hwnd: int, bind_method: str) -> dict:
    cls = win32gui.GetClassName(hwnd)
    title = win32gui.GetWindowText(hwnd)
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    wl, wt, wr, wb = win32gui.GetWindowRect(hwnd)
    client = get_client_rect(hwnd)
    border = {
        "left": client["x"] - wl,
        "top": client["y"] - wt,
        "right": wr - (client["x"] + client["w"]),
        "bottom": wb - (client["y"] + client["h"]),
    }
    return {
        "hwnd": hwnd,
        "pid": pid,
        "class_name": cls,
        "title": title,
        "window_rect": {"x": wl, "y": wt, "w": wr - wl, "h": wb - wt},
        "client_rect": client,
        "border": border,
        "bind_method": bind_method,
    }


def _enum_visible_windows() -> list[int]:
    """枚举所有可见顶层窗口句柄（排除 IME/工具窗）。"""
    out = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            out.append(hwnd)

    win32gui.EnumWindows(cb, None)
    return out


def bind_by_pid(pid: int) -> dict | None:
    """已知 pid 反查主窗口（排除 IME/工具窗，选客户区尺寸最大者）。"""
    best = None
    for hwnd in _enum_visible_windows():
        try:
            _, p = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            continue
        if p != pid:
            continue
        cls = win32gui.GetClassName(hwnd)
        if cls in ("IME", "MSCTFIME UI", "Ghost"):
            continue
        r = get_client_rect(hwnd)
        area = r["w"] * r["h"]
        if best is None or area > best[0]:
            best = (area, hwnd)
    if best is None:
        return None
    return _build_window_info(best[1], "pid")


def match_by_class(cfg: dict) -> dict | None:
    name = cfg.get("class_name")
    if not name:
        return None
    hwnd = win32gui.FindWindow(name, None)
    if hwnd:
        return _build_window_info(hwnd, "class")
    # 兜底：遍历比对类名（精确相等，大小写不敏感）
    for hwnd in _enum_visible_windows():
        if win32gui.GetClassName(hwnd).lower() == name.lower():
            return _build_window_info(hwnd, "class")
    return None


def match_by_title(cfg: dict) -> dict | None:
    keywords = [k.lower() for k in cfg.get("title_fallback", [])]
    for hwnd in _enum_visible_windows():
        title = win32gui.GetWindowText(hwnd).lower()
        if not title:
            continue
        # 精确相等匹配，避免子串误匹配其他窗口（如 Explorer 的 "minesweeper-bot"）
        if any(title == k for k in keywords):
            return _build_window_info(hwnd, "title")
    return None


def match_by_size(cfg: dict) -> dict | None:
    hint = cfg.get("client_size_hint") or {}
    hw, hh = hint.get("w"), hint.get("h")
    if not hw or not hh:
        return None
    best = None
    for hwnd in _enum_visible_windows():
        r = get_client_rect(hwnd)
        if r["w"] == hw and r["h"] == hh:
            return _build_window_info(hwnd, "size")
    return best


def find_game_window(cfg: dict, pid: int | None = None) -> dict | None:
    """多路找窗（优先级）：bind_pid → 类名 → 标题 → 客户区尺寸特征。"""
    pid = pid or cfg.get("bind_pid")
    if pid:
        info = bind_by_pid(int(pid))
        if info:
            return info
    for matcher in (match_by_class, match_by_title, match_by_size):
        try:
            info = matcher(cfg)
        except Exception:
            info = None
        if info:
            return info
    return None


def verify_game_pid(pid: int, expect: str = "winmine") -> bool:
    """kill 前置防护：校验 pid 的进程映像确为目标程序（防找窗误绑后强杀）。

    历史教训：标题子串匹配曾把 Trae/Explorer 窗口（title 含 "minesweeper-bot"）
    误绑为游戏窗口 → taskkill /F 直接杀掉 IDE。本函数保证强杀只作用于 winmine.exe。
    """
    import win32api
    try:
        h = win32api.OpenProcess(0x0400 | 0x0010, False, int(pid))  # QUERY_INFORMATION | VM_READ
        if not h:
            return False
        try:
            exe = win32process.GetModuleFileNameEx(h, 0)
        finally:
            win32api.CloseHandle(h)
        return expect.lower() in exe.lower()
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# 矩形
# --------------------------------------------------------------------------- #
def get_client_rect(hwnd: int) -> dict:
    """客户端矩形（屏幕坐标系）。"""
    l, t, r, b = win32gui.GetClientRect(hwnd)
    x, y = win32gui.ClientToScreen(hwnd, (l, t))
    return {"x": x, "y": y, "w": r - l, "h": b - t}


def get_window_rect(hwnd: int) -> dict:
    """窗口整体矩形（屏幕坐标系）。"""
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    return {"x": l, "y": t, "w": r - l, "h": b - t}


def bring_to_foreground(hwnd: int) -> bool:
    """前台化 + 置顶（返回是否真正成为前台窗口）。

    SetForegroundWindow 有系统级限制（后台进程可能被拒），
    HWND_TOPMOST 置顶并保持（截图/点击都作用于最上层窗口；
    若置顶后回落 NOTOPMOST，仍处 TOPMOST 的遮挡窗会再次盖住），
    再尝试前台化并用 GetForegroundWindow 校验。
    """
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        # 保持 TOPMOST：仅置顶一轮后落回 NOTOPMOST 会被仍处 TOPMOST 的
        # 遮挡窗（如置顶提示框）再次盖住，截图依旧污染。截图/点击型 bot
        # 要求游戏全程位于最上层，故置顶后不回落。
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        # 中文输入法浮窗可能盖住棋盘（影响 detect_board/切片判定）→ 切英文输入 + 隐藏重叠浮窗
        ensure_english_input(hwnd)
        hide_ime_overlay({"client_rect": get_client_rect(hwnd)})
        time.sleep(0.15)
        return win32gui.GetForegroundWindow() == hwnd
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# 输入法浮窗抑制（中文输入法浮窗遮住棋盘 → 切片判定异常）
# --------------------------------------------------------------------------- #
_WM_INPUTLANGCHANGEREQUEST = 0x0050
_HKL_ENGLISH_US = 0x04090409


def ensure_english_input(hwnd: int) -> bool:
    """把目标窗口线程的输入法切换为英文（美式键盘），从源头消除中文输入法浮窗。"""
    try:
        return bool(ctypes.windll.user32.PostMessageW(
            hwnd, _WM_INPUTLANGCHANGEREQUEST, 0, _HKL_ENGLISH_US))
    except Exception:
        return False


def _rects_overlap(a: dict, b: dict) -> bool:
    """两矩形（x/y/w/h，屏幕坐标）是否相交。"""
    ax0, ay0 = a["x"], a["y"]
    bx0, by0 = b["x"], b["y"]
    return ax0 < bx0 + b["w"] and bx0 < ax0 + a["w"] and ay0 < by0 + b["h"] and by0 < ay0 + a["h"]


def hide_ime_overlay(win: dict) -> int:
    """隐藏与游戏客户区相交的输入法浮窗（IME / MSCTFIME UI / ImeWnd…），返回隐藏数量。

    兜底于 ensure_english_input：老程序可能不响应输入法切换消息，直接 SW_HIDE 浮窗。
    浮窗在 IME 再次激活时会重建，隐藏无副作用。
    """
    r = win["client_rect"]
    hidden = 0
    for hwnd in _enum_visible_windows():
        try:
            cls = win32gui.GetClassName(hwnd)
        except Exception:
            continue
        if "IME" not in cls.upper():
            continue
        try:
            rect = get_window_rect(hwnd)
        except Exception:
            continue
        if rect["w"] <= 0 or rect["h"] <= 0:
            continue
        if not _rects_overlap(r, rect):
            continue
        try:
            win32gui.ShowWindow(hwnd, 0)  # SW_HIDE
            hidden += 1
        except Exception:
            continue
    return hidden


# --------------------------------------------------------------------------- #
# 坐标换算
# --------------------------------------------------------------------------- #
def resolve_screen_cell(win: dict, calib: dict, c: int, r: int) -> tuple[int, int]:
    """格子(col,row) → 屏幕坐标 (screen_x, screen_y) 中心点。

    客户区坐标 = board_origin + (col+0.5, row+0.5) * cell_size
    屏幕坐标   = client_rect.x + 客户区坐标
    """
    ox = calib["board_origin"]["x"]
    oy = calib["board_origin"]["y"]
    cell = calib["cell_size"]
    cx = ox + (c + 0.5) * cell
    cy = oy + (r + 0.5) * cell
    return int(round(win["client_rect"]["x"] + cx)), int(round(win["client_rect"]["y"] + cy))