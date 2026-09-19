"""Module xử lý, trích xuất và dịch thuật bài viết từ URL web, kèm tổng hợp Audio bản dịch."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from typing import Any, Callable

from bs4 import BeautifulSoup
import requests

PROJECT_DIR = Path(__file__).resolve().parents[3]
if str(PROJECT_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from ebook_translator.core.llm import LLMClient
from ebook_translator.core.tags import (
    extract_tags_from_text,
    format_tags,
    generate_topic_tags,
)

try:
    from ebook_translator.core.tts import (
        call_tts,
        call_vbee_tts,
        call_vieneu_tts,
        call_zerotts_tts,
        get_default_engine,
        get_reader_name,
        load_env,
        resolve_voice_code,
    )
except ImportError:
    try:
        from generate_podcast import (
            call_tts,
            call_vbee_tts,
            call_vieneu_tts,
            call_zerotts_tts,
            get_reader_name,
            load_env,
            resolve_voice_code,
        )
    except ImportError:
        call_tts = None
        call_zerotts_tts = None
        call_vieneu_tts = None
        call_vbee_tts = None
        resolve_voice_code = lambda v, engine=None: v or "maichi"
        get_reader_name = lambda v, engine=None: v or "Mai Chi"
        load_env = lambda p: {}

ENV_PATH = PROJECT_DIR / ".env"
ARTICLES_DIR = PROJECT_DIR / "output" / "articles"


@dataclass
class ArticleContent:
    url: str
    title: str
    author: str
    domain: str
    publish_date: str
    text: str
    html: str
    word_count: int
    video_title: str | None = None
    chapter_title: str | None = None
    chapter_index: int | None = None
    # Mô tả tiếng Việt các đoạn quảng cáo đã bị SponsorBlock cắt (rỗng nếu không cắt gì)
    sponsor_note: str = ""
    sponsor_removed_count: int = 0

    @property
    def is_chapter(self) -> bool:
        return bool(self.chapter_title or self.chapter_index)

    @property
    def safe_stem(self) -> str:
        """Tên file chuẩn hóa an toàn: nếu là chapter thì lấy tên theo chapter."""
        if self.is_chapter:
            ch_num = self.chapter_index
            raw_name = self.chapter_title or self.title
            clean = re.sub(r"[^\w\s-]", "", raw_name).strip()
            clean = re.sub(r"[-\s]+", "_", clean)
            clean = re.sub(r"_+", "_", clean).strip("_")
            has_prefix = bool(re.match(r"^(?:chương|chuong|chapter|ch)?_?\d+", clean, re.I))
            if ch_num is not None and not has_prefix:
                return f"Chuong_{ch_num:02d}_{clean}"
            return clean if clean else f"Chuong_{ch_num or 1:02d}"

        clean = re.sub(r"[^\w\s-]", "", self.title).strip()
        clean = re.sub(r"[-\s]+", "_", clean)
        stem = clean[:60] if clean else "article"
        domain_part = re.sub(r"[^\w]", "", self.domain)
        return f"{stem}_{domain_part}"


@dataclass
class TranslatedArticle:
    original: ArticleContent
    title_vi: str
    content_vi: str
    summary_vi: str
    char_count: int
    tags: list[str] = field(default_factory=list)
    md_path: Path | None = None
    audio_path: Path | None = None

    @property
    def tags_str(self) -> str:
        return format_tags(self.tags)


USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


def fetch_and_parse_article(url: str, timeout: int = 15) -> ArticleContent:
    """Tải và trích xuất nội dung sạch của bài viết từ đường dẫn URL."""
    url = url.strip()
    parsed_url = urllib.parse.urlparse(url)
    domain = parsed_url.netloc.replace("www.", "")

    headers = {
        "User-Agent": USER_AGENTS[0],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
        "Cache-Control": "no-cache",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        html = resp.text
    except Exception:
        # Thử lại với urllib nếu requests gặp trục trặc
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as u_res:
            html = u_res.read().decode("utf-8", errors="ignore")

    soup = BeautifulSoup(html, "html.parser")

    # 1. Trích xuất tiêu đề
    title = ""
    og_title = (
        soup.find("meta", property="og:title")
        or soup.find("meta", attrs={"name": "twitter:title"})
        or soup.find("meta", attrs={"name": "title"})
    )
    if og_title and og_title.get("content"):
        title = og_title["content"].strip()
    elif soup.title and soup.title.string:
        title = soup.title.string.strip()
    elif soup.find("h1"):
        title = soup.find("h1").get_text().strip()

    # Dọn dẹp hậu tố tên trang trong title (vd: "Founder Mode - Paul Graham" -> "Founder Mode")
    for sep in (" | ", " - ", " – ", " — "):
        if sep in title and domain.split(".")[0].lower() in title.lower():
            title = title.split(sep)[0].strip()

    if not title:
        title = parsed_url.path.strip("/").split("/")[-1].replace("-", " ").replace("_", " ").title() or "Untitled Article"

    # 2. Trích xuất tác giả
    author = ""
    og_author = (
        soup.find("meta", attrs={"name": "author"})
        or soup.find("meta", property="article:author")
        or soup.find("meta", attrs={"name": "twitter:creator"})
    )
    if og_author and og_author.get("content"):
        author = og_author["content"].strip()
    if not author:
        author_elem = soup.find(class_=re.compile(r"(author|byline|writer|creator)", re.I))
        if author_elem:
            author = author_elem.get_text().strip()
            author = re.sub(r"^(by|tác giả|đăng bởi)[:\s]*", "", author, flags=re.I).strip()
    if not author and "paulgraham.com" in domain:
        author = "Paul Graham"

    # 3. Trích xuất ngày đăng
    date_str = ""
    date_elem = (
        soup.find("meta", property="article:published_time")
        or soup.find("meta", attrs={"name": "date"})
        or soup.find("time")
    )
    if date_elem:
        date_str = date_elem.get("content") or date_elem.get("datetime") or date_elem.get_text().strip()
        if date_str and "T" in date_str:
            date_str = date_str.split("T")[0]

    # 4. Loại bỏ các thẻ rác, quảng cáo, script, nav
    for tag in soup(
        [
            "script",
            "style",
            "nav",
            "header",
            "footer",
            "aside",
            "noscript",
            "form",
            "svg",
            "iframe",
            "button",
        ]
    ):
        tag.decompose()

    for ad in soup.find_all(
        class_=re.compile(
            r"(ad|advertisement|banner|social-share|share-buttons|subscribe-box|cookie|popup|modal)",
            re.I,
        )
    ):
        ad.decompose()

    # 5. Tìm khối nội dung chính
    container = (
        soup.find("article")
        or soup.find(attrs={"itemprop": "articleBody"})
        or soup.find("main")
        or soup.find(class_=re.compile(r"(post|article|entry|story|content)[-_]?(content|body|text|main)", re.I))
    )

    extracted_text = ""
    if container:
        # Trích xuất các đoạn văn bản có ý nghĩa
        items = []
        for elem in container.find_all(["p", "h2", "h3", "h4", "blockquote", "li"]):
            txt = elem.get_text().strip()
            if len(txt) > 25:
                if elem.name in ("h2", "h3", "h4"):
                    items.append(f"\n### {txt}\n")
                elif elem.name == "blockquote":
                    items.append(f"> {txt}")
                elif elem.name == "li":
                    items.append(f"- {txt}")
                else:
                    items.append(txt)
        if sum(len(x) for x in items) > 250:
            extracted_text = "\n\n".join(items)

    # Nếu container chuẩn không tìm thấy hoặc nội dung quá ngắn (vd: site dạng Paul Graham)
    if not extracted_text or len(extracted_text) < 250:
        body = soup.body or soup
        for br in body.find_all("br"):
            br.replace_with("\n")
        raw_lines = [re.sub(r"[ \t]+", " ", line).strip() for line in body.get_text().splitlines()]
        blocks = []
        curr = []
        for line in raw_lines:
            if line:
                curr.append(line)
            elif curr:
                block = " ".join(curr).strip()
                if len(block) > 30 and not any(
                    noise in block.lower()
                    for noise in ["all rights reserved", "subscribe now", "terms of service", "privacy policy"]
                ):
                    blocks.append(block)
                curr = []
        if curr:
            block = " ".join(curr).strip()
            if len(block) > 30:
                blocks.append(block)
        extracted_text = "\n\n".join(blocks)

    # Làm sạch khoảng trắng thừa
    clean_text = re.sub(r"\n{3,}", "\n\n", extracted_text).strip()
    word_count = len(clean_text.split())

    return ArticleContent(
        url=url,
        title=title,
        author=author or "Tác giả",
        domain=domain,
        publish_date=date_str,
        text=clean_text,
        html=str(container) if container else html[:5000],
        word_count=word_count,
    )


ARTICLE_TRANSLATE_SYSTEM_PROMPT = """\
Bạn là một dịch giả và nhà báo tài ba, chuyên chuyển ngữ các bài viết, bài luận, và phân tích sâu sắc từ tiếng Anh sang tiếng Việt.

