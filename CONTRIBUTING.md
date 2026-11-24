# Contributing to Tapewright

Thanks for helping!
Bug reports, printer compatibility reports and pull requests are all welcome.

## Reporting problems

- **Bugs:** use the *Bug report* issue template.
  Include the output of `tapewright --version` and `tapewright status`, and start Tapewright from a terminal to capture any errors.
- **Other printer models:** use the *Printer compatibility report* template.
  Reports for models other than the PT-P700 are especially useful: include the USB ID from `lsusb` and whether the calibration ruler (`tapewright calibrate`) prints correctly.

## Development setup

Install the system packages listed in the README, then run straight from the checkout:

```sh
python3 -m tapewright          # GUI
python3 -m tapewright status   # CLI
```

No printer is needed for most work: `tapewright render … -o out.png` writes the exact 1-bit image that would be printed.

## Checks

Every pull request must pass these checks, which CI also runs:

```sh
make test                     # unit tests, no printer needed
ruff check tapewright tests   # lint
ruff format tapewright tests  # formatting
```

Add a unit test in `tests/` when you fix a bug or add renderer, protocol or layout behaviour.

## Code layout

| File | Contents |
|---|---|
| `tapewright/usb.py` | libusb (ctypes) and usblp transports |
| `tapewright/protocol.py` | status parsing, PackBits, job builder |
| `tapewright/render.py` | label model and Cairo/Pango renderer; all geometry is in printer dots (180 dpi) |
| `tapewright/barcode.py` | Code 128 encoder |
| `tapewright/gui.py` | GTK 4 / libadwaita editor |
| `tapewright/cli.py` | command line |

[docs/PROTOCOL.md](docs/PROTOCOL.md) describes the printer protocol.

## Pull requests

- Keep changes focused, and describe what you tested.
  Say whether you printed on real hardware, and with which model and tape.
- New source files start with the SPDX header used in the existing files.
- By contributing, you agree that your contribution is licensed under GPL-3.0-or-later.
- Do not add Brother's trademarks to the application's name or icon.
  Descriptive use ("for Brother P-touch printers") is fine.
