# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-24

First public release.

### Added

- Direct USB printing for the Brother PT-P700 using the P-touch raster protocol, through libusb or `/dev/usb/lp*`, with no driver needed.
- Live printer status: tape width, type and colours, and errors.
- Elements: text, image, QR code, Code 128 barcode and spacer.
- Automatic (left-to-right) and free (drag & drop) layouts.
  Switching to free layout leaves the rendered label pixel-identical.
- Snapping with guide lines, keyboard nudging, and a right-click context menu.
- Rotation to any angle, with a rotation handle on the canvas.
- Multi-select; align and distribute.
- Undo/redo.
- Borders, inverted printing, fixed or automatic length, and separate start and end padding.
- Variables for date/time and serial numbers.
- Preview with real-size rulers, including the printer's feed margins, and a calibration ruler print.
- Label files (`.twlabel`) and PNG export.
- Command-line interface: `status`, `print`, `render`, `calibrate`.
- Desktop integration: launcher, icon, MIME type, AppStream metadata, and a udev rule.

[Unreleased]: https://github.com/dimschlukas/tapewright/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/dimschlukas/tapewright/releases/tag/v0.1.0
