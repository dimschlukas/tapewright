# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 dimschlukas
"""Command line interface: status, print, render."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from . import render as R
from .protocol import TAPE_LABEL, JobOptions, Printer, PrinterError
from .usb import USBError, open_printer


def _label_from_args(a) -> R.Label:
    if a.file:
        with open(a.file, encoding="utf-8") as fh:
            lab = R.Label.from_json(fh.read())
    else:
        lab = R.Label(elements=[])
        for t in a.text or []:
            lab.elements.append(
                R.TextElement(
                    text=t.replace("\\n", "\n"),
                    font=a.font,
                    auto_size=a.size is None,
                    size=a.size or 16,
                    align=a.align,
                )
            )
        if a.qr:
            lab.elements.insert(0, R.QRElement(data=a.qr))
        if a.image:
            lab.elements.insert(0, R.ImageElement(path=a.image))
        lab.border = a.border
        lab.invert = a.invert
        if a.length:
            lab.length_mm = a.length
    return lab


def gui_entry():
    """Entry point for the desktop launcher (tapewright-gui [FILE])."""
    from .gui import main as gui_main

    return gui_main(sys.argv)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="tapewright",
        description="Label tool for Brother P-touch printers (PT-P700 and compatible)",
    )
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status", help="show printer and tape status")
    cal = sub.add_parser("calibrate", help="print a 100 mm ruler to check the printed length")
    cal.add_argument("--length", type=float, default=100.0, help="ruler length in mm")
    cal.add_argument("--margin", type=float, default=2.0, help="feed margin mm")
    g = sub.add_parser("gui", help="start the graphical editor (default)")
    g.add_argument("file", nargs="?", help="label file to open")
    for name in ("print", "render"):
        p = sub.add_parser(name, help=f"{name} a label")
        p.add_argument("file", nargs="?", help="saved .twlabel label file")
        p.add_argument(
            "-t", "--text", action="append", help="text block (repeatable, \\n = newline)"
        )
        p.add_argument(
            "-f", "--font", default="Sans Bold", help="Pango font, e.g. 'DejaVu Serif Italic'"
        )
        p.add_argument("-s", "--size", type=float, help="font size in pt (default: fit tape)")
        p.add_argument("--align", default="center", choices=["left", "center", "right"])
        p.add_argument("--qr", help="add a QR code with this content")
        p.add_argument("--image", help="add an image")
        p.add_argument(
            "-b", "--border", default="none", choices=["none", "rect", "rounded", "double"]
        )
        p.add_argument("--invert", action="store_true")
        p.add_argument("-l", "--length", type=float, help="fixed length in mm")
        p.add_argument(
            "-w",
            "--tape",
            type=int,
            choices=sorted(TAPE_LABEL),
            help="tape width mm (default: detect / from file)",
        )
        p.add_argument("-n", "--start", type=int, default=1, help="first value of {n}")
        if name == "print":
            p.add_argument("-c", "--copies", type=int, default=1)
            p.add_argument("--no-cut", action="store_true")
            p.add_argument("--chain", action="store_true")
            p.add_argument("--margin", type=float, default=2.0, help="feed margin mm")
        else:
            p.add_argument("-o", "--output", default="label.png")
    a = ap.parse_args(argv)

    if a.cmd in (None, "gui"):
        from .gui import main as gui_main

        return gui_main([sys.argv[0]] + ([a.file] if getattr(a, "file", None) else []))

    try:
        if a.cmd == "status":
            p = Printer(open_printer())
            try:
                st = p.status()
                print(f"{p.info.name} ({st.model})")
                print(st.summary())
            finally:
                p.close()
            return 0

        if a.cmd == "calibrate":
            p = Printer(open_printer())
            try:
                st = p.status()
                tw = st.tape_width or 12
                r = R.ruler_label(tw, a.length)
                opts = JobOptions(tape_width=tw, margin_dots=round(R.mm(a.margin)))
                p.print_pages(
                    [R.to_raster_lines(r, tw)], opts, progress=lambda m: print(m, file=sys.stderr)
                )
            finally:
                p.close()
            print(f"Printed a {a.length:g} mm calibration ruler with {a.margin:g} mm feed margin.")
            print("Measure: (1) the whole cut piece, (2) blank tape before the 0 line,")
            print(f"(3) the distance from the 0 line to the {a.length:g} line,")
            print("(4) blank tape after it.")
            return 0

        lab = _label_from_args(a)
        if not lab.elements:
            ap.error("nothing to print: give a file, --text, --qr or --image")

        if a.cmd == "render":
            if a.tape:
                lab.tape_width = a.tape
            r = R.render(lab, n=a.start)
            for w in r.warnings:
                print("warning:", w, file=sys.stderr)
            r.bitmap.convert("1").save(a.output, dpi=(R.DPI, R.DPI))
            print(f"{a.output}: {r.length_mm:.1f} mm on {TAPE_LABEL[lab.tape_width]} tape")
            return 0

        p = Printer(open_printer())
        try:
            st = p.status()
            if a.tape:
                lab.tape_width = a.tape
            elif not a.file and st.tape_width:
                lab.tape_width = st.tape_width
            opts = JobOptions(
                tape_width=lab.tape_width,
                auto_cut=not a.no_cut,
                chain=a.chain,
                margin_dots=round(R.mm(a.margin)),
            )
            pages = []
            for i in range(a.copies):
                if i == 0 or lab.uses_counter():
                    r = R.render(lab, n=a.start + i)
                    for w in r.warnings:
                        print("warning:", w, file=sys.stderr)
                    lines = R.to_raster_lines(r, lab.tape_width)
                pages.append(lines)
            p.print_pages(pages, opts, progress=lambda m: print(m, file=sys.stderr))
            print(f"Printed {a.copies} label(s), {r.length_mm:.1f} mm")
        finally:
            p.close()
        return 0
    except (USBError, PrinterError, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
