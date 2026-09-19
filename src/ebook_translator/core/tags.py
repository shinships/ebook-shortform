"""tags.py — Module trích xuất và định dạng hashtag chủ đề + loại output cho ấn phẩm, audio và bài viết."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# Từ khóa phổ biến ánh xạ sang hashtag chủ đề chuẩn
TOPIC_RULES: list[tuple[list[str], list[str]]] = [
    (
        ["seo", "google search", "ranking", "crawl", "indexation", "search console", "backlink", "google"],
        ["#google", "#seo"],
    ),
    (
        ["agent", "ai agent", "agentic", "multi-agent", "autonomous agent", "mcp", "tool use"],
        ["#agent", "#ai"],
    ),
    (
        ["chatgpt", "openai", "claude", "gemini", "llm", "large language model", "generative ai", "deep learning", "machine learning", "trí tuệ nhân tạo"],
        ["#ai", "#tech"],
    ),
    (
        ["founder", "startup", "venture", "khởi nghiệp", "y combinator", "paul graham", "co-founder"],
        ["#startup", "#kinhdoanh"],
    ),
    (
        ["productivity", "deep work", "habit", "atomic habit", "năng suất", "thói quen", "tập trung", "quản lý thời gian", "time management"],
        ["#nangsuat", "#kynang"],
    ),
    (
        ["investing", "stocks", "crypto", "bitcoin", "finance", "đầu tư", "tài chính", "tiền tệ", "cổ phiếu", "kinh tế"],
        ["#taichinh", "#dautu"],
    ),
    (
        ["marketing", "branding", "sales", "bán hàng", "thương hiệu", "quảng cáo", "tiếp thị"],
        ["#marketing", "#kinhdoanh"],
    ),
    (
        ["psychology", "tâm lý", "mindset", "cảm xúc", "cognitive", "nhận thức", "tư duy"],
        ["#tamly", "#mindset"],
    ),
    (
        ["leadership", "lãnh đạo", "management", "quản trị", "quản lý đội ngũ", "teamwork"],
        ["#lanhdao", "#quanly"],
    ),
    (
        ["refugee", "immigration", "người tị nạn", "nhập cư", "malaysia", "cna", "foreigners moving"],
        ["#xahoi", "#quocte"],
    ),
    (
        ["software", "developer", "coding", "programming", "lập trình", "công nghệ", "tech", "algorithm"],
        ["#congnghe", "#tech"],
    ),
    (
        ["health", "sức khỏe", "sleep", "nutrition", "dinh dưỡng", "fitness", "tập luyện"],
        ["#suckhoe", "#lifestyle"],
    ),
    (
        ["happiness", "hanh phuc", "hạnh phúc", "enjoyment", "satisfaction", "macronutrients of happiness"],
        ["#happiness", "#hanhphuc"],
    ),
    (
        ["focus", "attention", "tập trung", "chú ý", "neuroplasticity", "dẻo thần kinh", "bộ não"],
        ["#focus", "#naobo"],
    ),
    (
        ["economist", "economics", "kinh tế", "kinh tế học"],
        ["#kinhte", "#dautu"],
    ),
    (
        ["architecture", "kiến trúc", "đốt đền", "hồ thiệu trị"],
        ["#kientruc", "#nghethuat"],
    ),
    (
        ["communication", "giao tiếp", "thuyết phục", "đàm phán", "kết nối"],
        ["#giaotiep", "#kynang"],
    ),
]

# Ánh xạ loại output sang hashtag định dạng
OUTPUT_TYPE_TAGS: dict[str, str] = {
    "short": "#short",          # Bản tóm tắt (Shortform)
    "podcast": "#podcast",      # Podcast / Audio từ sách
    "audio": "#audio",          # Bản đọc audio (bài viết/YouTube)
    "dich": "#dich",            # Bản dịch
    "article": "#article",      # Bài viết dịch
}

def remove_diacritics(text: str) -> str:
    """Loại bỏ dấu tiếng Việt để tạo slug/hashtag chuẩn."""
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d").replace("Đ", "D")
    return text


STOP_WORDS = {
    remove_diacritics(w).lower()
    for w in {
        "a", "an", "the", "in", "on", "at", "by", "for", "with", "about", "against",
        "between", "into", "through", "during", "before", "after", "above", "below",
        "to", "from", "up", "down", "of", "off", "over", "under", "again", "further",
        "then", "once", "here", "there", "when", "where", "why", "how", "all", "any",
        "both", "each", "few", "more", "most", "other", "some", "such", "no", "nor",
        "not", "only", "own", "same", "so", "than", "too", "very", "can", "will",
        "just", "should", "now", "what", "does", "mean", "are", "many", "is",
        "và", "của", "cho", "với", "về", "trong", "trên", "dưới", "tại", "từ",
        "những", "các", "một", "bài", "viết", "sách", "làm", "sao", "thế", "nào",
        "được", "người", "đang", "như", "khi", "để", "bản", "tin", "tập", "podcast",
        "cuốn", "quyển", "chương", "phần",
    }
}


def clean_tag(raw: str) -> str:
    """Chuẩn hóa một hashtag: bắt đầu bằng #, chữ thường, không dấu, không ký tự đặc biệt, tối thiểu 2 ký tự."""
    s = raw.strip().lstrip("#").strip()
    s = remove_diacritics(s).lower()
    s = re.sub(r"[^\w]", "", s)
    return f"#{s}" if len(s) >= 2 else ""


