#!/usr/bin/env python3
"""Dong goi ban dai tong hop Bo Tu Jason Fried thanh file EPUB3 chuan dep."""

from __future__ import annotations

import html
import os
import re
import zipfile
from pathlib import Path

from ebook_translator.models import Book, Chapter, ImageAsset, TocEntry
from ebook_translator.writers.epub_writer import DEFAULT_CSS, write_epub

PROJECT_DIR = Path(__file__).resolve().parent.parent
BASE_NAME = "Jason Fried"
MD_FILE = PROJECT_DIR / f"{BASE_NAME}_short.md"
OUTPUT_EPUB = PROJECT_DIR / "output" / f"{BASE_NAME}_short.epub"

ENHANCED_CSS = DEFAULT_CSS + """
/* Tinh chinh giao dien Shortform */
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Georgia, serif;
    line-height: 1.75;
    color: #24292e;
    margin: 0 6%;
}

h1 {
    font-size: 1.8em;
    color: #1a365d;
    border-bottom: 2px solid #3182ce;
    padding-bottom: 0.3em;
    margin-top: 1em;
}

h2 {
    font-size: 1.4em;
    color: #2b6cb0;
    margin-top: 1.4em;
    border-bottom: 1px solid #e2e8f0;
    padding-bottom: 0.2em;
}

h3 {
    font-size: 1.2em;
    color: #2c5282;
    margin-top: 1.2em;
}

h4 {
    font-size: 1.05em;
    color: #4a5568;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

blockquote {
    border-left: 4px solid #3182ce;
    background-color: #ebf8ff;
    padding: 0.8em 1.2em;
    margin: 1.2em 0;
    border-radius: 0 8px 8px 0;
    color: #2c5282;
    font-size: 0.95em;
}

blockquote p {
    text-indent: 0;
    margin: 0.4em 0;
}

pre {
    background-color: #2d3748;
    color: #edf2f7;
    padding: 1em;
    border-radius: 6px;
    overflow-x: auto;
    font-size: 0.85em;
    line-height: 1.45;
}

code {
    background-color: #edf2f7;
    color: #c53030;
    padding: 0.2em 0.4em;
    border-radius: 3px;
    font-family: SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace;
    font-size: 0.9em;
}

pre code {
    background: none;
    color: inherit;
    padding: 0;
}

table {
    border-collapse: collapse;
    width: 100%;
    margin: 1.5em 0;
    font-size: 0.9em;
}

th, td {
    border: 1px solid #cbd5e0;
    padding: 0.6em 0.8em;
    text-align: left;
    vertical-align: top;
}

th {
    background-color: #edf2f7;
    font-weight: bold;
    color: #2d3748;
}

tr:nth-child(even) {
    background-color: #f7fafc;
}

ul, ol {
    margin: 0.8em 0 0.8em 1.5em;
    padding-left: 0.5em;
}

li {
    margin-bottom: 0.4em;
}

strong {
    color: #1a202c;
}

hr {
    border: none;
    border-top: 1px solid #e2e8f0;
    margin: 2em 0;
}
"""


