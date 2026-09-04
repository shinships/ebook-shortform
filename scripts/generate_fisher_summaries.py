#!/usr/bin/env python3
"""Pipeline tạo file EPUB tóm tắt chuyên sâu chuẩn Shortform cho 2 tác phẩm của Philip Fisher:
1. Common Stocks and Uncommon Profits and Other Writings
2. Paths to Wealth Through Common Stocks
3. Tuyển tập Master Omnibus kết hợp cả 2 tác phẩm.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF
from bs4 import BeautifulSoup

from ebook_translator.core.llm import LLMClient
from ebook_translator.models import Book, Chapter, ImageAsset, TocEntry
from ebook_translator.writers.epub_writer import DEFAULT_CSS, write_epub

WORKDIR = Path("workdir_fisher")
OUTPUT_DIR = Path("output")
FISHER_DIR = Path("/Users/mktmda/Documents/Fisher")
WORKDIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

WORDS_MIN, WORDS_MAX = 2400, 4000
READ_WPM = 160
ALLOWED_TAGS = {"p", "ul", "ol", "li", "strong", "em", "blockquote"}

LESSON_CSS = """
.reading-time { color: #666; font-size: 0.9em; text-indent: 0; margin-top: -0.4em; }
.lesson-intro p { text-indent: 0; }
.insights-box, .action-box, .exercise-box, .commentary-box, .assumptions-box {
  border-left: 4px solid #1a5276; background: #f4f8fb;
  padding: 0.8em 1.2em; margin: 1.4em 0; border-radius: 0 6px 6px 0;
}
.action-box, .exercise-box { border-left-color: #1e8449; background: #f0f9f4; }
.commentary-box { border-left-color: #b7950b; background: #fefcf3; font-style: italic; }
.commentary-box strong, .commentary-box em { font-style: normal; }
.assumptions-box { border-left-color: #922b21; background: #fdf2f2; }
.insights-box p, .action-box p, .exercise-box p, .commentary-box p, .assumptions-box p {
  text-indent: 0; margin: 0.4em 0;
}
.box-title { font-weight: bold; font-size: 1.1em; margin-bottom: 0.5em; font-style: normal; }
.insights-box ul, .exercise-box ol { margin: 0.3em 0 0.3em 1.2em; padding-left: 0.5em; }
.insights-box li, .exercise-box li { margin: 0.4em 0; }
.exercise-box .action-lead { margin-top: 0.8em; }
.recap-ref h3 { font-size: 1.05em; margin-bottom: 0.3em; }
"""

FULL_CSS = DEFAULT_CSS + "\n" + LESSON_CSS

LESSON_SYSTEM_TEMPLATE = """\
You are an expert writer of in-depth Vietnamese book guides in the prestigious Shortform microlearning style. \
Your guides go far beyond superficial summaries: for every major investment concept you answer:
1. WHY it matters (the fundamental economic/market reasoning),
2. HOW it works (the granular business mechanisms, operating metrics, and actual historical case studies from the book),
3. HOW the investor applies it practically.

Book: "{book_title}" by Philip A. Fisher.
Target Audience: Nhà đầu tư chứng khoán, chuyên viên phân tích tài chính, và độc giả muốn làm chủ nghệ thuật đầu tư tăng trưởng (Growth Investing) dài hạn.

WRITING GUIDELINES:
- Natural, highly articulate, intellectual Vietnamese financial prose. Address reader as "bạn".
- Comprehension-first: detail the author's logic, causal reasoning, and real historical case studies (e.g. Motorola, Dow Chemical, Texas Instruments, Campbell Soup, Corning Glass...).
- In main content, stay 100% faithful to Philip Fisher's ideas and data.
- In the "commentary" field: actively connect and contrast Fisher's philosophy with other titans: Warren Buffett ("85% Graham & 15% Fisher"), Charlie Munger (focus on moat and quality over deep value cigar-butts), Peter Lynch (Ten-baggers & PEG), Benjamin Graham, Howard Marks, and modern practical implications for today's market and Vietnam stock market (VN-Index).
- Ensure high depth, clarity, and practical wisdom.
"""

LESSON_PROMPT_TEMPLATE = """\
Write lesson {index}/{total} of the in-depth guide for "{book_title}".
Topic: {lesson_title}
Book section covered: {section_scope}

SOURCE MATERIAL FROM BOOK:
{source_text}

Return ONLY valid JSON (no markdown fence) with EXACTLY this JSON structure:
{{
  "title": "{lesson_title}",
  "intro": "<1-2 paragraphs <p> HTML: Bối cảnh phần này và VÌ SAO nội dung này mang tính quyết định đối với thành bại của nhà đầu tư>",
  "sections": [
    {{
      "heading": "<Tiêu đề mục con tiếng Việt>",
      "reasoning": "<2-3 câu văn xuôi tiếng Việt (KHÔNG thẻ HTML) tái hiện mạch lập luận logic của Fisher: vì sao đúng, xuất phát từ đâu>",
      "html": "<3-6 đoạn <p> HTML: phân tích chi tiết cơ chế hoạt động, số liệu, case study thực tế từ sách; dùng <strong>, <em>, <ul>, <li> nếu cần>",
      "commentary": "<1-2 đoạn <p> HTML: Hộp Góc nhìn thêm - đối chiếu với Warren Buffett, Charlie Munger, Peter Lynch, Benjamin Graham hoặc TTCK hiện đại/Việt Nam>"
    }}
  ],
  "key_insights": [
    "<Điểm cốt lõi 1, súc tích, đắt giá>",
    "<Điểm cốt lõi 2>",
    "<Điểm cốt lõi 3>",
    "<Điểm cốt lõi 4>"
  ],
  "assumptions_limits": "<1 đoạn <p> HTML: Giả định ngầm và những trường hợp lời khuyên này có giới hạn hoặc cần thận trọng>",
  "exercises": [
    "<Câu hỏi tự vấn 1 giúp rà soát danh mục hiện tại>",
    "<Câu hỏi tự vấn 2>",
    "<Câu hỏi tự vấn 3>"
  ],
  "action": "<1 đoạn <p> HTML: Một hành động cụ thể, thiết thực nhà đầu tư có thể áp dụng ngay lập tức>"
}}

CONSTRAINTS:
- 3 to 5 sections in "sections".
- 4 to 6 insights in "key_insights".
- 2 to 3 questions in "exercises".
- Rich, in-depth explanation across all fields totaling around 2,200 to 3,500 Vietnamese words.
- Allowed HTML tags ONLY: p, ul, ol, li, strong, em, blockquote.
"""


def extract_pages_text(pdf_path: str, start_p1: int, end_p1: int) -> str:
    """Trích xuất văn bản từ trang start_p1 đến end_p1 (1-based, inclusive)."""
    doc = fitz.open(pdf_path)
    chunks = []
    for pno in range(start_p1 - 1, min(end_p1, len(doc))):
        page = doc[pno]
        text = page.get_text()
        # Clean basic header/footer artifacts
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        # Skip pure page number lines
        filtered = [l for l in lines if not re.match(r"^\d+$", l)]
        chunks.append("\n".join(filtered))
    return "\n\n".join(chunks)


def sanitize_html(html_str: str) -> str:
    soup = BeautifulSoup(f"<div>{html_str}</div>", "lxml")
    for bad in soup.find_all(["script", "style"]):
        bad.decompose()
    for tag in soup.div.find_all(True):
        if tag.name not in ALLOWED_TAGS:
            tag.unwrap()
    return soup.div.decode_contents()


def plain_text(html_str: str) -> str:
    soup = BeautifulSoup(html_str, "lxml")
    return soup.get_text(separator=" ").strip()


def word_count(text: str) -> int:
    return len(text.split())


def render_lesson_html(data: dict, index: int, total: int, source_scope: str) -> str:
    all_text = " ".join([
        data["intro"],
        data["action"],
        data.get("assumptions_limits", ""),
        *data.get("key_insights", []),
        *data.get("exercises", []),
    ])
    for s in data.get("sections", []):
        all_text += f" {s.get('heading', '')} {s.get('reasoning', '')} {s.get('html', '')} {s.get('commentary', '')}"
    words = word_count(plain_text(all_text))
    minutes = max(1, round(words / READ_WPM))

    parts = [
        f"<h1>{data['title']}</h1>",
        f'<p class="reading-time">≈ {minutes} phút đọc · Bài {index}/{total} · {source_scope}</p>',
        f'<div class="lesson-intro">{sanitize_html(data["intro"])}</div>',
    ]

    for sec in data.get("sections", []):
        parts.append(f"<h2>{sec['heading']}</h2>")
        reasoning = sec.get("reasoning", "").strip()
        if reasoning:
            parts.append(f"<p><em>{reasoning}</em></p>")
        parts.append(sanitize_html(sec["html"]))
        commentary = sec.get("commentary", "").strip()
        if commentary and plain_text(commentary).strip():
            parts.append(
                '<div class="commentary-box"><p class="box-title">Góc nhìn thêm</p>'
                f"{sanitize_html(commentary)}</div>"
            )

    insights = "".join(f"<li>{x}</li>" for x in data.get("key_insights", []))
    parts.append(
        '<div class="insights-box"><p class="box-title">Điểm cốt lõi</p>'
        f"<ul>{insights}</ul></div>"
    )

    assumptions = data.get("assumptions_limits", "").strip()
    if assumptions and plain_text(assumptions).strip():
        parts.append(
            '<div class="assumptions-box"><p class="box-title">Giả định &amp; Giới hạn áp dụng</p>'
            f"{sanitize_html(assumptions)}</div>"
        )

    exercises = "".join(f"<li>{x}</li>" for x in data.get("exercises", []))
    parts.append(
        '<div class="exercise-box"><p class="box-title">Thực hành &amp; Tự vấn</p>'
        f"<ol>{exercises}</ol>"
        '<p class="action-lead"><strong>Hành động cụ thể:</strong></p>'
        f'{sanitize_html(data["action"])}</div>'
    )

    return "\n".join(parts)


def parse_llm_json(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n", "", cleaned)
        cleaned = re.sub(r"\n```$", "", cleaned)
    return json.loads(cleaned)


def generate_lesson_with_cache(
    llm: LLMClient,
    book_id: str,
    index: int,
    total: int,
    lesson_title: str,
    section_scope: str,
    book_title: str,
    source_text: str,
) -> dict:
    cache_file = WORKDIR / f"{book_id}_lesson_{index:02d}.json"
    if cache_file.exists():
        print(f"  [Cache hit] {cache_file.name}")
        return json.loads(cache_file.read_text(encoding="utf-8"))

    print(f"  [Generating {index}/{total}] {lesson_title} (~{word_count(source_text):,} source words)...")
    system_prompt = LESSON_SYSTEM_TEMPLATE.format(book_title=book_title)
    user_prompt = LESSON_PROMPT_TEMPLATE.format(
        index=index,
        total=total,
        book_title=book_title,
        lesson_title=lesson_title,
        section_scope=section_scope,
        source_text=source_text[:120000],  # safety cap ~30k words
    )

    response = llm.complete(
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        max_tokens=16000,
        json_mode=True,
    )

    data = parse_llm_json(response)
    data["lesson_index"] = index
    data["lesson_title"] = lesson_title
    data["section_scope"] = section_scope
    cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def generate_overview_with_cache(
    llm: LLMClient,
    book_id: str,
    book_title: str,
    lessons_data: list[dict],
    source_text: str,
) -> str:
    cache_file = WORKDIR / f"{book_id}_overview.json"
    if cache_file.exists():
        print(f"  [Cache hit] {cache_file.name}")
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    else:
        print(f"  [Generating Overview] Về cuốn sách này: {book_title}...")
        lesson_titles = "\n".join(f"{i}. {d['title']}" for i, d in enumerate(lessons_data, start=1))
        all_insights = "\n\n".join(
            f"Bài {i} - {d['title']}:\n" + "\n".join(f"- {x}" for x in d.get("key_insights", []))
            for i, d in enumerate(lessons_data, start=1)
        )
        prompt = f"""Write the opening chapter "Về cuốn sách này & Tóm tắt 1 trang" for the in-depth guide of:
"{book_title}" by Philip A. Fisher.

Lessons in this guide:
{lesson_titles}

Key insights from all lessons:
{all_insights}

Author background & context notes:
{source_text[:30000]}

Return ONLY valid JSON (no markdown fence):
{{
  "intro_html": "<3-5 đoạn <p> HTML giới thiệu: Philip Fisher là ai, vị thế lịch sử của tác phẩm, vì sao Warren Buffett tuyên bố '85% Graham & 15% Fisher', và cuốn sách này định hình tư duy đầu tư tăng trưởng hiện đại ra sao>",
  "one_page_summary_html": "<5-8 đoạn <p> HTML 'Tóm tắt 1 trang': xâu chuỗi toàn bộ mô hình tư duy cốt lõi của cuốn sách thành một bức tranh liền mạch, chặt chẽ, giúp người đọc nắm trọn triết lý trước khi đi sâu vào từng bài học>"
}}
Allowed HTML tags: p, ul, ol, li, strong, em, blockquote.
"""
        resp = llm.complete(
            system=LESSON_SYSTEM_TEMPLATE.format(book_title=book_title),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8000,
            json_mode=True,
        )
        data = parse_llm_json(resp)
        cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    html = f"""<h1>Về cuốn sách này &amp; Tóm tắt 1 trang</h1>
<p class="reading-time">Bức tranh toàn cảnh · Tác phẩm kinh điển của Philip A. Fisher</p>
<div class="lesson-intro">
{sanitize_html(data["intro_html"])}
</div>
<h2>Tóm tắt toàn bộ cuốn sách trong 1 trang</h2>
<div class="insights-box">
<p class="box-title">Trục tư duy cốt lõi</p>
{sanitize_html(data["one_page_summary_html"])}
</div>
"""
    return html


def generate_recap_with_cache(
    llm: LLMClient,
    book_id: str,
    book_title: str,
    lessons_data: list[dict],
) -> str:
    cache_file = WORKDIR / f"{book_id}_recap.json"
    if cache_file.exists():
        print(f"  [Cache hit] {cache_file.name}")
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    else:
        print(f"  [Generating Recap] Tổng kết: {book_title}...")
        all_insights = "\n\n".join(
            f"Bài {i} - {d['title']}:\n" + "\n".join(f"- {x}" for x in d.get("key_insights", []))
            for i, d in enumerate(lessons_data, start=1)
        )
        prompt = f"""Write the closing chapter "Tổng kết & Khung hành động" for the in-depth guide of:
"{book_title}" by Philip A. Fisher.

All key insights from the guide:
{all_insights}

Return ONLY valid JSON (no markdown fence):
{{
  "synthesis_html": "<4-6 đoạn <p> HTML: đúc kết và liên kết các bài học thành một hệ thống đầu tư tăng trưởng toàn diện, phân tích mối tương quan giữa năng lực quản trị - lợi thế cạnh tranh - định thời điểm mua/bán>",
  "framework_html": "<3-5 đoạn <p> HTML hoặc danh sách <ul><li>: 'Checklist thực chiến của nhà đầu tư Fisher' - tóm lược các bước kiểm tra khi soi xét một doanh nghiệp>",
  "closing_action_html": "<2 đoạn <p> HTML: lời nhắn nhủ tâm huyết gửi tới nhà đầu tư trên hành trình tích lũy tài sản dài hạn>"
}}
Allowed HTML tags: p, ul, ol, li, strong, em, blockquote.
"""
        resp = llm.complete(
            system=LESSON_SYSTEM_TEMPLATE.format(book_title=book_title),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8000,
            json_mode=True,
        )
        data = parse_llm_json(resp)
        cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    html = f"""<h1>Tổng kết: Khung Hành Động Của Nhà Đầu Tư Tăng Trưởng</h1>
<p class="reading-time">Đúc kết hệ thống &amp; Checklist thực chiến</p>
<div class="lesson-intro">
{sanitize_html(data["synthesis_html"])}
</div>
<h2>Checklist thực chiến: Bộ tiêu chuẩn Philip Fisher</h2>
<div class="insights-box">
<p class="box-title">Khung rà soát doanh nghiệp</p>
{sanitize_html(data["framework_html"])}
</div>
<h2>Lời kết gửi nhà đầu tư</h2>
<div class="action-box">
<p class="box-title">Nguyên tắc kiên định</p>
{sanitize_html(data["closing_action_html"])}
</div>
"""
    return html


def build_book_1(llm: LLMClient) -> tuple[Book, list[dict]]:
    book_id = "book1"
    pdf_path = "/Users/mktmda/Documents/Fisher/Common_Stocks_and_Uncommon_Profits_an_z_library_sk,_1lib_sk,.pdf"
    book_title = "Cổ Phiếu Thường, Lợi Nhuận Phi Thường & Các Tiểu Luận Kinh Điển"
    orig_title = "Common Stocks and Uncommon Profits and Other Writings"

    lessons_config = [
        (
            1,
            "Phương pháp 'Lời đồn đại' (Scuttlebutt) – Nghệ thuật điều tra thực địa",
            "Phần 1: Chương 1 & 2 (Dấu vết quá khứ & Sức mạnh lời đồn đại)",
            59, 74,
        ),
        (
            2,
            "15 Tiêu chí vàng chọn Cổ phiếu Siêu tăng trưởng (Phần 1: Sản phẩm, R&D & Biên lợi nhuận)",
            "Phần 1: Chương 3 (Tiêu chí 1 đến 7)",
            75, 90,
        ),
        (
            3,
            "15 Tiêu chí vàng chọn Cổ phiếu Siêu tăng trưởng (Phần 2: Quản trị, Nhân sự & Tính chính trực)",
            "Phần 1: Chương 3 (Tiêu chí 8 đến 15) & Chương 4 (Áp dụng vào nhu cầu)",
            91, 116,
        ),
        (
            4,
            "Khi nào nên Mua – Nghệ thuật chọn thời điểm vàng để mở vị thế",
            "Phần 1: Chương 5 (Thời điểm mua)",
            117, 132,
        ),
        (
            5,
            "Khi nào nên Bán – Và 3 lý do duy nhất được phép bán cổ phiếu",
            "Phần 1: Chương 6 (Khi nào nên bán và khi nào KHÔNG ĐƯỢC BÁN)",
            133, 141,
        ),
        (
            6,
            "Sự ồn ào vô nghĩa quanh Cổ tức – Ảo tưởng dòng tiền vs Tái đầu tư kép",
            "Phần 1: Chương 7 (Sự ồn ào quanh cổ tức)",
            142, 150,
        ),
        (
            7,
            "10 Điều KHÔNG NÊN làm của nhà đầu tư thông minh",
            "Phần 1: Chương 8 & 9 (Năm điều không nên & Năm điều không nên tiếp theo) và Chương 10",
            151, 202,
        ),
        (
            8,
            "4 Chiều kích của một Khoản đầu tư Thận trọng (Conservative Investors Sleep Well)",
            "Phần 2: Nhà đầu tư thận trọng ngủ ngon (4 chiều kích & định giá)",
            203, 252,
        ),
        (
            9,
            "Nguồn gốc & Sự trưởng thành của Triết lý đầu tư cá nhân",
            "Phần 3: Phát triển triết lý đầu tư & Phụ lục các yếu tố đánh giá",
            253, 310,
        ),
    ]

    total_lessons = len(lessons_config)
    lessons_data = []

    for idx, title, scope, start_p, end_p in lessons_config:
        src_text = extract_pages_text(pdf_path, start_p, end_p)
        ldata = generate_lesson_with_cache(
            llm=llm,
            book_id=book_id,
            index=idx,
            total=total_lessons,
            lesson_title=title,
            section_scope=scope,
            book_title=book_title,
            source_text=src_text,
        )
        lessons_data.append(ldata)

    # Overview & Recap
    intro_src = extract_pages_text(pdf_path, 13, 56)
    overview_html = generate_overview_with_cache(llm, book_id, book_title, lessons_data, intro_src)
    recap_html = generate_recap_with_cache(llm, book_id, book_title, lessons_data)

    chapters = []
    toc = []

    # Chapter 0: Overview
    chap_0 = Chapter(
        id="chap_0000",
        title="Về cuốn sách này & Tóm tắt 1 trang",
        html=overview_html,
        order=0,
    )
    chapters.append(chap_0)
    toc.append(TocEntry(title=chap_0.title, chapter_id=chap_0.id))

    # Lessons
    for idx, (lcfg, ldata) in enumerate(zip(lessons_config, lessons_data), start=1):
        l_html = render_lesson_html(ldata, lcfg[0], total_lessons, lcfg[2])
        chap = Chapter(
            id=f"chap_{idx:04d}",
            title=f"Bài {lcfg[0]}: {lcfg[1]}",
            html=l_html,
            order=idx,
        )
        chapters.append(chap)
        toc.append(TocEntry(title=chap.title, chapter_id=chap.id))

    # Recap
    chap_recap = Chapter(
        id=f"chap_{len(chapters):04d}",
        title="Tổng kết: Khung Hành Động Của Nhà Đầu Tư Tăng Trưởng",
        html=recap_html,
        order=len(chapters),
    )
    chapters.append(chap_recap)
    toc.append(TocEntry(title=chap_recap.title, chapter_id=chap_recap.id))

    # Cover image
    images = []
    cover_path = Path("covers/book1_cover.jpg")
    if cover_path.exists():
        images.append(
            ImageAsset(
                id="cover_img",
                filename="images/cover.jpg",
                data=cover_path.read_bytes(),
                media_type="image/jpeg",
                is_cover=True,
            )
        )

    book = Book(
        title=orig_title,
        title_translated=book_title,
        author="Philip A. Fisher",
        language="vi",
        chapters=chapters,
        images=images,
        toc=toc,
        css=FULL_CSS,
    )
    return book, lessons_data


def build_book_2(llm: LLMClient) -> tuple[Book, list[dict]]:
    book_id = "book2"
    pdf_path = "/Users/mktmda/Documents/Fisher/Paths_to_Wealth_Through_Common_Stocks_z_library_sk,_1lib_sk,.pdf"
    book_title = "Con Đường Dẫn Tới Giàu Có Qua Cổ Phiếu Thường"
    orig_title = "Paths to Wealth Through Common Stocks"

    lessons_config = [
        (
            1,
            "Thích ứng với các lực đẩy vĩ mô (Lạm phát, Vốn tổ chức, Cạnh tranh & Dân số)",
            "Phần I (Mục A, B, C, D): Các tác động then chốt của thập kỷ mới",
            17, 72,
        ),
        (
            2,
            "'Các nhà kinh tế học rời đi – Các nhà tâm lý học bước vào': Nghệ thuật định thời điểm",
            "Phần I (Mục E): Tâm lý học thị trường vs Ảo tưởng dự báo kinh tế học",
            73, 85,
        ),
        (
            3,
            "Bí mật tạo nên mức tăng giá cổ phiếu khủng khiếp nhất: Động cơ kép",
            "Phần II: Tăng trưởng EPS song hành cùng Mở rộng P/E Multiple",
            86, 106,
        ),
        (
            4,
            "5 Bước tuyển chọn nhà tư vấn đầu tư và quản lý gia sản phù hợp",
            "Phần III: Nhà đầu tư tìm kiếm điều gì và 5 bước chọn đúng chuyên gia",
            107, 137,
        ),
        (
            5,
            "Những vấn đề then chốt: M&A, Quyền biểu quyết và Chu kỳ bầu cử chính trị",
            "Phần IV: Những chuyện tưởng chừng vụn vặt nhưng vô cùng hệ trọng",
            138, 164,
        ),
        (
            6,
            "Giải mã các ngành tăng trưởng then chốt (Hóa chất, Điện tử, Dược phẩm & Bẫy tăng trưởng giả)",
            "Phần V: Các ngành kinh tế tăng trưởng vượt bậc và cạm bẫy cần tránh",
            165, 226,
        ),
    ]

    total_lessons = len(lessons_config)
    lessons_data = []

    for idx, title, scope, start_p, end_p in lessons_config:
        src_text = extract_pages_text(pdf_path, start_p, end_p)
        ldata = generate_lesson_with_cache(
            llm=llm,
            book_id=book_id,
            index=idx,
            total=total_lessons,
            lesson_title=title,
            section_scope=scope,
            book_title=book_title,
            source_text=src_text,
        )
        lessons_data.append(ldata)

    # Overview & Recap
    intro_src = extract_pages_text(pdf_path, 13, 16)
    overview_html = generate_overview_with_cache(llm, book_id, book_title, lessons_data, intro_src)
    recap_html = generate_recap_with_cache(llm, book_id, book_title, lessons_data)

    chapters = []
    toc = []

    # Chapter 0: Overview
    chap_0 = Chapter(
        id="chap_0000",
        title="Về cuốn sách này & Tóm tắt 1 trang",
        html=overview_html,
        order=0,
    )
    chapters.append(chap_0)
    toc.append(TocEntry(title=chap_0.title, chapter_id=chap_0.id))

    # Lessons
    for idx, (lcfg, ldata) in enumerate(zip(lessons_config, lessons_data), start=1):
        l_html = render_lesson_html(ldata, lcfg[0], total_lessons, lcfg[2])
        chap = Chapter(
            id=f"chap_{idx:04d}",
            title=f"Bài {lcfg[0]}: {lcfg[1]}",
            html=l_html,
            order=idx,
        )
        chapters.append(chap)
        toc.append(TocEntry(title=chap.title, chapter_id=chap.id))

    # Recap
    chap_recap = Chapter(
        id=f"chap_{len(chapters):04d}",
        title="Tổng kết: Con Đường Thực Tế Dẫn Tới Tự Do Tài Chính",
        html=recap_html,
        order=len(chapters),
    )
    chapters.append(chap_recap)
    toc.append(TocEntry(title=chap_recap.title, chapter_id=chap_recap.id))

    # Cover image
    images = []
    cover_path = Path("covers/book2_cover.jpg")
    if cover_path.exists():
        images.append(
            ImageAsset(
                id="cover_img",
                filename="images/cover.jpg",
                data=cover_path.read_bytes(),
                media_type="image/jpeg",
                is_cover=True,
            )
        )

    book = Book(
        title=orig_title,
        title_translated=book_title,
        author="Philip A. Fisher",
        language="vi",
        chapters=chapters,
        images=images,
        toc=toc,
        css=FULL_CSS,
    )
    return book, lessons_data


def build_omnibus(
    b1: Book, b1_lessons: list[dict], b2: Book, b2_lessons: list[dict]
) -> Book:
    omnibus_title = "Philip Fisher: Tinh Hoa Đầu Tư Tăng Trưởng (Tuyển Tập Tóm Tắt Chuyên Sâu)"
    orig_title = "Philip Fisher: The Definitive Growth Investment Omnibus"

    chapters = []
    toc = []

    # Master Overview
    master_intro_html = f"""<h1>Lời Tựa Tuyển Tập: Tinh Hoa Di Sản Philip Fisher</h1>
<p class="reading-time">Khởi nguyên và đỉnh cao của trường phái Đầu tư Tăng trưởng</p>
<div class="lesson-intro">
<p>Trong lịch sử tài chính thế giới, nếu Benjamin Graham được tôn vinh là người cha khai sinh ra trường phái Đầu tư Giá trị (Value Investing) dựa trên tài sản ròng và biên an toàn định lượng, thì <strong>Philip A. Fisher (1907 – 2004)</strong> chính là vị kiến trúc sư trưởng vĩ đại đặt nền móng cho trường phái <strong>Đầu tư Tăng trưởng (Growth Investing)</strong> hiện đại.</p>
<p>Chính Warren Buffett từng khẳng định câu nói bất hủ: <em>"Tôi là 85% Benjamin Graham và 15% Philip Fisher"</em>. Nếu Graham dạy Buffett cách bảo toàn vốn và không để mất tiền, thì chính Fisher đã khai sáng cho Buffett nhìn ra tiềm năng vĩ đại của việc nắm giữ những doanh nghiệp có lợi thế cạnh tranh phi thường trong hàng thập kỷ (như See's Candies, Coca-Cola hay sau này là Apple).</p>
<p>Tuyển tập này tập hợp trọn vẹn <strong>hai kiệt tác kinh điển nhất</strong> trong sự nghiệp của Philip Fisher được tóm tắt và bình giải chuyên sâu theo chuẩn Shortform cao cấp:</p>
<ul>
  <li><strong>Tập I: Cổ Phiếu Thường, Lợi Nhuận Phi Thường (Common Stocks and Uncommon Profits and Other Writings)</strong> — Gồm trọn vẹn 15 tiêu chí vàng, phương pháp điều tra thực địa Scuttlebutt, triết lý mua/bán, 4 chiều kích của khoản đầu tư thận trọng và sự trưởng thành của một triết lý đầu tư cá nhân.</li>
  <li><strong>Tập II: Con Đường Dẫn Tới Giàu Có Qua Cổ Phiếu Thường (Paths to Wealth Through Common Stocks)</strong> — Đào sâu vào động cơ kép (tăng trưởng EPS kết hợp bùng nổ P/E multiple), nghệ thuật tâm lý học thị trường vượt lên dự báo kinh tế học, và cách giải mã các ngành công nghiệp mũi nhọn.</li>
</ul>
<p>Mỗi bài học được thiết kế để bạn có thể nghiền ngẫm trong 15–25 phút, đầy đủ cơ chế hoạt động, ví dụ thực chiến từ các thương vụ thế kỷ, đối chiếu đa chiều cùng các nhà đầu tư lẫy lừng và bài tập phản tỉnh danh mục thực tế.</p>
</div>
"""
    chap_master_intro = Chapter(
        id="chap_master_intro",
        title="Lời Tựa Tuyển Tập: Tinh Hoa Di Sản Philip Fisher",
        html=master_intro_html,
        order=0,
    )
    chapters.append(chap_master_intro)
    toc.append(TocEntry(title=chap_master_intro.title, chapter_id=chap_master_intro.id))

    # Part I: Book 1
    part1_entry = TocEntry(
        title="Phần I: Cổ Phiếu Thường, Lợi Nhuận Phi Thường",
        chapter_id="",
    )
    for c in b1.chapters:
        new_c = Chapter(
            id=f"b1_{c.id}",
            title=c.title,
            html=c.html,
            order=len(chapters),
        )
        chapters.append(new_c)
        if not part1_entry.chapter_id:
            part1_entry.chapter_id = new_c.id
        part1_entry.children.append(TocEntry(title=new_c.title, chapter_id=new_c.id))
    toc.append(part1_entry)

    # Part II: Book 2
    part2_entry = TocEntry(
        title="Phần II: Con Đường Dẫn Tới Giàu Có Qua Cổ Phiếu Thường",
        chapter_id="",
    )
    for c in b2.chapters:
        new_c = Chapter(
            id=f"b2_{c.id}",
            title=c.title,
            html=c.html,
            order=len(chapters),
        )
        chapters.append(new_c)
        if not part2_entry.chapter_id:
            part2_entry.chapter_id = new_c.id
        part2_entry.children.append(TocEntry(title=new_c.title, chapter_id=new_c.id))
    toc.append(part2_entry)

    # Master Conclusion
    master_recap_html = f"""<h1>Lời Kết: Bản Đồ Tinh Hoa Của Nhà Đầu Tư Bền Vững</h1>
<p class="reading-time">Tích lũy tài sản trọn đời cùng Philip Fisher</p>
<div class="lesson-intro">
<p>Hành trình qua 15 bài học chuyên sâu của hai tác phẩm kinh điển cho ta thấy rằng: <strong>Đầu tư thành công không phải là việc tìm kiếm những mánh khóe giao dịch ngắn hạn hay cố đoán đỉnh đoán đáy thị trường</strong>. Đó là một nghệ thuật kỷ luật, đòi hỏi sự kiên nhẫn phi thường và khả năng nhận định chiều sâu phẩm chất của con người và doanh nghiệp.</p>
</div>
<h2>Bốn Trụ Cột Không Bao Giờ Lỗi Thời</h2>
<div class="insights-box">
<p class="box-title">Đúc kết từ toàn bộ di sản của Philip Fisher</p>
<ol>
  <li><strong>Lợi thế cạnh tranh xuất sắc &amp; Đổi mới liên tục:</strong> Không bao giờ đầu tư vào một doanh nghiệp hài lòng với hiện tại. Doanh nghiệp xứng đáng nắm giữ phải sở hữu bộ phận R&amp;D sắc bén, văn hóa đổi mới sáng tạo không ngừng nghỉ để tạo ra dòng sản phẩm tương lai trước khi đối thủ kịp nhận ra.</li>
  <li><strong>Tính chính trực &amp; Chiều sâu của Ban lãnh đạo:</strong> Năng lực kinh doanh quan trọng, nhưng sự chính trực đối với cổ đông thiểu số là điều kiện tiên quyết. Một ban lãnh đạo trung thực khi công ty gặp khó khăn chính là tấm lá chắn bảo vệ gia sản của bạn tốt nhất.</li>
  <li><strong>Sức mạnh của Động cơ Kép:</strong> Khoản lợi nhuận kếch xù trên thị trường chứng khoán được sinh ra khi lợi nhuận doanh nghiệp tăng trưởng gấp 3-5 lần, đồng thời nhận thức của công chúng chuyển dịch từ nghi ngờ sang tán thưởng, đẩy hệ số P/E tăng gấp 2-3 lần. Sự cộng hưởng này tạo nên những cổ phiếu tăng giá 10x đến 50x.</li>
  <li><strong>Kiên nhẫn phi thường – Không bán khi cổ phiếu đang thăng hoa:</strong> Bán một siêu cổ phiếu chỉ vì nó có vẻ "đã tăng quá nhiều" là một trong những sai lầm cay đắng nhất của nhà đầu tư. Thời điểm lý tưởng để bán một cổ phiếu xuất sắc gần như là: <em>KHÔNG BAO GIỜ</em> (trừ khi doanh nghiệp đã đánh mất phẩm chất ban đầu).</li>
</ol>
</div>
<h2>Lời Chúc Gửi Tới Độc Giả</h2>
<div class="action-box">
<p class="box-title">Hành trang thực chiến</p>
<p>Chúc bạn rèn luyện được sự điềm tĩnh trước mọi biến động vĩ mô, xây dựng một mạng lưới điều tra thực địa (Scuttlebutt) nhạy bén, và sở hữu một danh mục tinh gọn gồm những doanh nghiệp tuyệt vời nhất để tiền bạc không ngừng sinh sôi trong giấc ngủ an lành!</p>
</div>
"""
    chap_master_recap = Chapter(
        id="chap_master_recap",
        title="Lời Kết: Bản Đồ Tinh Hoa Của Nhà Đầu Tư Bền Vững",
        html=master_recap_html,
        order=len(chapters),
    )
    chapters.append(chap_master_recap)
    toc.append(TocEntry(title=chap_master_recap.title, chapter_id=chap_master_recap.id))

    # Cover image
    images = []
    cover_path = Path("covers/omnibus_cover.jpg")
    if cover_path.exists():
        images.append(
            ImageAsset(
                id="cover_img",
                filename="images/cover.jpg",
                data=cover_path.read_bytes(),
                media_type="image/jpeg",
                is_cover=True,
            )
        )

    return Book(
        title=orig_title,
        title_translated=omnibus_title,
        author="Philip A. Fisher",
        language="vi",
        chapters=chapters,
        images=images,
        toc=toc,
        css=FULL_CSS,
    )


def main():
    print("=" * 65)
    print("  🚀 PIPELINE TẠO EPUB TÓM TẮT CHUYÊN SÂU PHILIP FISHER")
    print("=" * 65)

    project_id = "video-dub-500504"
    print(f"Khởi tạo LLM Client (Google Cloud Vertex AI, Project: {project_id})...")
    llm = LLMClient(project_id=project_id)

    # 1. Book 1
    print("\n" + "=" * 50)
    print("📚 [1/3] Xử lý Sách 1: Cổ Phiếu Thường, Lợi Nhuận Phi Thường")
    print("=" * 50)
    b1, b1_lessons = build_book_1(llm)
    epub1_path = OUTPUT_DIR / "1_Co_Phieu_Thuong_Loi_Nhuan_Phi_Thuong_Tom_Tat_Chuyen_Sau.epub"
    write_epub(b1, str(epub1_path))
    print(f"  ✅ Đã xuất EPUB 1: {epub1_path} ({len(b1.chapters)} chương)")

    # 2. Book 2
    print("\n" + "=" * 50)
    print("📚 [2/3] Xử lý Sách 2: Con Đường Dẫn Tới Giàu Có Qua Cổ Phiếu Thường")
    print("=" * 50)
    b2, b2_lessons = build_book_2(llm)
    epub2_path = OUTPUT_DIR / "2_Con_Duong_Dan_Toi_Giau_Co_Qua_Co_Phieu_Tom_Tat_Chuyen_Sau.epub"
    write_epub(b2, str(epub2_path))
    print(f"  ✅ Đã xuất EPUB 2: {epub2_path} ({len(b2.chapters)} chương)")

    # 3. Omnibus
    print("\n" + "=" * 50)
    print("📚 [3/3] Xử lý Bản Tuyển Tập Master (Omnibus Edition)")
    print("=" * 50)
    omnibus = build_omnibus(b1, b1_lessons, b2, b2_lessons)
    epub3_path = OUTPUT_DIR / "Philip_Fisher_Bo_Doi_Kinh_Dien_Dau_Tu_Tang_Truong_Tom_Tat_Chuyen_Sau.epub"
    write_epub(omnibus, str(epub3_path))
    print(f"  ✅ Đã xuất Tuyển tập EPUB: {epub3_path} ({len(omnibus.chapters)} chương)")

    # 4. Sao chép vào /Users/mktmda/Documents/Fisher/
    print("\n" + "=" * 50)
    print("📂 [Sao chép] Đưa các file EPUB hoàn thiện vào /Users/mktmda/Documents/Fisher/")
    print("=" * 50)
    for p in [epub1_path, epub2_path, epub3_path]:
        dest = FISHER_DIR / p.name
        shutil.copy2(p, dest)
        size_kb = dest.stat().st_size // 1024
        print(f"  📦 Đã lưu: {dest} ({size_kb:,} KB)")

    # 5. Gửi tới Telegram (nếu có cấu hình)
    send_script = Path(__file__).parent / "send_to_telegram.py"
    if send_script.exists():
        print("\n" + "=" * 50)
        print("🚀 [Telegram] Gửi các file EPUB tới nhóm/topic Telegram")
        print("=" * 50)
        import subprocess
        for p, caption in [
            (epub1_path, "📘 <b>CỔ PHIẾU THƯỜNG, LỢI NHUẬN PHI THƯỜNG</b> (Philip Fisher)\nTóm tắt chuyên sâu 9 bài học chuẩn Shortform"),
            (epub2_path, "📗 <b>CON ĐƯỜNG DẪN TỚI GIÀU CÓ QUA CỔ PHIẾU THƯỜNG</b> (Philip Fisher)\nTóm tắt chuyên sâu 6 bài học chuẩn Shortform"),
            (epub3_path, "📕 <b>PHILIP FISHER: TINH HOA ĐẦU TƯ TĂNG TRƯỞNG</b>\nTuyển tập Master Omnibus tóm tắt chuyên sâu 2 tác phẩm kinh điển"),
        ]:
            subprocess.run(["python3", str(send_script), str(p), "--caption", caption])

    print("\n" + "=" * 65)
    print(f"🎉 HOÀN TẤT TOÀN BỘ TIẾN TRÌNH!")
    print(f"Tổng token sử dụng: {llm.input_tokens:,} in / {llm.output_tokens:,} out")
    print("=" * 65)


if __name__ == "__main__":
    main()
