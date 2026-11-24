# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 dimschlukas
"""Brother P-touch raster protocol (PT-P700 / P750W / E550W family).

Reference: Brother "Software Developer's Manual - Raster Command Reference"
for PT-E550W/P750W/P710BT, which the PT-P700 shares.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# Tape width (mm, as reported) -> (printable pins, left margin pins) on a 128 pin head
TAPE_GEOMETRY = {
    4: (24, 52),  # 3.5 mm
    6: (32, 48),
    9: (50, 39),
    12: (70, 29),
    18: (112, 8),
    24: (128, 0),
}
TAPE_WIDTHS = [4, 6, 9, 12, 18, 24]
TAPE_LABEL = {4: "3.5 mm", 6: "6 mm", 9: "9 mm", 12: "12 mm", 18: "18 mm", 24: "24 mm"}

MEDIA_TYPES = {
    0x00: "No media",
    0x01: "Laminated tape",
    0x03: "Non-laminated tape",
    0x04: "Fabric tape",
    0x11: "Heat-shrink tube (2:1)",
    0x13: "Flexible ID tape",
    0x14: "Strong adhesive tape",
    0x17: "Heat-shrink tube (3:1)",
    0xFF: "Incompatible tape",
}

TAPE_COLORS = {
    0x01: "White",
    0x02: "Other",
    0x03: "Clear",
    0x04: "Red",
    0x05: "Blue",
    0x06: "Yellow",
    0x07: "Green",
    0x08: "Black",
    0x09: "Clear (white text)",
    0x20: "Matte White",
    0x21: "Matte Clear",
    0x22: "Matte Silver",
    0x23: "Satin Gold",
    0x24: "Satin Silver",
    0x30: "Blue (D)",
    0x31: "Red (D)",
    0x40: "Fluorescent Orange",
    0x41: "Fluorescent Yellow",
    0x50: "Berry Pink (S)",
    0x51: "Light Gray (S)",
    0x52: "Lime Green (S)",
    0x60: "Yellow (F)",
    0x61: "Pink (F)",
    0x62: "Blue (F)",
    0x70: "White (heat-shrink)",
    0x90: "White (Flex ID)",
    0x91: "Yellow (Flex ID)",
    0xF0: "Cleaning",
    0xF1: "Stencil",
    0xFF: "Incompatible",
}
TEXT_COLORS = {
    0x01: "White",
    0x02: "Other",
    0x04: "Red",
    0x05: "Blue",
    0x08: "Black",
    0x0A: "Gold",
    0x62: "Blue (F)",
    0xF0: "Cleaning",
    0xF1: "Stencil",
    0xFF: "Incompatible",
}
# approximate sRGB for on-screen preview
COLOR_RGB = {
    "White": (1, 1, 1),
    "Clear": (0.93, 0.95, 0.97),
    "Clear (white text)": (0.85, 0.88, 0.9),
    "Red": (0.85, 0.15, 0.15),
    "Blue": (0.15, 0.35, 0.8),
    "Yellow": (1.0, 0.88, 0.2),
    "Green": (0.2, 0.65, 0.3),
    "Black": (0.08, 0.08, 0.08),
    "Matte White": (0.97, 0.97, 0.95),
    "Matte Clear": (0.9, 0.92, 0.93),
    "Matte Silver": (0.75, 0.76, 0.78),
    "Satin Gold": (0.83, 0.69, 0.3),
    "Satin Silver": (0.78, 0.78, 0.8),
    "Gold": (0.83, 0.69, 0.3),
    "Fluorescent Orange": (1.0, 0.5, 0.1),
    "Fluorescent Yellow": (0.9, 1.0, 0.1),
    "Berry Pink (S)": (0.85, 0.3, 0.55),
    "Light Gray (S)": (0.8, 0.8, 0.8),
    "Lime Green (S)": (0.6, 0.85, 0.2),
}

ERRORS_1 = {
    0x01: "No media",
    0x02: "End of media",
    0x04: "Cutter jam",
    0x08: "Weak batteries",
    0x10: "Printer in use",
    0x20: "Printer turned off",
    0x40: "High-voltage adapter",
    0x80: "Fan motor error",
}
ERRORS_2 = {
    0x01: "Replace media",
    0x02: "Expansion buffer full",
    0x04: "Communication error",
    0x08: "Communication buffer full",
    0x10: "Cover open",
    0x20: "Overheating",
    0x40: "Black marking not detected",
    0x80: "System error",
}
STATUS_TYPES = {
    0x00: "Status reply",
    0x01: "Printing completed",
    0x02: "Error occurred",
    0x04: "Exit IF mode",
    0x05: "Turned off",
    0x06: "Notification",
    0x07: "Phase change",
}
MODELS = {
    0x64: "PT-H500",
    0x65: "PT-E500",
    0x67: "PT-P700",
    0x68: "PT-P750W",
    0x69: "PT-E550W",
    0x76: "PT-P710BT",
}


class PrinterError(Exception):
    pass


@dataclass
class Status:
    raw: bytes
    model: str = ""
    media_width: int = 0
    media_type: int = 0
    tape_color: int = 0
    text_color: int = 0
    errors: list = field(default_factory=list)
    status_type: int = 0
    phase: int = 0
    notification: int = 0

    @classmethod
    def parse(cls, raw: bytes) -> Status:
        if len(raw) < 32 or raw[0] != 0x80 or raw[1] != 0x20:
            raise PrinterError(f"Invalid status reply ({len(raw)} bytes): {raw.hex()}")
        errs = [msg for bit, msg in ERRORS_1.items() if raw[8] & bit]
        errs += [msg for bit, msg in ERRORS_2.items() if raw[9] & bit]
        return cls(
            raw=raw,
            model=MODELS.get(raw[4], f"0x{raw[4]:02x}"),
            media_width=raw[10],
            media_type=raw[11],
            tape_color=raw[24],
            text_color=raw[25],
            errors=errs,
            status_type=raw[18],
            phase=raw[19],
            notification=raw[22],
        )

    @property
    def media_type_name(self):
        return MEDIA_TYPES.get(self.media_type, f"Unknown (0x{self.media_type:02x})")

    @property
    def tape_color_name(self):
        return TAPE_COLORS.get(self.tape_color, f"0x{self.tape_color:02x}")

    @property
    def text_color_name(self):
        return TEXT_COLORS.get(self.text_color, f"0x{self.text_color:02x}")

    @property
    def tape_width(self):
        """Nearest supported tape width key for TAPE_GEOMETRY (0 if none)."""
        if self.media_width == 0 or self.media_type == 0:
            return 0
        return min(TAPE_WIDTHS, key=lambda w: abs(w - self.media_width))

    def summary(self):
        if self.media_type == 0:
            tape = "No tape"
        else:
            tape = (
                f"{TAPE_LABEL.get(self.tape_width, f'{self.media_width} mm')} "
                f"{self.media_type_name}, {self.text_color_name} on {self.tape_color_name}"
            )
        return tape + (f" — {', '.join(self.errors)}" if self.errors else "")


def packbits(data: bytes) -> bytes:
    """TIFF PackBits encoder."""
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        # run of identical bytes?
        j = i + 1
        while j < n and j - i < 128 and data[j] == data[i]:
            j += 1
        if j - i >= 2:
            out.append((257 - (j - i)) & 0xFF)
            out.append(data[i])
            i = j
            continue
        # literal run
        j = i + 1
        while j < n and j - i < 128:
            if j + 1 < n and data[j] == data[j + 1]:
                break
            j += 1
        out.append(j - i - 1)
        out += data[i:j]
        i = j
    return bytes(out)


@dataclass
class JobOptions:
    tape_width: int = 12  # mm key of TAPE_GEOMETRY
    media_type: int = 0x01
    auto_cut: bool = True
    chain: bool = False  # chain printing: don't feed/cut after last label
    margin_dots: int = 14  # feed margin before/after label (~2 mm)
    high_quality: bool = False


def encode_page(lines: list[bytes]) -> bytes:
    """Encode 16-byte raster lines (one per 1/180 inch along the tape)."""
    out = bytearray(b"M\x02")  # TIFF (PackBits) compression
    for line in lines:
        if not any(line):
            out += b"Z"
        else:
            c = packbits(line)
            out += b"G" + len(c).to_bytes(2, "little") + c
    return bytes(out)


def build_job(pages: list[list[bytes]], opts: JobOptions) -> bytes:
    """Build a print job. Each page is a list of 16-byte raster lines."""
    job = bytearray(b"\x00" * 100)  # invalidate
    job += b"\x1b@"  # initialize
    job += b"\x1bia\x01"  # dynamic command mode: raster
    n = len(pages)
    for i, lines in enumerate(pages):
        page_kind = 0 if i == 0 else (2 if i == n - 1 else 1)
        flags = 0x80 | 0x04 | 0x02  # recover, width valid, media type valid
        if opts.high_quality:
            flags |= 0x40
        job += (
            b"\x1biz"
            + bytes([flags, opts.media_type, opts.tape_width, 0])
            + len(lines).to_bytes(4, "little")
            + bytes([page_kind, 0])
        )
        job += b"\x1biM" + bytes([0x40 if opts.auto_cut else 0x00])
        job += b"\x1biK" + bytes([0x00 if opts.chain else 0x08])
        job += b"\x1bid" + int(opts.margin_dots).to_bytes(2, "little")
        job += encode_page(lines)
        job += b"\x1a" if i == n - 1 else b"\x0c"
    return bytes(job)


class Printer:
    def __init__(self, transport):
        self.t = transport

    @property
    def info(self):
        return self.t.info

    def _drain(self):
        for _ in range(4):
            if not self.t.read(64, timeout_ms=50):
                break

    def status(self) -> Status:
        self._drain()
        self.t.write(b"\x00" * 100 + b"\x1b@" + b"\x1biS")
        raw = b""
        end = time.monotonic() + 3
        while len(raw) < 32 and time.monotonic() < end:
            raw += self.t.read(32 - len(raw), timeout_ms=500)
        if len(raw) < 32:
            raise PrinterError("Printer did not answer the status request")
        return Status.parse(raw[:32])

    def print_pages(self, pages, opts: JobOptions, progress=None, timeout=None):
        st = self.status()
        if st.errors:
            raise PrinterError(", ".join(st.errors))
        if st.media_type == 0:
            raise PrinterError("No tape cassette inserted")
        if st.tape_width != opts.tape_width:
            raise PrinterError(
                f"Label was designed for {TAPE_LABEL[opts.tape_width]} tape but the "
                f"printer has {TAPE_LABEL.get(st.tape_width, st.media_width)} tape"
            )
        opts.media_type = st.media_type
        job = build_job(pages, opts)
        copies = len(pages)
        if timeout is None:
            timeout = 20 + sum(len(p) for p in pages) / 180 * 25.4 / 10  # ~10 mm/s
        if progress:
            progress("Sending data…")
        self.t.write(job, timeout_ms=20000)
        if progress:
            progress("Printing…")
        # wait for "printing completed" per page or an error
        done = 0
        end = time.monotonic() + timeout
        buf = b""
        while time.monotonic() < end:
            buf += self.t.read(32, timeout_ms=500)
            while len(buf) >= 32:
                s = Status.parse(buf[:32])
                buf = buf[32:]
                if s.status_type == 0x02 or s.errors:
                    raise PrinterError("Printer error: " + (", ".join(s.errors) or "unknown"))
                if s.status_type == 0x01:
                    done += 1
                    if done >= copies:
                        return True
                    if progress:
                        progress(f"Printed {done}/{copies}…")
        raise PrinterError("Timed out waiting for the printer to finish")

    def close(self):
        self.t.close()