def md_to_html(md_text: str) -> str:
    """Chuyen doi markdown co ban sang XHTML hop le cho EPUB."""
    lines = md_text.splitlines()
    html_out = []
    in_code = False
    code_lines = []
    in_ul = False
    in_ol = False
    in_table = False
    table_lines = []
    in_quote = False
    quote_lines = []

    def flush_list():
        nonlocal in_ul, in_ol
        if in_ul:
            html_out.append("</ul>")
            in_ul = False
        if in_ol:
            html_out.append("</ol>")
            in_ol = False

    def flush_table():
        nonlocal in_table, table_lines
        if not in_table or not table_lines:
            return
        html_out.append("<table>")
        is_first = True
        for line in table_lines:
            if re.match(r"^\s*\|?\s*[-:]+[-| :]*$", line):
                continue  # bo qua separator |:---|:---|
            cols = [c.strip() for c in line.strip().strip("|").split("|")]
            if is_first:
                html_out.append("<thead><tr>")
                for c in cols:
                    html_out.append(f"<th>{format_inline(c)}</th>")
                html_out.append("</tr></thead><tbody>")
                is_first = False
            else:
                html_out.append("<tr>")
                for c in cols:
                    html_out.append(f"<td>{format_inline(c)}</td>")
                html_out.append("</tr>")
        if not is_first:
            html_out.append("</tbody>")
        html_out.append("</table>")
        in_table = False
        table_lines = []

    def flush_quote():
        nonlocal in_quote, quote_lines
        if not in_quote or not quote_lines:
            return
        html_out.append("<blockquote>")
        q_text = "\n".join(quote_lines)
        html_out.append(md_to_html(q_text))
        html_out.append("</blockquote>")
        in_quote = False
        quote_lines = []

    def format_inline(text: str) -> str:
        # escapes
        text = html.escape(text)
        # bold
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        # italic
        text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
        # code inline
        text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
        return text

    i = 0
    while i < len(lines):
        line = lines[i]

        # Code block
        if line.strip().startswith("```"):
            if in_code:
                # end code
                c_content = html.escape("\n".join(code_lines))
                html_out.append(f"<pre><code>{c_content}</code></pre>")
                code_lines = []
                in_code = False
            else:
                flush_list()
                flush_table()
                flush_quote()
                in_code = True
            i += 1
            continue

        if in_code:
            code_lines.append(line)
            i += 1
            continue

        # Blockquote
        if line.startswith(">"):
            flush_list()
            flush_table()
            in_quote = True
            quote_lines.append(re.sub(r"^>\s?", "", line))
            i += 1
            continue
        elif in_quote:
            flush_quote()

        # Table
        if "|" in line and (line.strip().startswith("|") or line.strip().endswith("|")):
            flush_list()
            in_table = True
            table_lines.append(line)
            i += 1
            continue
        elif in_table:
            flush_table()

        # Lists
        ul_match = re.match(r"^(\s*)[*\-]\s+(.*)$", line)
        ol_match = re.match(r"^(\s*)\d+\.\s+(.*)$", line)

        if ul_match:
            if not in_ul:
                flush_list()
                html_out.append("<ul>")
                in_ul = True
            html_out.append(f"<li>{format_inline(ul_match.group(2))}</li>")
            i += 1
            continue
        elif ol_match:
            if not in_ol:
                flush_list()
                html_out.append("<ol>")
                in_ol = True
            html_out.append(f"<li>{format_inline(ol_match.group(2))}</li>")
            i += 1
            continue
        else:
            flush_list()

        # Headings
        if line.startswith("# "):
            html_out.append(f"<h1>{format_inline(line[2:])}</h1>")
        elif line.startswith("## "):
            html_out.append(f"<h2>{format_inline(line[3:])}</h2>")
        elif line.startswith("### "):
            html_out.append(f"<h3>{format_inline(line[4:])}</h3>")
        elif line.startswith("#### "):
            html_out.append(f"<h4>{format_inline(line[5:])}</h4>")
        elif line.strip() == "---":
            html_out.append("<hr/>")
        elif not line.strip():
            pass
        else:
            html_out.append(f"<p>{format_inline(line)}</p>")

        i += 1

    flush_list()
    flush_table()
    flush_quote()
    if in_code:
        c_content = html.escape("\n".join(code_lines))
        html_out.append(f"<pre><code>{c_content}</code></pre>")

    return "\n".join(html_out)


