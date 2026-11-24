# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 dimschlukas
"""Code 128 (auto subset B/C) barcode encoder -> list of module widths."""

_PATTERNS = [
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312",
    "132212", "221213", "221312", "231212", "112232", "122132", "122231", "113222",
    "123122", "123221", "223211", "221132", "221231", "213212", "223112", "312131",
    "311222", "321122", "321221", "312212", "322112", "322211", "212123", "212321",
    "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121",
    "313121", "211331", "231131", "213113", "213311", "213131", "311123", "311321",
    "331121", "312113", "312311", "332111", "314111", "221411", "431111", "111224",
    "111422", "121124", "121421", "141122", "141221", "112214", "112412", "122114",
    "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112",
    "421211", "212141", "214121", "412121", "111143", "111341", "131141", "114113",
    "114311", "411113", "411311", "113141", "114131", "311141", "411131", "211412",
    "211214", "211232", "2331112",
]
START_B, START_C, CODE_B, CODE_C, STOP = 104, 105, 100, 99, 106


def _digits_run(s, i):
    j = i
    while j < len(s) and s[j].isdigit():
        j += 1
    return j - i


def code128_codes(data: str) -> list[int]:
    for ch in data:
        if not (32 <= ord(ch) < 127):
            raise ValueError(f"Code 128 supports printable ASCII only (got {ch!r})")
    codes = []
    i = 0
    mode = None
    while i < len(data):
        run = _digits_run(data, i)
        use_c = run >= 4 or (run >= 2 and run == len(data) - i and run % 2 == 0)
        if use_c:
            n = run - (run % 2)
            if mode != "C":
                codes.append(START_C if mode is None else CODE_C)
                mode = "C"
            for k in range(i, i + n, 2):
                codes.append(int(data[k:k + 2]))
            i += n
        else:
            if mode != "B":
                codes.append(START_B if mode is None else CODE_B)
                mode = "B"
            codes.append(ord(data[i]) - 32)
            i += 1
    if not codes:
        codes = [START_B]
    check = codes[0] + sum(c * k for k, c in enumerate(codes[1:], 1))
    codes.append(check % 103)
    codes.append(STOP)
    return codes


def code128_modules(data: str) -> list[int]:
    """Alternating bar/space widths in modules, starting with a bar."""
    out = []
    for c in code128_codes(data):
        out.extend(int(x) for x in _PATTERNS[c])
    return out
