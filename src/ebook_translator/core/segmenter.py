"""Cat chapter HTML thanh cac chunk vua co gui di dich.

Cat theo ranh gioi phan tu block-level top-level, khong bao gio cat giua tag.
Uoc luong token tho: ~4 ky tu / token.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

CHARS_PER_TOKEN = 4
DEFAULT_MAX_TOKENS = 3000


def split_html(html: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> list[str]:
    """Tra ve danh sach chunk HTML; ghep lai theo thu tu se ra noi dung goc."""
    max_chars = max_tokens * CHARS_PER_TOKEN
    soup = BeautifulSoup(f"<div>{html}</div>", "lxml")
    root = soup.div
    blocks = [str(child) for child in root.children if str(child).strip()]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for block in blocks:
        if current and current_len + len(block) > max_chars:
            chunks.append("".join(current))
            current, current_len = [], 0
        # mot block don qua dai (vd chapter chi co 1 <div> to) -> cat sau hon
        if len(block) > max_chars:
            for sub in _split_big_block(block, max_chars):
                if current and current_len + len(sub) > max_chars:
                    chunks.append("".join(current))
                    current, current_len = [], 0
                current.append(sub)
                current_len += len(sub)
        else:
            current.append(block)
            current_len += len(block)
    if current:
        chunks.append("".join(current))
    return chunks or [html]


def _split_big_block(block_html: str, max_chars: int) -> list[str]:
    """Block qua lon: mo mot cap wrapper va cat theo con truc tiep."""
    soup = BeautifulSoup(block_html, "lxml")
    # tim phan tu ngoai cung that su
    node = soup.body or soup
    while node is not None and len(getattr(node, "contents", []) or []) == 1 and getattr(node.contents[0], "name", None):
        node = node.contents[0]
    children = [str(c) for c in getattr(node, "children", []) if str(c).strip()]
    if len(children) <= 1:
        return [block_html]  # het cach cat sach se; chap nhan chunk to

    parts: list[str] = []
    current: list[str] = []
    current_len = 0
    for child in children:
        if current and current_len + len(child) > max_chars:
            parts.append("".join(current))
            current, current_len = [], 0
        current.append(child)
        current_len += len(child)
    if current:
        parts.append("".join(current))
    return parts