def extract_tags_from_text(text: str) -> list[str]:
    """Tìm các hashtag đã có trong chuỗi văn bản (ví dụ '#google #seo')."""
    found = re.findall(r"#([a-zA-Z0-9_\u00c0-\u024f]+)", text)
    tags = []
    for item in found:
        tag = clean_tag(item)
        if tag and tag not in tags:
            tags.append(tag)
        if len(tags) >= 2:
            break
    return tags


def generate_topic_tags(
    title: str = "",
    text: str = "",
    domain: str = "",
    llm: Any = None,
    output_type: str = "",
) -> list[str]:
    """Sinh 1-2 tag chủ đề + 1 tag loại output (nếu có).

    Args:
        output_type: Loại output — 'short', 'podcast', 'dich', 'article'.
    """
    # 1. Nếu trong text đã có sẵn format hashtag thì lấy luôn
    existing = extract_tags_from_text(text)
    if existing:
        return _append_output_tag(existing[:2], output_type)

    clean_title_space = title.replace("_", " ").replace("-", " ")
    corpus = f"{clean_title_space} {domain} {text}".lower()
    corpus_ascii = remove_diacritics(corpus)

    # 2. Quét theo bộ từ khóa quy tắc ưu tiên
    for keywords, tags in TOPIC_RULES:
        for kw in keywords:
            kw_clean = kw.lower()
            kw_ascii = remove_diacritics(kw_clean)
            if re.search(r"\b" + re.escape(kw_ascii) + r"\b", corpus_ascii) or kw_clean in corpus:
                return _append_output_tag(list(tags[:2]), output_type)

    # 3. Nếu có LLM và corpus đủ dài, thử hỏi LLM nhanh
    if llm and hasattr(llm, "complete"):
        try:
            prompt = (
                f"Đưa ra đúng 1-2 hashtag liên quan mật thiết nhất đến chủ đề sau. "
                f"Chỉ trả về 1-2 hashtag viết thường không dấu hoặc tiếng Anh, có dấu # (ví dụ: #google #seo, #ai #tech):\n\n"
                f"Tiêu đề: {title}\n"
                f"Tóm tắt: {text[:500]}"
            )
            res = llm.complete(
                system="Bạn là trợ lý gắn tag chủ đề thông minh. Chỉ trả về 1-2 hashtag cách nhau bằng dấu cách, không giải thích gì thêm.",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=30,
            ).strip()
            llm_tags = extract_tags_from_text(res)
            if llm_tags:
                return _append_output_tag(llm_tags[:2], output_type)
        except Exception:
            pass

    # 4. Trích xuất từ khóa nổi bật từ tiêu đề
    words = re.findall(r"[a-zA-Z0-9\u00c0-\u024f]+", clean_title_space)
    meaningful = [
        remove_diacritics(w).lower()
        for w in words
        if len(w) >= 3 and remove_diacritics(w).lower() not in STOP_WORDS
    ]

    selected_tags = []
    for w in meaningful:
        cleaned = clean_tag(w)
        if cleaned and cleaned not in selected_tags:
            selected_tags.append(cleaned)
        if len(selected_tags) >= 2:
            break

    if selected_tags:
        return _append_output_tag(selected_tags, output_type)

    # 5. Mặc định dự phòng
    base = ["#kienthuc", "#shortform"]
    return _append_output_tag(base, output_type)


def _append_output_tag(tags: list[str], output_type: str) -> list[str]:
    """Thêm tag loại output vào cuối danh sách nếu hợp lệ."""
    ot = output_type.strip().lower()
    ot_tag = OUTPUT_TYPE_TAGS.get(ot, "")
    if ot_tag and ot_tag not in tags:
        tags.append(ot_tag)
    return tags


def format_tags(tags: list[str]) -> str:
    """Định dạng danh sách tag thành chuỗi cách nhau bởi khoảng trắng (ví dụ: '#google #seo #short')."""
    if not tags:
        return ""
    cleaned = [clean_tag(t) for t in tags if clean_tag(t)]
    return " ".join(cleaned[:4])
