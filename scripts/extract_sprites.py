# -*- coding: utf-8 -*-
"""从 winmine.exe 位图资源提取精灵图（纯 ctypes，无新依赖）。

资源位图 = DIB（BITMAPINFOHEADER + 调色板 + 像素），补 BMP 文件头后经 cv2 解码存 PNG。
产物：perception/templates/raw/res_<id>.png + 尺寸清单。
"""
import ctypes
import struct
import sys
from ctypes import wintypes
from pathlib import Path

import cv2
import numpy as np

EXE = Path(r"D:\workbuddy\projects\minesweeper-bot\apps\winmine.exe")
OUT = Path(r"D:\workbuddy\projects\minesweeper-bot\perception\templates\raw")

RT_BITMAP = 2
LOAD_LIBRARY_AS_DATAFILE = 0x00000002
IS_INTRESOURCE = 0xFFFF

_kernel32 = ctypes.WinDLL("kernel32")


def _name_str(ptr: int) -> str:
    """资源名/类型：整数 atom（0 < ptr < 0x10000）→ '#id'；否则宽字符串。"""
    if 0 < ptr < 0x10000:
        return f"#{ptr}"
    return ctypes.wstring_at(ptr)


_ENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long)
_TYPEPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p, ctypes.c_long)

_kernel32.LoadLibraryExW.argtypes = (wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD)
_kernel32.LoadLibraryExW.restype = wintypes.HMODULE
_kernel32.EnumResourceTypesW.argtypes = (wintypes.HMODULE, _TYPEPROC, ctypes.c_long)
_kernel32.EnumResourceTypesW.restype = wintypes.BOOL
_kernel32.EnumResourceNamesW.argtypes = (wintypes.HMODULE, ctypes.c_void_p, _ENUMPROC, ctypes.c_long)
_kernel32.EnumResourceNamesW.restype = wintypes.BOOL
_kernel32.FindResourceW.argtypes = (wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p)
_kernel32.FindResourceW.restype = wintypes.HRSRC
_kernel32.LoadResource.argtypes = (wintypes.HMODULE, wintypes.HRSRC)
_kernel32.LoadResource.restype = wintypes.HGLOBAL
_kernel32.LockResource.argtypes = (wintypes.HGLOBAL,)
_kernel32.LockResource.restype = ctypes.c_void_p
_kernel32.SizeofResource.argtypes = (wintypes.HMODULE, wintypes.HRSRC)
_kernel32.SizeofResource.restype = wintypes.DWORD


def _dib_to_bmp(dib: bytes) -> bytes | None:
    """资源 DIB → BMP 文件字节。返回 None 表示解析失败。"""
    if len(dib) < 40:
        return None
    hdr_size = struct.unpack_from("<I", dib, 0)[0]
    if hdr_size < 12:
        return None
    if hdr_size == 12:  # BITMAPCOREHEADER
        bc = struct.unpack_from("<HHhHH", dib, 0)  # size,w,h,planes,bpp
        bpp, height = bc[4], bc[2]
        n_colors, palette = 0, b""
        if bpp <= 8:
            n_colors = 1 << bpp
            palette = dib[12:12 + n_colors * 3]
            palette = b"".join(bytes((palette[i * 3 + 2], palette[i * 3 + 1], palette[i * 3], 0))
                               for i in range(n_colors))
        core = struct.pack("<IiiHHIIiiII", 40, bc[1], bc[2], bc[3], bc[4],
                           0, 0, 0, 0, 0)
        body = core + palette + dib[12 + n_colors * 3:]
    else:
        h = struct.unpack_from("<IiiHHIIiiII", dib, 0)
        height = h[2]
        bpp = h[5]
        clr_used = h[7]
        if bpp <= 8 and clr_used == 0:
            clr_used = 1 << bpp
        palette = dib[hdr_size:hdr_size + clr_used * 4]
        body = dib
    if height < 0:
        pass  # top-down，cv2.imdecode 可处理负高 BMP
    offset = 14 + len(body[:40 + len(palette)] if hdr_size != 12 else 40 + len(palette))
    offset = 14 + (40 + len(palette))
    size = offset + len(body) - (40 + len(palette))
    size = offset + (len(body) - (40 + len(palette)))
    file_size = 14 + len(body)
    fh = struct.pack("<2sIHHI", b"BM", file_size, 0, 0, offset)
    # body 从 BITMAPINFOHEADER 开始（重新组合，因为 core 情况下已换 40 字节头）
    return fh + body


