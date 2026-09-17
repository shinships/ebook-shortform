import os
from pathlib import Path
from typing import Optional
import pymupdf
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
from PIL import Image
import io

def extract_cover_from_pdf(pdf_path: Path, output_image_path: Path) -> bool:
    """Trích xuất trang đầu tiên của file PDF làm ảnh bìa chất lượng cao."""
    try:
        doc = pymupdf.open(str(pdf_path))
        if len(doc) == 0:
            return False
        first_page = doc[0]
        # Render trang đầu ở độ phân giải 200 DPI
        pix = first_page.get_pixmap(dpi=200)
        output_image_path.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(output_image_path))
        return True
    except Exception as e:
        print(f"Error extracting cover from PDF {pdf_path}: {e}")
        return False

def extract_cover_from_epub(epub_path: Path, output_image_path: Path) -> bool:
    """Trích xuất ảnh bìa từ file EPUB."""
    try:
        book = epub.read_epub(str(epub_path))
        # 1. Tìm qua get_items_of_type(ITEM_IMAGE) có chứa từ 'cover'
        for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
            name = item.get_name().lower()
            if "cover" in name:
                output_image_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_image_path, "wb") as f:
                    f.write(item.get_content())
                return True

        # 2. Fallback: Lấy ảnh bất kỳ đầu tiên trong file EPUB
        for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
            output_image_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_image_path, "wb") as f:
                f.write(item.get_content())
            return True
        return False
    except Exception as e:
        print(f"Error extracting cover from EPUB {epub_path}: {e}")
        return False

def find_and_extract_book_cover(book_query: str, project_root: Path, output_cover_path: Path) -> Optional[Path]:
    """
    Tự động tìm kiếm file PDF hoặc EPUB trong `output/originals` hoặc `output/` hoặc `processing/`
    theo tên sách, trích xuất ảnh bìa và lưu vào output_cover_path.
    """
    search_dirs = [
        project_root / "output/originals",
        project_root / "output",
        project_root / "processing",
    ]

    # Chuẩn hoá query
    clean_query = book_query.lower().replace("-", " ").replace("_", " ")
    query_tokens = [t for t in clean_query.split() if len(t) > 2 and t not in ["podcast", "short", "tinh", "gon", "audio", "chuyen", "sau"]]

    candidates = []
    for s_dir in search_dirs:
        if not s_dir.exists():
            continue
        for f in s_dir.glob("*.*"):
            if f.suffix.lower() in [".pdf", ".epub"]:
                stem = f.stem.lower().replace("-", " ").replace("_", " ")
                # Đếm số token khớp
                matches = sum(1 for token in query_tokens if token in stem)
                if matches > 0:
                    candidates.append((matches, f))

    if not candidates:
        return None

    # Sắp xếp lấy file khớp nhiều nhất, ưu tiên PDF
    candidates.sort(key=lambda x: (x[0], 1 if x[1].suffix.lower() == '.pdf' else 0), reverse=True)
    best_file = candidates[0][1]
    print(f"Found matching book file: {best_file.name}")

    if best_file.suffix.lower() == ".pdf":
        if extract_cover_from_pdf(best_file, output_cover_path):
            return output_cover_path
    elif best_file.suffix.lower() == ".epub":
        if extract_cover_from_epub(best_file, output_cover_path):
            return output_cover_path

    return None
