# -*- coding: utf-8 -*-
"""digit 对齐实盘验证：高雷数盘（16x16/120）逐格采集数字样本，
每次点击后立即增量采样（死盘不浪费：已翻开数字格仍有效，仅排除雷格）。
真值 = 数字颜色 oracle（与模板通道独立）。输出 per-digit diff 统计与 mismatch 明细。
注册表自动设 16×16/120（进程重启生效），结束恢复原值。"""
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np
import yaml

ROOT = Path(r"D:\workbuddy\projects\minesweeper-bot")
sys.path.insert(0, str(ROOT))

from perception import calibrate, capture, grid_perception, windowing  # noqa: E402
from perception.template_match import load as load_templates  # noqa: E402
from action import action  # noqa: E402

ORACLE_RGB = {
    (0, 0, 255): 1, (0, 128, 0): 2, (255, 0, 0): 3, (0, 0, 128): 4,
    (128, 0, 0): 5, (0, 128, 128): 6, (0, 0, 0): 7, (128, 128, 128): 8,
}
ROUNDS = 40
TARGET_PER_DIGIT = 12

log = open(ROOT / "scripts/_d4_probe.txt", "w", encoding="utf-8")

_REG_BACKUP: dict[str, int] = {}


def _reg_get(name):
    import win32api
    import win32con
    try:
        hk = win32api.RegOpenKeyEx(win32con.HKEY_CURRENT_USER,
                                   r"Software\Microsoft\winmine", 0,
                                   win32con.KEY_QUERY_VALUE)
        v, _ = win32api.RegQueryValueEx(hk, name)
        win32api.RegCloseKey(hk)
        return int(v)
    except Exception:
        return None


def _reg_set(values):
    import win32api
    import win32con
    hk = win32api.RegCreateKey(win32con.HKEY_CURRENT_USER,
                               r"Software\Microsoft\winmine")
    for name, v in values.items():
        win32api.RegSetValueEx(hk, name, 0, win32con.REG_DWORD, v)
    win32api.RegCloseKey(hk)


def restore_registry():
    if _REG_BACKUP:
        try:
            _reg_set(_REG_BACKUP)
        except Exception as e:
            print(f"registry restore failed: {e!r}", file=log)


def out(msg):
    print(msg, file=log)


def oracle_digit(cell):
    """中心 10x10 主色匹配（cv2 BGR → 转 RGB 再查表）。"""
    region = cell[3:13, 3:13].reshape(-1, 3)
    cnt = Counter(tuple(int(v) for v in p)[::-1] for p in region)  # BGR→RGB
    for rgb, _ in cnt.most_common():
        if rgb in ORACLE_RGB:
            return ORACLE_RGB[rgb]
    return None


def is_mine_cell(cell, tpls):
    """雷格排除（死盘雷心黑色会污染 oracle 的 digit_7 统计）。"""
    for lb in ("mine", "mine_red", "mine_cross"):
        d = float(np.abs(cell.astype(np.int16) - tpls[lb].astype(np.int16)).mean())
        if d < 30:
            return True
    return False


def sample_frame(frame, calib, tpls, got, digit_diffs, mism_details, round_i):
    """扫描当前帧：oracle 判真值，与正确模板比 diff；排除雷格。"""
    n = 0
    for r in range(calib["rows"]):
        for c in range(calib["cols"]):
            cell = grid_perception.slice_cell(frame, calib, c, r)
            if int(cell[0, 0].max()) >= 240:      # 覆盖格
                continue
            if is_mine_cell(cell, tpls):
                continue
            od = oracle_digit(cell)
            if od is None:
                continue
            n += 1
            diffs = {lb: float(np.abs(cell.astype(np.int16) - t.astype(np.int16)).mean())
                     for lb, t in tpls.items() if lb.startswith("digit_")}
            d_true = diffs[f"digit_{od}"]
            digit_diffs[od].append(d_true)
            got[od] += 1
            best = min(diffs, key=diffs.get)
            if best != f"digit_{od}":
                mism_details.append((round_i, c, r, od, best, round(d_true, 1)))
                cv2.imwrite(str(ROOT / f"scripts/_d4_m_{od}_{c}_{r}.png"),
                            cv2.resize(cell, None, fx=12, fy=12, interpolation=cv2.INTER_NEAREST))
    return n


