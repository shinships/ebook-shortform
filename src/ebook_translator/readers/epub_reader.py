"""Doc EPUB thanh Book model."""

from __future__ import annotations

import posixpath
import warnings

import ebooklib
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from ebooklib import epub

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from ebook_translator.models import Book, Chapter, ImageAsset, TocEntry


def read_epub(path: str) -> Book:
    eb = epub.read_epub(path, options={"ignore_ncx": False})

    title = _first_meta(eb, "title") or "Untitled"
    author = _first_meta(eb, "creator") or ""
    language = _first_meta(eb, "language") or "en"

    images, href_to_filename = _extract_images(eb)
    css = _extract_css(eb)
    chapters, href_to_chapter_id = _extract_chapters(eb, href_to_filename)
    toc = _extract_toc(eb.toc, href_to_chapter_id)

    return Book(
        title=title,
        author=author,
        language=language,
        chapters=chapters,
        images=images,
        toc=toc,
        css=css,
    )


def _first_meta(eb: epub.EpubBook, name: str) -> str:
    values = eb.get_metadata("DC", name)
    return values[0][0].strip() if values else ""


def _cover_id_from_meta(eb: epub.EpubBook) -> str | None:
    """ID cua anh bia lay tu OPF metadata.

    EPUB2 danh dau bia bang <meta name="cover" content="<id>"/>. ebooklib KHONG
    tra qua get_metadata("OPF", "cover") ma gom moi the <meta> vao key "meta",
    nen phai loc theo attribute name. (get_metadata("OPF","cover") chi bat dang
    <meta property="cover"> hiem gap — van thu de chac an.)
    """
    for _value, attrs in eb.get_metadata("OPF", "meta") or []:
        if attrs.get("name") == "cover" and attrs.get("content"):
            return attrs["content"]
    for _value, attrs in eb.get_metadata("OPF", "cover") or []:
        if attrs.get("content"):
            return attrs["content"]
    return None


def _extract_images(eb: epub.EpubBook) -> tuple[list[ImageAsset], dict[str, str]]:
    """Lay toan bo anh; tra ve map href-goc -> ten file moi trong output."""
    images: list[ImageAsset] = []
    href_map: dict[str, str] = {}
    cover_id = _cover_id_from_meta(eb)

    for i, item in enumerate(eb.get_items_of_type(ebooklib.ITEM_IMAGE), start=1):
        ext = posixpath.splitext(item.get_name())[1] or ".png"
        filename = f"images/img_{i:04d}{ext}"
        item_id = item.get_id() or ""
        is_cover = (
            item_id == cover_id
            or "cover" in item.get_name().lower()
            or "cover" in item_id.lower()
        )
        images.append(
            ImageAsset(
                id=f"img_{i:04d}",
                filename=filename,
                data=item.get_content(),
                media_type=item.media_type,
                is_cover=is_cover,
            )
        )
        href_map[item.get_name()] = filename
    # EPUB3 co the danh dau cover bang properties
    for item in eb.get_items():
        if "cover-image" in (getattr(item, "properties", None) or []):
            for img in images:
                if href_map.get(item.get_name()) == img.filename:
                    img.is_cover = True
    # ebooklib tach anh bia (EPUB3 cover-image / OPF meta cover) thanh loai
    # ITEM_COVER rieng, KHONG nam trong ITEM_IMAGE -> phai lay rieng, neu khong
    # bia goc bi mat. Chi them neu chua co anh bia nao duoc danh dau o tren.
    if not any(img.is_cover for img in images):
        n = len(images)
        for item in eb.get_items_of_type(ebooklib.ITEM_COVER):
            if href_map.get(item.get_name()):
                continue  # da co trong danh sach
            n += 1
            ext = posixpath.splitext(item.get_name())[1] or ".jpg"
            filename = f"images/img_{n:04d}{ext}"
            images.append(
                ImageAsset(
                    id=f"img_{n:04d}",
                    filename=filename,
                    data=item.get_content(),
                    media_type=item.media_type or "image/jpeg",
                    is_cover=True,
                )
            )
            href_map[item.get_name()] = filename
    return images, href_map


