"""Doc file dau vao (.epub/.pdf) thanh Book, kem OCR trang scan neu can."""

from __future__ import annotations

import sys
from pathlib import Path

from ebook_translator.core.llm import LLMClient
from ebook_translator.models import Book


def load_book(input_path: Path, llm: LLMClient | None, workdir: Path) -> Book:
    """Dispatch theo duoi file; PDF di qua buoc phat hien + OCR trang scan."""
    suffix = input_path.suffix.lower()
    if suffix == ".epub":
        from ebook_translator.readers.epub_reader import read_epub

        return read_epub(str(input_path))
    return _read_pdf_with_ocr(str(input_path), llm, workdir)


def _read_pdf_with_ocr(path: str, llm: LLMClient | None, workdir: Path) -> Book:
    import fitz

    from ebook_translator.readers.ocr import detect_scan_pages, ocr_pages
    from ebook_translator.readers.pdf_reader import read_pdf

    doc = fitz.open(path)
    scan_pages = detect_scan_pages(doc)
    doc.close()

    scan_texts = None
    if scan_pages:
        print(f"Phát hiện {len(scan_pages)} trang scan — OCR bằng Claude vision...")
        if llm is None:
            print("  (bỏ qua OCR vì không có kết nối API)", file=sys.stderr)
        else:
            scan_texts = ocr_pages(path, scan_pages, llm, workdir)
    return read_pdf(path, scan_texts=scan_texts)