def covered_cells(frame, calib):
    oy, ox = calib["board_origin"]["y"], calib["board_origin"]["x"]
    cs = calib["cell_size"]
    out_cells = []
    for r in range(calib["rows"]):
        for c in range(calib["cols"]):
            if int(frame[oy + r * cs, ox + c * cs].max()) >= 240:
                out_cells.append((c, r))
    return out_cells


def main():
    windowing.enable_dpi_aware()
    cfg = yaml.safe_load(open(ROOT / "config/window.yaml", encoding="utf-8"))
    # 注册表强制 16×16/120（杀掉现有实例使配置生效），结束恢复
    global _REG_BACKUP
    _REG_BACKUP = {k: v for k in ("Difficulty", "Height", "Width", "Mines")
                   if (v := _reg_get(k)) is not None}
    old = windowing.find_game_window(cfg)
    if old:
        if not windowing.verify_game_pid(old["pid"]):
            out(f"REFUSE taskkill pid={old['pid']}: 进程映像非 winmine（找窗可能误绑）")
            return
        subprocess.run(["taskkill", "/F", "/PID", str(old["pid"])],
                       check=False, capture_output=True)
        time.sleep(1.0)
    _reg_set({"Difficulty": 3, "Height": 16, "Width": 16, "Mines": 120})
    out(f"registry backup={_REG_BACKUP}")
    proc = subprocess.Popen([cfg["exe_path"]])
    time.sleep(2.5)
    win = windowing.find_game_window(cfg, pid=proc.pid)
    windowing.bring_to_foreground(win["hwnd"])
    time.sleep(0.5)
    frame = capture.grab_client(win)
    calib = calibrate.detect_board(frame)
    out(f"board {calib['rows']}x{calib['cols']} (custom 16x16/120 expected)")
    if calib["rows"] != 16:
        out("BAD SHAPE — 请手动把 winmine 设为自定义 16x16/120 后重跑")
        return

    tpls = load_templates()
    digit_diffs = defaultdict(list)
    mism_details = []
    got = Counter()
    rng = np.random.default_rng(7)

    for round_i in range(ROUNDS):
        # 重开新局
        frame = capture.grab_client(win)
        anchor = calibrate.detect_anchor(frame)
        face = anchor.get("face_rect")
        if not face:
            out(f"[round {round_i}] no face, abort")
            break
        action.restart((win["client_rect"]["x"] + face["x"] + face["w"] // 2,
                        win["client_rect"]["y"] + face["y"] + face["h"] // 2))
        time.sleep(0.6)
        frame = capture.grab_client(win)
        if len(covered_cells(frame, calib)) < calib["rows"] * calib["cols"]:
            continue  # 重开失败，下轮再试
        # 逐击增量采样，直到死
        dead = False
        clicks = 0
        while not dead:
            covered = covered_cells(frame, calib)
            if not covered:
                break
            c, r = covered[int(rng.integers(0, len(covered)))]
            action.reveal(c, r, win, calib)
            time.sleep(0.4)
            frame = capture.grab_client(win)
            clicks += 1
            n = sample_frame(frame, calib, tpls, got, digit_diffs, mism_details, round_i)
            out(f"[round {round_i}] click{clicks} ({c},{r}) digit_cells={n} hist={dict(sorted(got.items()))}")
            # 死亡判定：出现 mine_red（红底雷只在死盘出现）
            dead = any(
                float(np.abs(grid_perception.slice_cell(frame, calib, cc, rr).astype(np.int16)
                             - tpls["mine_red"].astype(np.int16)).mean()) < 30
                for rr in range(calib["rows"]) for cc in range(calib["cols"])
                if int(grid_perception.slice_cell(frame, calib, cc, rr)[0, 0].max()) < 240
            )
        if all(got[d] >= TARGET_PER_DIGIT for d in range(1, 9)):
            out("coverage complete, early stop")
            break

    out("\nper-digit diff vs true template (min/median/max):")
    for d in range(1, 9):
        v = sorted(digit_diffs[d])
        if v:
            out(f"  digit_{d}: n={len(v)} min={v[0]:.1f} med={v[len(v)//2]:.1f} max={v[-1]:.1f} "
                f"pass20={sum(1 for x in v if x <= 20)}/{len(v)}")
        else:
            out(f"  digit_{d}: NO SAMPLE")
    out(f"\nmismatch {len(mism_details)}:")
    for m in mism_details[:20]:
        out(f"  {m}")


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        import traceback
        traceback.print_exc(file=log)
    log.close()
    print(open(ROOT / "scripts/_d4_probe.txt", encoding="utf-8").read())
