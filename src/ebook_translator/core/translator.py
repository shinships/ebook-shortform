"""Dich Book model qua Claude API: noi dung chapter + tieu de/TOC."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter

from bs4 import BeautifulSoup

from ebook_translator.core.cache import TranslationCache
from ebook_translator.core.glossary import Glossary, _parse_json
from ebook_translator.core.llm import LLMClient
from ebook_translator.core.segmenter import split_html
from ebook_translator.models import Book

CONTEXT_TAIL_CHARS = 800

SYSTEM_TEMPLATE = """\
You are an expert English-to-Vietnamese book translator.

Book: "{title}" by {author}.
{book_context}

TRANSLATION STYLE:
- Vietnamese that is natural and easy to understand, but stays faithful to the original meaning. Do not add, omit, or embellish ideas.
- Keep sentence-level fidelity: every sentence in the source must be represented in the translation.
- Use consistent terminology throughout, following the glossary below strictly.

GLOSSARY (English => Vietnamese; "keep" or same text means keep the original form):
{glossary}

FORMAT RULES (critical):
- Input is an HTML fragment. Return the SAME HTML structure with ONLY the human-readable text translated.
- Keep every tag, attribute, id, class, href, src EXACTLY unchanged.
- Do NOT translate content inside <code>, <pre>, <script>, <style>. Keep URLs, numbers, and code identifiers as-is.
- Return ONLY the translated HTML fragment — no explanations, no markdown fences.
"""

CHUNK_PROMPT = """\
{context_block}Translate the following HTML fragment to Vietnamese:

{chunk}"""

TITLES_PROMPT = """\
Translate the following book chapter/section titles from English to Vietnamese.
These titles appear in the table of contents of the book "{title}".
{book_context}

Rules:
- Consistent with this glossary:
{glossary}
- Keep numbering/labels like "Chapter 3", "Part II", "Appendix A" translated naturally ("Chương 3", "Phần II", "Phụ lục A").
- Return ONLY valid JSON mapping the item number to the Vietnamese title, e.g. {{"1": "...", "2": "..."}}. No markdown fence.

