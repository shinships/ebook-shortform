"""Tool dich ebook tieng Anh (PDF/EPUB) sang tieng Viet, xuat EPUB."""

import sys

__version__ = "0.1.0"

# Console Windows mac dinh cp1252 khong in duoc tieng Viet; tool nay in
# progress tieng Viet o nhieu module nen ep UTF-8 ngay khi import package.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
del _stream
