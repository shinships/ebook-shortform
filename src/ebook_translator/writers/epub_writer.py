"""Lap Book model (da dich) thanh file EPUB3."""

from __future__ import annotations

from ebooklib import epub

from ebook_translator.models import Book, TocEntry

DEFAULT_CSS = """
body { line-height: 1.6; margin: 0 5%; font-family: serif; }
h1, h2, h3 { line-height: 1.3; }
img { max-width: 100%; height: auto; }
p { margin: 0.4em 0; text-indent: 1.2em; }
h1 + p, h2 + p, h3 + p, img + p { text-indent: 0; }
"""


def write_epub(book: Book, output_path: str) -> None:
    eb = epub.EpubBook()
    eb.set_identifier(f"ebook-translator-{abs(hash(book.title)):x}")
    eb.set_title(book.title_translated or book.title)
    eb.set_language("vi")
    if book.author:
        eb.add_author(book.author)
    if book.title_translated and book.title:
        # giu title goc lam metadata phu
        eb.add_metadata("DC", "title", book.title, {"id": "original-title"})

    css_content = book.css or DEFAULT_CSS
    css_item = epub.EpubItem(
        uid="style_main",
        file_name="styles/main.css",
        media_type="text/css",
        content=css_content.encode("utf-8"),
    )
    eb.add_item(css_item)

    cover_img = next((im for im in book.images if im.is_cover), None)
    for img in book.images:
        if img is cover_img:
            continue  # bia xu ly rieng qua set_cover ben duoi
        eb.add_item(
            epub.EpubItem(
                uid=img.id,
                file_name=img.filename,
                media_type=img.media_type,
                content=img.data,
            )
        )

    # Bia: set_cover tao item anh (properties="cover-image") + meta name=cover +
    # trang bia XHTML -> hien thi tin cay tren ca reader EPUB2 lan EPUB3.
    has_cover_page = False
    if cover_img is not None:
        eb.set_cover(cover_img.filename, cover_img.data, create_page=True)
        has_cover_page = True

    chapter_items: dict[str, epub.EpubHtml] = {}
    spine: list = (["cover"] if has_cover_page else []) + ["nav"]
    for chap in book.chapters:
        item = epub.EpubHtml(
            uid=chap.id,
            title=chap.title_translated or chap.title or f"Chương {chap.order}",
            file_name=f"{chap.id}.xhtml",
            lang="vi",
        )
        # ebooklib tu boc fragment thanh XHTML day du khi ghi
        item.content = chap.html
        item.add_item(css_item)
        eb.add_item(item)
        chapter_items[chap.id] = item
        spine.append(item)

    eb.toc = _build_toc(book.toc, chapter_items) or [
        epub.Link(f"{c.id}.xhtml", c.title_translated or c.title or f"Chương {c.order}", c.id)
        for c in book.chapters
    ]
    eb.add_item(epub.EpubNcx())
    eb.add_item(epub.EpubNav())
    eb.spine = spine

    epub.write_epub(output_path, eb)


def _build_toc(entries: list[TocEntry], chapter_items: dict) -> list:
    result = []
    for e in entries:
        if not e.chapter_id or e.chapter_id not in chapter_items:
            children = _build_toc(e.children, chapter_items)
            if children:
                result.extend(children)
            continue
        href = f"{e.chapter_id}.xhtml" + (f"#{e.anchor}" if e.anchor else "")
        title = e.title_translated or e.title
        link = epub.Link(href, title, f"toc_{e.chapter_id}_{e.anchor or 'top'}")
        children = _build_toc(e.children, chapter_items)
        result.append((link, children) if children else link)
    return result
