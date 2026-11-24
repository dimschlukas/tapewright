# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 dimschlukas
"""GTK4 / libadwaita label editor for Brother P-touch printers."""
from __future__ import annotations

import copy
import os
import sys
import threading

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from . import render as R  # noqa: E402
from .protocol import (COLOR_RGB, TAPE_GEOMETRY, TAPE_LABEL, TAPE_WIDTHS,  # noqa: E402
                       JobOptions, Printer, PrinterError)
from .usb import USBError, open_printer  # noqa: E402

APP_ID = "io.github.dimschlukas.Tapewright"
FILE_EXT = ".twlabel"
APP_NAME = "Tapewright"
from . import __version__ as VERSION  # noqa: E402
WEBSITE = "https://github.com/dimschlukas/tapewright"

ICONS = {"text": "insert-text-symbolic", "image": "image-x-generic-symbolic",
         "qr": "view-grid-symbolic", "barcode": "view-continuous-symbolic",
         "spacer": "view-dual-symbolic"}

ALIGN = ["left", "center", "right"]
ALIGN_NAMES = ["Left", "Center", "Right"]
BORDERS = ["none", "rect", "rounded", "double"]
BORDER_NAMES = ["None", "Rectangle", "Rounded", "Double"]
EC = ["L", "M", "Q", "H"]
EC_NAMES = ["Low (7%)", "Medium (15%)", "Quartile (25%)", "High (30%)"]

VAR_HELP = ("Variables: {date}  {time}  {datetime}  {date:%d.%m.%Y}  "
            "{n}  {n:03d} (serial number, increases per copy)")


# ------------------------------------------------------------------ helpers

def esc(t):
    return GLib.markup_escape_text(t) if t else t


def spin_row(title, value, lo, hi, step, digits, on_change, subtitle=None):
    title, subtitle = esc(title), esc(subtitle)
    row = Adw.SpinRow.new_with_range(lo, hi, step)
    row.set_title(title)
    if subtitle:
        row.set_subtitle(subtitle)
    row.set_digits(digits)
    row.set_value(value)
    row.connect("notify::value", lambda r, _p: on_change(r.get_value()))
    return row


def switch_row(title, active, on_change, subtitle=None):
    title, subtitle = esc(title), esc(subtitle)
    row = Adw.SwitchRow(title=title)
    if subtitle:
        row.set_subtitle(subtitle)
    row.set_active(active)
    row.connect("notify::active", lambda r, _p: on_change(r.get_active()))
    return row


def combo_row(title, names, index, on_change, subtitle=None):
    title, subtitle = esc(title), esc(subtitle)
    row = Adw.ComboRow(title=title)
    if subtitle:
        row.set_subtitle(subtitle)
    row.set_model(Gtk.StringList.new(names))
    row.set_selected(max(0, index))
    row.connect("notify::selected", lambda r, _p: on_change(r.get_selected()))
    return row


def entry_row(title, text, on_change):
    row = Adw.EntryRow(title=esc(title))
    row.set_text(text)
    row.connect("changed", lambda r: on_change(r.get_text()))
    return row


def group(title=None, desc=None):
    g = Adw.PreferencesGroup()
    if title:
        g.set_title(esc(title))
    if desc:
        g.set_description(esc(desc))
    return g


# ------------------------------------------------------------------ preview

class Preview(Gtk.DrawingArea):
    PAD = 24

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.result = None
        self.zoom = 3.0
        self.set_draw_func(self.draw)
        self.set_hexpand(True)

    def set_result(self, result):
        self.result = result
        self.update_size()
        self.queue_draw()

    def update_size(self):
        tw = self.win.label.tape_width
        tape_h = R.mm(3.5 if tw == 4 else tw)
        L = self.result.length_dots if self.result else 100
        self.set_content_width(int(L * self.zoom + 2 * self.PAD))
        self.set_content_height(int(tape_h * self.zoom + 2 * self.PAD + 22))

    def draw(self, area, cr, width, height):
        res = self.result
        if res is None:
            return
        z = self.zoom
        tw = self.win.label.tape_width
        tape_h = R.mm(3.5 if tw == 4 else tw)
        L = res.length_dots
        x0 = max(self.PAD, (width - L * z) / 2)
        y0 = self.PAD
        tape_rgb, ink_rgb = self.win.tape_colors()

        # tape body + shadow
        cr.set_source_rgba(0, 0, 0, 0.18)
        cr.rectangle(x0 + 3, y0 + 3, L * z, tape_h * z)
        cr.fill()
        cr.set_source_rgb(*tape_rgb)
        cr.rectangle(x0, y0, L * z, tape_h * z)
        cr.fill()
        # printable area outline
        py = y0 + (tape_h - res.print_height) / 2 * z
        cr.set_source_rgba(0.2, 0.5, 1.0, 0.35)
        cr.set_line_width(1)
        cr.set_dash([4, 4])
        cr.rectangle(x0 + 0.5, py + 0.5, L * z - 1, res.print_height * z - 1)
        cr.stroke()
        cr.set_dash([])
        # ink
        mask, _keep = R._mask_from_bitmap(res.bitmap)
        cr.save()
        cr.translate(x0, py)
        cr.scale(z, z)
        pat = cairo.SurfacePattern(mask)
        pat.set_filter(cairo.FILTER_NEAREST)
        cr.set_source_rgb(*ink_rgb)
        cr.mask(pat)
        cr.restore()
        # ruler (mm)
        fg = self.get_color()
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.7)
        ry = y0 + tape_h * z + 6
        total_mm = res.length_mm
        cr.set_line_width(1)
        cr.select_font_face("Sans")
        cr.set_font_size(10)
        m = 0
        while m <= total_mm + 0.01:
            x = round(x0 + R.mm(m) * z) + 0.5
            tick = 8 if m % 10 == 0 else (5 if m % 5 == 0 else 2.5)
            if z * R.DOTS_PER_MM >= 3 or m % 5 == 0:
                cr.move_to(x, ry)
                cr.line_to(x, ry + tick)
            if m % 10 == 0 and m > 0:
                cr.move_to(x + 2, ry + 16)
                cr.show_text(f"{m}")
            m += 1
        cr.stroke()


