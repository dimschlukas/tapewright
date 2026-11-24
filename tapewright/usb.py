# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 dimschlukas
"""Minimal USB transport for Brother P-touch printers.

Uses libusb-1.0 through ctypes (no third-party Python packages needed).
Falls back to the kernel usblp character device (/dev/usb/lp*) if libusb
cannot open the device.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import glob
import os
import select
import time

BROTHER_VID = 0x04F9

# product id -> (name, head pins, dpi)
KNOWN_PRINTERS = {
    0x2061: ("PT-P700", 128, 180),
    0x2064: ("PT-P700 (Editor Lite mode)", 128, 180),
    0x2062: ("PT-P750W", 128, 180),
    0x2065: ("PT-P750W (Editor Lite mode)", 128, 180),
    0x2060: ("PT-E550W", 128, 180),
    0x20AF: ("PT-P710BT", 128, 180),
    0x2073: ("PT-D450", 128, 180),
    0x2074: ("PT-D600", 128, 180),
}

LIBUSB_ERROR_TIMEOUT = -7
LIBUSB_ERROR_NOT_FOUND = -5
LIBUSB_ERROR_NOT_SUPPORTED = -12


class USBError(IOError):
    pass


class _DeviceDescriptor(ctypes.Structure):
    _fields_ = [
        ("bLength", ctypes.c_uint8),
        ("bDescriptorType", ctypes.c_uint8),
        ("bcdUSB", ctypes.c_uint16),
        ("bDeviceClass", ctypes.c_uint8),
        ("bDeviceSubClass", ctypes.c_uint8),
        ("bDeviceProtocol", ctypes.c_uint8),
        ("bMaxPacketSize0", ctypes.c_uint8),
        ("idVendor", ctypes.c_uint16),
        ("idProduct", ctypes.c_uint16),
        ("bcdDevice", ctypes.c_uint16),
        ("iManufacturer", ctypes.c_uint8),
        ("iProduct", ctypes.c_uint8),
        ("iSerialNumber", ctypes.c_uint8),
        ("bNumConfigurations", ctypes.c_uint8),
    ]


_lib = None


def _libusb():
    global _lib
    if _lib is not None:
        return _lib
    name = ctypes.util.find_library("usb-1.0") or "libusb-1.0.so.0"
    lib = ctypes.CDLL(name)
    vp = ctypes.c_void_p
    lib.libusb_init.argtypes = [ctypes.POINTER(vp)]
    lib.libusb_exit.argtypes = [vp]
    lib.libusb_get_device_list.argtypes = [vp, ctypes.POINTER(ctypes.POINTER(vp))]
    lib.libusb_get_device_list.restype = ctypes.c_ssize_t
    lib.libusb_free_device_list.argtypes = [ctypes.POINTER(vp), ctypes.c_int]
    lib.libusb_get_device_descriptor.argtypes = [vp, ctypes.POINTER(_DeviceDescriptor)]
    lib.libusb_get_bus_number.argtypes = [vp]
    lib.libusb_get_bus_number.restype = ctypes.c_uint8
    lib.libusb_get_device_address.argtypes = [vp]
    lib.libusb_get_device_address.restype = ctypes.c_uint8
    lib.libusb_open.argtypes = [vp, ctypes.POINTER(vp)]
    lib.libusb_close.argtypes = [vp]
    lib.libusb_set_auto_detach_kernel_driver.argtypes = [vp, ctypes.c_int]
    lib.libusb_kernel_driver_active.argtypes = [vp, ctypes.c_int]
    lib.libusb_detach_kernel_driver.argtypes = [vp, ctypes.c_int]
    lib.libusb_attach_kernel_driver.argtypes = [vp, ctypes.c_int]
    lib.libusb_claim_interface.argtypes = [vp, ctypes.c_int]
    lib.libusb_release_interface.argtypes = [vp, ctypes.c_int]
    lib.libusb_bulk_transfer.argtypes = [
        vp,
        ctypes.c_ubyte,
        ctypes.POINTER(ctypes.c_ubyte),
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_uint,
    ]
    lib.libusb_get_string_descriptor_ascii.argtypes = [
        vp,
        ctypes.c_uint8,
        ctypes.POINTER(ctypes.c_ubyte),
        ctypes.c_int,
    ]
    lib.libusb_strerror.argtypes = [ctypes.c_int]
    lib.libusb_strerror.restype = ctypes.c_char_p
    _lib = lib
    return lib


def _err(lib, code, what):
    return USBError(f"{what}: {lib.libusb_strerror(code).decode()} ({code})")


class DeviceInfo:
    def __init__(self, bus, address, pid, name, pins, dpi, serial="", path=None):
        self.bus, self.address, self.pid = bus, address, pid
        self.name, self.pins, self.dpi, self.serial = name, pins, dpi, serial
        self.path = path  # for lp backend

    def __repr__(self):
        where = self.path or f"usb {self.bus:03d}:{self.address:03d}"
        return f"<{self.name} {where} {self.serial}>"


class LibUSBTransport:
    EP_OUT = 0x02
    EP_IN = 0x81

    def __init__(self, info: DeviceInfo | None = None):
        self.lib = _libusb()
        self.ctx = ctypes.c_void_p()
        r = self.lib.libusb_init(ctypes.byref(self.ctx))
        if r < 0:
            raise _err(self.lib, r, "libusb_init")
        self.handle = None
        self.detached = False
        try:
            self._open(info)
        except Exception:
            self.lib.libusb_exit(self.ctx)
            raise

    @staticmethod
    def enumerate() -> list[DeviceInfo]:
        lib = _libusb()
        ctx = ctypes.c_void_p()
        if lib.libusb_init(ctypes.byref(ctx)) < 0:
            return []
        out = []
        lst = ctypes.POINTER(ctypes.c_void_p)()
        n = lib.libusb_get_device_list(ctx, ctypes.byref(lst))
        try:
            for i in range(max(n, 0)):
                dev = lst[i]
                d = _DeviceDescriptor()
                if lib.libusb_get_device_descriptor(dev, ctypes.byref(d)) != 0:
                    continue
                if d.idVendor != BROTHER_VID or d.idProduct not in KNOWN_PRINTERS:
                    continue
                name, pins, dpi = KNOWN_PRINTERS[d.idProduct]
                serial = ""
                h = ctypes.c_void_p()
                if lib.libusb_open(dev, ctypes.byref(h)) == 0:
                    buf = (ctypes.c_ubyte * 64)()
                    k = lib.libusb_get_string_descriptor_ascii(h, d.iSerialNumber, buf, 64)
                    if k > 0:
                        serial = bytes(buf[:k]).decode(errors="replace")
                    lib.libusb_close(h)
                out.append(
                    DeviceInfo(
                        lib.libusb_get_bus_number(dev),
                        lib.libusb_get_device_address(dev),
                        d.idProduct,
                        name,
                        pins,
                        dpi,
                        serial,
                    )
                )
        finally:
            if n >= 0:
                lib.libusb_free_device_list(lst, 1)
            lib.libusb_exit(ctx)
        return out

    def _open(self, info):
        lib = self.lib
        lst = ctypes.POINTER(ctypes.c_void_p)()
        n = lib.libusb_get_device_list(self.ctx, ctypes.byref(lst))
        found = None
        try:
            for i in range(max(n, 0)):
                dev = lst[i]
                d = _DeviceDescriptor()
                lib.libusb_get_device_descriptor(dev, ctypes.byref(d))
                if d.idVendor != BROTHER_VID or d.idProduct not in KNOWN_PRINTERS:
                    continue
                if info and (
                    lib.libusb_get_bus_number(dev) != info.bus
                    or lib.libusb_get_device_address(dev) != info.address
                ):
                    continue
                h = ctypes.c_void_p()
                r = lib.libusb_open(dev, ctypes.byref(h))
                if r != 0:
                    raise _err(lib, r, "Cannot open printer (permissions?)")
                found = h
                name, pins, dpi = KNOWN_PRINTERS[d.idProduct]
                self.info = info or DeviceInfo(
                    lib.libusb_get_bus_number(dev),
                    lib.libusb_get_device_address(dev),
                    d.idProduct,
                    name,
                    pins,
                    dpi,
                )
                break
        finally:
            if n >= 0:
                lib.libusb_free_device_list(lst, 1)
        if not found:
            raise USBError("No supported Brother P-touch printer found")
        self.handle = found
        if self.info.pid in (0x2064, 0x2065):
            raise USBError(
                "Printer is in 'Editor Lite' mode. Press and hold the "
                "Editor Lite button until the green LED turns off."
            )
        if lib.libusb_kernel_driver_active(self.handle, 0) == 1:
            r = lib.libusb_detach_kernel_driver(self.handle, 0)
            if r != 0:
                raise _err(lib, r, "Cannot detach usblp kernel driver")
            self.detached = True
        r = lib.libusb_claim_interface(self.handle, 0)
        if r != 0:
            raise _err(lib, r, "Cannot claim interface")

    def write(self, data: bytes, timeout_ms=5000):
        lib = self.lib
        pos = 0
        while pos < len(data):
            chunk = data[pos : pos + 4096]
            buf = (ctypes.c_ubyte * len(chunk)).from_buffer_copy(chunk)
            done = ctypes.c_int()
            r = lib.libusb_bulk_transfer(
                self.handle, self.EP_OUT, buf, len(chunk), ctypes.byref(done), timeout_ms
            )
            if r != 0:
                raise _err(lib, r, "USB write failed")
            pos += done.value

    def read(self, size=32, timeout_ms=1000) -> bytes:
        buf = (ctypes.c_ubyte * 64)()
        done = ctypes.c_int()
        r = self.lib.libusb_bulk_transfer(
            self.handle, self.EP_IN, buf, 64, ctypes.byref(done), timeout_ms
        )
        if r == LIBUSB_ERROR_TIMEOUT:
            return bytes(buf[: done.value])
        if r != 0:
            raise _err(self.lib, r, "USB read failed")
        return bytes(buf[: done.value])

    def close(self):
        if self.handle:
            self.lib.libusb_release_interface(self.handle, 0)
            if self.detached:
                self.lib.libusb_attach_kernel_driver(self.handle, 0)
            self.lib.libusb_close(self.handle)
            self.handle = None
        if self.ctx:
            self.lib.libusb_exit(self.ctx)
            self.ctx = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class LPTransport:
    """Kernel usblp device (/dev/usb/lpN). Needs membership in group 'lp'."""

    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR)
        self.info = DeviceInfo(0, 0, 0x2061, "P-touch (usblp)", 128, 180, path=path)

    @staticmethod
    def candidates():
        return sorted(glob.glob("/dev/usb/lp*"))

    def write(self, data, timeout_ms=5000):
        view = memoryview(data)
        while view:
            n = os.write(self.fd, view)
            view = view[n:]

    def read(self, size=32, timeout_ms=1000):
        end = time.monotonic() + timeout_ms / 1000
        out = b""
        while len(out) < size and time.monotonic() < end:
            r, _, _ = select.select([self.fd], [], [], max(0, end - time.monotonic()))
            if not r:
                break
            chunk = os.read(self.fd, size - len(out))
            if not chunk:
                time.sleep(0.02)
                continue
            out += chunk
        return out

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def open_printer(info: DeviceInfo | None = None):
    """Open the first (or given) printer, trying libusb then usblp."""
    errors = []
    try:
        return LibUSBTransport(info)
    except (USBError, OSError) as e:
        errors.append(str(e))
        if "Editor Lite" in str(e):
            raise
    for path in LPTransport.candidates():
        try:
            return LPTransport(path)
        except OSError as e:
            errors.append(f"{path}: {e}")
    raise USBError("; ".join(errors) or "No printer found")
