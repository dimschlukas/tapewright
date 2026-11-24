# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 dimschlukas
"""Label model and renderer (Cairo/Pango at the printer's native 180 dpi)."""

from __future__ import annotations

import datetime as _dt
import json
import math
import os
import re
from dataclasses import asdict, dataclass, field, fields

import cairo
import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo  # noqa: E402
from PIL import Image, ImageOps  # noqa: E402

from .barcode import code128_modules  # noqa: E402
from .protocol import TAPE_GEOMETRY  # noqa: E402

DPI = 180
DOTS_PER_MM = DPI / 25.4
HEAD_PINS = 128


def mm(v):
    return v * DOTS_PER_MM


# ---------------------------------------------------------------- elements
# Every element also carries x_mm / y_mm / h_mm. They are only used when the
# label's layout is "free": position of the element box's top-left corner
# (x from the label's left end, y from the top of the printable area) and the
# box height. h_mm == 0 means "not placed yet".
# `rotation` (degrees clockwise, any angle) applies in both layouts.

PLACEMENT_FIELDS = ("x_mm", "y_mm", "h_mm", "rotation")
TAPE_MM = {4: 3.5, 6: 6.0, 9: 9.0, 12: 12.0, 18: 18.0, 24: 24.0}


def _sc(angle):
    """|sin|, |cos| of an angle in degrees, exact for multiples of 90."""
    a = float(angle) % 360
    if a % 90 == 0:
        return (0.0, 1.0) if a % 180 == 0 else (1.0, 0.0)
    r = math.radians(a)
    return abs(math.sin(r)), abs(math.cos(r))


def rotated_size(w, h, angle):
    """Axis-aligned bounding box (w, h) of a w x h rectangle rotated by angle."""
    s, c = _sc(angle)
    if s == 0:
        return w, h
    if c == 0:
        return h, w
    return w * c + h * s, w * s + h * c


@dataclass
class TextElement:
    x_mm: float = 0.0
    y_mm: float = 0.0
    h_mm: float = 0.0
    rotation: int = 0
    kind: str = "text"
    text: str = "Label"
    font: str = "Sans Bold"  # Pango description without size
    auto_size: bool = True
    size: float = 16.0  # points (used when auto_size is off)
    fit_glyphs: bool = False  # auto size to visible glyphs, not line height
    align: str = "center"  # left/center/right (between lines)
    line_spacing: float = 1.0
    letter_spacing: float = 0.0  # points
    underline: bool = False
    strike: bool = False
    v_offset: float = 0.0  # mm, positive moves down

    def title(self):
        t = self.text.replace("\n", " ⏎ ").strip()
        return t if t else "(empty text)"


@dataclass
class ImageElement:
    x_mm: float = 0.0
    y_mm: float = 0.0
    h_mm: float = 0.0
    rotation: int = 0
    kind: str = "image"
    path: str = ""
    height_pct: float = 100.0
    dither: bool = True
    threshold: int = 128
    invert: bool = False

    def title(self):
        return os.path.basename(self.path) or "(no image)"


@dataclass
class QRElement:
    x_mm: float = 0.0
    y_mm: float = 0.0
    h_mm: float = 0.0
    rotation: int = 0
    kind: str = "qr"
    data: str = "https://example.com"
    height_pct: float = 100.0
    error_correction: str = "M"  # L/M/Q/H

    def title(self):
        return f"QR: {self.data}"


@dataclass
class BarcodeElement:
    x_mm: float = 0.0
    y_mm: float = 0.0
    h_mm: float = 0.0
    rotation: int = 0
    kind: str = "barcode"
    data: str = "123456"
    module: int = 2  # dots per narrow bar
    show_text: bool = True
    text_size: float = 6.0  # points
    height_pct: float = 100.0

    def title(self):
        return f"Code128: {self.data}"


@dataclass
class SpacerElement:
    x_mm: float = 0.0
    y_mm: float = 0.0
    h_mm: float = 0.0
    rotation: int = 0
    kind: str = "spacer"
    width_mm: float = 3.0
    line: bool = False  # draw a vertical separator line
    line_width: int = 2  # dots

    def title(self):
        return f"Spacer {self.width_mm:g} mm" + (" | line" if self.line else "")


