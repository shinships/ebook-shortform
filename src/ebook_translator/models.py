"""Data model chung cho moi dinh dang dau vao."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ImageAsset:
    id: str
    filename: str  # ten file noi bo trong EPUB output, vd "images/img_0001.png"
    data: bytes
    media_type: str  # "image/png", "image/jpeg", ...
    is_cover: bool = False


@dataclass
class Chapter:
    id: str  # dinh danh on dinh, dung lam ten file xhtml va cache key
    title: str  # tieu de goc (tieng Anh); co the rong
    html: str  # noi dung body (khong gom <html>/<head>)
    order: int
    title_translated: str = ""


@dataclass
class TocEntry:
    title: str
    chapter_id: str
    anchor: str = ""  # fragment trong chapter, vd "sec-2"
    children: list["TocEntry"] = field(default_factory=list)
    title_translated: str = ""


@dataclass
class Book:
    title: str
    author: str
    language: str
    chapters: list[Chapter] = field(default_factory=list)
    images: list[ImageAsset] = field(default_factory=list)
    toc: list[TocEntry] = field(default_factory=list)
    css: str = ""  # CSS gop (giu tu EPUB goc hoac CSS mac dinh cho PDF)
    title_translated: str = ""

    def iter_toc(self) -> list[TocEntry]:
        """Duyet phang toan bo cay TOC."""
        result: list[TocEntry] = []

        def walk(entries: list[TocEntry]) -> None:
            for e in entries:
                result.append(e)
                walk(e.children)

        walk(self.toc)
        return result
