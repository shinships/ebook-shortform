"""Tom tat sach thanh "sach huong dan chuyen sau" kieu Shortform.

Moi bai tra loi: vi sao y tuong quan trong, co che hoat dong ra sao, ap dung
the nao — kem vi du tu sach, binh luan mo rong ("Goc nhin them") va bai tap
thuc hanh cuoi bai.

Ba pha:
1. analyze_book  — 1 call LLM: boi canh sach + phan loai chuong content/skip.
2. plan_lessons  — thuan code: gop/tach chuong thanh bai hoc 15-25 phut doc.
3. summarize_book — moi bai 1 call LLM tra JSON; chuong dai thi map-reduce
   (trich notes tung chunk truoc). Ket qua lap thanh Book moi de ghi EPUB.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup

from ebook_translator.core.cache import TranslationCache
from ebook_translator.core.glossary import _parse_json
from ebook_translator.core.llm import LLMClient
from ebook_translator.core.segmenter import split_html
from ebook_translator.models import Book, Chapter
from ebook_translator.writers.epub_writer import DEFAULT_CSS

PROMPT_VERSION = "sf-v2"  # doi prompt thi bump de invalidate cache bai hoc
WORDS_MIN, WORDS_MAX = 2400, 4000  # tu tieng Viet moi bai ~ 15-25 phut doc
READ_WPM = 160  # toc do doc tham tieng Viet, dung cho badge thoi gian
MERGE_BELOW_WORDS = 1500  # nhom chuong nguon nho hon nguong nay thi gop tiep
MERGED_SOURCE_CAP = 15_000  # tran so tu nguon cua mot nhom gop
SPLIT_ABOVE_WORDS = 25_000  # chuong nguon lon hon thi tach nhieu bai
WORDS_PER_PART = 20_000  # kich thuoc muc tieu moi phan khi tach
MAP_REDUCE_ABOVE_WORDS = 9_000  # nguon lon hon thi trich notes tung chunk truoc
MIN_BOOK_WORDS = 6_000  # duoi nguong nay ca sach thanh 1 bai duy nhat
SKIP_TITLE_RE = re.compile(
    r"(contents|copyright|index|acknowledg|dedication|praise for"
    r"|about the author|also by|bibliograph|notes|glossary|title page|cover)",
    re.I,
)
ALLOWED_TAGS = {"p", "ul", "ol", "li", "strong", "em", "blockquote"}

LESSON_CSS = """
.reading-time { color: #777; font-size: 0.85em; text-indent: 0; margin-top: -0.4em; }
.lesson-intro p { text-indent: 0; }
.insights-box, .action-box, .exercise-box, .commentary-box {
  border-left: 4px solid #4a7dbd; background: #f0f4fa;
  padding: 0.7em 1em; margin: 1.2em 0; border-radius: 0 6px 6px 0;
}
.action-box, .exercise-box { border-left-color: #3f9d63; background: #eef7f1; }
.commentary-box { border-left-color: #b58a3c; background: #faf5ec; font-style: italic; }
.commentary-box strong, .commentary-box em { font-style: normal; }
.assumptions-box { border-left-color: #9c4f4f; background: #faf0f0; }
.insights-box p, .action-box p, .exercise-box p, .commentary-box p, .assumptions-box p {
  text-indent: 0; margin: 0.3em 0;
}
.box-title { font-weight: bold; font-size: 1.05em; margin-bottom: 0.4em; font-style: normal; }
.insights-box ul, .exercise-box ol { margin: 0.2em 0 0.2em 1.2em; padding-left: 0.4em; }
.insights-box li, .exercise-box li { margin: 0.35em 0; }
.exercise-box .action-lead { margin-top: 0.7em; }
.recap-ref h3 { font-size: 1em; margin-bottom: 0.2em; }
"""

ANALYSIS_PROMPT = """\
You are preparing a Vietnamese microlearning summary ("sách tóm tắt") of a book.
Book: "{title}" by {author}.

Below is every chapter, one per line: id | title | word count | opening excerpt.

{chapter_lines}

Return ONLY valid JSON, no markdown fence, with EXACTLY this shape:
{{
  "title_vi": "<tựa sách dịch sang tiếng Việt, tự nhiên>",
  "book_context": "<2-3 câu tiếng Việt: thể loại, nội dung chính, giá trị của cuốn sách>",
  "audience": "<1 câu tiếng Việt: cuốn sách dành cho ai>",
  "themes": ["<chủ đề cốt lõi bằng tiếng Việt>", "..."],
  "chapter_roles": {{"<chapter id>": "content", "...": "skip"}}
}}
Rules:
- themes: 3-6 items.
- chapter_roles: "skip" = front/back matter (title page, table of contents, copyright,
  index, endnotes, bibliography, ads, dedication); "content" = real material worth
  summarizing. EVERY chapter id from the list above must appear exactly once.
- A chapter that merely LISTS the book's chapters, sections, or their one-line gists is a
  table of contents — mark it "skip" even when it has no heading and reads like prose.
  Telltale signs: it names several chapter titles in sequence, or reads as an outline of
  the whole book rather than developing one idea.
"""

LESSON_SYSTEM = """\
You are an expert writer of in-depth Vietnamese book guides \
(in the style of Shortform). Your guides go far beyond quick takeaways: \
for every major idea you answer three questions — WHY it matters, \
HOW it works (the mechanism), and HOW the reader can apply it.

Book: "{title}" by {author}.
{book_context}
Audience: {audience}
Core themes: {themes}

WRITING STYLE:
- Natural, thoughtful Vietnamese. Address the reader as "bạn".
- Comprehension-first: explain the reasoning and mechanism behind each idea,
  not just the conclusion. Use the book's OWN examples, stories, and data.
- In the main content (intro, sections, insights, exercises, action), never
  invent facts, examples, numbers, or advice that are not in the source material.
- EXCEPTION — the "commentary" field of a section: there you MAY draw on
  widely-established outside knowledge (other well-known books, famous studies,
  common counterarguments) to add context, comparison, or critique. Name the
  book/researcher when you reference one. Only include commentary when you have
  something genuinely valuable; otherwise leave it empty. Never mix outside
  knowledge into any other field.
- If the source references figures/charts, describe their point in words.
- Keep tone and structure consistent across all lessons of the book.
"""

NOTES_PROMPT = """\
Extract detailed notes in Vietnamese from the following part of the chapter.
Capture: key arguments, examples, stories, data points, memorable quotes.
Write plain-text bullet lines ("- ..."), no commentary, no markdown headings.
Be thorough — these notes are the ONLY thing the summary writer will see.

Part of chapter "{chapter_title}":

{chunk}"""

LESSON_PROMPT = """\
Write lesson {index}/{total} of the guide. It covers this part of the book: {chapter_titles}.
{prev_lesson_line}
CRITICAL SCOPE RULE: base this lesson ONLY on the source material below. Do not write about
principles, chapters, or examples from elsewhere in the book, even if you recall them — the
source material below defines the entire scope of this lesson. Ignore any chapter numbering
you may recall from the book; it does not map to this lesson.

Source material:
{source}

Return ONLY valid JSON (no markdown fence) with EXACTLY this shape:
{{
  "title": "<tiêu đề bài học tiếng Việt, ngắn gọn, nói rõ bài này giải quyết vấn đề gì>",
  "intro": "<1-2 đoạn <p> HTML: phần này của sách nói về gì và VÌ SAO nó quan trọng với người đọc>",
  "sections": [{{
    "heading": "<tiêu đề mục tiếng Việt>",
    "reasoning": "<BẮT BUỘC — 2-3 câu tiếng Việt tái hiện MẠCH LẬP LUẬN của tác giả: tiền đề tác giả xuất phát từ đâu, suy luận thế nào để đi tới kết luận. Trả lời 'VÌ SAO đúng', không chỉ 'đúng cái gì'. Viết như văn xuôi thường, KHÔNG dùng HTML tag.>",
    "html": "<3-6 đoạn <p>: giải thích CƠ CHẾ của ý tưởng (hoạt động thế nào trong thực tế), kèm ví dụ/câu chuyện/số liệu cụ thể từ sách; có thể dùng <ul>/<li>, <strong>, <em>, <blockquote>>",
    "commentary": "<TÙY CHỌN — 1-2 đoạn <p>: đối chiếu với nghiên cứu/sách khác, phản biện, hoặc bối cảnh bổ sung từ kiến thức NGOÀI cuốn sách (nêu rõ nguồn tham chiếu). Để chuỗi rỗng nếu không có gì thật sự đáng nói.>"
  }}],
  "key_insights": ["<ý chính cô đọng, 1-2 câu tiếng Việt>", "..."],
  "assumptions_limits": "<BẮT BUỘC — 1 đoạn <p> HTML (2-4 câu): bối cảnh/giả định ngầm mà tác giả coi là hiển nhiên (thời đại, thị trường, loại người/tổ chức mà lập luận này dựa vào), và khi nào lời khuyên trong bài KHÔNG áp dụng được hoặc cần thận trọng. Đây là ý kiến đánh giá của bạn (người viết guide), không phải ý tác giả — được phép dùng kiến thức ngoài sách.>",
  "exercises": ["<câu hỏi tự vấn giúp người đọc soi ý tưởng vào đời sống/công việc của chính mình>", "..."],
  "action": "<1 đoạn <p> HTML: MỘT hành động cụ thể người đọc có thể làm ngay hôm nay từ bài học này>"
}}
Constraints:
- 2-5 sections; 3-6 key_insights; 2-3 exercises.
- TOTAL length across intro + sections + commentary + assumptions_limits + insights + exercises + action: {words_min}-{words_max} Vietnamese words.
- Allowed HTML tags ONLY: p, ul, ol, li, strong, em, blockquote (assumptions_limits and action use plain <p>; reasoning has NO HTML tags at all).
"""

OVERVIEW_PROMPT = """\
Write the opening lesson "Về cuốn sách này" for the Vietnamese in-depth guide of
"{title}" by {author}.
{book_context}
Audience: {audience}
Core themes: {themes}

The guide contains these lessons:
{lesson_titles}

Key insights collected from all lessons (use these to write the one-page summary):
{insights_block}

Return ONLY valid JSON (no markdown fence):
{{
  "intro_html": "<2-3 đoạn <p> tiếng Việt: cuốn sách nói về gì, tác giả là ai, vì sao nó đáng đọc và đáng đào sâu>",
  "one_page_summary_html": "<4-6 đoạn <p> tiếng Việt: 'tóm tắt 1 trang' — xâu chuỗi toàn bộ lập luận chính của cuốn sách thành một mạch liền, để người đọc nắm được bức tranh lớn trước khi vào từng bài>"
}}
Allowed HTML tags ONLY: p, ul, ol, li, strong, em."""

RECAP_PROMPT = """\
Write the closing recap lesson "Tổng kết" for the Vietnamese in-depth guide of
"{title}" by {author}.

Here are all key insights collected from the lessons:
{insights_block}

Return ONLY valid JSON (no markdown fence):
{{
  "synthesis_html": "<2-4 đoạn <p> tiếng Việt: xâu chuỗi các ý lớn của cả cuốn sách thành một bức tranh tổng thể, không lặp lại máy móc từng ý>",
  "final_action_html": "<1 đoạn <p>: lời nhắn hành động cuối cùng cho người đọc>"
}}
Allowed HTML tags ONLY: p, ul, ol, li, strong, em."""


# ---------------------------------------------------------------------------
# Phase 1: phan tich sach
# ---------------------------------------------------------------------------


@dataclass
class BookAnalysis:
    book_context: str = ""
    audience: str = ""
    themes: list[str] = field(default_factory=list)
    title_vi: str = ""
    chapter_roles: dict[str, str] = field(default_factory=dict)

    @property
    def version(self) -> str:
        """Hash cac truong co anh huong den noi dung bai hoc.

        title_vi khong nam trong hash: no chi la tua sach cua EPUB, khong vao
        prompt bai hoc — sua tua thi cache bai hoc van con hieu luc.
        """
        payload = json.dumps(
            {
                "ctx": self.book_context,
                "aud": self.audience,
                "themes": self.themes,
                "roles": self.chapter_roles,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(
                {
                    "title_vi": self.title_vi,
                    "book_context": self.book_context,
                    "audience": self.audience,
                    "themes": self.themes,
                    "chapter_roles": self.chapter_roles,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> "BookAnalysis":
        # utf-8-sig: file nay user sua tay duoc, editor Windows hay them BOM
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return cls(
            book_context=str(data.get("book_context", "")),
            audience=str(data.get("audience", "")),
            themes=[str(t) for t in data.get("themes", [])],
            title_vi=str(data.get("title_vi", "")),
            chapter_roles={str(k): str(v) for k, v in (data.get("chapter_roles") or {}).items()},
        )


def _plain_text(html: str) -> str:
    return BeautifulSoup(html, "lxml").get_text(" ", strip=True)


def _word_count(text: str) -> int:
    return len(text.split())


def analyze_book(book: Book, llm: LLMClient) -> BookAnalysis:
    """1 call LLM: boi canh sach, tua tieng Viet, phan loai chuong."""
    lines: list[str] = []
    budget = 50_000
    for chap in book.chapters:
        text = _plain_text(chap.html)
        excerpt = text[:600].replace("\n", " ")
        line = f"{chap.id} | {chap.title or '(không tiêu đề)'} | {_word_count(text)} từ | {excerpt}"
        if budget - len(line) < 0:
            excerpt = ""
            line = f"{chap.id} | {chap.title or '(không tiêu đề)'} | {_word_count(text)} từ |"
        lines.append(line)
        budget -= len(line)

    raw = llm.complete(
        system="You are a professional book-summary planner.",
        messages=[
            {
                "role": "user",
                "content": ANALYSIS_PROMPT.format(
                    title=book.title,
                    author=book.author or "unknown",
                    chapter_lines="\n".join(lines),
                ),
            }
        ],
        max_tokens=4000,
    )
    data = _parse_json(raw) or {}
    analysis = BookAnalysis(
        book_context=str(data.get("book_context", "")),
        audience=str(data.get("audience", "")),
        themes=[str(t) for t in data.get("themes", []) if str(t).strip()],
        title_vi=str(data.get("title_vi", "")).strip(),
        chapter_roles={str(k): str(v) for k, v in (data.get("chapter_roles") or {}).items()},
    )
    _apply_role_safety_net(book, analysis)
    return analysis


def _apply_role_safety_net(book: Book, analysis: BookAnalysis) -> None:
    """Luoi an toan code-side de khong phu thuoc hoan toan vao LLM."""
    for chap in book.chapters:
        words = _word_count(_plain_text(chap.html))
        role = analysis.chapter_roles.get(chap.id)
        if role not in ("content", "skip"):
            role = "content"
        if words < 100:
            role = "skip"
        elif SKIP_TITLE_RE.search(chap.title or "") and words < 400:
            role = "skip"
        analysis.chapter_roles[chap.id] = role


# ---------------------------------------------------------------------------
# Phase 2: len ke hoach bai hoc (thuan code)
# ---------------------------------------------------------------------------


@dataclass
class LessonPlan:
    index: int  # 1-based, chi tinh bai noi dung
    chapter_ids: list[str]
    source_title: str  # "Chương 3", "Chương 3–5", "Chương 7 (phần 2/3)"
    part: int = 0  # >0 khi mot chuong dai tach nhieu phan
    n_parts: int = 0
    source_words: int = 0


def plan_lessons(book: Book, analysis: BookAnalysis) -> list[LessonPlan]:
    """Gop chuong ngan / tach chuong dai thanh danh sach bai hoc."""
    content = [c for c in book.chapters if analysis.chapter_roles.get(c.id) == "content"]
    counts = {c.id: _word_count(_plain_text(c.html)) for c in content}
    total_words = sum(counts.values())

    # sach rat ngan -> mot bai duy nhat
    if content and total_words < MIN_BOOK_WORDS:
        return [
            LessonPlan(
                index=1,
                chapter_ids=[c.id for c in content],
                source_title=_source_label([c.order for c in content]),
                source_words=total_words,
            )
        ]

    plans: list[LessonPlan] = []
    group: list[Chapter] = []
    group_words = 0

    def flush_group() -> None:
        nonlocal group, group_words
        if not group:
            return
        plans.append(
            LessonPlan(
                index=0,
                chapter_ids=[c.id for c in group],
                source_title=_source_label([c.order for c in group]),
                source_words=group_words,
            )
        )
        group, group_words = [], 0

    for chap in content:
        words = counts[chap.id]
        if words > SPLIT_ABOVE_WORDS:
            flush_group()
            n_parts = max(2, math.ceil(words / WORDS_PER_PART))
            for part in range(1, n_parts + 1):
                plans.append(
                    LessonPlan(
                        index=0,
                        chapter_ids=[chap.id],
                        source_title=f"{_source_label([chap.order])} (phần {part}/{n_parts})",
                        part=part,
                        n_parts=n_parts,
                        source_words=words // n_parts,
                    )
                )
            continue
        if group and (group_words >= MERGE_BELOW_WORDS or group_words + words > MERGED_SOURCE_CAP):
            flush_group()
        group.append(chap)
        group_words += words
    flush_group()

    # nhom cuoi qua nho -> gop lui vao bai truoc (neu bai truoc khong phai phan tach)
    if len(plans) >= 2 and plans[-1].source_words < MERGE_BELOW_WORDS and plans[-2].part == 0:
        last = plans.pop()
        prev = plans[-1]
        prev.chapter_ids.extend(last.chapter_ids)
        prev.source_words += last.source_words
        orders = _orders_from_ids(book, prev.chapter_ids)
        prev.source_title = _source_label(orders)

    for i, plan in enumerate(plans, start=1):
        plan.index = i
    return plans


def _orders_from_ids(book: Book, ids: list[str]) -> list[int]:
    by_id = {c.id: c.order for c in book.chapters}
    return [by_id[i] for i in ids if i in by_id]


def _source_label(orders: list[int]) -> str:
    if not orders:
        return "Chương ?"
    if len(orders) == 1:
        return f"Chương {orders[0]}"
    return f"Chương {min(orders)}–{max(orders)}"


def format_plan_table(book: Book, plans: list[LessonPlan]) -> str:
    """Bang ke hoach in ra console truoc khi sinh bai."""
    by_id = {c.id: c for c in book.chapters}
    lines = [f"{'Bài':<5}{'Nguồn':<22}{'Từ nguồn':>10}  Tiêu đề chương gốc"]
    for plan in plans:
        titles = "; ".join(
            (by_id[cid].title or by_id[cid].id) for cid in plan.chapter_ids
        )
        if len(titles) > 60:
            titles = titles[:57] + "..."
        lines.append(f"{plan.index:<5}{plan.source_title:<22}{plan.source_words:>10,}  {titles}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 3: sinh bai hoc
# ---------------------------------------------------------------------------


def summarize_book(
    book: Book,
    llm: LLMClient,
    analysis: BookAnalysis,
    plans: list[LessonPlan],
    cache: TranslationCache,
    max_lessons: int | None = None,
) -> Book:
    """Sinh toan bo bai hoc + overview + recap, tra ve Book tom tat."""
    system = LESSON_SYSTEM.format(
        title=book.title,
        author=book.author or "unknown",
        book_context=analysis.book_context,
        audience=analysis.audience,
        themes=", ".join(analysis.themes) or "(chưa rõ)",
    )
    version = f"{PROMPT_VERSION}:{analysis.version}"
    by_id = {c.id: c for c in book.chapters}

    selected = plans if max_lessons is None else plans[:max_lessons]
    total = len(plans)
    lessons: list[dict] = []
    prev_gist = ""
    for plan in selected:
        print(
            f"Bài {plan.index}/{total} ({plan.source_title}) — "
            f"đã dùng ~{llm.total_tokens // 1000}k token",
            flush=True,
        )
        source = _build_source(plan, by_id, llm, cache, version)
        key = TranslationCache.key(f"lesson:{plan.index}:{source}", llm.model, version)
        cached = cache.get(key)
        if cached is not None:
            lesson = json.loads(cached)
        else:
            lesson = _generate_lesson(llm, system, plan, total, source, prev_gist, by_id)
            cache.put(key, json.dumps(lesson, ensure_ascii=False))
        lessons.append(lesson)
        prev_gist = f"{lesson['title']}: {lesson['key_insights'][0]}" if lesson["key_insights"] else lesson["title"]

    chapters: list[Chapter] = []
    partial = max_lessons is not None and max_lessons < total

    # bai mo dau + tong ket chi sinh khi chay du (khong o che do test)
    if not partial:
        overview_html = _generate_overview(llm, book, analysis, lessons, cache, version)
        chapters.append(Chapter(id="bai_0000", title="Về cuốn sách này", html=overview_html, order=0))

    for k, (plan, lesson) in enumerate(zip(selected, lessons), start=1):
        html = _render_lesson_html(lesson, plan.index, total, plan.source_title)
        chapters.append(
            Chapter(id=f"bai_{k:04d}", title=lesson["title"], html=html, order=k)
        )

    if not partial and len(lessons) >= 2:
        recap_html = _generate_recap(llm, book, lessons, cache, version)
        k = len(chapters)
        chapters.append(Chapter(id=f"bai_{k:04d}", title="Tổng kết", html=recap_html, order=k))

    title_vi = analysis.title_vi or book.title
    cover = [img for img in book.images if img.is_cover]
    return Book(
        title=f"{title_vi} — Tóm tắt",
        author=book.author,
        language="vi",
        chapters=chapters,
        images=cover,
        toc=[],  # de trong -> epub_writer tu dung TOC phang tu chapter titles
        css=DEFAULT_CSS + LESSON_CSS,
    )


def _build_source(
    plan: LessonPlan,
    by_id: dict[str, Chapter],
    llm: LLMClient,
    cache: TranslationCache,
    version: str,
) -> str:
    """Chuan bi nguon cho prompt bai hoc: text tho hoac notes map-reduce."""
    if plan.part > 0:
        chap = by_id[plan.chapter_ids[0]]
        chunks = split_html(chap.html, max_tokens=6000)
        per_part = max(1, math.ceil(len(chunks) / plan.n_parts))
        part_chunks = chunks[(plan.part - 1) * per_part : plan.part * per_part]
        html = "".join(part_chunks)
        title = f"{chap.title} (phần {plan.part}/{plan.n_parts})"
    else:
        html = "".join(by_id[cid].html for cid in plan.chapter_ids)
        title = "; ".join(by_id[cid].title or by_id[cid].id for cid in plan.chapter_ids)

    text = _plain_text(html)
    if _word_count(text) <= MAP_REDUCE_ABOVE_WORDS:
        return text

    # map-reduce: trich notes tung chunk (cache tung chunk)
    notes_parts: list[str] = []
    chunks = split_html(html, max_tokens=6000)
    for ki, chunk in enumerate(chunks, start=1):
        chunk_text = _plain_text(chunk)
        if not chunk_text.strip():
            continue
        key = TranslationCache.key(f"notes:{chunk_text}", llm.model, version)
        notes = cache.get(key)
        if notes is None:
            print(f"  trích notes chunk {ki}/{len(chunks)}...", flush=True)
            notes = llm.complete(
                system="You extract faithful, detailed notes from book chapters.",
                messages=[
                    {
                        "role": "user",
                        "content": NOTES_PROMPT.format(chapter_title=title, chunk=chunk_text),
                    }
                ],
                max_tokens=4000,
            ).strip()
            cache.put(key, notes)
        notes_parts.append(notes)
    return (
        "Detailed notes extracted from the full chapter text "
        "(use ONLY this material):\n" + "\n".join(notes_parts)
    )


def _generate_lesson(
    llm: LLMClient,
    system: str,
    plan: LessonPlan,
    total: int,
    source: str,
    prev_gist: str,
    by_id: dict[str, Chapter],
) -> dict:
    prev_line = (
        f"The previous lesson covered — {prev_gist}\n" if prev_gist else ""
    )
    prompt = LESSON_PROMPT.format(
        index=plan.index,
        total=total,
        chapter_titles=_prompt_titles(plan, by_id),
        prev_lesson_line=prev_line,
        source=source,
        words_min=WORDS_MIN,
        words_max=WORDS_MAX,
    )
    raw = llm.complete(
        system=system,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=32000,
        json_mode=True,
    )
    data = _parse_json(raw)
    errors = _validate_lesson(data)
    if errors:
        retry_prompt = prompt + (
            "\n\nIMPORTANT: your previous attempt had these problems, fix ALL of them:\n- "
            + "\n- ".join(errors)
        )
        raw2 = llm.complete(
            system=system,
            messages=[{"role": "user", "content": retry_prompt}],
            max_tokens=32000,
            json_mode=True,
        )
        data2 = _parse_json(raw2)
        errors2 = _validate_lesson(data2)
        if not errors2:
            data = data2
        elif data2 is not None and not _fatal_errors(errors2):
            print(f"  [canh bao] bai {plan.index}: {'; '.join(errors2)}", file=sys.stderr)
            data = data2
        elif data is not None and not _fatal_errors(errors):
            print(f"  [canh bao] bai {plan.index}: {'; '.join(errors)}", file=sys.stderr)
        else:
            raise RuntimeError(
                f"Không sinh được bài {plan.index} ({plan.source_title}): "
                + "; ".join(errors2 or errors)
            )
    return _sanitize_lesson(data)


def _prompt_titles(plan: LessonPlan, by_id: dict[str, Chapter]) -> str:
    """Tieu de nguon cho prompt: chi dung title that.

    Khong dua id (vd "chap_0004") hay nhan "Chương N" vao prompt — N la thu tu
    file trong EPUB, de bi LLM hieu nham la so chuong that cua sach.
    """
    titles = [by_id[cid].title.strip() for cid in plan.chapter_ids if by_id[cid].title.strip()]
    label = "; ".join(f'"{t}"' for t in titles) if titles else "(phần không có tiêu đề)"
    if plan.part > 0:
        label += f" — phần {plan.part}/{plan.n_parts}"
    return label


def _fatal_errors(errors: list[str]) -> bool:
    fatal_prefixes = ("JSON", "missing", "wrong type", "section")
    return any(e.startswith(fatal_prefixes) for e in errors)


def _validate_lesson(data: dict | None) -> list[str]:
    if data is None:
        return ["JSON did not parse"]
    errors: list[str] = []
    for key, typ in (
        ("title", str),
        ("intro", str),
        ("sections", list),
        ("key_insights", list),
        ("assumptions_limits", str),
        ("exercises", list),
        ("action", str),
    ):
        if key not in data:
            errors.append(f"missing key '{key}'")
        elif not isinstance(data[key], typ):
            errors.append(f"wrong type for '{key}'")
    if errors:
        return errors

    sections = data["sections"]
    if not (2 <= len(sections) <= 5):
        errors.append(f"expected 2-5 sections, got {len(sections)}")
    for i, sec in enumerate(sections):
        if not isinstance(sec, dict) or not isinstance(sec.get("heading"), str) or not isinstance(sec.get("html"), str):
            errors.append(f"section {i} must be an object with string 'heading' and 'html'")
        elif "commentary" in sec and not isinstance(sec["commentary"], str):
            errors.append(f"section {i} 'commentary' must be a string (may be empty)")
        elif not isinstance(sec.get("reasoning", ""), str):
            errors.append(f"section {i} 'reasoning' must be a string")
        elif not str(sec.get("reasoning", "")).strip():
            errors.append(f"section {i} missing 'reasoning' (must explain the author's argument)")
    insights = data["key_insights"]
    if not (3 <= len(insights) <= 6):
        errors.append(f"expected 3-6 key_insights, got {len(insights)}")
    if any(not isinstance(x, str) for x in insights):
        errors.append("every key_insight must be a string")
    exercises = data["exercises"]
    if not (2 <= len(exercises) <= 3):
        errors.append(f"expected 2-3 exercises, got {len(exercises)}")
    if any(not isinstance(x, str) for x in exercises):
        errors.append("every exercise must be a string")
    if errors:
        return errors

    words = _lesson_word_count(data)
    if not (WORDS_MIN * 0.75 <= words <= WORDS_MAX * 1.2):
        errors.append(
            f"total length is {words} Vietnamese words; required {WORDS_MIN}-{WORDS_MAX}"
        )
    return errors


def _lesson_word_count(data: dict) -> int:
    parts = [
        data["intro"],
        data["action"],
        str(data.get("assumptions_limits", "")),
        *data["key_insights"],
        *data["exercises"],
    ]
    parts += [
        f"{s.get('heading', '')} {s.get('reasoning', '')} {s.get('html', '')} {s.get('commentary', '')}"
        for s in data["sections"]
    ]
    return _word_count(_plain_text(" ".join(parts)))


def _sanitize_html(html: str) -> str:
    """Bo tag ngoai allowlist (giu text), xoa script/style."""
    soup = BeautifulSoup(f"<div>{html}</div>", "lxml")
    for bad in soup.find_all(["script", "style"]):
        bad.decompose()
    for tag in soup.div.find_all(True):
        if tag.name not in ALLOWED_TAGS:
            tag.unwrap()
    return soup.div.decode_contents()


def _sanitize_lesson(data: dict) -> dict:
    return {
        "title": _plain_text(str(data["title"])).strip(),
        "intro": _sanitize_html(str(data["intro"])),
        "sections": [
            {
                "heading": _plain_text(str(s.get("heading", ""))).strip(),
                "reasoning": _plain_text(str(s.get("reasoning", "") or "")).strip(),
                "html": _sanitize_html(str(s.get("html", ""))),
                "commentary": _sanitize_html(str(s.get("commentary", "") or "")),
            }
            for s in data["sections"]
        ],
        "key_insights": [_plain_text(str(x)).strip() for x in data["key_insights"]],
        "assumptions_limits": _sanitize_html(str(data.get("assumptions_limits", "") or "")),
        "exercises": [_plain_text(str(x)).strip() for x in data["exercises"]],
        "action": _sanitize_html(str(data["action"])),
    }


# ---------------------------------------------------------------------------
# Render HTML (code lap cau truc de dong nhat 100%)
# ---------------------------------------------------------------------------


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _reading_minutes(words: int) -> int:
    return max(1, round(words / READ_WPM))


def _render_lesson_html(lesson: dict, index: int, total: int, source_title: str) -> str:
    words = _lesson_word_count(lesson)
    minutes = _reading_minutes(words)
    parts = [
        f"<h1>{_escape(lesson['title'])}</h1>",
        f'<p class="reading-time">≈ {minutes} phút đọc · Bài {index}/{total} · {_escape(source_title)}</p>',
        f'<div class="lesson-intro">{lesson["intro"]}</div>',
    ]
    for sec in lesson["sections"]:
        parts.append(f"<h2>{_escape(sec['heading'])}</h2>")
        reasoning = (sec.get("reasoning") or "").strip()
        if reasoning:
            parts.append(f"<p><em>{_escape(reasoning)}</em></p>")
        parts.append(sec["html"])
        commentary = (sec.get("commentary") or "").strip()
        if commentary and _plain_text(commentary).strip():
            parts.append(
                '<div class="commentary-box"><p class="box-title">Góc nhìn thêm</p>'
                f"{commentary}</div>"
            )
    insights = "".join(f"<li>{_escape(x)}</li>" for x in lesson["key_insights"])
    parts.append(
        '<div class="insights-box"><p class="box-title">Điểm chính</p>'
        f"<ul>{insights}</ul></div>"
    )
    assumptions = (lesson.get("assumptions_limits") or "").strip()
    if assumptions and _plain_text(assumptions).strip():
        parts.append(
            '<div class="assumptions-box"><p class="box-title">Giả định &amp; giới hạn</p>'
            f"{assumptions}</div>"
        )
    exercises = "".join(f"<li>{_escape(x)}</li>" for x in lesson["exercises"])
    parts.append(
        '<div class="exercise-box"><p class="box-title">Thực hành</p>'
        f"<ol>{exercises}</ol>"
        '<p class="action-lead"><strong>Hành động ngay:</strong></p>'
        f'{lesson["action"]}</div>'
    )
    return "\n".join(parts)


def _generate_overview(
    llm: LLMClient,
    book: Book,
    analysis: BookAnalysis,
    lessons: list[dict],
    cache: TranslationCache,
    version: str,
) -> str:
    lesson_titles = "\n".join(f"{i}. {les['title']}" for i, les in enumerate(lessons, start=1))
    insight_blocks = []
    for i, les in enumerate(lessons, start=1):
        insight_lines = "\n".join(f"- {x}" for x in les["key_insights"])
        insight_blocks.append(f"Bài {i} — {les['title']}:\n{insight_lines}")
    prompt = OVERVIEW_PROMPT.format(
        title=book.title,
        author=book.author or "unknown",
        book_context=analysis.book_context,
        audience=analysis.audience,
        themes=", ".join(analysis.themes) or "(chưa rõ)",
        lesson_titles=lesson_titles,
        insights_block="\n\n".join(insight_blocks),
    )
    key = TranslationCache.key(f"overview:{prompt}", llm.model, version)
    cached = cache.get(key)
    if cached is not None:
        data = json.loads(cached)
    else:
        print("Đang viết bài mở đầu...", flush=True)
        raw = llm.complete(
            system="You write engaging Vietnamese book-summary introductions.",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=3000,
        )
        data = _parse_json(raw) or {}
        cache.put(key, json.dumps(data, ensure_ascii=False))

    intro = _sanitize_html(str(data.get("intro_html", "")))
    one_page = _sanitize_html(str(data.get("one_page_summary_html", "")))

    return "\n".join(
        [
            "<h1>Về cuốn sách này</h1>",
            intro,
            "<h2>Tóm tắt 1 trang</h2>",
            one_page,
        ]
    )


def _generate_recap(
    llm: LLMClient,
    book: Book,
    lessons: list[dict],
    cache: TranslationCache,
    version: str,
) -> str:
    blocks = []
    for i, les in enumerate(lessons, start=1):
        insight_lines = "\n".join(f"- {x}" for x in les["key_insights"])
        blocks.append(f"Bài {i} — {les['title']}:\n{insight_lines}")
    insights_block = "\n\n".join(blocks)

    prompt = RECAP_PROMPT.format(
        title=book.title,
        author=book.author or "unknown",
        insights_block=insights_block,
    )
    key = TranslationCache.key(f"recap:{prompt}", llm.model, version)
    cached = cache.get(key)
    if cached is not None:
        data = json.loads(cached)
    else:
        print("Đang viết bài tổng kết...", flush=True)
        raw = llm.complete(
            system="You write insightful Vietnamese book-summary conclusions.",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=3000,
        )
        data = _parse_json(raw) or {}
        cache.put(key, json.dumps(data, ensure_ascii=False))

    synthesis = _sanitize_html(str(data.get("synthesis_html", "")))
    final_action = _sanitize_html(str(data.get("final_action_html", "")))

    ref_parts = ['<div class="recap-ref"><h2>Nhìn lại toàn bộ điểm chính</h2>']
    for i, les in enumerate(lessons, start=1):
        items = "".join(f"<li>{_escape(x)}</li>" for x in les["key_insights"])
        ref_parts.append(f"<h3>Bài {i} — {_escape(les['title'])}</h3><ul>{items}</ul>")
    ref_parts.append("</div>")

    return "\n".join(
        [
            "<h1>Tổng kết</h1>",
            synthesis,
            f'<div class="action-box"><p class="box-title">Lời nhắn cuối</p>{final_action}</div>',
            "".join(ref_parts),
        ]
    )