ELEMENT_TYPES = {
    c.kind: c for c in (TextElement, ImageElement, QRElement, BarcodeElement, SpacerElement)
}
ELEMENT_NAMES = {
    "text": "Text",
    "image": "Image",
    "qr": "QR code",
    "barcode": "Barcode",
    "spacer": "Spacer",
}


def element_from_dict(d):
    cls = ELEMENT_TYPES[d["kind"]]
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class Label:
    tape_width: int = 12
    length_mm: float = 0.0  # 0 = automatic
    pad_start_mm: float = 2.0  # blank space before the first element
    pad_end_mm: float = 2.0  # blank space after the last element
    v_pad_mm: float = 0.0  # vertical padding inside border
    gap_mm: float = 2.0  # gap between elements
    content_align: str = "center"  # for fixed-length labels (flow layout)
    layout: str = "flow"  # flow = automatic left->right, free = drag & drop
    invert: bool = False
    border: str = "none"  # none/rect/rounded/double
    border_width: int = 3  # dots
    border_inset: int = 0  # dots from tape print edge
    border_radius_mm: float = 1.5
    threshold: int = 128
    elements: list = field(default_factory=lambda: [TextElement()])

    def to_json(self):
        d = asdict(self)
        d["format"] = "tapewright/1"
        return json.dumps(d, indent=2)

    @classmethod
    def from_json(cls, s):
        d = json.loads(s)
        names = {f.name for f in fields(cls)}
        if "pad_mm" in d:  # files from before start/end padding were split
            d.setdefault("pad_start_mm", d["pad_mm"])
            d.setdefault("pad_end_mm", d["pad_mm"])
        kw = {k: v for k, v in d.items() if k in names and k != "elements"}
        lab = cls(**kw)
        lab.elements = [element_from_dict(e) for e in d.get("elements", [])]
        return lab

    def uses_counter(self):
        """True if any element contains the {n} serial number variable."""
        pat = re.compile(r"\{n(:[^}]*)?\}")
        return any(
            pat.search(getattr(e, "text", "") or getattr(e, "data", "") or "")
            for e in self.elements
        )


# ---------------------------------------------------------------- variables

_VAR_RE = re.compile(r"\{(date|time|datetime|n)(?::([^}]*))?\}")


def expand(text: str, n: int = 1, now=None) -> str:
    now = now or _dt.datetime.now()

    def sub(m):
        name, spec = m.group(1), m.group(2)
        if name == "n":
            try:
                return format(n, spec or "")
            except ValueError:
                return str(n)
        default = {"date": "%Y-%m-%d", "time": "%H:%M", "datetime": "%Y-%m-%d %H:%M"}[name]
        return now.strftime(spec or default)

    return _VAR_RE.sub(sub, text)


# ---------------------------------------------------------------- rendering


class RenderError(Exception):
    pass


class _Ctx:
    def __init__(self, label, n):
        self.label, self.n = label, n
        self.free = label.layout == "free"
        self.warnings = []


def _pct(el, ctx):
    """In the free layout the element height is set directly by h_mm."""
    return 100.0 if ctx.free else getattr(el, "height_pct", 100.0)


def _make_layout(cr, el: TextElement, size_pt: float, text: str):
    layout = PangoCairo.create_layout(cr)
    pctx = layout.get_context()
    PangoCairo.context_set_resolution(pctx, DPI)
    fo = cairo.FontOptions()
    fo.set_antialias(cairo.ANTIALIAS_GRAY)
    fo.set_hint_style(cairo.HINT_STYLE_SLIGHT)
    fo.set_hint_metrics(cairo.HINT_METRICS_OFF)
    PangoCairo.context_set_font_options(pctx, fo)
    layout.context_changed()
    desc = Pango.FontDescription.from_string(el.font or "Sans")
    desc.set_size(max(1, int(size_pt * Pango.SCALE)))
    layout.set_font_description(desc)
    layout.set_alignment(
        {"left": Pango.Alignment.LEFT, "right": Pango.Alignment.RIGHT}.get(
            el.align, Pango.Alignment.CENTER
        )
    )
    if el.line_spacing and el.line_spacing != 1.0:
        layout.set_line_spacing(float(el.line_spacing))
    attrs = Pango.AttrList()
    if el.letter_spacing:
        attrs.insert(Pango.attr_letter_spacing_new(int(el.letter_spacing * Pango.SCALE * DPI / 72)))
    if el.underline:
        attrs.insert(Pango.attr_underline_new(Pango.Underline.SINGLE))
    if el.strike:
        attrs.insert(Pango.attr_strikethrough_new(True))
    layout.set_attributes(attrs)
    layout.set_text(text, -1)
    return layout


