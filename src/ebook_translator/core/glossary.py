"""Xay dung va quan ly glossary EN->VI de dam bao dich nhat quan."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from bs4 import BeautifulSoup

from ebook_translator.core.llm import LLMClient
from ebook_translator.models import Book

SAMPLE_RATIO = 0.15
MAX_SAMPLE_CHARS = 60_000

BUILD_PROMPT = """\
You are preparing a glossary for translating an English book into Vietnamese.

Book title: {title}
Author: {author}

Below are sampled excerpts from the book. Identify:
1. Proper nouns (people, places, organizations) that recur — decide whether to keep them as-is or translate them.
2. Domain terms / recurring key concepts that must be translated consistently throughout the book.
3. A one-sentence description of the book's genre and subject (in Vietnamese) to give translators context.

Rules for Vietnamese renderings:
- Person names: keep original spelling.
- Well-known place names: use the established Vietnamese name (e.g. "London" -> "Luân Đôn" only if truly conventional; otherwise keep original).
- Technical terms: translate, and put the English term in parentheses on first use is NOT needed here — just give the consistent Vietnamese term.

Return ONLY valid JSON, no markdown fence, with this shape:
{{
  "book_context": "<mo ta ngan bang tieng Viet ve the loai va noi dung>",
  "terms": {{"English term": "ban dich tieng Viet hoac giu nguyen", ...}}
}}
Limit to at most 60 of the most important terms.

Excerpts:
{samples}
"""


class Glossary:
    def __init__(self, terms: dict[str, str], book_context: str = ""):
        self.terms = terms
        self.book_context = book_context

    @property
    def version(self) -> str:
        """Hash noi dung glossary — doi glossary thi cache dich cu mat hieu luc."""
        payload = json.dumps(
            {"terms": self.terms, "ctx": self.book_context},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def to_prompt_block(self) -> str:
        if not self.terms:
            return ""
        lines = [f"- {en} => {vi}" for en, vi in sorted(self.terms.items())]
        return "\n".join(lines)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(
                {"book_context": self.book_context, "terms": self.terms},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> "Glossary":
        # utf-8-sig: file nay user sua tay duoc, editor Windows hay them BOM
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return cls(terms=data.get("terms", {}), book_context=data.get("book_context", ""))


def build_glossary(book: Book, llm: LLMClient) -> Glossary:
    """Pass 1: lay mau noi dung, nho Claude de xuat glossary."""
    samples = _sample_text(book)
    if not samples.strip():
        return Glossary(terms={})
    prompt = BUILD_PROMPT.format(title=book.title, author=book.author, samples=samples)
    raw = llm.complete(
        system="You are a professional literary translation planner.",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=4000,
    )
    data = _parse_json(raw)
    if data is None:
        return Glossary(terms={})
    terms = {
        str(k): str(v)
        for k, v in (data.get("terms") or {}).items()
        if isinstance(k, str) and k.strip()
    }
    return Glossary(terms=terms, book_context=str(data.get("book_context", "")))


def _sample_text(book: Book) -> str:
    """Lay ~15% text, rai deu cac chapter, toi da MAX_SAMPLE_CHARS."""
    parts: list[str] = []
    budget = MAX_SAMPLE_CHARS
    for chap in book.chapters:
        if budget <= 0:
            break
        text = BeautifulSoup(chap.html, "lxml").get_text(" ", strip=True)
        if not text:
            continue
        take = min(int(len(text) * SAMPLE_RATIO) + 200, 4000, budget)
        parts.append(f"--- [{chap.title or chap.id}] ---\n{text[:take]}")
        budget -= take
    return "\n\n".join(parts)


def _parse_json(raw: str) -> dict | None:
    raw = raw.strip()
    # bo markdown fence neu model lo them vao
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
        return None