NGUYÊN TẮC DỊCH THUẬT:
1. TRUNG THỰC & CHUẨN XÁC: Truyền tải trọn vẹn mọi ý tưởng, dẫn chứng, và sắc thái cảm xúc của tác giả. Tuyệt đối không thêm thắt quan điểm cá nhân, không cắt xén nội dung.
2. VĂN PHONG TIẾNG VIỆT TỰ NHIÊN: Diễn đạt bằng câu cú gãy gọn, trong sáng, giàu nhạc điệu, chuẩn văn phong báo chí/tri thức hiện đại. Tránh lối dịch 'word-by-word' gượng gạo.
3. THUẬT NGỮ CHUYÊN MÔN: Dịch nghĩa tiếng Việt rõ ràng, kèm thuật ngữ gốc tiếng Anh đặt trong ngoặc đơn ở lần xuất hiện đầu tiên (Ví dụ: "Chế độ Nhà sáng lập (Founder Mode)", "Bẫy quản trị gián tiếp (Micromanagement Trap)").
4. CẤU TRÚC ĐỊNH DẠNG:
   - Dòng 1: Tiêu đề tiếng Việt chuẩn xác, hấp dẫn.
   - Các tiêu đề phụ giữ định dạng markdown ## hoặc ###.
   - Các đoạn văn tách bạch mạch lạc bằng 2 dấu xuống dòng.
   - Đảm bảo toàn bộ bài viết từ đầu đến cuối đều được dịch đầy đủ.