def _dib_to_bmp2(dib: bytes) -> bytes | None:
    """资源 DIB → BMP 文件字节（统一处理 core/normal 头）。"""
    if len(dib) < 12:
        return None
    hdr_size = struct.unpack_from("<I", dib, 0)[0]
    if hdr_size >= 40:
        info, pixels = dib, dib[hdr_size:]  # 占位；直接整块用
        return b"BM" + struct.pack("<IHHI", 14 + len(dib), 0, 0, 14) + dib
    if hdr_size == 12:
        w, h, bpp = struct.unpack_from("<hhH", dib, 4)[0], struct.unpack_from("<h", dib, 6)[0], struct.unpack_from("<H", dib, 10)[0]
        n_colors = (1 << bpp) if bpp <= 8 else 0
        pal_src = dib[12:12 + n_colors * 3]
        pal = b"".join(bytes((pal_src[i * 3 + 2], pal_src[i * 3 + 1], pal_src[i * 3], 0))
                       for i in range(len(pal_src) // 3))
        info40 = struct.pack("<IiiHHIIiiII", 40, w, h, 1, bpp, 0, 0, 0, 0, 0)
        pixels = dib[12 + n_colors * 3:]
        return b"BM" + struct.pack("<IHHI", 14 + 40 + len(pal) + len(pixels), 0, 0, 14 + 40 + len(pal)) + info40 + pal + pixels
    return None


def extract() -> list[tuple[str, int, int]]:
    OUT.mkdir(parents=True, exist_ok=True)
    hmod = _kernel32.LoadLibraryExW(str(EXE), None, LOAD_LIBRARY_AS_DATAFILE)
    if not hmod:
        raise RuntimeError(f"LoadLibraryExW failed err={ctypes.GetLastError():#x}")

    found: list[tuple[int, str]] = []

    @_ENUMPROC
    def cb(_h, rtype, rname, _param):
        found.append((ctypes.cast(rtype, ctypes.c_void_p).value or 0, _name_str(ctypes.cast(rname, ctypes.c_void_p).value or 0)))
        return True

    # 全类型普查
    types: list[str] = []

    @_TYPEPROC
    def tcb(_h, rtype, _param):
        types.append(_name_str(ctypes.cast(rtype, ctypes.c_void_p).value or 0))
        return True

    _kernel32.EnumResourceTypesW(hmod, tcb, 0)
    type_rows = []
    for t in types:
        n: list[str] = []

        @_ENUMPROC
        def ncb(_h, _t, rname, _p):
            n.append(_name_str(ctypes.cast(rname, ctypes.c_void_p).value or 0))
            return True

        targ = ctypes.c_void_p(int(t[1:])) if t.startswith("#") else ctypes.c_wchar_p(t)
        _kernel32.EnumResourceNamesW(hmod, targ, ncb, 0)
        type_rows.append((t, n))

    # 回填 RT_BITMAP 名单（cb 仅作存在性；实际名单来自普查）
    for t, n in type_rows:
        if t == "#2":
            found = [(RT_BITMAP, name) for name in n]
            break

    if not found:
        detail = "; ".join(f"{t}[{len(n)}]:{','.join(n[:12])}" for t, n in type_rows)
        raise RuntimeError(f"no RT_BITMAP; types: {detail} (lasterr={ctypes.GetLastError():#x})")

    rows = []
    for _t, name in found:
        hres = _kernel32.FindResourceW(hmod, _res_arg(name), ctypes.c_void_p(RT_BITMAP))
        if not hres:
            continue
        size = _kernel32.SizeofResource(hmod, hres)
        hglob = _kernel32.LoadResource(hmod, hres)
        ptr = _kernel32.LockResource(hglob)
        if not ptr:
            continue
        dib = ctypes.string_at(ptr, size)
        bmp = _dib_to_bmp2(dib)
        if bmp is None:
            print(f"res {name}: DIB parse failed ({size}B)")
            continue
        img = cv2.imdecode(np.frombuffer(bmp, np.uint8), cv2.IMREAD_UNCHANGED)
        if img is None:
            print(f"res {name}: cv2 decode failed ({size}B)")
            continue
        out = OUT / f"res_{name.lstrip('#')}.png"
        cv2.imwrite(str(out), img)
        rows.append((name, img.shape[1], img.shape[0], size))
    for name, w, h, size in rows:
        print(f"res {name}: {w}x{h} ({size}B)")
    return [(n, w, h) for n, w, h, _ in rows]


def _res_arg(name: str):
    """FindResource 的名字参数：'#id' → 整数 atom；否则宽字符串。"""
    if name.startswith("#"):
        return ctypes.c_void_p(int(name[1:]))
    return ctypes.c_wchar_p(name)


if __name__ == "__main__":
    import traceback
    _log = io.StringIO() if False else open(r"D:\workbuddy\projects\minesweeper-bot\scripts\_extract_log.txt", "w", encoding="utf-8")
    try:
        rows = extract()
        for name, w, h, _ in rows:
            print(f"res {name}: {w}x{h}", file=_log)
        print(f"TOTAL {len(rows)}", file=_log)
        sys.exit(0 if rows else 1)
    except Exception:
        traceback.print_exc(file=_log)
        sys.exit(1)
    finally:
        _log.close()
