#!/usr/bin/env python3
"""Chuyen doi file EPUB tom tat Shortform sang file Markdown (.md) chuan."""

from __future__ import annotations

import argparse
from pathlib import Path
from bs4 import BeautifulSoup, NavigableString, Tag

from ebook_translator.readers.epub_reader import read_epub


def element_to_md(el) -> str:
    if isinstance(el, NavigableString):
        return str(el)
    if not isinstance(el, Tag):
        return ""

    tag = el.name
    inner = "".join(element_to_md(c) for c in el.children)

    if tag == "strong":
        return f"**{inner}**"
    elif tag == "em":
        return f"*{inner}*"
    elif tag == "p":
        cls = el.get("class", [])
        if "reading-time" in cls:
            return f"> ⏱️ *{inner.strip()}*\n\n"
        return f"{inner.strip()}\n\n"
    elif tag == "h1":
        return f"# {inner.strip()}\n\n"
    elif tag == "h2":
        return f"## {inner.strip()}\n\n"
    elif tag == "h3":
        return f"### {inner.strip()}\n\n"
    elif tag == "ul":
        items = []
        for li in el.find_all("li", recursive=False):
            li_text = "".join(element_to_md(c) for c in li.children).strip()
            items.append(f"- {li_text}")
        return "\n".join(items) + "\n\n"
    elif tag == "ol":
        items = []
        for i, li in enumerate(el.find_all("li", recursive=False), 1):
            li_text = "".join(element_to_md(c) for c in li.children).strip()
            items.append(f"{i}. {li_text}")
        return "\n".join(items) + "\n\n"
    elif tag == "div":
        cls = el.get("class", [])
        if "lesson-intro" in cls:
            lines = [l.strip() for l in inner.split("\n\n") if l.strip()]
            return "> 📖 **Tổng quan bài học:**\n" + "\n>\n".join(f"> {l}" for l in lines) + "\n\n"
        elif "commentary-box" in cls:
            lines = [l.strip() for l in inner.split("\n\n") if l.strip()]
            return "> [!TIP]\n> **GÓC NHÌN MỞ RỘNG (SHORTFORM COMMENTARY)**\n" + "\n>\n".join(f"> {l}" for l in lines) + "\n\n"
        elif "assumptions-box" in cls:
            lines = [l.strip() for l in inner.split("\n\n") if l.strip()]
            return "> [!WARNING]\n> **GIỚI HẠN & PHẢN BIỆN (ASSUMPTIONS & LIMITS)**\n" + "\n>\n".join(f"> {l}" for l in lines) + "\n\n"
        elif "insights-box" in cls:
            lines = [l.strip() for l in inner.split("\n\n") if l.strip()]
            return "> [!NOTE]\n> **ĐIỂM CỐT LÕI (KEY INSIGHTS)**\n" + "\n>\n".join(f"> {l}" for l in lines) + "\n\n"
        elif "exercise-box" in cls or "action-box" in cls:
            lines = [l.strip() for l in inner.split("\n\n") if l.strip()]
            return "> [!IMPORTANT]\n> **BÀI TẬP TỰ VẤN & HÀNH ĐỘNG THỰC TIỄN**\n" + "\n>\n".join(f"> {l}" for l in lines) + "\n\n"
        else:
            return inner + "\n\n"
    elif tag == "blockquote":
        lines = [l.strip() for l in inner.split("\n\n") if l.strip()]
        return "\n>\n".join(f"> {l}" for l in lines) + "\n\n"
    return inner


def convert_epub_to_md(epub_path: Path, output_md: Path) -> None:
    book = read_epub(epub_path)
    lines = [
        f"# {book.title}",
        f"**Tác giả:** {book.author or 'Jason Fried & David Heinemeier Hansson'}",
        f"**Phương pháp biên soạn:** Chuẩn Microlearning Shortform (Việt hóa chuyên sâu)",
        "",
        "---",
        "",
    ]

    for chap in book.chapters:
        soup = BeautifulSoup(chap.html, "html.parser")
        chap_md = "".join(element_to_md(c) for c in soup.children).strip()
        lines.append(chap_md)
        lines.append("\n\n---\n\n")

    output_md.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    print(f"✅ Đã xuất Markdown: {output_md} ({output_md.stat().st_size:,} bytes)")


def main():
    parser = argparse.ArgumentParser(description="Chuyển đổi EPUB tóm tắt sang Markdown")
    parser.add_argument("epub", help="Đường dẫn file EPUB")
    parser.add_argument("-o", "--output", help="Đường dẫn file .md đầu ra")
    args = parser.parse_args()

    epub_path = Path(args.epub)
    output_md = Path(args.output) if args.output else epub_path.with_suffix(".md")
    convert_epub_to_md(epub_path, output_md)


if __name__ == "__main__":
    main()