def main():
    if not MD_FILE.exists():
        print(f"Khong tim thay {MD_FILE}")
        return

    content = MD_FILE.read_text(encoding="utf-8")

    # Lay cover tu Rework hoac Crazy neu co
    cover_data = None
    crazy_epub = Path("/Users/mktmda/Downloads/Jason Fried/It_Doesnt_Have_to_Be_Crazy_at_Work.epub")
    if crazy_epub.exists():
        with zipfile.ZipFile(crazy_epub, "r") as z:
            for name in z.namelist():
                if "cover" in name.lower() and any(name.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png"]):
                    cover_data = z.read(name)
                    print(f"Da trich xuat anh bia tu {crazy_epub.name} ({len(cover_data)} bytes)")
                    break

    images = []
    if cover_data:
        images.append(ImageAsset(
            id="cover_img",
            filename="images/cover.jpeg",
            media_type="image/jpeg",
            data=cover_data,
            is_cover=True,
        ))

    # Phan tach markdown thanh cac chuong lon
    # Chuong 1: Tong quan & 1-Page Summary
    # Chuong 2: Tru cot 1: Getting Real
    # Chuong 3: Tru cot 2: Rework
    # Chuong 4: Tru cot 3: Remote
    # Chuong 5: Tru cot 4: Crazy at Work
    # Chuong 6: Khung chuyen hoa hanh dong & Ban do tien hoa
    
    sections = [
        ("chuong_0", "Tổng Quan & Luận Đề Cốt Lõi", r"(# BỘ TỨ TRIẾT LÝ.*?(?=### TRỤ CỘT 1))"),
        ("chuong_1", "Trụ Cột 1: Kiến Trúc Sản Phẩm Thực Chiến (Getting Real)", r"(### TRỤ CỘT 1: KIẾN TRÚC SẢN PHẨM THỰC CHIẾN.*?(?=### TRỤ CỘT 2))"),
        ("chuong_2", "Trụ Cột 2: Phản Khởi Nghiệp & Vận Hành Thực Chất (Rework)", r"(### TRỤ CỘT 2: PHẢN KHỞI NGHIỆP.*?(?=### TRỤ CỘT 3))"),
        ("chuong_3", "Trụ Cột 3: Giải Phóng Không Gian & Vận Hành Bất Đồng Bộ (Remote)", r"(### TRỤ CỘT 3: GIẢI PHÓNG KHÔNG GIAN.*?(?=### TRỤ CỘT 4))"),
        ("chuong_4", "Trụ Cột 4: Doanh Nghiệp Điềm Tĩnh & Năng Lượng Con Người (Crazy at Work)", r"(### TRỤ CỘT 4: DOANH NGHIỆP ĐIỀM TĨNH.*?(?=## 3\. KHUNG CHUYỂN HÓA HÀNH ĐỘNG))"),
        ("chuong_5", "Khung Chuyển Hóa Hành Động & Bản Đồ Tiến Hóa", r"(## 3\. KHUNG CHUYỂN HÓA HÀNH ĐỘNG.*)"),
    ]

    chapters = []
    toc_entries = []

    for order, (c_id, c_title, pattern) in enumerate(sections, 1):
        m = re.search(pattern, content, re.DOTALL)
        if m:
            sec_md = m.group(1).strip()
        else:
            sec_md = f"# {c_title}\n\nNội dung đang được cập nhật."

        sec_html = md_to_html(sec_md)
        chap = Chapter(
            id=c_id,
            title=c_title,
            order=order,
            html=sec_html,
            title_translated=c_title,
        )
        chapters.append(chap)
        toc_entries.append(TocEntry(
            title=c_title,
            chapter_id=c_id,
            title_translated=c_title,
        ))

    book = Book(
        title="Bộ Tứ Triết Lý 37signals / Basecamp: Getting Real - Rework - Remote - It Doesn't Have to Be Crazy at Work",
        author="Jason Fried & David Heinemeier Hansson (DHH)",
        language="vi",
        title_translated="Bộ Tứ Triết Lý 37signals / Basecamp: Doanh Nghiệp Điềm Tĩnh & Tinh Gọn (Tóm Tắt Shortform)",
        chapters=chapters,
        toc=toc_entries,
        images=images,
        css=ENHANCED_CSS,
    )

    OUTPUT_EPUB.parent.mkdir(parents=True, exist_ok=True)
    write_epub(book, str(OUTPUT_EPUB))
    print(f"✅ Đã tạo thành công file EPUB: {OUTPUT_EPUB} ({OUTPUT_EPUB.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
