"""Doc PDF (text that) thanh Book model bang PyMuPDF.

Chia chapter theo outline (bookmark) neu co; khong co thi dung heuristic
font-size; fallback cuoi la chia theo cum trang.
"""

from __future__ import annotations

import html as html_mod
import re
from dataclasses import dataclass

import fitz  # PyMuPDF

from ebook_translator.models import Book, Chapter, ImageAsset, TocEntry

MIN_IMAGE_AREA = 5000  # px^2 — bo icon trang tri
PAGES_PER_FALLBACK_CHAPTER = 12


@dataclass
class _Block:
    kind: str  # "text" | "image"
    y: float
    html: str = ""
    image_index: int = 0


def read_pdf(path: str, scan_texts: dict[int, str] | None = None) -> Book:
    """scan_texts: map page_number (0-based) -> markdown text tu OCR (neu co)."""
    doc = fitz.open(path)
    title = (doc.metadata or {}).get("title") or ""
    author = (doc.metadata or {}).get("author") or ""
    if not title:
        title = re.sub(r"\.pdf$", "", path.replace("\\", "/").rsplit("/", 1)[-1], flags=re.I)

    images: list[ImageAsset] = []
    seen_xrefs: dict[int, str] = {}
    page_blocks: list[list[_Block]] = []
    body_size = _dominant_font_size(doc)

    for pno in range(doc.page_count):
        page = doc[pno]
        blocks: list[_Block] = []
        if scan_texts and pno in scan_texts:
            blocks.extend(_blocks_from_markdown(scan_texts[pno]))
        else:
            blocks.extend(_text_blocks(page, body_size))
        blocks.extend(_image_blocks(doc, page, images, seen_xrefs))
        blocks.sort(key=lambda b: b.y)
        page_blocks.append(blocks)

    outline = doc.get_toc(simple=True)  # [[level, title, page_1based], ...]
    if outline:
        chapters, toc = _chapters_from_outline(outline, page_blocks, doc.page_count)
    else:
        chapters, toc = _chapters_by_heuristic(page_blocks)

    doc.close()
    return Book(
        title=title.strip(),
        author=author.strip(),
        language="en",
        chapters=chapters,
        images=images,
        toc=toc,
    )


