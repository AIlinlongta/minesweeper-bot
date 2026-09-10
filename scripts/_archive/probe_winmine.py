# -*- coding: utf-8 -*-
"""m0 探针：启动 winmine.exe，探测窗口类名/标题/矩形，截屏验证渲染。
产物：docs/winmine_first_run.png + stdout 窗口信息（回填 window.yaml）。
"""
import subprocess
import time

import mss
import mss.tools
import win32gui

EXE = r"D:\workbuddy\projects\minesweeper-bot\apps\winmine.exe"
SHOT = r"D:\workbuddy\projects\minesweeper-bot\docs\winmine_first_run.png"


def find_mine_window():
    found = []

    def cb(hwnd, _acc):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        cls = win32gui.GetClassName(hwnd)
        if "Minesweeper" in cls or "扫雷" in title or "Minesweeper" in title:
            found.append((hwnd, cls, title))

    win32gui.EnumWindows(cb, None)
    return found


def main():
    subprocess.Popen([EXE])
    time.sleep(2.5)

    found = find_mine_window()
    if not found:
        print("RESULT: NOT_FOUND")
        return 1

    for hwnd, cls, title in found:
        print(f"WINDOW: class={cls!r} title={title!r} rect={win32gui.GetWindowRect(hwnd)}")

    hwnd = found[0][0]
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.5)

    rect = win32gui.GetWindowRect(hwnd)
    with mss.mss() as s:
        img = s.grab({"left": rect[0], "top": rect[1],
                      "width": rect[2] - rect[0], "height": rect[3] - rect[1]})
        mss.tools.to_png(img.rgb, img.size, output=SHOT)
    print(f"RESULT: OK shot={SHOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