def _rect(r):
    return r.x / Pango.SCALE, r.y / Pango.SCALE, r.width / Pango.SCALE, r.height / Pango.SCALE


def _text_ref(layout, el):
    """Height of the text's rotated bounding box; auto-size fits this to the
    available height (plain height at 0 degrees, the text's length at 90)."""
    ink, logical = layout.get_pixel_extents()
    w = max(ink.x + ink.width, logical.x + logical.width) - min(ink.x, logical.x)
    h = ink.height if el.fit_glyphs else logical.height
    return rotated_size(w, h, el.rotation)[1]


def _measure_text(cr, el: TextElement, avail_h, ctx):
    text = expand(el.text, ctx.n)
    if not text.strip():
        return 0, 0, None
    rotated = float(el.rotation) % 360 != 0
    if el.auto_size:
        probe = _make_layout(cr, el, 100.0, text)
        ref = _text_ref(probe, el)
        if ref <= 0:
            return 0, 0, None
        size = 100.0 * avail_h / ref
        layout = _make_layout(cr, el, size, text)
        # correct for non-linear hinting
        ref = _text_ref(layout, el)
        if ref > avail_h:
            size *= avail_h / ref
            layout = _make_layout(cr, el, size, text)
    else:
        layout = _make_layout(cr, el, el.size, text)
    ink, logical = layout.get_extents()
    ix, iy, iw, ih = _rect(ink)
    lx, ly, lw, lh = _rect(logical)
    if not el.auto_size and not ctx.free and not rotated and lh > avail_h + 0.5:
        ctx.warnings.append(f"Text “{text[:20]}” is taller than the tape")
    x0 = min(ix, lx)
    width = max(ix + iw, lx + lw) - x0

    def draw(cr, x, top, h):
        if el.fit_glyphs:
            y = top + (h - ih) / 2 - iy
        else:
            y = top + (h - lh) / 2 - ly
        y += mm(el.v_offset)
        cr.move_to(x - x0, y)
        PangoCairo.show_layout(cr, layout)

    return math.ceil(width), (ih if el.fit_glyphs else lh), draw


_img_cache = {}


def _load_image(path, target_h):
    """Return a grayscale PIL image scaled to target_h (white background)."""
    if not path or not os.path.exists(path):
        raise RenderError(f"Image not found: {path}")
    key = (path, os.path.getmtime(path), target_h)
    if key in _img_cache:
        return _img_cache[key]
    if path.lower().endswith((".svg", ".svgz")):
        gi.require_version("Rsvg", "2.0")
        from gi.repository import Rsvg

        handle = Rsvg.Handle.new_from_file(path)
        ok, w, h = handle.get_intrinsic_size_in_pixels()
        if not ok or not w or not h:
            w, h = 100, 100
        tw = max(1, round(w * target_h / h))
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, tw, target_h)
        c = cairo.Context(surf)
        c.set_source_rgb(1, 1, 1)
        c.paint()
        vp = Rsvg.Rectangle()
        vp.x, vp.y, vp.width, vp.height = 0, 0, tw, target_h
        handle.render_document(c, vp)
        surf.flush()
        img = Image.frombuffer(
            "RGBA", (tw, target_h), bytes(surf.get_data()), "raw", "BGRA", surf.get_stride(), 1
        ).convert("L")
    else:
        src = Image.open(path)
        src = ImageOps.exif_transpose(src)
        if src.mode in ("RGBA", "LA", "P"):
            src = src.convert("RGBA")
            bg = Image.new("RGBA", src.size, (255, 255, 255, 255))
            src = Image.alpha_composite(bg, src)
        src = src.convert("L")
        tw = max(1, round(src.width * target_h / src.height))
        img = src.resize((tw, target_h), Image.LANCZOS)
    if len(_img_cache) > 32:
        _img_cache.clear()
    _img_cache[key] = img
    return img