def _dominant_font_size(doc: fitz.Document) -> float:
    """Tim co chu pho bien nhat (co chu cua van ban thuong)."""
    sizes: dict[float, int] = {}
    step = max(1, doc.page_count // 20)
    for pno in range(0, doc.page_count, step):
        d = doc[pno].get_text("dict")
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    size = round(span["size"], 1)
                    sizes[size] = sizes.get(size, 0) + len(span.get("text", ""))
    if not sizes:
        return 11.0
    return max(sizes.items(), key=lambda kv: kv[1])[0]


def _text_blocks(page: fitz.Page, body_size: float) -> list[_Block]:
    result: list[_Block] = []
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        lines_text: list[str] = []
        max_size = 0.0
        for line in block.get("lines", []):
            spans = [s.get("text", "") for s in line.get("spans", [])]
            for s in line.get("spans", []):
                max_size = max(max_size, s["size"])
            lines_text.append("".join(spans))
        text = _join_hyphenated(lines_text)
        if not text.strip():
            continue
        tag = "p"
        if max_size >= body_size * 1.6:
            tag = "h1"
        elif max_size >= body_size * 1.25:
            tag = "h2"
        escaped = html_mod.escape(text.strip())
        result.append(
            _Block(kind="text", y=block["bbox"][1], html=f"<{tag}>{escaped}</{tag}>")
        )
    return result


def _join_hyphenated(lines: list[str]) -> str:
    """Noi cac dong trong block; xu ly tu bi cat bang dau gach ngang cuoi dong."""
    out = ""
    for line in lines:
        line = line.rstrip()
        if not line:
            continue
        if out.endswith("-") and line and line[0].islower():
            out = out[:-1] + line
        elif out:
            out += " " + line
        else:
            out = line
    return out


def _blocks_from_markdown(md: str) -> list[_Block]:
    """Chuyen text markdown tu OCR thanh block HTML, giu thu tu bang y gia."""
    blocks: list[_Block] = []
    y = 0.0
    for para in re.split(r"\n\s*\n", md):
        para = para.strip()
        if not para:
            continue
        m = re.match(r"^(#{1,3})\s+(.*)", para)
        if m:
            level = len(m.group(1))
            content = html_mod.escape(m.group(2).strip())
            tag = f"h{level}"
        else:
            content = html_mod.escape(re.sub(r"\s*\n\s*", " ", para))
            tag = "p"
        blocks.append(_Block(kind="text", y=y, html=f"<{tag}>{content}</{tag}>"))
        y += 1.0
    return blocks


def _image_blocks(
    doc: fitz.Document,
    page: fitz.Page,
    images: list[ImageAsset],
    seen_xrefs: dict[int, str],
) -> list[_Block]:
    result: list[_Block] = []
    for info in page.get_image_info(xrefs=True):
        xref = info.get("xref", 0)
        if not xref:
            continue
        bbox = info["bbox"]
        area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
        if area < MIN_IMAGE_AREA:
            continue
        if xref in seen_xrefs:
            filename = seen_xrefs[xref]
        else:
            try:
                extracted = doc.extract_image(xref)
            except Exception:
                continue
            ext = extracted["ext"]
            n = len(images) + 1
            filename = f"images/img_{n:04d}.{ext}"
            images.append(
                ImageAsset(
                    id=f"img_{n:04d}",
                    filename=filename,
                    data=extracted["image"],
                    media_type=f"image/{'jpeg' if ext == 'jpg' else ext}",
                    is_cover=(page.number == 0 and area > page.rect.get_area() * 0.5),
                )
            )
            seen_xrefs[xref] = filename
        result.append(
            _Block(
                kind="image",
                y=bbox[1],
                html=f'<p class="image"><img src="{filename}" alt=""/></p>',
            )
        )
    return result


def _chapters_from_outline(
    outline: list, page_blocks: list[list[_Block]], page_count: int
) -> tuple[list[Chapter], list[TocEntry]]:
    """Chia chapter theo bookmark cap 1; cap sau giu lam TOC con."""
    top = [(lvl, t, max(0, p - 1)) for lvl, t, p in outline if lvl == 1]
    if not top:
        top = [(1, t, max(0, p - 1)) for lvl, t, p in outline[:1]]

    chapters: list[Chapter] = []
    toc: list[TocEntry] = []
    # noi dung truoc chapter dau tien (bia, loi noi dau khong bookmark)
    first_start = top[0][2]
    order = 0
    if first_start > 0:
        order += 1
        html = _render_pages(page_blocks, 0, first_start)
        if html.strip():
            chapters.append(Chapter(id=f"chap_{order:04d}", title="", html=html, order=order))

    for idx, (_lvl, title, start) in enumerate(top):
        end = top[idx + 1][2] if idx + 1 < len(top) else page_count
        order += 1
        chap_id = f"chap_{order:04d}"
        html = _render_pages(page_blocks, start, max(end, start + 1))
        chapters.append(Chapter(id=chap_id, title=title.strip(), html=html, order=order))
        entry = TocEntry(title=title.strip(), chapter_id=chap_id)
        # bookmark cap 2 nam trong khoang trang nay -> TOC con (khong co anchor rieng)
        for lvl2, t2, p2 in outline:
            if lvl2 == 2 and start <= max(0, p2 - 1) < end:
                entry.children.append(TocEntry(title=t2.strip(), chapter_id=chap_id))
        toc.append(entry)
    return chapters, toc


def _chapters_by_heuristic(
    page_blocks: list[list[_Block]],
) -> tuple[list[Chapter], list[TocEntry]]:
    """Khong co outline: cat chapter tai moi <h1>; fallback chia theo cum trang."""
    # gom toan bo block, danh dau vi tri h1
    flat: list[_Block] = [b for blocks in page_blocks for b in blocks]
    h1_positions = [i for i, b in enumerate(flat) if b.html.startswith("<h1>")]

    chapters: list[Chapter] = []
    toc: list[TocEntry] = []

    if len(h1_positions) >= 2:
        boundaries = h1_positions if h1_positions[0] == 0 else [0] + h1_positions
        for order, start in enumerate(boundaries, start=1):
            next_idx = boundaries.index(start) + 1
            end = boundaries[next_idx] if next_idx < len(boundaries) else len(flat)
            part = flat[start:end]
            chap_id = f"chap_{order:04d}"
            title = ""
            if part and part[0].html.startswith("<h1>"):
                title = re.sub(r"<[^>]+>", "", part[0].html).strip()
            chapters.append(
                Chapter(
                    id=chap_id,
                    title=title,
                    html="".join(b.html for b in part),
                    order=order,
                )
            )
            if title:
                toc.append(TocEntry(title=title, chapter_id=chap_id))
        return chapters, toc

    # fallback: cum trang
    order = 0
    for start in range(0, len(page_blocks), PAGES_PER_FALLBACK_CHAPTER):
        order += 1
        html = _render_pages(
            page_blocks, start, min(start + PAGES_PER_FALLBACK_CHAPTER, len(page_blocks))
        )
        if html.strip():
            chapters.append(
                Chapter(id=f"chap_{order:04d}", title="", html=html, order=order)
            )
    return chapters, toc


def _render_pages(page_blocks: list[list[_Block]], start: int, end: int) -> str:
    return "".join(
        b.html for blocks in page_blocks[start:end] for b in blocks
    )
