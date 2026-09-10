# -*- coding: utf-8 -*-
"""凸起态/雷态实盘验证（m1 收尾①，v1.1 §6.2）。

真值来源（游戏机制，非后门）：
  P1 右键循环：covered → flag → question → covered。
     question 缺失（游戏"标记(?)"关闭）→ 注册表 Marks=1 + 进程重启重试 →
     仍失败则报 FAIL 并给出手动开启指引。
  P2 死亡盘三雷态（多命）：
     首击落子布雷（首击必安全）→ 撒 5 随机旗（高概率含错旗）→ 随机左键点击至死 →
     死盘审计：
       - mine_red 恰 1 个且 == 末击格；
       - 被旗格 ∈ {flag(旗对), mine_cross(旗错)}；
       - 未旗未击且未覆盖 → 必为 mine；
       - mine + mine_red + mine_cross + flag == mines_total；
       - 无 unknown；全部已开格模板 diff 记录入统计（验收线 ≤ ACCEPT_DIFF）。
     顺带收集 blank 实盘样本（flag 实例在 P1/P2 都有）。
输出 per-class diff 统计 + PASS/FAIL。"""
import random
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(r"D:\workbuddy\projects\minesweeper-bot")
sys.path.insert(0, str(ROOT))

from perception import calibrate, capture, grid_perception, windowing  # noqa: E402
from perception.template_match import ACCEPT_DIFF, load as load_templates  # noqa: E402
from action import action  # noqa: E402

MAX_LIVES = 12
FLAGS_PER_LIFE = 5
CLICK_PAUSE = 0.45

log = open(ROOT / "scripts/_sprites_probe.txt", "w", encoding="utf-8")


def out(msg):
    print(msg, file=log)


def expected_label(state: str) -> str:
    if state == "0":
        return "blank"
    if state in [str(i) for i in range(1, 9)]:
        return f"digit_{state}"
    return state


def cell_diff(cell: np.ndarray, tpls: dict, label: str) -> float:
    return float(np.abs(cell.astype(np.int16) - tpls[label].astype(np.int16)).mean())


def relaunch(cfg, old_win, old_proc):
    """杀掉现有进程重启（注册表 Marks 变更需进程重启生效）。"""
    try:
        if old_proc is not None and old_proc.poll() is None:
            old_proc.kill()
        else:
            if not windowing.verify_game_pid(old_win["pid"]):
                raise RuntimeError(f"REFUSE taskkill pid={old_win['pid']}: 进程映像非 winmine（找窗可能误绑）")
            subprocess.run(["taskkill", "/F", "/PID", str(old_win["pid"])],
                           check=False, capture_output=True)
    except Exception as e:
        out(f"kill failed: {e!r}")
    time.sleep(1.0)
    proc = subprocess.Popen([cfg["exe_path"]])
    time.sleep(2.5)
    win = windowing.find_game_window(cfg, pid=proc.pid) or windowing.find_game_window(cfg)
    windowing.bring_to_foreground(win["hwnd"])
    time.sleep(0.3)
    return win, proc