Titles:
{titles}"""


def translate_titles(book: Book, llm: LLMClient, glossary: Glossary) -> None:
    """Dich toan bo tieu de (chapter + TOC) trong MOT request de nhat quan.

    Ghi ket qua vao title_translated cua chapter/toc va book.title_translated.
    """
    unique: list[str] = []
    seen: set[str] = set()

    def collect(title: str) -> None:
        t = title.strip()
        if t and t not in seen:
            seen.add(t)
            unique.append(t)

    collect(book.title)
    for chap in book.chapters:
        collect(chap.title)
    for entry in book.iter_toc():
        collect(entry.title)

    if not unique:
        return

    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(unique, start=1))
    raw = llm.complete(
        system="You are an expert English-to-Vietnamese book translator.",
        messages=[
            {
                "role": "user",
                "content": TITLES_PROMPT.format(
                    title=book.title,
                    book_context=glossary.book_context,
                    glossary=glossary.to_prompt_block() or "(none)",
                    titles=numbered,
                ),
            }
        ],
        max_tokens=4000,
    )
    data = _parse_json(raw) or {}
    mapping: dict[str, str] = {}
    for i, original in enumerate(unique, start=1):
        translated = str(data.get(str(i), "")).strip()
        mapping[original] = translated or original

    book.title_translated = mapping.get(book.title.strip(), book.title)
    for chap in book.chapters:
        if chap.title.strip():
            chap.title_translated = mapping.get(chap.title.strip(), chap.title)
    for entry in book.iter_toc():
        entry.title_translated = mapping.get(entry.title.strip(), entry.title)


def translate_book(
    book: Book,
    llm: LLMClient,
    glossary: Glossary,
    cache: TranslationCache,
    max_chapters: int | None = None,
) -> None:
    """Dich noi dung tung chapter (in-place). Chunk da dich duoc cache."""
    system = SYSTEM_TEMPLATE.format(
        title=book.title,
        author=book.author or "unknown",
        book_context=glossary.book_context,
        glossary=glossary.to_prompt_block() or "(none)",
    )

    chapters = book.chapters if max_chapters is None else book.chapters[:max_chapters]
    total = len(chapters)
    for ci, chap in enumerate(chapters, start=1):
        chunks = split_html(chap.html)
        translated_parts: list[str] = []
        prev_tail = ""
        for ki, chunk in enumerate(chunks, start=1):
            print(
                f"Chương {ci}/{total} — chunk {ki}/{len(chunks)} — "
                f"đã dùng ~{llm.total_tokens // 1000}k token",
                flush=True,
            )
            key = TranslationCache.key(chunk, llm.model, glossary.version)
            translated = cache.get(key)
            if translated is None:
                translated = _translate_chunk(llm, system, chunk, prev_tail)
                cache.put(key, translated)
            translated_parts.append(translated)
            prev_tail = _text_tail(translated)
        chap.html = "".join(translated_parts)

        # thay tieu de trong noi dung bang ban dich (heading dau tien khop title goc)
        if chap.title and chap.title_translated and chap.title != chap.title_translated:
            chap.html = _replace_heading(chap.html, chap.title, chap.title_translated)


def _translate_chunk(llm: LLMClient, system: str, chunk: str, prev_tail: str) -> str:
    context_block = ""
    if prev_tail:
        context_block = (
            "For continuity, here is the end of the previous translated passage "
            f"(do NOT repeat it in your output):\n...{prev_tail}\n\n"
        )
    prompt = CHUNK_PROMPT.format(context_block=context_block, chunk=chunk)
    result = llm.complete(system=system, messages=[{"role": "user", "content": prompt}])
    result = _strip_fences(result)

    if not _tags_match(chunk, result):
        # nhac lai mot lan neu cau truc tag bi lech
        retry_prompt = prompt + (
            "\n\nIMPORTANT: your previous attempt changed the HTML structure. "
            "Preserve every HTML tag and attribute exactly as in the input."
        )
        retried = _strip_fences(
            llm.complete(system=system, messages=[{"role": "user", "content": retry_prompt}])
        )
        if _tags_match(chunk, retried):
            return retried
        print("  [canh bao] cau truc HTML lech sau retry, dung ban dich hien co", file=sys.stderr)
    return result


def _strip_fences(text: str) -> str:
    text = text.strip()
    return re.sub(r"^```(?:html|xml)?\s*|\s*```$", "", text).strip()


def _tags_match(original: str, translated: str) -> bool:
    def tag_counts(html: str) -> Counter:
        soup = BeautifulSoup(html, "lxml")
        return Counter(tag.name for tag in soup.find_all())

    a, b = tag_counts(original), tag_counts(translated)
    # cho phep lech nhe cac tag inline (model doi cach ngat), nhung tag mang ngu nghia phai khop
    strict = {"img", "table", "tr", "td", "th", "ul", "ol", "li", "a", "code", "pre",
              "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}
    for name in strict:
        if a.get(name, 0) != b.get(name, 0):
            return False
    # tong so block <p>/<div> khong duoc lech qua 20%
    for name in ("p", "div"):
        ca, cb = a.get(name, 0), b.get(name, 0)
        if ca and abs(ca - cb) > max(2, ca * 0.2):
            return False
    return True


def _text_tail(html: str, limit: int = CONTEXT_TAIL_CHARS) -> str:
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    return text[-limit:]


def _replace_heading(html: str, original: str, translated: str) -> str:
    soup = BeautifulSoup(f"<div>{html}</div>", "lxml")
    for name in ("h1", "h2", "h3"):
        for tag in soup.find_all(name):
            # sau khi dich, heading co the da la tieng Viet hoac con nguyen tieng Anh
            current = tag.get_text(" ", strip=True)
            if current == original:
                tag.string = translated
                return soup.div.decode_contents()
    return html
