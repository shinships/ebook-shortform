"""OCR trang PDF scan bang Claude vision (khong can Tesseract).

Trang duoc coi la scan khi gan nhu khong co text layer nhung co anh phu
gan het trang. Trang scan duoc render PNG va gui Claude transcribe thanh
markdown (heading danh dau bang #), sau do di qua pipeline nhu PDF text.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import fitz

from ebook_translator.core.llm import LLMClient

MIN_TEXT_CHARS = 40  # duoi nguong nay coi nhu khong co text layer
RENDER_DPI = 200

OCR_PROMPT = """\
Transcribe ALL text on this scanned book page, faithfully and completely, in the original English.

Rules:
- Output the text in reading order.
- Mark headings with markdown (#, ##, ###) based on visual size/prominence.
- Separate paragraphs with a blank line.
- Join words broken by end-of-line hyphenation.
- Skip page headers/footers and page numbers.
- If the page has no text (pure image/blank), output exactly: [NO TEXT]
- Output ONLY the transcription, no commentary, no markdown fence."""


def detect_scan_pages(doc: fitz.Document) -> list[int]:
    """Tra ve danh sach page number (0-based) la trang scan."""
    result = []
    for pno in range(doc.page_count):
        page = doc[pno]
        text_len = len(page.get_text().strip())
        if text_len >= MIN_TEXT_CHARS:
            continue
        # co anh phu >= 70% dien tich trang?
        page_area = page.rect.get_area()
        covered = 0.0
        for info in page.get_image_info():
            b = info["bbox"]
            covered += max(0.0, (b[2] - b[0]) * (b[3] - b[1]))
        if page_area and covered / page_area >= 0.7:
            result.append(pno)
    return result


def ocr_pages(
    pdf_path: str,
    page_numbers: list[int],
    llm: LLMClient,
    workdir: str | Path,
) -> dict[int, str]:
    """OCR cac trang chi dinh; cache theo so trang trong workdir."""
    cache_dir = Path(workdir) / "ocr"
    cache_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    results: dict[int, str] = {}
    for i, pno in enumerate(page_numbers, start=1):
        cache_file = cache_dir / f"page_{pno:05d}.json"
        if cache_file.exists():
            try:
                results[pno] = json.loads(cache_file.read_text(encoding="utf-8"))["text"]
                continue
            except Exception:
                pass
        print(f"OCR trang {pno + 1} ({i}/{len(page_numbers)})...", flush=True)
        pix = doc[pno].get_pixmap(dpi=RENDER_DPI)
        png_b64 = base64.standard_b64encode(pix.tobytes("png")).decode("ascii")
        text = llm.complete(
            system="You are a precise OCR engine for scanned book pages.",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": png_b64,
                            },
                        },
                        {"type": "text", "text": OCR_PROMPT},
                    ],
                }
            ],
        ).strip()
        if text == "[NO TEXT]":
            text = ""
        results[pno] = text
        cache_file.write_text(
            json.dumps({"text": text}, ensure_ascii=False), encoding="utf-8"
        )
    doc.close()
    return results
