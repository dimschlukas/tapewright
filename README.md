# Tapewright

[![CI](https://github.com/dimschlukas/tapewright/actions/workflows/ci.yml/badge.svg)](https://github.com/dimschlukas/tapewright/actions/workflows/ci.yml)
[![License: GPL v3+](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](LICENSE)

Tapewright is a label designer for Brother P-touch USB label printers on Linux.
It talks to the printer directly over USB, so it needs no CUPS driver and no Brother software.

![Tapewright screenshot](docs/screenshot.png)

> Brother and P-touch are trademarks of Brother Industries, Ltd.
> This is an independent project, not affiliated with or endorsed by Brother.

## Supported printers

| Model | Status |
|---|---|
| PT-P700 | tested |
| PT-P750W, PT-E550W, PT-P710BT (USB), PT-D450, PT-D600 | same raster protocol family; untested, [reports welcome](../../issues/new?template=printer_report.yml) |

Supported tapes are TZe tapes 3.5, 6, 9, 12, 18 and 24 mm wide.

## Features

- **Elements:** text, images, QR codes, Code 128 barcodes and spacers.
- **Text:**
  - any installed font and face;
  - auto-size to the tape height, or a fixed point size;
  - multi-line, with line alignment, line and letter spacing, underline and strikethrough.
- **Images:** PNG, JPEG, SVG and more, with dithering or a threshold.
- **Two layouts:**
  - *Automatic* places elements left to right along the tape.
  - *Free* lets you drag elements anywhere in the preview and resize them with the corner handle.
    Elements snap to the tape centre and edges and to other elements.
  - The first drag switches a label to free layout without moving anything.
- **Rotation** to any angle: drag the round handle, type an exact angle, or rotate in 90° steps.
- **Multi-select:** move a group together, align it (left, centre, right, top, middle, bottom), or distribute three or more elements evenly.
- **Undo/redo.**
- **Rulers** in real millimetres:
  - horizontal from the cut edge of the printed piece, including the printer's feed margin at each end;
  - vertical from the top edge of the tape, with the printable band marked.
- **Label options:**
  - borders: rectangle, rounded or double;
  - inverted (white-on-black) printing;
  - fixed or automatic length;
  - separate start and end padding.
- **Variables:** `{date}`, `{time}`, `{datetime}`, `{date:%d.%m.%Y}`, and the serial number `{n}` / `{n:03d}`, which increases with each copy.
- **Printer integration:**
  - live status: tape width, type, colours and errors;
  - follows the inserted cassette automatically;
  - the preview is dot-accurate and drawn in the real tape and ink colours.
- **Printing:** copies, auto cut, chain printing, feed margin, and a calibration ruler.
- **Files and CLI:** save and load labels (`.twlabel`, JSON), export PNG at 180 dpi, and a command-line interface for scripting.

## Installation

**Requirements:**
- Python ≥ 3.10
- GTK ≥ 4.10 and libadwaita ≥ 1.5, with PyGObject
- pycairo, Pillow and libusb-1.0
- optional: `python-qrcode` for QR codes and librsvg for SVG images

### Arch Linux

```sh
git clone https://github.com/dimschlukas/tapewright.git
cd tapewright/packaging/arch && makepkg -si
```

### Other distributions

Install the system packages first:

| Distribution | Command |
|---|---|
| Debian 13 / Ubuntu 24.04 or newer | `sudo apt install python3-gi python3-gi-cairo python3-pil gir1.2-gtk-4.0 gir1.2-adw-1 libusb-1.0-0 python3-qrcode pipx` |
| Fedora | `sudo dnf install python3-gobject python3-cairo python3-pillow gtk4 libadwaita libusb1 python3-qrcode pipx` |

Then install Tapewright and its desktop integration:

```sh
git clone https://github.com/dimschlukas/tapewright.git && cd tapewright
pipx install --system-site-packages .
sudo make install-data reload-udev    # launcher, icon, file type, udev rule
```

Replug the printer afterwards so the new udev rule applies.

### Run from source without installing

```sh
python3 -m tapewright
```

## Usage

Start *Tapewright* from your application menu, or run `tapewright`.
Add elements with **Add**, edit them in the *Element* tab, and print with **Print** (Ctrl+P).

| Action | Mouse / keyboard |
|---|---|
| Move | drag an element; arrow keys nudge 1 dot, Shift+arrows 1 mm |
| Resize | drag the square handle at the bottom-right corner |
| Rotate | drag the round handle above the element (15° snapping; hold Shift for free rotation), Ctrl+R / Ctrl+Shift+R for 90° |
| Select several | Ctrl/Shift+click, drag a box over empty space, Ctrl+A |
| Snap off | hold Shift while dragging |
| Context menu | right-click an element: align, centre, front/back, duplicate, delete |
| Duplicate / delete | Ctrl+D / Delete |
| Undo / redo | Ctrl+Z / Ctrl+Shift+Z |
| New, open, save, export PNG | Ctrl+N, Ctrl+O, Ctrl+S, Ctrl+E |

### Command line

```sh
tapewright status
tapewright print -t "Hello\nWorld" -b rounded -c 3
tapewright print examples/asset-tag.twlabel -c 10 -n 1   # serial numbers 1..10
tapewright render -t "Test" --qr https://example.com -o out.png
tapewright calibrate                                     # print a 100 mm ruler
```

Run `tapewright <command> --help` for all options.

## Troubleshooting

- **"Cannot open printer" or a permissions error:** install the udev rule with `sudo make install-data reload-udev`, then replug the printer.
  Without the rule, Tapewright falls back to `/dev/usb/lp0`, which requires membership in group `lp`.
- **Printer not found and a USB drive appears instead:** the green *Editor Lite* LED is lit.
  Hold the Editor Lite button until it goes off.
- **The printed piece is longer than the label:** the printer adds a blank feed margin at each end (2 mm by default; *Print → Feed margin*).
  The preview shows it hatched, and the ruler measures the whole cut piece.
- **Lengths seem wrong:** print *Print → Print calibration ruler*.
  The distance from the 0 line to the 100 line should be exactly 100 mm.

## Contributing

Bug reports, printer compatibility reports and pull requests are welcome.
See [CONTRIBUTING.md](CONTRIBUTING.md); the printer protocol is documented in [docs/PROTOCOL.md](docs/PROTOCOL.md).

## License

Copyright © 2026 dimschlukas.

Tapewright is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.
See [LICENSE](LICENSE).