def _mask_from_bitmap(bits: Image.Image):
    """PIL '1'/'L' image (0 = ink) -> cairo A8 surface (ink = opaque)."""
    w, h = bits.size
    alpha = ImageOps.invert(bits.convert("L"))
    stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_A8, w)
    data = bytearray(stride * h)
    raw = alpha.tobytes()
    for y in range(h):
        data[y * stride : y * stride + w] = raw[y * w : (y + 1) * w]
    return cairo.ImageSurface.create_for_data(data, cairo.FORMAT_A8, w, h, stride), data


def _measure_image(cr, el: ImageElement, avail_h, ctx):
    th = max(1, round(avail_h * _pct(el, ctx) / 100))
    try:
        img = _load_image(el.path, th)
    except Exception as e:  # noqa: BLE001
        ctx.warnings.append(str(e))
        return 0, 0, None
    if el.invert:
        img = ImageOps.invert(img)
    if el.dither:
        bits = img.convert("1")  # Floyd-Steinberg
    else:
        bits = img.point(lambda v: 255 if v >= el.threshold else 0, "1")
    surf, keep = _mask_from_bitmap(bits)

    def draw(cr, x, top, h):
        _ = keep
        cr.mask_surface(surf, x, top + (h - th) / 2)

    return bits.width, th, draw


