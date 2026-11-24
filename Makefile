# Installs the desktop integration files (launcher, icon, MIME type, AppStream
# metadata, udev rule). The Python package itself is installed with pip/pipx or
# your distribution's package (see README).
PREFIX  ?= /usr/local
DESTDIR ?=
UDEVDIR ?= /etc/udev/rules.d
ID      := io.github.dimschlukas.Tapewright

.PHONY: install-data uninstall-data test lint reload-udev

install-data:
	install -Dm644 data/$(ID).desktop      $(DESTDIR)$(PREFIX)/share/applications/$(ID).desktop
	install -Dm644 data/$(ID).svg          $(DESTDIR)$(PREFIX)/share/icons/hicolor/scalable/apps/$(ID).svg
	install -Dm644 data/$(ID).metainfo.xml $(DESTDIR)$(PREFIX)/share/metainfo/$(ID).metainfo.xml
	install -Dm644 data/$(ID).xml          $(DESTDIR)$(PREFIX)/share/mime/packages/$(ID).xml
	install -Dm644 data/60-tapewright.rules $(DESTDIR)$(UDEVDIR)/60-tapewright.rules

uninstall-data:
	rm -f $(DESTDIR)$(PREFIX)/share/applications/$(ID).desktop \
	      $(DESTDIR)$(PREFIX)/share/icons/hicolor/scalable/apps/$(ID).svg \
	      $(DESTDIR)$(PREFIX)/share/metainfo/$(ID).metainfo.xml \
	      $(DESTDIR)$(PREFIX)/share/mime/packages/$(ID).xml \
	      $(DESTDIR)$(UDEVDIR)/60-tapewright.rules

reload-udev:
	udevadm control --reload-rules && udevadm trigger --subsystem-match=usb --attr-match=idVendor=04f9
	update-mime-database $(PREFIX)/share/mime || true

test:
	python3 -m unittest discover -s tests -v

lint:
	ruff check tapewright tests
	ruff format --check tapewright tests