def _extract_css(eb: epub.EpubBook) -> str:
    parts = []
    for item in eb.get_items_of_type(ebooklib.ITEM_STYLE):
        try:
            parts.append(item.get_content().decode("utf-8", errors="replace"))
        except Exception:
            continue
    return "\n\n".join(parts)


def _extract_chapters(
    eb: epub.EpubBook, href_to_filename: dict[str, str]
) -> tuple[list[Chapter], dict[str, str]]:
    """Duyet spine theo thu tu doc, moi document la mot chapter."""
    chapters: list[Chapter] = []
    href_to_chapter_id: dict[str, str] = {}

    docs_by_id = {
        item.get_id(): item
        for item in eb.get_items_of_type(ebooklib.ITEM_DOCUMENT)
    }
    order = 0
    for spine_id, _linear in eb.spine:
        item = docs_by_id.get(spine_id)
        if item is None:
            continue
        # Bo qua trang nav va trang bia (se tu sinh lai o output): neu khong bo,
        # doc lai mot file da xuat se an trang bia thanh mot "chuong" rong.
        props = getattr(item, "properties", None) or []
        name = item.get_name().lower()
        if (
            "nav" in props
            or spine_id == "nav"
            or name.endswith("nav.xhtml")
            or spine_id == "cover"
            or name.endswith("cover.xhtml")
            or "cover-image" in props
        ):
            continue
        order += 1
        chapter_id = f"chap_{order:04d}"
        soup = BeautifulSoup(item.get_content(), "lxml")
        body = soup.body or soup
        _rewrite_image_srcs(body, item.get_name(), href_to_filename)
        title = _guess_title(body)
        html = body.decode_contents() if soup.body else str(soup)
        chapters.append(Chapter(id=chapter_id, title=title, html=html, order=order))
        href_to_chapter_id[item.get_name()] = chapter_id
    return chapters, href_to_chapter_id


def _rewrite_image_srcs(body, doc_href: str, href_to_filename: dict[str, str]) -> None:
    """Doi src anh (duong dan tuong doi trong EPUB goc) sang ten file output."""
    base = posixpath.dirname(doc_href)
    for tag in body.find_all(["img", "image"]):
        attr = "src" if tag.name == "img" else "xlink:href"
        src = tag.get(attr) or tag.get("href")
        if not src:
            continue
        resolved = posixpath.normpath(posixpath.join(base, src))
        new = href_to_filename.get(resolved) or href_to_filename.get(src)
        if new:
            if tag.name == "img":
                tag["src"] = new
            else:
                # chuyen svg <image> thanh <img> don gian cho de xu ly
                tag.name = "img"
                tag.attrs = {"src": new}


def _guess_title(body) -> str:
    for name in ("h1", "h2", "h3"):
        tag = body.find(name)
        if tag:
            text = tag.get_text(" ", strip=True)
            if text:
                return text
    return ""


def _extract_toc(toc_items, href_to_chapter_id: dict[str, str]) -> list[TocEntry]:
    entries: list[TocEntry] = []
    for item in toc_items or []:
        if isinstance(item, tuple):  # (Section/Link, children)
            node, children = item
            entry = _toc_entry_from(node, href_to_chapter_id)
            if entry is not None:
                entry.children = _extract_toc(children, href_to_chapter_id)
                entries.append(entry)
            else:
                entries.extend(_extract_toc(children, href_to_chapter_id))
        else:
            entry = _toc_entry_from(item, href_to_chapter_id)
            if entry is not None:
                entries.append(entry)
    return entries


def _toc_entry_from(node, href_to_chapter_id: dict[str, str]) -> TocEntry | None:
    title = getattr(node, "title", "") or ""
    href = getattr(node, "href", "") or ""
    if not title:
        return None
    file_part, _, anchor = href.partition("#")
    chapter_id = href_to_chapter_id.get(file_part, "")
    return TocEntry(title=title.strip(), chapter_id=chapter_id, anchor=anchor)
