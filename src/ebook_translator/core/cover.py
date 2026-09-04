"""Xac thuc anh bia sach, va cho phep chi dinh anh bia thay the.

EPUB do calibre convert thuong khai bao nham bia: khi sach goc khong co bia
that, calibre lay dai anh dau tien trong manifest lam bia — thuong la mot trang
scan noi dung (index, muc luc, hinh minh hoa). Ket qua la sach xuat ra co "bia"
la mot trang chu li ti.

Module nay dung LLM vision kiem tra anh bia co that su la bia sach khong. Neu
khong, bia bi bo (kem goi y dung --cover). Nguoi dung co the chi dinh anh bia
rieng qua --cover <file>; khi do bo qua buoc kiem tra.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

from ebook_translator.core.llm import LLMClient
from ebook_translator.models import Book, ImageAsset

# Cac dinh dang anh EPUB3 bat buoc reader ho tro. Anh ngoai danh sach nay
# (vd webp) duoc chuyen sang JPEG de khong bi reader bo qua.
EPUB_SAFE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/svg+xml"}

_SUFFIX_TO_TYPE = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}

COVER_CHECK_PROMPT = """\
Is this image the FRONT COVER of a book?

A front cover shows the book title as designed cover art, usually with the \
author's name.

It is NOT a front cover if it is: a scanned interior page (index, table of \
contents, copyright page, body text, footnotes), a figure/diagram/chart/table, \
a photograph without title text, or a blank page.

Answer with exactly one word: YES or NO."""


def resolve_cover(
    book: Book,
    llm: LLMClient | None,
    cover_path: str | None,
    workdir: Path,
) -> None:
    """Chot anh bia cho `book` (sua truc tiep book.images).

    - cover_path co gia tri -> dung anh do lam bia, bo qua kiem tra.
    - Nguoc lai -> kiem tra bia sach goc bang LLM vision; khong dat thi bo bia.
    """
    if cover_path:
        _set_manual_cover(book, Path(cover_path))
        return

    current = next((im for im in book.images if im.is_cover), None)
    if current is None:
        print(
            "  Sách gốc không khai báo ảnh bìa — sách tóm tắt sẽ không có bìa.\n"
            "  (Muốn có bìa: chạy lại kèm --cover <file-ảnh>)"
        )
        return
    if llm is None:
        return  # khong co ket noi API -> giu nguyen bia goc

    if _is_book_cover(current, llm, workdir):
        return

    current.is_cover = False
    print(
        "  Ảnh bìa trong file gốc không phải bìa sách (thường là trang scan nội "
        "dung do calibre lấy nhầm) — đã bỏ.\n"
        "  (Muốn có bìa đúng: chạy lại kèm --cover <file-ảnh>)"
    )


# ---- bia do nguoi dung chi dinh ----


def _set_manual_cover(book: Book, path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"Không tìm thấy file ảnh bìa: {path}")
    data = path.read_bytes()
    media_type = _SUFFIX_TO_TYPE.get(path.suffix.lower(), "")
    if not media_type:
        raise SystemExit(
            f"Định dạng ảnh bìa không nhận dạng được: {path.suffix}\n"
            "Dùng .jpg, .png, .gif hoặc .webp"
        )
    data, media_type = _to_epub_safe(data, media_type)

    for img in book.images:
        img.is_cover = False
    ext = ".jpg" if media_type == "image/jpeg" else Path(path).suffix.lower()
    book.images.append(
        ImageAsset(
            id="img_cover",
            filename=f"images/cover{ext}",
            data=data,
            media_type=media_type,
            is_cover=True,
        )
    )
    print(f"  Dùng ảnh bìa chỉ định: {path.name} ({len(data):,} bytes)")


def _to_epub_safe(data: bytes, media_type: str) -> tuple[bytes, str]:
    """Chuyen anh sang JPEG neu dinh dang khong nam trong chuan EPUB3."""
    if media_type in EPUB_SAFE_TYPES:
        return data, media_type
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
    except Exception as exc:
        raise SystemExit(f"Không chuyển được ảnh bìa sang JPEG: {exc}")
    print(f"  (chuyển bìa {media_type} -> image/jpeg cho tương thích reader)")
    return buf.getvalue(), "image/jpeg"


# ---- kiem tra bia bang LLM vision ----


def _is_book_cover(img: ImageAsset, llm: LLMClient, workdir: Path) -> bool:
    """Hoi LLM vision xem anh co phai bia sach khong; cache theo noi dung anh."""
    import base64

    digest = hashlib.md5(img.data).hexdigest()
    cache_file = Path(workdir) / "cover_check.json"
    cache: dict[str, bool] = {}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    if digest in cache:
        return bool(cache[digest])

    media_type = img.media_type
    data = img.data
    if media_type not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
        data, media_type = _to_epub_safe(data, media_type)

    try:
        answer = llm.complete(
            system="You classify book images. Answer with one word only.",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64.standard_b64encode(data).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": COVER_CHECK_PROMPT},
                    ],
                }
            ],
            max_tokens=2000,
        )
    except Exception as exc:  # loi mang/API -> giu nguyen bia goc, khong chan
        print(f"  (bỏ qua kiểm tra bìa: {exc})", file=sys.stderr)
        return True

    ok = "yes" in answer.strip().lower()[:10]
    cache[digest] = ok
    try:
        cache_file.write_text(json.dumps(cache), encoding="utf-8")
    except Exception:
        pass
    return ok