def _measure_qr(cr, el: QRElement, avail_h, ctx):
    data = expand(el.data, ctx.n)
    if not data:
        return 0, 0, None
    try:
        import qrcode
        from qrcode.constants import (
            ERROR_CORRECT_H,
            ERROR_CORRECT_L,
            ERROR_CORRECT_M,
            ERROR_CORRECT_Q,
        )
    except ImportError:
        ctx.warnings.append("QR codes need the python 'qrcode' package")
        return 0, 0, None
    ec = {
        "L": ERROR_CORRECT_L,
        "M": ERROR_CORRECT_M,
        "Q": ERROR_CORRECT_Q,
        "H": ERROR_CORRECT_H,
    }.get(el.error_correction, ERROR_CORRECT_M)
    qr = qrcode.QRCode(error_correction=ec, border=0)
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    target = avail_h * _pct(el, ctx) / 100
    module = int(target // n)
    if module < 1:
        ctx.warnings.append("QR code has too much data for this tape width")
        module = 1
    elif module < 2:
        ctx.warnings.append("QR modules are only 1 dot wide; it may not scan reliably")
    size = n * module

    def draw(cr, x, top, h):
        y0 = top + (h - size) / 2
        x0 = x
        for r, row in enumerate(matrix):
            for c, v in enumerate(row):
                if v:
                    cr.rectangle(x0 + c * module, y0 + r * module, module, module)
        cr.fill()

    return size, size, draw


def _measure_barcode(cr, el: BarcodeElement, avail_h, ctx):
    data = expand(el.data, ctx.n)
    if not data:
        return 0, 0, None
    try:
        modules = code128_modules(data)
    except ValueError as e:
        ctx.warnings.append(str(e))
        return 0, 0, None
    m = max(1, int(el.module))
    quiet = 10 * m
    width = sum(modules) * m + 2 * quiet
    total_h = avail_h * _pct(el, ctx) / 100
    tlayout = None
    text_h = 0
    if el.show_text:
        tel = TextElement(text=data, font="Sans", auto_size=False, size=el.text_size)
        tlayout = _make_layout(cr, tel, el.text_size, data)
        _, lg = tlayout.get_pixel_extents()
        text_h = lg.height
        tw = lg.width
    bar_h = max(4, total_h - text_h)

    def draw(cr, x, top, h):
        y0 = top + (h - total_h) / 2
        cx = x + quiet
        for i, w in enumerate(modules):
            if i % 2 == 0:
                cr.rectangle(cx, y0, w * m, round(bar_h))
            cx += w * m
        cr.fill()
        if tlayout:
            cr.move_to(x + (width - tw) / 2, y0 + bar_h)
            PangoCairo.show_layout(cr, tlayout)

    return width, total_h, draw


def _measure_spacer(cr, el: SpacerElement, avail_h, ctx):
    w = max(1, round(mm(el.width_mm)))

    def draw(cr, x, top, h):
        if el.line:
            lw = max(1, el.line_width)
            cr.rectangle(x + (w - lw) // 2, top, lw, h)
            cr.fill()

    return w, avail_h, draw


_MEASURE = {
    "text": _measure_text,
    "image": _measure_image,
    "qr": _measure_qr,
    "barcode": _measure_barcode,
    "spacer": _measure_spacer,
}


@dataclass
class RenderResult:
    bitmap: Image.Image  # mode 'L', 0 = ink, size (length, printable height)
    length_dots: int
    print_height: int
    warnings: list
    # per element (same order as label.elements): (x, y, w, h) in dots, label
    # coordinates with y relative to the top of the printable area; None if
    # the element produced nothing
    boxes: list = field(default_factory=list)
    content_top: int = 0  # top of the content area inside border/padding
    content_height: int = 0
    frame: int = 0  # border thickness incl. inset
    side_start: float = 0  # x where content starts (border + start padding)
    side_end: float = 0  # distance from the label end to where content ends
    # free layout with automatic length: the label is trimmed so the leftmost
    # element starts at the start padding. x_shift (dots) was added to every
    # element's x_mm; to store a label x back: x_mm = (x - x_shift) / DOTS_PER_MM
    x_shift: float = 0
    sizes: list = field(default_factory=list)  # unrotated (w, h) per element
    # per element: the height its size was fitted to (for scalable elements)
    targets: list = field(default_factory=list)

    @property
    def length_mm(self):
        return self.length_dots / DOTS_PER_MM


def scalable(el):
    """Elements whose size follows h_mm as the height of their (rotated) box.
    For the others (barcode, spacer) h_mm is their unrotated height: bar or
    line length. Fixed-size text follows its font size."""
    return el.kind in ("image", "qr") or (el.kind == "text" and el.auto_size)


def _measure(scr, el, el_avail, ctx):
    """Measure an element. Returns (box_w, box_h, w, h, draw, rotation) where box_*
    is the rotated bounding box and w/h the unrotated size passed to draw."""
    ang = float(el.rotation) % 360
    w, h, draw = _MEASURE[el.kind](scr, el, el_avail, ctx)
    if draw is None or w <= 0:
        return None
    if not ang:
        return w, h, w, h, draw, 0
    target = el_avail * _pct(el, ctx) / 100
    bh = rotated_size(w, h, ang)[1]
    if (el.kind == "image" or (el.kind == "qr" and ang % 90)) and abs(bh - target) > 0.5:
        # scale so that the rotated bounding box fits the target height
        again = _MEASURE[el.kind](scr, el, el_avail * target / bh, ctx)
        if again[2] is not None:
            w, h, draw = again
    elif not scalable(el) and bh > el_avail + 0.5 and not ctx.free:
        ctx.warnings.append(
            f"Rotated {ELEMENT_NAMES[el.kind].lower()} is taller than the tape is wide"
        )
    bw, bh = rotated_size(w, h, ang)
    return bw, bh, w, h, draw, ang


def render(label: Label, n: int = 1, x_shift: float | None = None) -> RenderResult:
    """Render a label. In the free layout with automatic length the label is
    trimmed to its content; pass x_shift to force a particular trim offset
    (used while dragging so the canvas doesn't move under the pointer)."""
    pins, _margin = TAPE_GEOMETRY[label.tape_width]
    H = pins
    ctx = _Ctx(label, n)

    bw = label.border_width if label.border != "none" else 0
    frame = label.border_inset + bw
    if label.border == "double":
        frame += bw * 2
    top = frame + round(mm(label.v_pad_mm))
    avail = H - 2 * top
    if avail < 4:
        ctx.warnings.append("Border/padding leave no room for content")
        avail = max(4, avail)

    # measuring pass on a scratch surface
    scratch = cairo.ImageSurface(cairo.FORMAT_RGB24, 8, 8)
    scr = cairo.Context(scratch)
    gap = mm(label.gap_mm)
    side_start = frame + mm(label.pad_start_mm)
    side_end = frame + mm(label.pad_end_mm)
    fixed = bool(label.length_mm and label.length_mm > 0)
    boxes = [None] * len(label.elements)
    sizes = [None] * len(label.elements)
    targets = [None] * len(label.elements)
    items = []  # (index, box_w, box_h, w, h, draw, rotation)
    for i, el in enumerate(label.elements):
        el_avail = avail
        if ctx.free and el.h_mm > 0:
            el_avail = max(2, round(mm(el.h_mm)))
        m = _measure(scr, el, el_avail, ctx)
        if m:
            items.append((i, *m))
            sizes[i] = (m[2], m[3])
            targets[i] = el_avail * _pct(el, ctx) / 100

    shift = 0
    if ctx.free:
        # placed elements keep their position; unplaced ones go to the right end
        right = side_start
        for i, w, h, *_ in items:
            el = label.elements[i]
            if el.h_mm > 0:
                boxes[i] = (round(mm(el.x_mm)), round(mm(el.y_mm)), w, h)
                right = max(right, boxes[i][0] + w + gap)
        for i, w, h, *_ in items:
            if boxes[i] is None:
                boxes[i] = (round(right), round(top + (avail - h) / 2), w, h)
                right += w + gap
        if not fixed:
            # trim: the leftmost element starts exactly at the start padding
            if x_shift is not None:
                shift = round(x_shift)
            elif any(boxes):
                shift = round(side_start) - min(b[0] for b in boxes if b)
            boxes = [(b[0] + shift, b[1], b[2], b[3]) if b else None for b in boxes]
        content_right = max((b[0] + b[2] for b in boxes if b), default=side_start)
        if fixed:
            L = round(mm(label.length_mm))
            if content_right > L - frame + 0.5:
                ctx.warnings.append("Some content lies beyond the end of the label")
        else:
            L = max(1, math.ceil(content_right + side_end))
        if any(
            b and (b[0] < frame - 0.5 or b[1] < frame - 0.5 or b[1] + b[3] > H - frame + 0.5)
            for b in boxes
        ):
            ctx.warnings.append("Some content lies outside the printable area and is cut off")
    else:
        content_w = sum(it[1] for it in items) + gap * max(0, len(items) - 1)
        if fixed:
            L = round(mm(label.length_mm))
            spare = L - side_start - side_end - content_w
            if spare < 0:
                need = (content_w + side_start + side_end) / DOTS_PER_MM
                ctx.warnings.append(
                    f"Content needs {need:.1f} mm but label is {label.length_mm:g} mm"
                )
            x = side_start + {"left": 0, "right": spare}.get(label.content_align, spare / 2)
        else:
            L = None
            x = side_start
        for i, w, h, *_ in items:
            boxes[i] = (round(x), round(top + (avail - h) / 2), w, h)
            x += w + gap
        if L is None:  # same formula as the free layout, so converting is exact
            content_right = max((b[0] + b[2] for b in boxes if b), default=side_start)
            L = max(1, math.ceil(content_right + side_end))

    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, L, H)
    cr = cairo.Context(surf)
    cr.set_source_rgb(1, 1, 1)
    cr.paint()
    cr.set_source_rgb(0, 0, 0)
    cr.set_antialias(cairo.ANTIALIAS_GRAY)

    # border
    if label.border != "none" and bw > 0:

        def frame_path(inset, radius):
            x, y = inset + bw / 2, inset + bw / 2
            w, h = L - 2 * x, H - 2 * y
            if w <= 0 or h <= 0:
                return
            r = min(radius, w / 2, h / 2)
            if r <= 0:
                cr.rectangle(x, y, w, h)
            else:
                cr.new_sub_path()
                cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
                cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
                cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
                cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
                cr.close_path()

        cr.set_line_width(bw)
        radius = mm(label.border_radius_mm) if label.border in ("rounded", "double") else 0
        frame_path(label.border_inset, radius)
        cr.stroke()
        if label.border == "double":
            frame_path(label.border_inset + 2 * bw, max(0, radius - 2 * bw))
            cr.stroke()

    # clip content to inside of frame
    cr.save()
    cr.rectangle(frame, frame, max(0, L - 2 * frame), max(0, H - 2 * frame))
    cr.clip()
    for i, bw_, bh_, w, h, draw, rot in items:
        bx, by, _, _ = boxes[i]
        cr.save()
        if rot:
            cr.translate(bx + bw_ / 2, by + bh_ / 2)
            cr.rotate(math.radians(rot))
            draw(cr, -w / 2, -h / 2, h)
        else:
            draw(cr, bx, by, h)
        cr.restore()
    cr.restore()
    surf.flush()

    img = Image.frombuffer(
        "RGBX", (L, H), bytes(surf.get_data()), "raw", "BGRX", surf.get_stride(), 1
    ).convert("L")
    thr = label.threshold
    img = img.point(lambda v: 255 if v >= thr else 0)
    if label.invert:
        img = ImageOps.invert(img)
    return RenderResult(
        img,
        L,
        H,
        ctx.warnings,
        boxes,
        top,
        avail,
        frame,
        side_start,
        side_end,
        shift,
        sizes,
        targets,
    )


def freeze_layout(label: Label, result: RenderResult):
    """Switch a label to the free layout, keeping every element where the
    current (flow) layout put it."""
    for el, box, size, target in zip(
        label.elements, result.boxes, result.sizes, result.targets, strict=True
    ):
        if box is None:
            continue
        x, y, w, h = box
        el.x_mm = round((x - result.x_shift) / DOTS_PER_MM, 4)
        el.y_mm = round(y / DOTS_PER_MM, 4)
        # scalable content was fitted to `target`; storing that (not the measured
        # box, which can be a dot smaller after rounding) reproduces it exactly
        el.h_mm = round(max(target if scalable(el) else size[1], 2) / DOTS_PER_MM, 4)
    label.layout = "free"


def to_raster_lines(result: RenderResult, tape_width: int) -> list[bytes]:
    """Convert a rendered label to 16-byte raster lines for the 128-pin head."""
    pins, margin = TAPE_GEOMETRY[tape_width]
    ink = result.bitmap.point(lambda v: 255 if v < 128 else 0, "1")  # 1 = print
    full = Image.new("1", (result.length_dots, HEAD_PINS), 0)
    full.paste(ink, (0, margin))
    # each label column becomes one raster line; top of label = first bit (MSB)
    cols = full.transpose(Image.Transpose.TRANSPOSE)
    raw = cols.tobytes()
    bpl = HEAD_PINS // 8
    return [raw[i * bpl : (i + 1) * bpl] for i in range(result.length_dots)]


def ruler_label(tape_width: int, length_mm: float = 100.0) -> RenderResult:
    """A calibration label: a millimetre ruler across the whole printable
    height, with marks every 1/5/10 mm and a full-height line at 0 and the end."""
    pins, _ = TAPE_GEOMETRY[tape_width]
    L = round(mm(length_mm)) + 1
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, L, pins)
    cr = cairo.Context(surf)
    cr.set_source_rgb(1, 1, 1)
    cr.paint()
    cr.set_source_rgb(0, 0, 0)
    cr.set_antialias(cairo.ANTIALIAS_NONE)
    for m in range(int(length_mm) + 1):
        x = round(mm(m))
        if m in (0, int(length_mm)):
            h = pins
        elif m % 10 == 0:
            h = pins * 0.55
        elif m % 5 == 0:
            h = pins * 0.35
        else:
            h = pins * 0.2
        cr.rectangle(min(x, L - 1), pins - h, 1, h)
        cr.fill()
        if m % 10 == 0 and 0 < m < length_mm:
            tel = TextElement(text=str(m), font="Sans Bold")
            lay = _make_layout(cr, tel, max(3.0, pins * 0.3 * 72 / DPI), str(m))
            ink, lg = lay.get_pixel_extents()
            cr.move_to(x - lg.width / 2, 0)
            PangoCairo.show_layout(cr, lay)
    surf.flush()
    img = (
        Image.frombuffer(
            "RGBX", (L, pins), bytes(surf.get_data()), "raw", "BGRX", surf.get_stride(), 1
        )
        .convert("L")
        .point(lambda v: 255 if v >= 128 else 0)
    )
    return RenderResult(img, L, pins, [], [], 0, pins, 0)