def restart_face(win):
    anchor = calibrate.detect_anchor(capture.grab_client(win))
    face = anchor.get("face_rect")
    if face:
        action.restart((win["client_rect"]["x"] + face["x"] + face["w"] // 2,
                        win["client_rect"]["y"] + face["y"] + face["h"] // 2))
        time.sleep(0.6)


def fresh_calib(win):
    frame = capture.grab_client(win)
    return calibrate.detect_board(frame)


def read_marks_registry():
    try:
        import win32api
        import win32con
        hk = win32api.RegOpenKeyEx(win32con.HKEY_CURRENT_USER,
                                   r"Software\Microsoft\winmine", 0,
                                   win32con.KEY_QUERY_VALUE)
        v, _ = win32api.RegQueryValueEx(hk, "Mark")   # 实测值名为 Mark（非 Marks）
        win32api.RegCloseKey(hk)
        return int(v)
    except Exception:
        return None


def set_marks_registry(value: int) -> bool:
    try:
        import win32api
        import win32con
        hk = win32api.RegCreateKey(win32con.HKEY_CURRENT_USER,
                                   r"Software\Microsoft\winmine")
        win32api.RegSetValueEx(hk, "Mark", 0, win32con.REG_DWORD, value)
        win32api.RegCloseKey(hk)
        return True
    except Exception as e:
        out(f"registry write failed: {e!r}")
        return False


_REG_BACKUP: dict[str, int] = {}


def _reg_set(values: dict[str, int]) -> None:
    import win32api
    import win32con
    hk = win32api.RegCreateKey(win32con.HKEY_CURRENT_USER,
                               r"Software\Microsoft\winmine")
    for name, v in values.items():
        win32api.RegSetValueEx(hk, name, 0, win32con.REG_DWORD, v)
    win32api.RegCloseKey(hk)


def _reg_get(name: str) -> int | None:
    try:
        import win32api
        import win32con
        hk = win32api.RegOpenKeyEx(win32con.HKEY_CURRENT_USER,
                                   r"Software\Microsoft\winmine", 0,
                                   win32con.KEY_QUERY_VALUE)
        v, _ = win32api.RegQueryValueEx(hk, name)
        win32api.RegCloseKey(hk)
        return int(v)
    except Exception:
        return None


def setup():
    """冷启动 + 注册表强制 9×9/10 盘（Difficulty=3 自定义 + Height/Width=9 + Mines=10）。

    实测：该韩文移植版把盘面配置持久化在 HKCU\\Software\\Microsoft\\winmine，
    且 Difficulty 数值语义未知（1/0/2 均未回到 9×9，原值 3=自定义 16×16/120），
    故直接写自定义尺寸 —— 9×9 查表即 beginner/10，mines_total 查表值与真实一致。
    结束时 restore_registry() 恢复原值。"""
    windowing.enable_dpi_aware()
    with open(ROOT / "config/window.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    old = windowing.find_game_window(cfg)
    if old:
        _kill(old)
    global _REG_BACKUP
    _REG_BACKUP = {k: v for k in ("Difficulty", "Height", "Width", "Mines")
                   if (v := _reg_get(k)) is not None}
    _reg_set({"Difficulty": 3, "Height": 9, "Width": 9, "Mines": 10})
    proc = subprocess.Popen([cfg["exe_path"]])
    time.sleep(2.5)
    win = windowing.find_game_window(cfg, pid=proc.pid)
    windowing.bring_to_foreground(win["hwnd"])
    time.sleep(0.3)
    out(f"registry backup={_REG_BACKUP}")
    return cfg, win, proc


def restore_registry():
    """恢复探针运行前的盘面注册表（用户手动设的自定义 16×16/120 等）。"""
    if _REG_BACKUP:
        try:
            _reg_set(_REG_BACKUP)
        except Exception as e:
            out(f"registry restore failed: {e!r}")


def _kill(win):
    if not windowing.verify_game_pid(win["pid"]):
        raise RuntimeError(f"REFUSE taskkill pid={win['pid']}: 进程映像非 winmine（找窗可能误绑）")
    subprocess.run(["taskkill", "/F", "/PID", str(win["pid"])],
                   check=False, capture_output=True)
    time.sleep(1.0)


def right_click_states(win, calib, target):
    """对 target 连续三次右键，每次捕获分类状态。返回 [state1, state2, state3]。"""
    seq = []
    for _ in range(3):
        action.click(target[0], target[1], "right", win, calib)
        time.sleep(CLICK_PAUSE)
        cell = grid_perception.slice_cell(capture.grab_client(win),
                                          calib, target[0], target[1])
        seq.append(grid_perception.classify_cell(cell)["state"])
    return seq


# --------------------------------------------------------------------------- #
# P1 右键循环：covered → flag → question → covered
# --------------------------------------------------------------------------- #
def p1_right_click_cycle(cfg, win, proc):
    diff_log = defaultdict(list)
    restart_face(win)
    calib = fresh_calib(win)
    if calib["rows"] == 0:
        out("P1 no fresh board")
        return False, diff_log, win, proc
    target = (calib["cols"] // 2, calib["rows"] // 2)
    seq = right_click_states(win, calib, target)
    out(f"P1 cycle seq={seq} (registry Marks={read_marks_registry()})")

    if seq[1] == "question":
        # 再走一轮采集 flag/question 实例 diff（起点 covered，结束还原 covered）
        action.click(target[0], target[1], "right", win, calib)   # →flag
        time.sleep(CLICK_PAUSE)
        cell = grid_perception.slice_cell(capture.grab_client(win),
                                          calib, target[0], target[1])
        diff_log["flag"].append(cell_diff(cell, load_templates(), "flag"))
        action.click(target[0], target[1], "right", win, calib)   # →question
        time.sleep(CLICK_PAUSE)
        cell = grid_perception.slice_cell(capture.grab_client(win),
                                          calib, target[0], target[1])
        diff_log["question"].append(cell_diff(cell, load_templates(), "question"))
        action.click(target[0], target[1], "right", win, calib)   # →covered
        time.sleep(CLICK_PAUSE)
        ok = seq == ["flag", "question", "covered"]
        out(f"P1 result ok={ok}")
        return ok, diff_log, win, proc

    # question 缺失 → 注册表开启 + 进程重启重试
    out("P1 question missing → set registry Marks=1 and relaunch")
    if set_marks_registry(1):
        win, proc = relaunch(cfg, win, proc)
        restart_face(win)
        calib = fresh_calib(win)
        if calib["rows"] > 0:
            target = (calib["cols"] // 2, calib["rows"] // 2)
            seq = right_click_states(win, calib, target)
            out(f"P1 retry cycle seq={seq}")
            if seq[1] == "question":
                ok = seq == ["flag", "question", "covered"]
                out(f"P1 result(ok, diff pending P2 flag samples)={ok}")
                return ok, diff_log, win, proc
    out("P1 question UNVERIFIED — 请手动：游戏菜单勾选 标记(?) 后重跑")
    return False, diff_log, win, proc


# --------------------------------------------------------------------------- #
# P2 死亡盘三雷态
# --------------------------------------------------------------------------- #
def p2_mine_states(win):
    tpls = load_templates()
    diff_log = defaultdict(list)
    unknown_total = 0
    lives_dead = 0
    audit_failures = []
    rng = random.Random(20260909)

    def scan(f, calib):
        """记录当前帧所有已开格 diff；返回 {(c,r):state} 与 unknown 数。"""
        nonlocal unknown_total
        items = grid_perception.classify_board(f, calib)
        unk = 0
        for it in items:
            st = it["state"]
            if st == "covered":
                continue
            if st == "unknown":
                unk += 1
                continue
            lb = expected_label(st)
            cell = grid_perception.slice_cell(f, calib, it["c"], it["r"])
            diff_log[lb].append(cell_diff(cell, tpls, lb))
        unknown_total += unk
        return {(it["c"], it["r"]): it["state"] for it in items}, unk

    for life in range(1, MAX_LIVES + 1):
        if "mine" in diff_log and "mine_cross" in diff_log and lives_dead >= 2:
            break
        restart_face(win)
        calib = fresh_calib(win)
        if calib["rows"] == 0:
            out(f"P2 life{life}: no fresh board")
            continue
        mines_total = calib["mines_total"]
        states, _ = scan(capture.grab_client(win), calib)
        covered = [k for k, s in states.items() if s == "covered"]
        if len(covered) != calib["rows"] * calib["cols"]:
            out(f"P2 life{life}: board not fully covered ({len(covered)})")
            continue

        # 首击（必安全，落子布雷；可能大 flood）
        c0, r0 = rng.choice(covered)
        action.reveal(c0, r0, win, calib)
        time.sleep(CLICK_PAUSE)
        states, _ = scan(capture.grab_client(win), calib)

        # 撒旗（布雷后 → 存在错旗概率）
        covered = [k for k, s in states.items() if s == "covered"]
        rng.shuffle(covered)
        flagged = covered[:FLAGS_PER_LIFE]
        for fc, fr in flagged:
            action.flag(fc, fr, win, calib)
            time.sleep(CLICK_PAUSE)
            frame = capture.grab_client(win)
            cell = grid_perception.slice_cell(frame, calib, fc, fr)
            st = grid_perception.classify_cell(cell)["state"]
            diff_log["flag"].append(cell_diff(cell, tpls, "flag"))
            if st != "flag":
                audit_failures.append(f"life{life}: flag({fc},{fr}) got={st}")
                out(f"P2 life{life}: flag placement mismatch ({fc},{fr}) got={st}")
            states, _ = scan(frame, calib)

        # 随机点击至死
        dead, dead_frame, last_click = False, None, None
        for _ in range(40):
            frame = capture.grab_client(win)
            states, _ = scan(frame, calib)
            if any(s in ("mine", "mine_red", "mine_cross") for s in states.values()):
                dead, dead_frame = True, capture.grab_client(win)
                break
            covered = [k for k, s in states.items() if s == "covered"]
            if not covered:
                out(f"P2 life{life}: board cleared without death (won)")
                break
            last_click = rng.choice(covered)
            action.reveal(last_click[0], last_click[1], win, calib)
            time.sleep(CLICK_PAUSE)
        if not dead:
            out(f"P2 life{life}: no death this life")
            continue
        lives_dead += 1

        # 死盘审计
        states, unk = scan(dead_frame, calib)
        cnt = Counter(states.values())
        errs = []
        if unk:
            errs.append(f"unknown={unk}")
        mr = [k for k, s in states.items() if s == "mine_red"]
        if len(mr) != 1:
            errs.append(f"mine_red count={len(mr)}")
        elif last_click and mr[0] != last_click:
            errs.append(f"mine_red at {mr[0]} != last_click {last_click}")
        for k in flagged:                    # 被旗格：旗对→flag / 旗错→mine_cross
            if states.get(k) not in ("flag", "mine_cross"):
                errs.append(f"flagged {k} got={states.get(k)}")
        if mines_total is not None:
            # 恒等式：雷数 = 裸雷 + 踩中红底雷 + 旗对的旗。
            # mine_cross 是"错旗位"（无雷），不计入雷数（v1 修正：此前误加 cross 导致 15≠10）。
            total_mines = cnt["mine"] + cnt["mine_red"] + cnt["flag"]
            if total_mines != mines_total:
                errs.append(f"mine total {total_mines} != {mines_total}")
        else:
            out(f"P2 life{life}: mines_total unknown (custom board), formula N/A")
        # 未旗未击且未覆盖 → 必为 mine；已开格（此前点击）→ 必为数字/空白
        for (cc, rr), s in states.items():
            if s == "covered" or (cc, rr) in flagged or (cc, rr) == last_click:
                continue
            if s == "mine" or s in [str(i) for i in range(9)]:
                continue
            errs.append(f"cell({cc},{rr}) unexpected state={s}")
        if errs:
            audit_failures.append(f"life{life}: " + "; ".join(errs))
            out(f"P2 life{life} AUDIT FAIL: {errs}")
        else:
            out(f"P2 life{life} audit OK: mine={cnt['mine']} mine_red={cnt['mine_red']} "
                f"cross={cnt['mine_cross']} flag={cnt['flag']} (total={mines_total})")

    out(f"P2 dead lives={lives_dead} unknown_total={unknown_total} "
        f"audit_failures={len(audit_failures)}")
    return diff_log, lives_dead, audit_failures


# --------------------------------------------------------------------------- #
def report(diff_log):
    out("\nper-class live diff vs template (min/med/max):")
    all_ok = True
    need = {"flag": 3, "question": 1, "mine": 3, "mine_red": 1, "mine_cross": 1, "blank": 5}
    for cls in ("flag", "question", "mine", "mine_red", "mine_cross", "blank"):
        v = sorted(diff_log.get(cls, []))
        if not v:
            out(f"  {cls:10s}: NO SAMPLE")
            all_ok = False
            continue
        mx = v[-1]
        ok = len(v) >= need[cls] and mx <= ACCEPT_DIFF
        all_ok &= ok
        out(f"  {cls:10s}: n={len(v)} min={v[0]:.1f} med={v[len(v)//2]:.1f} max={mx:.1f} "
            f"{'OK' if ok else 'FAIL'}")
    return all_ok


def main():
    cfg, win, proc = setup()
    calib = fresh_calib(win)
    if (calib["rows"], calib["cols"], calib["mines_total"]) != (9, 9, 10):
        out(f"board {calib['rows']}x{calib['cols']}/"
            f"{calib['mines_total']} 非标准初级盘 — 自定义盘 mines_total 不可信；"
            f"请手动把 winmine 设为初级(9×9)后重跑")
        print(open(ROOT / "scripts/_sprites_probe.txt", encoding="utf-8").read())
        log.close()
        sys.exit(2)
    out("board 9x9/10 (standard beginner)")
    p1_ok, diff_log, win, proc = p1_right_click_cycle(cfg, win, proc)
    dl2, lives_dead, audit_failures = p2_mine_states(win)
    for k, v in dl2.items():
        diff_log[k].extend(v)
    stats_ok = report(diff_log)
    p2_ok = lives_dead >= 1 and not audit_failures
    out(f"\nP1 right-click cycle: {'PASS' if p1_ok else 'FAIL'}")
    out(f"P2 death audit ({lives_dead} lives): {'PASS' if p2_ok else 'FAIL'}")
    out(f"OVERALL: {'PASS' if (p1_ok and p2_ok and stats_ok) else 'FAIL'}")


if __name__ == "__main__":
    _exit = 0
    try:
        main()
    except SystemExit as e:
        _exit = int(e.code or 0)
    except Exception:
        import traceback
        traceback.print_exc(file=log)
        _exit = 1
    finally:
        restore_registry()
    log.close()
    print(open(ROOT / "scripts/_sprites_probe.txt", encoding="utf-8").read())
    sys.exit(_exit)