# ------------------------------------------------------------------ window

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, path=None):
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(1200, 820)
        self.label = R.Label()
        self.path = None
        self.dirty = False
        self.sel = 0
        self.copies = 1
        self.counter_start = 1
        self.auto_tape = True
        self.job = JobOptions()
        self.status = None
        self.printer_name = None
        self.dev_lock = threading.Lock()
        self.printing = False
        self._render_pending = False
        self._building = False

        self.toast = Adw.ToastOverlay()
        tv = Adw.ToolbarView()
        self.toast.set_child(tv)
        self.set_content(self.toast)

        # header
        hb = Adw.HeaderBar()
        self.title_widget = Adw.WindowTitle(title=APP_NAME, subtitle="Untitled")
        hb.set_title_widget(self.title_widget)
        menu = Gio.Menu()
        sec = Gio.Menu()
        sec.append("New label", "win.new")
        sec.append("Open…", "win.open")
        sec.append("Save", "win.save")
        sec.append("Save as…", "win.save-as")
        menu.append_section(None, sec)
        sec2 = Gio.Menu()
        sec2.append("Export PNG…", "win.export")
        menu.append_section(None, sec2)
        sec3 = Gio.Menu()
        sec3.append("About", "win.about")
        menu.append_section(None, sec3)
        mb = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Menu")
        hb.pack_end(mb)
        self.print_btn = Gtk.Button(label="Print")
        self.print_btn.add_css_class("suggested-action")
        self.print_btn.set_tooltip_text("Print (Ctrl+P)")
        self.print_btn.connect("clicked", lambda *_: self.do_print())
        hb.pack_end(self.print_btn)
        self.copies_spin = Gtk.SpinButton.new_with_range(1, 999, 1)
        self.copies_spin.set_tooltip_text("Copies")
        self.copies_spin.connect("value-changed", self._on_copies_header)
        cbox = Gtk.Box(spacing=4)
        cbox.append(Gtk.Label(label="Copies"))
        cbox.append(self.copies_spin)
        hb.pack_end(cbox)
        open_btn = Gtk.Button(icon_name="document-open-symbolic", tooltip_text="Open (Ctrl+O)")
        open_btn.set_action_name("win.open")
        hb.pack_start(open_btn)
        save_btn = Gtk.Button(icon_name="document-save-symbolic", tooltip_text="Save (Ctrl+S)")
        save_btn.set_action_name("win.save")
        hb.pack_start(save_btn)
        tv.add_top_bar(hb)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        tv.set_content(root)

        # printer status bar
        sbar = Gtk.Box(spacing=8, margin_start=12, margin_end=12, margin_top=8, margin_bottom=4)
        self.status_icon = Gtk.Image.new_from_icon_name("printer-symbolic")
        self.status_label = Gtk.Label(label="Looking for printer…", xalign=0, hexpand=True)
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh printer status")
        refresh.add_css_class("flat")
        refresh.connect("clicked", lambda *_: self.poll_status(force=True))
        sbar.append(self.status_icon)
        sbar.append(self.status_label)
        sbar.append(Gtk.Label(label="Zoom"))
        self.auto_zoom = True
        self.zoom_scale = zoom = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.5, 10, 0.25)
        zoom.set_value(3)
        zoom.set_size_request(140, -1)
        zoom.connect("value-changed", self._on_zoom)
        sbar.append(zoom)
        fit = Gtk.Button(icon_name="zoom-fit-best-symbolic", tooltip_text="Fit label to window")
        fit.add_css_class("flat")
        fit.connect("clicked", lambda *_: self.fit_zoom(force=True))
        sbar.append(fit)
        sbar.append(refresh)
        root.append(sbar)

        # preview
        self.preview = Preview(self)
        self.psw = psw = Gtk.ScrolledWindow()
        psw.set_child(self.preview)
        psw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        psw.set_min_content_height(170)
        psw.set_propagate_natural_height(True)
        psw.set_max_content_height(380)
        frame = Gtk.Frame(margin_start=12, margin_end=12)
        frame.add_css_class("view")
        frame.set_child(psw)
        root.append(frame)
        self.info_label = Gtk.Label(xalign=0, margin_start=14, margin_top=4, margin_bottom=6)
        self.info_label.add_css_class("dim-label")
        self.info_label.set_wrap(True)
        root.append(self.info_label)

        # bottom: element list | editor stack
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True)
        paned.set_position(300)
        paned.set_shrink_start_child(False)
        root.append(paned)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        left.set_size_request(240, -1)
        tb = Gtk.Box(spacing=4, margin_start=12, margin_end=6, margin_top=6, margin_bottom=6)
        add_menu = Gio.Menu()
        for kind, name in R.ELEMENT_NAMES.items():
            add_menu.append(name, f"win.add-element::{kind}")
        add_btn = Gtk.MenuButton(label="Add", icon_name="list-add-symbolic", menu_model=add_menu,
                                 tooltip_text="Add element")
        add_btn.set_always_show_arrow(True)
        tb.append(add_btn)
        tb.append(Gtk.Box(hexpand=True))
        for icon, tip, cb in (("go-up-symbolic", "Move left", lambda *_: self.move(-1)),
                              ("go-down-symbolic", "Move right", lambda *_: self.move(1)),
                              ("edit-copy-symbolic", "Duplicate", lambda *_: self.duplicate()),
                              ("user-trash-symbolic", "Delete", lambda *_: self.delete())):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.add_css_class("flat")
            b.connect("clicked", cb)
            tb.append(b)
        left.append(tb)
        self.listbox = Gtk.ListBox()
        self.listbox.add_css_class("navigation-sidebar")
        self.listbox.connect("row-selected", self._on_row_selected)
        lsw = Gtk.ScrolledWindow(vexpand=True)
        lsw.set_child(self.listbox)
        left.append(lsw)
        hint = Gtk.Label(label="Elements are placed left → right along the tape.",
                         wrap=True, xalign=0, margin_start=12, margin_end=12, margin_bottom=8)
        hint.add_css_class("dim-label")
        hint.add_css_class("caption")
        left.append(hint)
        paned.set_start_child(left)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.stack = Adw.ViewStack()
        switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        switcher.set_margin_top(6)
        switcher.set_halign(Gtk.Align.CENTER)
        right.append(switcher)
        self.element_page = Gtk.ScrolledWindow(vexpand=True)
        self.label_page = Gtk.ScrolledWindow(vexpand=True)
        self.print_page = Gtk.ScrolledWindow(vexpand=True)
        self.stack.add_titled_with_icon(self.element_page, "element", "Element", "document-edit-symbolic")
        self.stack.add_titled_with_icon(self.label_page, "label", "Label & Border", "view-paged-symbolic")
        self.stack.add_titled_with_icon(self.print_page, "print", "Print", "printer-symbolic")
        right.append(self.stack)
        paned.set_end_child(right)

        self._setup_actions()
        if path:
            self.load(path)
        else:
            self.rebuild_all()
        self.poll_status(force=True)
        GLib.timeout_add_seconds(4, self._poll_tick)
        self.connect("close-request", self._on_close_request)
        self.connect("notify::default-width", lambda *_: GLib.idle_add(self._refit))
        self.connect("notify::maximized", lambda *_: GLib.timeout_add(100, self._refit))

    def _refit(self):
        if self.auto_zoom and self.preview.result:
            self.fit_zoom()
            self.preview.update_size()
            self.preview.queue_draw()
        return False

    # ------------------------------------------------------------ actions
    def _setup_actions(self):
        app = self.get_application()

        def act(name, cb, accel=None, param=None):
            a = Gio.SimpleAction.new(name, GLib.VariantType.new(param) if param else None)
            a.connect("activate", cb)
            self.add_action(a)
            if accel:
                app.set_accels_for_action(f"win.{name}", accel)

        act("new", lambda *_: self.confirm_discard(self.new_label), ["<Ctrl>n"])
        act("open", lambda *_: self.confirm_discard(self.open_dialog), ["<Ctrl>o"])
        act("save", lambda *_: self.save(), ["<Ctrl>s"])
        act("save-as", lambda *_: self.save(as_new=True), ["<Ctrl><Shift>s"])
        act("export", lambda *_: self.export_png(), ["<Ctrl>e"])
        act("print", lambda *_: self.do_print(), ["<Ctrl>p"])
        act("about", lambda *_: self.show_about())
        act("add-element", lambda a, v: self.add_element(v.get_string()), None, "s")

    def toast_msg(self, text, timeout=3):
        t = Adw.Toast.new(text)
        t.set_timeout(timeout)
        self.toast.add_toast(t)

    # ------------------------------------------------------------ model changes
    def changed(self, structure=False):
        if self._building:
            return
        self.dirty = True
        self.update_title()
        if structure:
            self.rebuild_list()
        else:
            self.update_row_titles()
        self.schedule_render()

    def schedule_render(self):
        if not self._render_pending:
            self._render_pending = True
            GLib.timeout_add(60, self._do_render)

    def _do_render(self):
        self._render_pending = False
        try:
            res = R.render(self.label, n=self.counter_start)
        except Exception as e:  # noqa: BLE001
            self.info_label.set_text(f"Render error: {e}")
            return False
        self.result = res
        self.preview.result = res
        self.fit_zoom()
        self.preview.set_result(res)
        tw = self.label.tape_width
        pins = TAPE_GEOMETRY[tw][0]
        info = (f"Label length {res.length_mm:.1f} mm  ·  {TAPE_LABEL[tw]} tape  ·  "
                f"printable height {pins} dots ({pins / R.DOTS_PER_MM:.1f} mm)")
        if self.label.uses_counter():
            info += f"  ·  preview shows n = {self.counter_start}"
        if res.warnings:
            info += "\n⚠ " + "  ⚠ ".join(dict.fromkeys(res.warnings))
        self.info_label.set_text(info)
        return False

    def update_title(self):
        name = os.path.basename(self.path) if self.path else "Untitled"
        self.title_widget.set_subtitle(("• " if self.dirty else "") + name)

    def tape_colors(self):
        tape, ink = (1, 1, 1), (0.05, 0.05, 0.05)
        st = self.status
        if st and st.media_type and st.tape_width == self.label.tape_width:
            tape = COLOR_RGB.get(st.tape_color_name, tape)
            ink = COLOR_RGB.get(st.text_color_name, ink)
            if st.tape_color_name == "Clear (white text)":
                ink = (1, 1, 1)
        return tape, ink

    # ------------------------------------------------------------ element list
    def rebuild_all(self):
        self.copies_spin.set_value(self.copies)
        self.rebuild_list()
        self.build_label_page()
        self.build_print_page()
        self.update_title()
        self.schedule_render()

    def rebuild_list(self):
        self._building = True
        while (row := self.listbox.get_first_child()) is not None:
            self.listbox.remove(row)
        self.row_labels = []
        for el in self.label.elements:
            box = Gtk.Box(spacing=8, margin_top=6, margin_bottom=6)
            box.append(Gtk.Image.new_from_icon_name(ICONS.get(el.kind, "image-missing")))
            lb = Gtk.Label(label=el.title(), xalign=0, hexpand=True)
            lb.set_ellipsize(Pango.EllipsizeMode.END)
            box.append(lb)
            self.row_labels.append(lb)
            self.listbox.append(box)
        self._building = False
        if self.label.elements:
            self.sel = min(max(self.sel, 0), len(self.label.elements) - 1)
            self.listbox.select_row(self.listbox.get_row_at_index(self.sel))
        else:
            self.sel = -1
            self.build_element_page()

    def update_row_titles(self):
        for lb, el in zip(self.row_labels, self.label.elements):
            lb.set_text(el.title())

    def _on_row_selected(self, lb, row):
        if self._building:
            return
        if row is None:
            return
        self.sel = row.get_index()
        self.build_element_page()

    def add_element(self, kind):
        el = R.ELEMENT_TYPES[kind]()
        if kind == "text" and self.label.elements:
            prev = next((e for e in reversed(self.label.elements) if e.kind == "text"), None)
            if prev:
                el.font = prev.font
        pos = self.sel + 1 if self.sel >= 0 else len(self.label.elements)
        self.label.elements.insert(pos, el)
        self.sel = pos
        self.changed(structure=True)
        self.stack.set_visible_child_name("element")
        if kind == "image":
            self.choose_image(el)

    def move(self, d):
        i = self.sel
        j = i + d
        if 0 <= i < len(self.label.elements) and 0 <= j < len(self.label.elements):
            els = self.label.elements
            els[i], els[j] = els[j], els[i]
            self.sel = j
            self.changed(structure=True)

    def duplicate(self):
        if 0 <= self.sel < len(self.label.elements):
            self.label.elements.insert(self.sel + 1, copy.deepcopy(self.label.elements[self.sel]))
            self.sel += 1
            self.changed(structure=True)

    def delete(self):
        if 0 <= self.sel < len(self.label.elements):
            del self.label.elements[self.sel]
            self.sel = min(self.sel, len(self.label.elements) - 1)
            self.changed(structure=True)

    # ------------------------------------------------------------ element editor
    def _page(self, scrolled, groups):
        page = Adw.PreferencesPage()
        for g in groups:
            page.add(g)
        scrolled.set_child(page)

    def build_element_page(self):
        if not (0 <= self.sel < len(self.label.elements)):
            st = Adw.StatusPage(title="No element", icon_name="list-add-symbolic",
                                description="Use “Add” to put text, images, QR codes or "
                                            "barcodes on the label.")
            self.element_page.set_child(st)
            return
        el = self.label.elements[self.sel]
        builder = getattr(self, f"_edit_{el.kind}")
        self._page(self.element_page, builder(el))

    def _set(self, obj, attr, conv=None, structure=False):
        def cb(v):
            setattr(obj, attr, conv(v) if conv else v)
            self.changed(structure=structure)
        return cb

    def _edit_text(self, el: R.TextElement):
        g1 = group("Text", VAR_HELP)
        tvf = Gtk.Frame()
        tview = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8, bottom_margin=8,
                             left_margin=8, right_margin=8, accepts_tab=False)
        tview.set_size_request(-1, 70)
        buf = tview.get_buffer()
        buf.set_text(el.text)

        def on_text(b):
            el.text = b.get_text(b.get_start_iter(), b.get_end_iter(), False)
            self.changed()
        buf.connect("changed", on_text)
        tvf.set_child(tview)
        g1.add(tvf)
        GLib.idle_add(lambda: tview.grab_focus() and False)

        g2 = group("Font")
        frow = Adw.ActionRow(title=esc("Font family & style"))
        fbtn = Gtk.FontDialogButton(dialog=Gtk.FontDialog(title="Choose font"))
        fbtn.set_level(Gtk.FontLevel.FACE)
        fbtn.set_use_font(True)
        fbtn.set_valign(Gtk.Align.CENTER)
        fbtn.set_font_desc(Pango.FontDescription.from_string(el.font))

        def on_font(b, _p):
            d = b.get_font_desc()
            if d:
                d = d.copy()
                d.unset_fields(Pango.FontMask.SIZE)
                el.font = d.to_string()
                self.changed()
        fbtn.connect("notify::font-desc", on_font)
        frow.add_suffix(fbtn)
        g2.add(frow)

        size_row = spin_row("Size (pt)", el.size, 2, 200, 0.5, 1, self._set(el, "size"))
        size_row.set_sensitive(not el.auto_size)
        fit_row = switch_row("Fit to visible glyphs", el.fit_glyphs, self._set(el, "fit_glyphs"),
                             "Maximise ink height instead of line height")
        fit_row.set_sensitive(el.auto_size)

        def on_auto(v):
            el.auto_size = v
            size_row.set_sensitive(not v)
            fit_row.set_sensitive(v)
            self.changed()
        g2.add(switch_row("Auto size", el.auto_size, on_auto, "Fill the tape height"))
        g2.add(fit_row)
        g2.add(size_row)

        g3 = group("Style")
        g3.add(combo_row("Line alignment", ALIGN_NAMES, ALIGN.index(el.align),
                         lambda i: self._set(el, "align")(ALIGN[i])))
        g3.add(spin_row("Line spacing", el.line_spacing, 0.5, 3, 0.05, 2,
                        self._set(el, "line_spacing")))
        g3.add(spin_row("Letter spacing (pt)", el.letter_spacing, -5, 30, 0.1, 1,
                        self._set(el, "letter_spacing")))
        g3.add(switch_row("Underline", el.underline, self._set(el, "underline")))
        g3.add(switch_row("Strikethrough", el.strike, self._set(el, "strike")))
        g3.add(spin_row("Vertical offset (mm)", el.v_offset, -10, 10, 0.1, 1,
                        self._set(el, "v_offset")))
        return [g1, g2, g3]

    def choose_image(self, el):
        dlg = Gtk.FileDialog(title="Choose image")
        filt = Gtk.FileFilter()
        filt.set_name("Images")
        for p in ("*.png", "*.jpg", "*.jpeg", "*.bmp", "*.gif", "*.svg", "*.webp", "*.tif", "*.tiff"):
            filt.add_pattern(p)
            filt.add_pattern(p.upper())
        fl = Gio.ListStore.new(Gtk.FileFilter)
        fl.append(filt)
        dlg.set_filters(fl)

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            el.path = f.get_path()
            self.changed(structure=True)
        dlg.open(self, None, done)

    def _edit_image(self, el: R.ImageElement):
        g = group("Image", "PNG, JPEG, SVG, … are converted to black & white.")
        row = Adw.ActionRow(title="File", subtitle=esc(el.path) or "No file chosen")
        b = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)
        b.connect("clicked", lambda *_: self.choose_image(el))
        row.add_suffix(b)
        g.add(row)
        g.add(spin_row("Height (% of tape)", el.height_pct, 5, 100, 5, 0, self._set(el, "height_pct")))
        thr = spin_row("Threshold", el.threshold, 1, 254, 1, 0, self._set(el, "threshold", int))
        thr.set_sensitive(not el.dither)

        def on_dither(v):
            el.dither = v
            thr.set_sensitive(not v)
            self.changed()
        g.add(switch_row("Dither (photos)", el.dither, on_dither,
                         "Floyd–Steinberg; turn off for logos and line art"))
        g.add(thr)
        g.add(switch_row("Invert", el.invert, self._set(el, "invert")))
        return [g]

    def _edit_qr(self, el: R.QRElement):
        g = group("QR code", VAR_HELP)
        g.add(entry_row("Content (URL, text, …)", el.data, self._set(el, "data")))
        g.add(combo_row("Error correction", EC_NAMES, EC.index(el.error_correction),
                        lambda i: self._set(el, "error_correction")(EC[i])))
        g.add(spin_row("Height (% of tape)", el.height_pct, 10, 100, 5, 0, self._set(el, "height_pct")))
        return [g]

    def _edit_barcode(self, el: R.BarcodeElement):
        g = group("Barcode (Code 128)", VAR_HELP)
        g.add(entry_row("Content", el.data, self._set(el, "data")))
        g.add(spin_row("Bar width (dots)", el.module, 1, 6, 1, 0, self._set(el, "module", int),
                       "1 dot = 0.14 mm; 2+ recommended for scanning"))
        g.add(spin_row("Height (% of tape)", el.height_pct, 20, 100, 5, 0, self._set(el, "height_pct")))
        ts = spin_row("Text size (pt)", el.text_size, 3, 30, 0.5, 1, self._set(el, "text_size"))
        ts.set_sensitive(el.show_text)

        def on_show(v):
            el.show_text = v
            ts.set_sensitive(v)
            self.changed()
        g.add(switch_row("Show text", el.show_text, on_show))
        g.add(ts)
        return [g]

    def _edit_spacer(self, el: R.SpacerElement):
        g = group("Spacer", "Adds empty space, optionally with a separator line.")
        g.add(spin_row("Width (mm)", el.width_mm, 0, 200, 0.5, 1, self._set(el, "width_mm")))
        lw = spin_row("Line width (dots)", el.line_width, 1, 10, 1, 0, self._set(el, "line_width", int))
        lw.set_sensitive(el.line)

        def on_line(v):
            el.line = v
            lw.set_sensitive(v)
            self.changed()
        g.add(switch_row("Separator line", el.line, on_line))
        g.add(lw)
        return [g]

    # ------------------------------------------------------------ label page
    def build_label_page(self):
        lab = self.label
        g1 = group("Tape")
        self.tape_row = combo_row("Tape width", [TAPE_LABEL[w] for w in TAPE_WIDTHS],
                                  TAPE_WIDTHS.index(lab.tape_width), self._on_tape)
        self.tape_row.set_sensitive(not self.auto_tape)

        def on_auto(v):
            self.auto_tape = v
            self.tape_row.set_sensitive(not v)
            if v:
                self.apply_status_tape()
        g1.add(switch_row("Use tape in printer", self.auto_tape, on_auto,
                          "Follow the cassette that is inserted"))
        g1.add(self.tape_row)

        g2 = group("Length & layout")
        len_row = spin_row("Length (mm)", lab.length_mm or 50, 5, 1000, 1, 1,
                           lambda v: (setattr(lab, "length_mm", v), self.changed()))
        len_row.set_sensitive(lab.length_mm > 0)

        def on_fixed(v):
            lab.length_mm = len_row.get_value() if v else 0.0
            len_row.set_sensitive(v)
            al_row.set_sensitive(v)
            self.changed()
        g2.add(switch_row("Fixed length", lab.length_mm > 0, on_fixed,
                          "Otherwise the label is as long as its content"))
        g2.add(len_row)
        al_row = combo_row("Content alignment", ALIGN_NAMES, ALIGN.index(lab.content_align),
                           lambda i: self._set(lab, "content_align")(ALIGN[i]))
        al_row.set_sensitive(lab.length_mm > 0)
        g2.add(al_row)
        g2.add(spin_row("End padding (mm)", lab.pad_mm, 0, 50, 0.5, 1, self._set(lab, "pad_mm"),
                        "Space at the left and right ends"))
        g2.add(spin_row("Gap between elements (mm)", lab.gap_mm, 0, 50, 0.5, 1, self._set(lab, "gap_mm")))
        g2.add(spin_row("Vertical padding (mm)", lab.v_pad_mm, 0, 5, 0.1, 1, self._set(lab, "v_pad_mm")))

        g3 = group("Border")
        bw = spin_row("Line width (dots)", lab.border_width, 1, 12, 1, 0, self._set(lab, "border_width", int),
                      "1 dot ≈ 0.14 mm")
        ins = spin_row("Inset (dots)", lab.border_inset, 0, 20, 1, 0, self._set(lab, "border_inset", int))
        rad = spin_row("Corner radius (mm)", lab.border_radius_mm, 0, 10, 0.25, 2,
                       self._set(lab, "border_radius_mm"))

        def on_border(i):
            lab.border = BORDERS[i]
            for r in (bw, ins):
                r.set_sensitive(i != 0)
            rad.set_sensitive(BORDERS[i] in ("rounded", "double"))
            self.changed()
        g3.add(combo_row("Style", BORDER_NAMES, BORDERS.index(lab.border), on_border))
        g3.add(bw)
        g3.add(ins)
        g3.add(rad)
        on_border_sens = BORDERS.index(lab.border)
        bw.set_sensitive(on_border_sens != 0)
        ins.set_sensitive(on_border_sens != 0)
        rad.set_sensitive(lab.border in ("rounded", "double"))

        g4 = group("Appearance")
        g4.add(switch_row("Invert (white on black)", lab.invert, self._set(lab, "invert")))
        g4.add(spin_row("Darkness threshold", lab.threshold, 1, 254, 1, 0,
                        self._set(lab, "threshold", int),
                        "Higher = bolder text when converting to black & white"))
        self._page(self.label_page, [g1, g2, g3, g4])

    def _on_tape(self, i):
        if self.label.tape_width != TAPE_WIDTHS[i]:
            self.label.tape_width = TAPE_WIDTHS[i]
            self.changed()

    # ------------------------------------------------------------ print page
    def build_print_page(self):
        job = self.job
        g1 = group("Copies")
        self.copies_row = spin_row("Copies", self.copies, 1, 999, 1, 0, self._on_copies)
        g1.add(self.copies_row)
        g1.add(spin_row("Serial number start", self.counter_start, 0, 999999, 1, 0,
                        self._on_counter, "Value of {n} on the first label"))
        g2 = group("Cutting & feed")
        g2.add(switch_row("Auto cut", job.auto_cut, lambda v: setattr(job, "auto_cut", v),
                          "Cut after each label"))
        g2.add(switch_row("Chain printing", job.chain, lambda v: setattr(job, "chain", v),
                          "Don't feed & cut after the last label (saves tape on the next job)"))
        g2.add(spin_row("Feed margin (mm)", job.margin_dots / R.DOTS_PER_MM, 0.5, 30, 0.5, 1,
                        lambda v: setattr(job, "margin_dots", round(R.mm(v))),
                        "Blank tape before and after the label"))
        g3 = group()
        pb = Gtk.Button(label="Print", halign=Gtk.Align.CENTER)
        pb.add_css_class("suggested-action")
        pb.add_css_class("pill")
        pb.connect("clicked", lambda *_: self.do_print())
        g3.add(pb)
        self._page(self.print_page, [g1, g2, g3])

    def _on_copies(self, v):
        self.copies = int(v)
        if int(self.copies_spin.get_value()) != self.copies:
            self.copies_spin.set_value(self.copies)

    def _on_copies_header(self, spin):
        self.copies = int(spin.get_value())
        if hasattr(self, "copies_row") and int(self.copies_row.get_value()) != self.copies:
            self.copies_row.set_value(self.copies)

    def _on_counter(self, v):
        self.counter_start = int(v)
        self.schedule_render()

    def fit_zoom(self, force=False):
        if force:
            self.auto_zoom = True
        res = self.preview.result
        if not self.auto_zoom or res is None:
            return
        vw = self.psw.get_width() or 1100
        z = (vw - 2 * Preview.PAD - 8) / max(1, res.length_dots)
        z = max(1.0, min(6.0, z))
        self._zooming = True
        self.zoom_scale.set_value(z)
        self._zooming = False
        self.preview.zoom = z
        if force:
            self.preview.update_size()
            self.preview.queue_draw()

    def _on_zoom(self, scale):
        if not getattr(self, "_zooming", False):
            self.auto_zoom = False
        self.preview.zoom = scale.get_value()
        self.preview.update_size()
        self.preview.queue_draw()

    # ------------------------------------------------------------ printer I/O
    def _poll_tick(self):
        if self.is_active() or self.status is None:
            self.poll_status()
        return True

    def poll_status(self, force=False):
        if self.printing:
            return

        def work():
            if not self.dev_lock.acquire(blocking=force):
                return
            try:
                t = open_printer()
                p = Printer(t)
                try:
                    st = p.status()
                    name = t.info.name
                finally:
                    p.close()
                GLib.idle_add(self._on_status, st, name, None)
            except (USBError, PrinterError, OSError) as e:
                GLib.idle_add(self._on_status, None, None, str(e))
            finally:
                self.dev_lock.release()
        threading.Thread(target=work, daemon=True).start()

    def _on_status(self, st, name, err):
        prev = self.status
        self.status = st
        self.printer_name = name
        if st is None:
            self.status_icon.set_from_icon_name("printer-error-symbolic")
            msg = err or "Printer not found"
            if "No supported" in msg or "No printer" in msg:
                msg = "No P-touch printer connected — plug it in via USB and switch it on"
            self.status_label.set_text(msg)
        else:
            ok = not st.errors and st.media_type
            self.status_icon.set_from_icon_name("printer-symbolic" if ok else "printer-warning-symbolic")
            self.status_label.set_text(f"{name}:  {st.summary()}")
            self.apply_status_tape()
        if (prev is None) != (st is None) or (prev and st and prev.raw[24:26] != st.raw[24:26]):
            self.preview.queue_draw()
        return False

    def apply_status_tape(self):
        st = self.status
        if self.auto_tape and st and st.tape_width and st.tape_width != self.label.tape_width:
            self.label.tape_width = st.tape_width
            self._building = True
            self.tape_row.set_selected(TAPE_WIDTHS.index(st.tape_width))
            self._building = False
            self.schedule_render()
            self.toast_msg(f"Switched to {TAPE_LABEL[st.tape_width]} tape")

    def do_print(self):
        if self.printing:
            return
        label = copy.deepcopy(self.label)
        copies = self.copies
        start = self.counter_start
        job = copy.copy(self.job)
        job.tape_width = label.tape_width
        if not label.elements:
            self.toast_msg("The label is empty")
            return
        self.printing = True
        self.print_btn.set_sensitive(False)
        self.status_label.set_text("Preparing…")

        def progress(msg):
            GLib.idle_add(self.status_label.set_text, msg)

        def work():
            err = None
            try:
                if label.uses_counter():
                    pages = [R.to_raster_lines(R.render(label, n=start + i), label.tape_width)
                             for i in range(copies)]
                else:
                    lines = R.to_raster_lines(R.render(label, n=start), label.tape_width)
                    pages = [lines] * copies
                with self.dev_lock:
                    p = Printer(open_printer())
                    try:
                        p.print_pages(pages, job, progress=progress)
                    finally:
                        p.close()
            except Exception as e:  # noqa: BLE001
                err = str(e)
            GLib.idle_add(self._print_done, err, copies, label.uses_counter())
        threading.Thread(target=work, daemon=True).start()

    def _print_done(self, err, copies, counter):
        self.printing = False
        self.print_btn.set_sensitive(True)
        if err:
            dlg = Adw.AlertDialog(heading="Printing failed", body=err)
            dlg.add_response("ok", "OK")
            dlg.present(self)
        else:
            self.toast_msg(f"Printed {copies} label{'s' if copies > 1 else ''}")
            if counter:
                self.counter_start += copies
                self.build_print_page()
                self.schedule_render()
        self.poll_status(force=True)
        return False

    # ------------------------------------------------------------ files
    def confirm_discard(self, then):
        if not self.dirty:
            then()
            return
        dlg = Adw.AlertDialog(heading="Discard unsaved changes?",
                              body="The current label has unsaved changes.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("discard", "Discard")
        dlg.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda d, r: then() if r == "discard" else None)
        dlg.present(self)

    def _on_close_request(self, *_):
        if not self.dirty:
            return False

        def really():
            self.dirty = False
            self.close()
        self.confirm_discard(really)
        return True

    def new_label(self):
        tw = self.label.tape_width
        self.label = R.Label(tape_width=tw)
        self.path = None
        self.dirty = False
        self.sel = 0
        self.counter_start = 1
        self.rebuild_all()

    def _filters(self):
        f = Gtk.FileFilter()
        f.set_name("Tapewright labels")
        f.add_pattern("*" + FILE_EXT)
        f.add_pattern("*.p700")
        f.add_pattern("*.json")
        fl = Gio.ListStore.new(Gtk.FileFilter)
        fl.append(f)
        return fl

    def open_dialog(self):
        dlg = Gtk.FileDialog(title="Open label")
        dlg.set_filters(self._filters())

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            self.load(f.get_path())
        dlg.open(self, None, done)

    def load(self, path):
        try:
            with open(path, encoding="utf-8") as fh:
                self.label = R.Label.from_json(fh.read())
        except Exception as e:  # noqa: BLE001
            self.toast_msg(f"Cannot open {os.path.basename(path)}: {e}", 6)
            self.rebuild_all()
            return
        self.path = path
        self.dirty = False
        self.sel = 0
        self.auto_tape = False
        self.rebuild_all()
        st = self.status
        if st and st.tape_width and st.tape_width != self.label.tape_width:
            self.toast_msg(f"This label is for {TAPE_LABEL[self.label.tape_width]} tape; "
                           f"the printer has {TAPE_LABEL[st.tape_width]}", 6)

    def save(self, as_new=False):
        if self.path and not as_new:
            self._write(self.path)
            return
        dlg = Gtk.FileDialog(title="Save label")
        dlg.set_filters(self._filters())
        dlg.set_initial_name((os.path.splitext(os.path.basename(self.path))[0] if self.path
                              else "label") + FILE_EXT)

        def done(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            path = f.get_path()
            if not path.endswith((FILE_EXT, ".json", ".p700")):
                path += FILE_EXT
            self._write(path)
        dlg.save(self, None, done)

    def _write(self, path):
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.label.to_json())
        except OSError as e:
            self.toast_msg(f"Save failed: {e}", 6)
            return
        self.path = path
        self.dirty = False
        self.update_title()
        self.toast_msg(f"Saved {os.path.basename(path)}")

    def export_png(self):
        dlg = Gtk.FileDialog(title="Export PNG")
        base = os.path.splitext(os.path.basename(self.path))[0] if self.path else "label"
        dlg.set_initial_name(base + ".png")

        def done(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            try:
                r = R.render(self.label, n=self.counter_start)
                r.bitmap.convert("1").save(f.get_path(), dpi=(R.DPI, R.DPI))
                self.toast_msg("Exported PNG (180 dpi)")
            except Exception as e:  # noqa: BLE001
                self.toast_msg(f"Export failed: {e}", 6)
        dlg.save(self, None, done)

    def show_about(self):
        about = Adw.AboutDialog(application_name=APP_NAME,
                                application_icon=APP_ID,
                                version=VERSION,
                                developer_name="dimschlukas",
                                website=WEBSITE,
                                issue_url=WEBSITE + "/issues",
                                copyright="© 2026 dimschlukas",
                                comments="Label designer for Brother P-touch label printers "
                                         "(PT-P700 and compatible) on Linux.\n\n"
                                         "Brother and P-touch are trademarks of Brother "
                                         "Industries, Ltd. This project is not affiliated "
                                         "with or endorsed by Brother.",
                                license_type=Gtk.License.GPL_3_0)
        about.present(self)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)

    def do_activate(self):
        MainWindow(self).present()

    def do_open(self, files, n_files, hint):
        for f in files:
            MainWindow(self, f.get_path()).present()


def main(argv=None):
    return App().run(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    sys.exit(main())