"""


# ── Dịch theo lượt nói, giữ nguyên ranh giới người nói ──

_TURN_TAG = "@@T{idx}@@"
_TURN_TAG_RE = re.compile(r"@@T(\d+)@@")

_TURN_TRANSLATE_SUFFIX = """

QUY TẮC ĐẶC BIỆT VỀ DẤU PHÂN LƯỢT:
- Văn bản được chia thành các lượt nói, mỗi lượt bắt đầu bằng một dấu dạng @@T0@@, @@T1@@, @@T2@@...
- Hãy GIỮ NGUYÊN các dấu này, đúng số thứ tự, đúng vị trí đầu mỗi lượt, trong bản dịch.
- KHÔNG thêm dấu mới, KHÔNG bỏ bớt dấu, KHÔNG đổi số thứ tự, KHÔNG dịch chính các dấu đó.
- Dịch đầy đủ toàn bộ nội dung giữa các dấu, không cắt xén.
- KHÔNG xuất tiêu đề bài viết; chỉ xuất đúng các lượt nói đã đánh dấu."""


def translate_turns(
    turns: list[tuple[str, str]],
    llm: LLMClient | None = None,
    model: str | None = None,
    title: str = "",
    author: str = "",
    max_chunk_chars: int = 10000,
    on_progress: Callable[[str, str], None] | None = None,
) -> list[tuple[str, str]]:
    """Dịch danh sách lượt nói sang tiếng Việt, GIỮ NGUYÊN ranh giới người nói.

    Ranh giới người nói được phát hiện trên transcript gốc (nhờ marker ">>"),
    nhưng phần đọc lại là bản dịch tiếng Việt — nên phải dịch theo lượt thì ánh
    xạ người nói ↔ giọng đọc mới còn đúng.

    Mỗi lượt được đánh dấu @@Tn@@ và mô hình được yêu cầu giữ nguyên dấu. Nếu một
    chunk trả về không đủ dấu, chunk đó được dịch lại từng lượt một; nếu vẫn hỏng
    thì gộp cả chunk vào một lượt (mất ranh giới nhưng KHÔNG mất nội dung).
    """
    if not turns:
        return []
    if llm is None:
        llm = LLMClient(model=model)

    # Gom lượt thành chunk, không bao giờ cắt giữa một lượt
    chunks: list[list[int]] = []
    current: list[int] = []
    current_len = 0
    for idx, (_, text) in enumerate(turns):
        if current and current_len + len(text) > max_chunk_chars:
            chunks.append(current)
            current, current_len = [], 0
        current.append(idx)
        current_len += len(text)
    if current:
        chunks.append(current)

    print(f"🧠 Đang dịch {len(turns)} lượt nói theo {len(chunks)} cụm bằng AI ({llm.model})...")

    def translate_one(text: str) -> str:
        return llm.complete(
            system=ARTICLE_TRANSLATE_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Dịch trọn vẹn đoạn lời thoại sau sang tiếng Việt. "
                    f"Chỉ trả về bản dịch, không thêm tiêu đề hay giải thích.\n\n{text}"
                ),
            }],
            max_tokens=6000,
        ).strip()

    translated: dict[int, str] = {}

    for c_idx, chunk in enumerate(chunks, 1):
        detail = f"Đang dịch cụm lời thoại {c_idx}/{len(chunks)}..."
        print(f"   ⏳ {detail}")
        if on_progress:
            on_progress(detail, llm.model)

        if len(chunk) == 1:
            idx = chunk[0]
            translated[idx] = translate_one(turns[idx][1])
            continue

        tagged = "\n\n".join(_TURN_TAG.format(idx=i) + " " + turns[i][1] for i in chunk)
        prompt = (
            f"Dịch trọn vẹn phần lời thoại sau sang tiếng Việt:\n"
            f"Tiêu đề nguồn: '{title}'\n"
            f"Tác giả / kênh: '{author}'\n\n{tagged}"
        )
        try:
            res = llm.complete(
                system=ARTICLE_TRANSLATE_SYSTEM_PROMPT + _TURN_TRANSLATE_SUFFIX,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=8000,
            ).strip()
        except Exception as e:
            print(f"   ⚠️ Cụm {c_idx} lỗi khi dịch: {e}", file=sys.stderr)
            res = ""

        parts = _split_tagged(res, chunk)
        if parts is not None:
            translated.update(parts)
            continue

        # Dự phòng 1: dịch từng lượt một (chậm hơn nhưng chắc chắn giữ ranh giới)
        print(
            f"   🔄 Cụm {c_idx}: mô hình làm mất dấu phân lượt, chuyển sang dịch từng lượt...",
            file=sys.stderr,
        )
        ok = True
        for idx in chunk:
            try:
                translated[idx] = translate_one(turns[idx][1])
            except Exception as e:
                print(f"   ⚠️ Lượt {idx} lỗi: {e}", file=sys.stderr)
                ok = False
                break
        if ok:
            continue

        # Dự phòng 2: gộp cả cụm vào lượt đầu — mất ranh giới, KHÔNG mất nội dung
        print(
            f"   ⚠️ Cụm {c_idx}: gộp {len(chunk)} lượt vào một lượt để không mất nội dung.",
            file=sys.stderr,
        )
        merged = res or "\n\n".join(turns[i][1] for i in chunk)
        translated[chunk[0]] = _TURN_TAG_RE.sub("", merged).strip()
        for idx in chunk[1:]:
            translated[idx] = ""

    out: list[tuple[str, str]] = []
    for idx, (speaker_id, _) in enumerate(turns):
        body = (translated.get(idx) or "").strip()
        if body:
            out.append((speaker_id, body))
    return out


def _split_tagged(res: str, chunk: list[int]) -> dict[int, str] | None:
    """Tách kết quả dịch theo dấu @@Tn@@. Trả None nếu thiếu dấu."""
    if not res:
        return None
    matches = list(_TURN_TAG_RE.finditer(res))
    found = [int(m.group(1)) for m in matches]
    if sorted(found) != sorted(chunk):
        return None

    parts: dict[int, str] = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(res)
        body = res[m.end():end].strip()
        if body:
            parts[int(m.group(1))] = body
    # Mọi lượt phải có nội dung, nếu không coi như hỏng
    return parts if len(parts) == len(chunk) else None


def translate_article(
    article: ArticleContent,
    llm: LLMClient | None = None,
    model: str | None = None,
    save_output: bool = True,
    on_progress: Callable[[str, str], None] | None = None,
) -> TranslatedArticle:
    """Dịch toàn văn bài viết sang tiếng Việt và lưu trữ kết quả."""
    if llm is None:
        llm = LLMClient(model=model)

    print(f"🧠 Đang dịch bài viết '{article.title}' ({article.word_count} từ) bằng AI ({llm.model})...")

    # Nếu bài viết rất dài (> 12.000 ký tự), chia đoạn để dịch đảm bảo chất lượng
    max_chunk_chars = 12000
    if len(article.text) > max_chunk_chars:
        paragraphs = article.text.split("\n\n")
        chunks = []
        current_chunk = []
        current_len = 0
        for p in paragraphs:
            if current_len + len(p) > max_chunk_chars and current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = [p]
                current_len = len(p)
            else:
                current_chunk.append(p)
                current_len += len(p)
        if current_chunk:
            chunks.append("\n\n".join(current_chunk))

        translated_chunks = []
        title_vi = ""
        for idx, chunk in enumerate(chunks, start=1):
            detail = f"Đang dịch phần {idx}/{len(chunks)}..."
            print(f"   ⏳ {detail}")
            if on_progress:
                on_progress(detail, llm.model)
            prompt = (
                f"Dịch trọn vẹn phần {idx}/{len(chunks)} của bài viết sau sang tiếng Việt:\n"
                f"Tiêu đề bài viết gốc: '{article.title}'\n"
                f"Tác giả: '{article.author}'\n\n"
                f"{chunk}"
            )
            res = llm.complete(
                system=ARTICLE_TRANSLATE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=6000,
            ).strip()

            if idx == 1:
                # Trích xuất tiêu đề dịch ở phần đầu nếu có markdown header
                lines = res.splitlines()
                if lines and lines[0].startswith(("#", "**")):
                    title_vi = lines[0].strip("#* ").strip()
            translated_chunks.append(res)

        content_vi = "\n\n".join(translated_chunks)
        if not title_vi:
            try:
                title_vi = llm.complete(
                    system="Dịch tiêu đề bài viết sau sang tiếng Việt tự nhiên, không thêm dấu ngoặc hay giải thích:",
                    messages=[{"role": "user", "content": article.title}],
                    max_tokens=100,
                ).strip().strip('"\'*#')
            except Exception:
                title_vi = article.title
    else:
        if on_progress:
            on_progress("Đang dịch toàn văn nội dung...", llm.model)
        prompt = (
            f"Hãy dịch toàn văn bài viết sau sang tiếng Việt chất lượng cao:\n\n"
            f"Tiêu đề gốc: {article.title}\n"
            f"Tác giả: {article.author}\n"
            f"Nguồn: {article.domain} ({article.url})\n\n"
            f"Nội dung bài viết:\n{article.text}"
        )
        res = llm.complete(
            system=ARTICLE_TRANSLATE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=16384,
        ).strip()

        lines = res.splitlines()
        first_line = lines[0].strip() if lines else ""
        if first_line.startswith(("#", "**")):
            title_vi = first_line.strip("#* ").strip()
            content_vi = "\n".join(lines[1:]).strip()
        elif len(first_line) < 150 and not first_line.endswith("."):
            title_vi = first_line
            content_vi = "\n".join(lines[1:]).strip()
        else:
            title_vi = article.title
            content_vi = res

    # Nếu nội dung là 1 Chapter của video YouTube: đảm bảo title_vi là tiêu đề chương tiếng Việt súc tích
    if article.is_chapter:
        try:
            ch_raw = article.chapter_title or article.title
            ch_prompt = (
                f"Dịch tên phân đoạn/chapter sau sang tiếng Việt chuẩn xác, súc tích (chỉ trả về tiêu đề dịch, không giải thích hay thêm ngoặc): '{ch_raw}'"
            )
            ch_vi = llm.complete(
                system="Bạn là chuyên gia biên dịch ngôn ngữ. Hãy dịch tên chương súc tích, tự nhiên, không thêm số thứ tự.",
                messages=[{"role": "user", "content": ch_prompt}],
                max_tokens=60,
            ).strip().strip('"\'*#')
            if ch_vi:
                title_vi = ch_vi
        except Exception:
            pass

    # Tạo tóm tắt nhanh 1-2 câu kèm 1-2 hashtag chủ đề để làm caption/preview
    if on_progress:
        on_progress("Đang đúc kết bản tóm tắt cốt lõi và chủ đề...", llm.model)
    summary_prompt = (
        f"Hãy đọc nội dung bài viết sau và thực hiện 2 nhiệm vụ:\n"
        f"1. Viết một tóm tắt ngắn gọn trong đúng 1-2 câu súc tích (khoảng 50-80 từ) "
        f"đúc kết thông điệp cốt lõi nhất của bài viết. "
        f"Đi thẳng vào nội dung chính, tuyệt đối không dùng câu rào đón như 'Thông điệp cốt lõi là...' hay 'Bài viết này...'.\n"
        f"2. Gợi ý đúng 1-2 hashtag ngắn gọn liên quan mật thiết nhất đến chủ đề cốt lõi "
        f"(tiếng Việt không dấu hoặc tiếng Anh, viết thường, có dấu #, ví dụ: #google #seo, #startup #quanly, #ai #tech).\n\n"
        f"Định dạng trả về:\n"
        f"Tóm tắt: <câu tóm tắt>\n"
        f"Tags: <#tag1 #tag2>\n\n"
        f"Nội dung bài viết:\n{content_vi[:3500]}"
    )
    res_summary = llm.complete(
        system="Bạn là trợ lý đúc kết thông điệp sắc bén và gắn hashtag chủ đề chuẩn xác.",
        messages=[{"role": "user", "content": summary_prompt}],
        max_tokens=500,
    ).strip()

    tags: list[str] = []
    summary_vi = res_summary
    if "Tags:" in res_summary or "tags:" in res_summary.lower():
        parts = re.split(r"(?i)\n\s*tags:\s*", res_summary, maxsplit=1)
        if len(parts) == 2:
            summary_vi = re.sub(r"(?i)^tóm\s*tắt:\s*", "", parts[0].strip()).strip()
            tags = extract_tags_from_text(parts[1])

    if not tags:
        tags = generate_topic_tags(
            title=title_vi or article.title,
            text=f"{summary_vi} {content_vi[:500]}",
            domain=article.domain,
        )

    tags_str = format_tags(tags)

    md_path = None
    if save_output:
        ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
        md_path = ARTICLES_DIR / f"{article.safe_stem}_vi.md"
        meta_lines = [f"# {title_vi}\n"]
        if article.is_chapter:
            meta_lines.append(f"**Chương:** {article.title}  ")
            if article.video_title:
                meta_lines.append(f"**Video gốc:** {article.video_title}  ")
        else:
            meta_lines.append(f"**Tiêu đề gốc:** {article.title}  ")
        meta_lines.append(f"**Tác giả / Kênh:** {article.author}  ")
        meta_lines.append(f"**Nguồn:** [{article.domain}]({article.url})  ")
        meta_lines.append(f"**Thời gian trích xuất:** {time.strftime('%Y-%m-%d %H:%M:%S')}  \n")
        meta_lines.append(f"> 💡 **Tóm tắt cốt lõi:** {summary_vi}  ")
        meta_lines.append(f"> 🏷️ **Chủ đề:** {tags_str}\n\n---\n\n")
        meta_header = "\n".join(meta_lines)
        full_md = meta_header + content_vi
        md_path.write_text(full_md, encoding="utf-8")
        print(f"📝 Đã lưu bản dịch bài viết tại: {md_path}")

    return TranslatedArticle(
        original=article,
        title_vi=title_vi,
        content_vi=content_vi,
        summary_vi=summary_vi,
        char_count=len(content_vi),
        tags=tags,
        md_path=md_path,
    )


def clean_text_for_tts(text: str) -> str:
    """Làm sạch các ký tự cú pháp markdown để giọng đọc AI đọc trơn tru và tự nhiên."""
    # Bỏ markdown links [text](url) -> text
    cleaned = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    # Bỏ URLs trần
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    # Bỏ tiêu đề markdown #, ##, ###
    cleaned = re.sub(r"^#{1,6}\s*", "", cleaned, flags=re.MULTILINE)
    # Bỏ dấu in đậm/nghiêng
    cleaned = cleaned.replace("**", "").replace("*", "").replace("`", "")
    # Bỏ trích dẫn >
    cleaned = re.sub(r"^>\s*", "", cleaned, flags=re.MULTILINE)
    # Bỏ gạch phân cách
    cleaned = re.sub(r"^[-\*_]{3,}\s*$", "", cleaned, flags=re.MULTILINE)
    # Bỏ dấu gạch đầu dòng
    cleaned = re.sub(r"^[-•]\s*", "", cleaned, flags=re.MULTILINE)
    # Chuẩn hóa khoảng trắng và dòng trống
    cleaned = re.sub(r"\n{2,}", "\n\n", cleaned)
    return cleaned.strip()


def generate_article_audio(
    translated: TranslatedArticle,
    output_path: Path | None = None,
    voice: str | None = None,
    speed: float = 1.15,
    engine: str | None = None,
) -> Path | None:
    """Tổng hợp file Audio MP3 đọc trọn vẹn bản dịch bài viết bằng ZeroTTS (mặc định) hoặc VieNeu / Vbee."""
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    if output_path is None:
        if translated.original.is_chapter:
            output_path = ARTICLES_DIR / f"{translated.original.safe_stem}.mp3"
        else:
            output_path = ARTICLES_DIR / f"{translated.original.safe_stem}_vi.mp3"

    env = load_env(ENV_PATH)
    active_engine = (engine or os.environ.get("TTS_ENGINE") or env.get("TTS_ENGINE") or "zerotts").lower().strip()
    target_voice = resolve_voice_code(voice, engine=active_engine)
    reader_name = get_reader_name(target_voice, engine=active_engine)

    # Biên soạn kịch bản đọc bài viết với lời dẫn chuyên nghiệp
    clean_body = clean_text_for_tts(translated.content_vi)
    spoken_intro = f"{translated.title_vi}.\n\n"
    if translated.original.is_chapter:
        spoken_outro = f"\n\nCác bạn vừa lắng nghe bản dịch {translated.title_vi} của kênh {translated.original.author}. Cảm ơn các bạn đã lắng nghe!"
    else:
        spoken_outro = f"\n\nCác bạn vừa lắng nghe bản dịch bài viết {translated.title_vi} của tác giả {translated.original.author}. Cảm ơn các bạn đã lắng nghe!"
    full_speech_text = spoken_intro + clean_body + spoken_outro

    engine_label = "ZeroTTS 48kHz" if active_engine == "zerotts" else ("VieNeu-TTS 48kHz" if active_engine == "vieneu" else "Vbee Cloud")
    print(f"🎙️ Bắt đầu tạo Audio bài viết ({len(full_speech_text)} ký tự) bằng {engine_label} với Host [{reader_name}] ({speed}x)...")

    if call_tts is not None:
        success = call_tts(
            text=full_speech_text,
            output_path=output_path,
            voice=target_voice,
            engine=active_engine,
            speed=speed,
            fallback=True,
        )
    elif active_engine == "zerotts" and call_zerotts_tts is not None:
        success = call_zerotts_tts(text=full_speech_text, output_path=output_path, voice=target_voice, speed=speed)
    elif active_engine == "vieneu" and call_vieneu_tts is not None:
        success = call_vieneu_tts(text=full_speech_text, output_path=output_path, voice=target_voice, speed=speed)
    elif call_vbee_tts is not None:
        app_id = os.environ.get("VBEE_APP_ID") or env.get("VBEE_APP_ID")
        token = os.environ.get("VBEE_TOKEN") or env.get("VBEE_TOKEN")
        success = call_vbee_tts(text=full_speech_text, output_path=output_path, app_id=app_id, token=token, voice_code=target_voice, speed=speed)
    else:
        success = False

    if success and output_path.exists():
        print(f"🎉 Đã tạo thành công Audio bài viết: {output_path}")
        translated.audio_path = output_path
        return output_path

    print(f"❌ Không thể tạo Audio bài viết cho {translated.title_vi}", file=sys.stderr)
    return None
