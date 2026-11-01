#!/usr/bin/env python3
from struct import unpack_from

def hangul_count(path: str) -> int:
    data = open(path, "rb").read()
    num_tables = unpack_from(">H", data, 4)[0]
    off = 12
    cmap_off = None
    for _ in range(num_tables):
        tag, _csum, offset, _length = unpack_from(">4sIII", data, off)
        off += 16
        if tag == b"cmap":
            cmap_off = offset
            break
    if cmap_off is None:
        return -1
    _version, n_enc = unpack_from(">HH", data, cmap_off)
    cps = set()
    for i in range(n_enc):
        _plat, _enc, sub = unpack_from(">HHI", data, cmap_off + 4 + i * 8)
        fmt = unpack_from(">H", data, cmap_off + sub)[0]
        if fmt != 4:
            continue
        _length, _lang, seg_count_x2 = unpack_from(">HHH", data, cmap_off + sub + 2)
        seg_count = seg_count_x2 // 2
        end_count = list(unpack_from(">%dH" % seg_count, data, cmap_off + sub + 14))
        start_count = list(
            unpack_from(">%dH" % seg_count, data, cmap_off + sub + 16 + seg_count * 2)
        )
        for a, b in zip(start_count, end_count):
            for c in range(a, min(b, 0xD7A3) + 1):
                if 0xAC00 <= c <= 0xD7A3:
                    cps.add(c)
    return len(cps)


for p in (
    "/home/pi/fingerprint/ui/static/fonts/gungsuh.ttf",
    "/home/pi/fingerprint/ui/static/fonts/gungsuh-bold.ttf",
):
    n = hangul_count(p)
    print(p.split("/")[-1], "hangul=", n)
