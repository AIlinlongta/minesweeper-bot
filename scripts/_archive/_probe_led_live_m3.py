# -*- coding: utf-8 -*-
"""m3 计数器实盘探针：新局读 010 → 落旗 009 → 右键问号回 010 → reconcile 全过。"""
import subprocess
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(r"D:\workbuddy\projects\minesweeper-bot")
sys.path.insert(0, str(ROOT))

from perception import calibrate, capture, grid_perception, state, windowing  # noqa: E402
from action import action  # noqa: E402


def reg_get(name):
    import win32api, win32con
    try:
        hk = win32api.RegOpenKeyEx(win32con.HKEY_CURRENT_USER,
                                   r"Software\Microsoft\winmine", 0,
                                   win32con.KEY_QUERY_VALUE)
        v, _ = win32api.RegQueryValueEx(hk, name)
        win32api.RegCloseKey(hk)
        return int(v)
    except Exception:
        return None


def reg_set(values):
    import win32api, win32con
    hk = win32api.RegCreateKey(win32con.HKEY_CURRENT_USER,
                               r"Software\Microsoft\winmine")
    for k, v in values.items():
        win32api.RegSetValueEx(hk, k, 0, win32con.REG_DWORD, v)
    win32api.RegCloseKey(hk)


def kill(win):
    if not windowing.verify_game_pid(win["pid"]):
        raise RuntimeError(f"REFUSE taskkill pid={win['pid']}")
    subprocess.run(["taskkill", "/F", "/PID", str(win["pid"])],
                   check=False, capture_output=True)
    time.sleep(1.0)


def launch(cfg):
    proc = subprocess.Popen([cfg["exe_path"]])
    time.sleep(2.5)
    win = windowing.find_game_window(cfg, pid=proc.pid)
    windowing.bring_to_foreground(win["hwnd"])
    time.sleep(0.3)
    return win, proc


def main() -> int:
    windowing.enable_dpi_aware()
    with open(ROOT / "config/window.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    old = windowing.find_game_window(cfg)
    if old:
        kill(old)
    backup = {k: v for k in ("Difficulty", "Height", "Width", "Mines")
              if (v := reg_get(k)) is not None}
    reg_set({"Difficulty": 3, "Height": 9, "Width": 9, "Mines": 10})
    print(f"registry backup={backup}")
    win, proc = None, None
    try:
        proc = subprocess.Popen([cfg["exe_path"]])
        time.sleep(2.5)
        win = windowing.find_game_window(cfg, pid=proc.pid)
        windowing.bring_to_foreground(win["hwnd"])
        time.sleep(0.3)
        calib = calibrate.detect_board(capture.grab_client(win))
        assert calib["rows"] > 0 and calib["cell_size"] == 16, f"calib {calib}"

        ok = True
        frame = capture.grab_client(win)
        board = grid_perception.classify_board(frame, calib)
        c0 = state.read_mines_counter(frame)
        r0 = state.reconcile(board, c0, 10)
        print(f"fresh: counter={c0} (expect 10) reconcile={r0}")
        ok &= (c0 == 10) and r0
        cv2_ok = __import__("cv2")
        cv2_ok.imwrite(str(ROOT / "scripts/_m3_dbg/counter_fresh.png"),
                       frame[0:45, 0:60])

        # 落旗 (0,0) → 009
        action.flag(0, 0, win, calib)
        time.sleep(0.4)
        frame = capture.grab_client(win)
        board = grid_perception.classify_board(frame, calib)
        c1 = state.read_mines_counter(frame)
        r1 = state.reconcile(board, c1, 10)
        print(f"flagged(0,0): counter={c1} (expect 9) reconcile={r1} "
              f"cell={board[0]['state']}")
        ok &= (c1 == 9) and r1 and board[0]["state"] == "flag"
        cv2_ok.imwrite(str(ROOT / "scripts/_m3_dbg/counter_flagged.png"),
                       frame[0:45, 0:60])

        # 再右键 → question → 计数回 010
        action.flag(0, 0, win, calib)
        time.sleep(0.4)
        frame = capture.grab_client(win)
        board = grid_perception.classify_board(frame, calib)
        c2 = state.read_mines_counter(frame)
        r2 = state.reconcile(board, c2, 10)
        print(f"question(0,0): counter={c2} (expect 10) reconcile={r2} "
              f"cell={board[0]['state']}")
        ok &= (c2 == 10) and r2 and board[0]["state"] == "question"

        # 连续读 5 次稳定性
        stable = [state.read_mines_counter(capture.grab_client(win))
                  for _ in range(5)]
        print(f"stable reads: {stable}")
        ok &= all(v == 10 for v in stable)

        print("RESULT:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        if win:
            kill(win)
        if backup:
            reg_set(backup)
        print(f"registry restored={backup}")


if __name__ == "__main__":
    sys.exit(main())
