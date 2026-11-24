# PT-P700 USB raster protocol

These are notes from implementing Tapewright.
They are based on Brother's publicly available *Software Developer's Manual – Raster Command Reference* for the PT-E550W/P750W/P710BT, and verified against a PT-P700.

## USB

| Item | Value |
|---|---|
| Vendor ID | `04f9` (Brother) |
| Product ID | `2061` = PT-P700 in printer mode; `2064` = Editor Lite (mass storage) mode |
| Interface | interface 0, class 7 (printer), protocol 2 (bidirectional) |
| Endpoints | bulk OUT `0x02`, bulk IN `0x81`, 64-byte packets |

## Print head and tape geometry

The print head has 128 pins at 180 dpi.
One raster line is 16 bytes, which is one 1/180-inch column across the tape.
The most significant bit of byte 0 is the top edge of the label as you read it.

| Tape | Printable pins | Unused pins on each side |
|---|---|---|
| 3.5 mm | 24 | 52 |
| 6 mm | 32 | 48 |
| 9 mm | 50 | 39 |
| 12 mm | 70 | 29 |
| 18 mm | 112 | 8 |
| 24 mm | 128 | 0 |

## Status

Send `00`×100, then `ESC @` (`1b 40`), then `ESC i S` (`1b 69 53`).
The printer answers with 32 bytes:

| Byte | Meaning |
|---|---|
| 0–3 | `80 20 42 30` (header, size, 'B', series) |
| 4 | model: `0x67` = PT-P700, `0x68` = P750W |
| 8 | error 1: `01` no media, `02` end of media, `04` cutter jam, `10` in use, `20` turned off, `80` fan |
| 9 | error 2: `01` replace media, `04` communication error, `10` cover open, `20` overheating, `80` system error |
| 10 | tape width in mm (3.5 mm tape reports 4) |
| 11 | media type: `00` none, `01` laminated, `03` non-laminated, `11`/`17` heat-shrink, `FF` incompatible |
| 18 | status type: `00` reply, `01` printing completed, `02` error, `06` notification, `07` phase change |
| 19 | phase: `00` editing, `01` printing |
| 24 | tape colour: `01` white, `03` clear, `04` red, `05` blue, `06` yellow, … |
| 25 | text colour: `01` white, `04` red, `05` blue, `08` black, `0A` gold |

During printing, the printer sends 32-byte status messages on its own initiative.
It sends one with status type `01` (printing completed) after each page.

## Print job

The job starts with this header, sent once:

```
00 × 100                  invalidate
1b 40                     ESC @   initialize
1b 69 61 01               ESC i a raster mode
```

Then each page is sent as follows:

```
1b 69 7a f n2 n3 n4 l0 l1 l2 l3 p 00
                          ESC i z print information
                            f  = 0x80 recover | 0x04 width valid | 0x02 media valid
                            n2 = media type, n3 = width mm, n4 = 0
                            l  = raster line count (little-endian)
                            p  = 0 first page, 1 middle page, 2 last page
1b 69 4d m                ESC i M mode: bit 6 = auto cut
1b 69 4b a                ESC i K advanced: bit 3 = no chain printing (feed + cut at end)
1b 69 64 d0 d1            ESC i d feed margin in dots (little-endian)
4d 02                     M 02    TIFF PackBits compression
47 len_lo len_hi <data>   G       one raster line (PackBits-compressed 16 bytes)
5a                        Z       an empty raster line
0c | 1a                   FF = end of page (more follow); 1A = end of job, feed and cut
```
