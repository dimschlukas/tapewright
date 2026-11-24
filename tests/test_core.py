# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 dimschlukas
"""Hardware-free tests for the protocol, barcode and renderer."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tapewright import render as R  # noqa: E402
from tapewright.barcode import code128_codes, code128_modules  # noqa: E402
from tapewright.protocol import (TAPE_GEOMETRY, JobOptions, Status,  # noqa: E402
                                 build_job, packbits)


def unpackbits(data: bytes) -> bytes:
    out, i = bytearray(), 0
    while i < len(data):
        n = data[i]
        i += 1
        if n < 128:
            out += data[i:i + n + 1]
            i += n + 1
        elif n > 128:
            out += bytes([data[i]]) * (257 - n)
            i += 1
    return bytes(out)


class PackBitsTest(unittest.TestCase):
    def test_roundtrip(self):
        cases = [b"", b"\x00", b"\x00" * 16, b"\xff" * 300, bytes(range(16)),
                 b"\x01\x01\x02\x03\x03\x03\x04" * 5, bytes([0, 255] * 70)]
        for c in cases:
            self.assertEqual(unpackbits(packbits(c)), c, c)

    def test_run_compresses(self):
        self.assertEqual(packbits(b"\xaa" * 16), b"\xf1\xaa")


class Code128Test(unittest.TestCase):
    def test_uses_subset_c_for_digits(self):
        codes = code128_codes("123456")
        self.assertEqual(codes[0], 105)               # Start C
        self.assertEqual(codes[1:4], [12, 34, 56])
        self.assertEqual(codes[-1], 106)              # Stop

    def test_checksum(self):
        # Start B (104) + 'A'(33)*1 + 'B'(34)*2 = 205 -> 205 % 103 = 102
        self.assertEqual(code128_codes("AB")[-2], 102)

    def test_module_count(self):
        codes = code128_codes("Tape-42")
        self.assertEqual(sum(code128_modules("Tape-42")), 11 * (len(codes) - 1) + 13)

    def test_rejects_non_ascii(self):
        with self.assertRaises(ValueError):
            code128_codes("ä")


class StatusTest(unittest.TestCase):
    RAW = bytes.fromhex("802042306730000000000c010000000000000000000000000308000000000000")

    def test_parse(self):
        s = Status.parse(self.RAW)
        self.assertEqual(s.model, "PT-P700")
        self.assertEqual(s.tape_width, 12)
        self.assertEqual(s.media_type_name, "Laminated tape")
        self.assertEqual(s.tape_color_name, "Clear")
        self.assertEqual(s.text_color_name, "Black")
        self.assertEqual(s.errors, [])

    def test_errors(self):
        raw = bytearray(self.RAW)
        raw[9] = 0x10
        self.assertIn("Cover open", Status.parse(bytes(raw)).errors)


class RenderTest(unittest.TestCase):
    def test_every_tape_width(self):
        for tw, (pins, margin) in TAPE_GEOMETRY.items():
            lab = R.Label(tape_width=tw, border="rect",
                          elements=[R.TextElement(text="Hi")])
            res = R.render(lab)
            self.assertEqual(res.bitmap.size[1], pins)
            lines = R.to_raster_lines(res, tw)
            self.assertEqual(len(lines), res.length_dots)
            self.assertTrue(all(len(line) == 16 for line in lines))
            # nothing may be printed outside the printable pins
            for line in lines:
                v = int.from_bytes(line, "big")
                if margin:
                    self.assertEqual(v >> (128 - margin), 0)
                    self.assertEqual(v & ((1 << margin) - 1), 0)

    def test_fixed_length(self):
        lab = R.Label(length_mm=40, elements=[R.TextElement(text="x")])
        self.assertAlmostEqual(R.render(lab).length_mm, 40, delta=0.2)

    def test_counter_variable(self):
        lab = R.Label(elements=[R.TextElement(text="No {n:03d}")])
        self.assertTrue(lab.uses_counter())
        self.assertEqual(R.expand("No {n:03d}", n=7), "No 007")
        self.assertFalse(R.Label(elements=[R.TextElement(text="{date}")]).uses_counter())

    def test_json_roundtrip(self):
        lab = R.Label(border="double", elements=[R.TextElement(text="a\nb"),
                                                 R.QRElement(data="x"),
                                                 R.SpacerElement(line=True)])
        again = R.Label.from_json(lab.to_json())
        self.assertEqual(again, lab)


class FreeLayoutTest(unittest.TestCase):
    def _label(self):
        return R.Label(tape_width=12, border="rounded",
                       elements=[R.QRElement(data="https://example.com"),
                                 R.TextElement(text="Two\nlines", align="left"),
                                 R.SpacerElement(line=True),
                                 R.BarcodeElement(data="12345678")])

    def test_freeze_is_lossless(self):
        for tw in (6, 12, 18, 24):
            lab = self._label()
            lab.tape_width = tw
            before = R.render(lab)
            R.freeze_layout(lab, before)
            self.assertEqual(lab.layout, "free")
            after = R.render(lab)
            self.assertEqual(before.length_dots, after.length_dots)
            self.assertEqual(list(before.bitmap.getdata()), list(after.bitmap.getdata()), tw)
            self.assertEqual(before.boxes, after.boxes)

    def test_move_and_auto_length(self):
        lab = self._label()
        R.freeze_layout(lab, R.render(lab))
        lab.elements[1].x_mm += 10
        res = R.render(lab)
        self.assertEqual(res.boxes[1][0], round(R.mm(lab.elements[1].x_mm)) + res.x_shift)
        # moving the last element further right makes an automatic label longer
        before = res.length_dots
        lab.elements[-1].x_mm += 20
        self.assertGreater(R.render(lab).length_dots, before)

    def test_auto_length_trims_to_start_padding(self):
        lab = self._label()
        lab.pad_start_mm, lab.pad_end_mm = 1.0, 5.0
        R.freeze_layout(lab, R.render(lab))
        for el in lab.elements:          # move everything 15 mm to the right
            el.x_mm += 15
        res = R.render(lab)
        start = res.frame + R.mm(1.0)
        self.assertEqual(min(b[0] for b in res.boxes), round(start))
        right = max(b[0] + b[2] for b in res.boxes)
        self.assertAlmostEqual(res.length_dots - right, res.frame + R.mm(5.0), delta=1)

    def test_start_and_end_padding_in_flow(self):
        lab = R.Label(pad_start_mm=1.0, pad_end_mm=6.0, elements=[R.TextElement(text="x")])
        res = R.render(lab)
        x, _, w, _ = res.boxes[0]
        self.assertEqual(x, round(R.mm(1.0)))
        self.assertAlmostEqual(res.length_dots - (x + w), R.mm(6.0), delta=1)

    def test_old_files_keep_padding(self):
        lab = R.Label.from_json('{"pad_mm": 4.0, "elements": []}')
        self.assertEqual((lab.pad_start_mm, lab.pad_end_mm), (4.0, 4.0))

    def test_rotation(self):
        lab = R.Label(tape_width=24, elements=[R.TextElement(text="Vertical", rotation=90)])
        res = R.render(lab)
        x, y, w, h = res.boxes[0]
        self.assertGreater(h, w)                     # tall box
        self.assertLessEqual(h, res.content_height)  # auto-size fits the length to the tape
        for rot in (90, 180, 270):
            lab = self._label()
            for el in lab.elements:
                el.rotation = rot
            before = R.render(lab)
            R.freeze_layout(lab, before)
            after = R.render(lab)
            self.assertEqual(before.boxes, after.boxes, rot)
            self.assertEqual(list(before.bitmap.getdata()), list(after.bitmap.getdata()), rot)

    def test_resize_changes_height(self):
        lab = R.Label(layout="free", elements=[R.QRElement(data="x", h_mm=4.0)])
        res = R.render(lab)
        self.assertLessEqual(res.boxes[0][3], round(R.mm(4.0)))

    def test_unplaced_elements_get_a_position(self):
        lab = R.Label(layout="free", elements=[R.TextElement(text="a"), R.TextElement(text="b")])
        b = R.render(lab).boxes
        self.assertLess(b[0][0] + b[0][2], b[1][0])


class AnyAngleTest(unittest.TestCase):
    def test_rotated_size(self):
        self.assertEqual(R.rotated_size(10, 4, 0), (10, 4))
        self.assertEqual(R.rotated_size(10, 4, 90), (4, 10))
        self.assertEqual(R.rotated_size(10, 4, 180), (10, 4))
        w, h = R.rotated_size(10, 10, 45)
        self.assertAlmostEqual(w, 14.142, places=2)
        self.assertAlmostEqual(h, 14.142, places=2)

    def test_auto_size_fits_rotated_box(self):
        for ang in (15, 30, 45, 60, 330):
            lab = R.Label(tape_width=24, elements=[R.TextElement(text="Angle", rotation=ang),
                                                   R.ImageElement(path="", rotation=ang)])
            res = R.render(lab)
            x, y, w, h = res.boxes[0]
            self.assertLessEqual(h, res.content_height + 1, ang)
            self.assertGreater(h, res.content_height * 0.9, ang)

    def test_qr_at_45_degrees_fits(self):
        lab = R.Label(tape_width=24, elements=[R.QRElement(data="hello", rotation=45)])
        res = R.render(lab)
        self.assertLessEqual(res.boxes[0][3], res.content_height + 1)

    def test_calibration_ruler(self):
        r = R.ruler_label(12, 100)
        self.assertEqual(r.length_dots, round(R.mm(100)) + 1)
        lines = R.to_raster_lines(r, 12)
        self.assertTrue(any(lines[0]) and any(lines[-1]))     # full-height end lines


class JobTest(unittest.TestCase):
    def test_structure(self):
        lines = [b"\x00" * 16, b"\xff" * 16]
        job = build_job([lines, lines], JobOptions(tape_width=12))
        self.assertTrue(job.startswith(b"\x00" * 100 + b"\x1b@\x1bia\x01"))
        self.assertEqual(job.count(b"\x1biz"), 2)
        self.assertTrue(job.endswith(b"\x1a"))
        self.assertIn(b"\x0c", job)


if __name__ == "__main__":
    unittest.main()
