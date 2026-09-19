"""Module trích xuất transcript (phụ đề) từ video YouTube và chuyển đổi thành ArticleContent.

Quy trình:
1. Nhận diện URL YouTube (youtube.com, youtu.be, m.youtube.com).
2. Dùng yt-dlp trích xuất metadata (tiêu đề, kênh, thời lượng, chapters) và phụ đề (ưu tiên English).
3. Parse file phụ đề VTT/SRT → hỗ trợ cắt theo mốc thời gian [start, end] hoặc theo Chapter.
4. Trả về ArticleContent tương thích pipeline dịch + TTS hiện tại.
"""

from __future__ import annotations

import html
import io
import math
import re
import sys
import tempfile
import time
import urllib.request
from collections import deque
from collections.abc import Sequence
from pathlib import Path
from typing import Any

try:
    import yt_dlp
except ImportError:
    yt_dlp = None  # type: ignore[assignment]

try:
    from PIL import Image, ImageDraw, ImageFilter
except ImportError:
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]
    ImageFilter = None  # type: ignore[assignment]

_PROJECT_DIR = Path(__file__).resolve().parents[3]
if str(_PROJECT_DIR / "src") not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIR / "src"))

from ebook_translator.core.article import ArticleContent
from ebook_translator.core.sponsorblock import (
    describe_segments_vi,
    fetch_sponsor_segments,
    merge_segments,
    segments_in_window,
)


# ── 1. Nhận diện URL YouTube, Video ID & Thumbnail ──

_YT_PATTERNS = [
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/watch\?", re.I),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/live/", re.I),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/shorts/", re.I),
    re.compile(r"(?:https?://)?youtu\.be/", re.I),
    re.compile(r"(?:https?://)?m\.youtube\.com/watch\?", re.I),
]


def is_youtube_url(url: str) -> bool:
    """Kiểm tra URL có phải là liên kết YouTube hợp lệ hay không."""
    url = url.strip()
    return any(p.search(url) for p in _YT_PATTERNS)


def extract_youtube_video_id(url: str) -> str | None:
    """Trích xuất YouTube video ID (11 ký tự) từ bất kỳ dạng URL YouTube nào."""
    if not url:
        return None
    url = url.strip()
    patterns = [
        r"(?:v=|\/vi\/|youtu\.be\/|\/v\/|\/embed\/|\/shorts\/|\/live\/)([a-zA-Z0-9_-]{11})",
        r"[?&]v=([a-zA-Z0-9_-]{11})",
        r"^([a-zA-Z0-9_-]{11})$",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def download_youtube_thumbnail(video_id_or_url: str) -> Image.Image | None:
    """Tải ảnh thumbnail chất lượng cao nhất của video YouTube.

    Thử lần lượt:
      1. maxresdefault.jpg (1280x720 hoặc 1920x1080)
      2. sddefault.jpg (640x480)
      3. hqdefault.jpg (480x360)
      4. 0.jpg
    """
    if Image is None:
        return None
    vid = extract_youtube_video_id(video_id_or_url)
    if not vid:
        return None

    candidates = [
        f"https://img.youtube.com/vi/{vid}/maxresdefault.jpg",
        f"https://img.youtube.com/vi/{vid}/sddefault.jpg",
        f"https://img.youtube.com/vi/{vid}/hqdefault.jpg",
        f"https://img.youtube.com/vi/{vid}/0.jpg",
    ]

    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

    for url in candidates:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    data = resp.read()
                    img = Image.open(io.BytesIO(data)).convert("RGB")
                    # YouTube trả ảnh placeholder 120x90 nếu video không có maxres
                    if img.width > 120 and img.height > 90:
                        return img
        except Exception:
            continue
    return None


def make_square_youtube_artwork(thumbnail_img: Image.Image, size: int = 1400) -> Image.Image:
    """Chuyển đổi ảnh thumbnail 16:9 của YouTube thành ảnh vuông 1400x1400 chuẩn Apple Podcasts & CarPlay.

    Bố cục:
    - Nền: phóng to tràn khung 1400x1400, làm mờ Gaussian (radius=60), phủ tối 45% tạo hiệu ứng Ambient Blur sang trọng.
    - Tiêu điểm chính giữa: thumbnail gốc thu phóng cân đối (chiều rộng 1260px), bo góc mượt mà (radius=24)
      và viền tinh tế, giữ nguyên vẹn 100% tiêu đề chữ và gương mặt nhân vật mà không bị xén.
    """
    if Image is None:
        return thumbnail_img

    # 1. Nền mờ Ambient Blur
    bg_ratio = max(size / thumbnail_img.width, size / thumbnail_img.height)
    bg_w, bg_h = int(thumbnail_img.width * bg_ratio), int(thumbnail_img.height * bg_ratio)
    bg = thumbnail_img.resize((bg_w, bg_h), Image.Resampling.BILINEAR)
    left = (bg_w - size) // 2
    top = (bg_h - size) // 2
    bg = bg.crop((left, top, left + size, top + size))
    bg = bg.filter(ImageFilter.GaussianBlur(radius=60))
    dark = Image.new("RGB", (size, size), (15, 15, 20))
    bg = Image.blend(bg, dark, 0.45)

    # 2. Ảnh thẻ chính 16:9 ở giữa
    card_w = 1260
    scale = card_w / thumbnail_img.width
    card_h = int(thumbnail_img.height * scale)
    fg = thumbnail_img.resize((card_w, card_h), Image.Resampling.LANCZOS)

    # Bo góc radius 24
    radius = 24
    mask = Image.new("L", (card_w, card_h), 0)
    draw_mask = ImageDraw.Draw(mask)
    draw_mask.rounded_rectangle([(0, 0), (card_w, card_h)], radius=radius, fill=255)

    offset_x = (size - card_w) // 2
    offset_y = (size - card_h) // 2
    bg.paste(fg, (offset_x, offset_y), mask)

    # Viền mờ tinh tế
    draw_bg = ImageDraw.Draw(bg)
    draw_bg.rounded_rectangle(
        [(offset_x, offset_y), (offset_x + card_w, offset_y + card_h)],
        radius=radius,
        outline=(255, 255, 255, 50),
        width=2,
    )
    return bg


def generate_youtube_podcast_cover(video_id_or_url: str, output_path: Path) -> Path | None:
    """Tải thumbnail YouTube, tạo ảnh vuông 1400x1400 chuẩn Podcast và lưu vào đĩa."""
    raw_img = download_youtube_thumbnail(video_id_or_url)
    if not raw_img:
        return None
    artwork = make_square_youtube_artwork(raw_img)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    artwork.save(output_path, "JPEG", quality=92)
    return output_path


def parse_time_str(time_val: str | int | float | None) -> float | None:
    """Chuyển đổi chuỗi thời gian thành số giây (float).

    Hỗ trợ:
      - Số giây thuần túy: 2748, "2748", 300.5
      - Định dạng MM:SS: "45:48", "05:30"
      - Định dạng HH:MM:SS: "01:15:30", "00:45:48"
      - Định dạng YouTube: "45m48s", "1h15m", "300s", "1h2m3s"
    """
    if time_val is None:
        return None
    if isinstance(time_val, (int, float)):
        return max(0.0, float(time_val))

    val = str(time_val).strip().lower()
    if not val:
        return None

    # 1. Định dạng 1h2m3s / 45m48s / 300s / 10m
    m_hms = re.match(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s?)?$", val)
    if m_hms and (m_hms.group(1) or m_hms.group(2) or (m_hms.group(3) and any(c in val for c in "hms"))):
        hours = float(m_hms.group(1) or 0)
        minutes = float(m_hms.group(2) or 0)
        seconds = float(m_hms.group(3) or 0)
        return max(0.0, hours * 3600 + minutes * 60 + seconds)

    # 2. Định dạng HH:MM:SS hoặc MM:SS
    parts = val.split(":")
    if len(parts) == 3:
        try:
            return max(0.0, float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2]))
        except ValueError:
            pass
    elif len(parts) == 2:
        try:
            return max(0.0, float(parts[0]) * 60 + float(parts[1]))
        except ValueError:
            pass

    # 3. Số giây thuần túy
    try:
        return max(0.0, float(val))
    except ValueError:
        return None


def format_time_str(seconds: float | int | None) -> str:
    """Định dạng thời lượng từ giây thành chuỗi HH:MM:SS hoặc MM:SS."""
    if seconds is None or seconds < 0:
        return "00:00"
    secs_int = int(seconds)
    hours = secs_int // 3600
    minutes = (secs_int % 3600) // 60
    secs = secs_int % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def extract_time_from_url(url: str) -> float | None:
    """Trích xuất mốc thời gian bắt đầu từ tham số URL (ví dụ: ?t=2748 hoặc &t=45m48s)."""
    m = re.search(r"[?&#]t=([0-9hms]+)", url, re.I)
    if m:
        return parse_time_str(m.group(1))
    return None


# ── 2. Trích xuất metadata video ──

def extract_youtube_info(url: str) -> dict[str, Any]:
    """Trích xuất metadata (tiêu đề, kênh, thời lượng, mô tả, chapters) từ video YouTube.

    Trả về dict với các key: title, channel, duration, description, video_id, url, chapters.
    Không tải video, chỉ lấy thông tin.
    """
    if yt_dlp is None:
        raise ImportError("yt-dlp chưa được cài đặt. Chạy: pip install yt-dlp")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    return {
        "title": info.get("title", "Untitled Video"),
        "channel": info.get("channel") or info.get("uploader") or "Unknown Channel",
        "duration": info.get("duration", 0),
        "description": info.get("description", ""),
        "video_id": info.get("id", ""),
        "url": info.get("webpage_url") or url,
        "upload_date": info.get("upload_date", ""),
        "view_count": info.get("view_count", 0),
        "like_count": info.get("like_count", 0),
        "chapters": info.get("chapters") or [],
    }


# Mỗi lần gọi get_youtube_chapters là một vòng extract_info đầy đủ (vài giây).
# Bàn phím chọn chương trên Telegram re-render sau mỗi cú tap nên phải có memo,
# nếu không callback sẽ quá hạn.
_CHAPTERS_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_CHAPTERS_TTL = 1800.0


def get_youtube_chapters(url: str, *, use_cache: bool = True) -> list[dict[str, Any]]:
    """Lấy danh sách các Chapter (chương mục) từ video YouTube nếu có.

    Kết quả được memo theo video id trong _CHAPTERS_TTL giây để tránh gọi lặp
    extract_info (bot có thể hỏi nhiều lần trong một lượt tương tác).
    """
    cache_key = extract_youtube_video_id(url) or url
    if use_cache:
        hit = _CHAPTERS_CACHE.get(cache_key)
        if hit and (time.time() - hit[0]) < _CHAPTERS_TTL:
            return hit[1]

    info = extract_youtube_info(url)
    chapters = info.get("chapters") or []
    _CHAPTERS_CACHE[cache_key] = (time.time(), chapters)
    return chapters


def clean_chapter_raw_title(raw_title: str) -> str:
    """Loại bỏ các tiền tố số thứ tự hoặc 'Chapter X:' có sẵn trong title trên YouTube."""
    cleaned = raw_title.strip()
    cleaned = re.sub(r"^(?:chapter|chương|ch\.?|part)?\s*\d+[\s:.-]+", "", cleaned, flags=re.I).strip()
    return cleaned if cleaned else raw_title.strip()


# ── 2b. Chọn chương: số lẻ, dải, hoặc danh sách rời rạc ──

# Dấu phân cách dải ("9-11", "9 đến 11") và danh sách ("9,10,11", "2 + 5").
_CH_RANGE_SEP = r"(?:-|–|—|→|to|đến|den|tới|toi)"
_CH_LIST_SEP = r"[,+&]"
_CH_PREFIX = r"^(?:chapters?|chương|chuong|ch)\.?\s*"


def parse_chapter_spec(spec: str | int | None) -> list[int]:
    """Phân tích chuỗi chọn chương thành danh sách chỉ số 1-based.

    "9" -> [9];  "9-11" -> [9,10,11];  "9,10,11" -> [9,10,11];  "2,5" -> [2,5]
    "chương 9" -> [9];  "9 đến 11" -> [9,10,11]

    Trả về [] nếu không phải dạng số thuần (ví dụ "Chapter 9: Mở đầu") để
    caller tự fallback sang khớp theo tên chapter.
    """
    if spec is None:
        return []
    if isinstance(spec, int):
        return [spec] if spec > 0 else []

    text = str(spec).strip().lower()
    if not text:
        return []
    text = re.sub(_CH_PREFIX, "", text).strip()
    if not text:
        return []

    # Chỉ chấp nhận chuỗi thuần số + dấu phân cách, còn lại coi như tên chapter.
    shape = rf"\d{{1,3}}(?:\s*(?:{_CH_RANGE_SEP}|{_CH_LIST_SEP})\s*\d{{1,3}})*"
    if not re.fullmatch(shape, text):
        return []

    picked: set[int] = set()
    for part in re.split(_CH_LIST_SEP, text):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(rf"(\d{{1,3}})\s*{_CH_RANGE_SEP}\s*(\d{{1,3}})", part)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo > hi:
                lo, hi = hi, lo
            picked.update(range(lo, hi + 1))
        elif part.isdigit():
            picked.add(int(part))
        else:
            return []

    return sorted(i for i in picked if i > 0)


def compact_chapter_spec(indices: Sequence[int]) -> str:
    """Nén danh sách chỉ số về dạng chuỗi ngắn gọn để lưu vào job dict.

    [9,10,11] -> "9-11";  [2,5] -> "2,5";  [9] -> "9";  [] -> ""
    """
    idxs = sorted({int(i) for i in (indices or []) if int(i) > 0})
    if not idxs:
        return ""

    parts: list[str] = []
    run_start = prev = idxs[0]
    for i in idxs[1:]:
        if i == prev + 1:
            prev = i
            continue
        parts.append(str(run_start) if run_start == prev else f"{run_start}-{prev}")
        run_start = prev = i
    parts.append(str(run_start) if run_start == prev else f"{run_start}-{prev}")
    return ",".join(parts)


def _is_contiguous(indices: Sequence[int]) -> bool:
    """Danh sách chỉ số có liền mạch không (đã sort, không trùng)."""
    return bool(indices) and list(indices) == list(range(indices[0], indices[-1] + 1))


# ── 2c. Số học khoảng thời gian (dùng chung cho chọn chương và cắt quảng cáo) ──

def _normalize_ranges(
    ranges: Sequence[tuple[float | None, float | None]] | None,
    epsilon: float = 0.5,
) -> list[tuple[float, float | None]]:
    """Sắp xếp và gộp các khoảng chồng lấn hoặc liền kề (cách nhau <= epsilon giây).

    epsilon là bắt buộc: yt-dlp cho chapters[n].end_time == chapters[n+1].start_time,
    không gộp thì một dải chương liền kề sẽ thành nhiều cửa sổ rời và cue ở biên bị
    kiểm tra hai lần.
    """
    cleaned: list[tuple[float, float]] = []
    for item in ranges or []:
        if not item:
            continue
        raw_s, raw_e = item
        s = 0.0 if raw_s is None else float(raw_s)
        e = math.inf if raw_e is None else float(raw_e)
        if e < s:
            s, e = e, s
        cleaned.append((s, e))

    if not cleaned:
        return []

    cleaned.sort()
    merged: list[tuple[float, float]] = [cleaned[0]]
    for s, e in cleaned[1:]:
        last_s, last_e = merged[-1]
        if s <= last_e + epsilon:
            merged[-1] = (last_s, max(last_e, e))
        else:
            merged.append((s, e))

    return [(s, None if math.isinf(e) else e) for s, e in merged]


def _in_any_range(
    c_start: float,
    c_end: float,
    ranges: Sequence[tuple[float | None, float | None]],
) -> bool:
    """Cue có GIAO với khoảng nào không.

    Dùng cho cửa sổ cần GIỮ — overlap (chứ không phải chứa trọn) để không đánh
    rơi câu nằm vắt ngang biên chương. Yêu cầu phần giao có độ dài dương: một cue
    kết thúc đúng lúc cửa sổ bắt đầu thì không chứa nội dung nào của cửa sổ, và
    nếu tính là giao thì khi chọn nhiều chương rời rạc, cue nằm trọn trong khe
    giữa hai chương vẫn lọt vào.
    """
    for s, e in ranges:
        lo = -math.inf if s is None else s
        hi = math.inf if e is None else e
        if c_end > lo and c_start < hi:
            return True
    return False


def _midpoint_in_any(
    c_start: float,
    c_end: float,
    ranges: Sequence[tuple[float | None, float | None]],
) -> bool:
    """Điểm giữa của cue có nằm trong khoảng nào không.

    Dùng cho đoạn cần CẮT BỎ — chặt hơn overlap để một cue chỉ chạm mép đoạn
    quảng cáo vài phần mười giây không bị vứt oan.
    """
    mid = (c_start + c_end) / 2.0
    for s, e in ranges:
        lo = 0.0 if s is None else s
        hi = math.inf if e is None else e
        if lo <= mid <= hi:
            return True
    return False


def resolve_time_ranges(
    url: str,
    start_time: str | int | float | None = None,
    end_time: str | int | float | None = None,
    chapter: str | int | None = None,
    chapters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Xác định danh sách khoảng thời gian cần giữ, kèm nhãn phân đoạn.

    Trả về dict: ranges, segment_label, chapter_index, chapter_indices,
    chapter_title, start_sec, end_sec.

    Thứ tự ưu tiên (giữ nguyên như trước):
    1. Chỉ định theo chapter — số lẻ, dải (9-11), danh sách (2,5), hoặc từ khóa tên.
    2. Chỉ định start_time và/hoặc end_time rõ ràng.
    3. Tự động đọc tham số ?t= từ URL YouTube.

    Raises:
        ValueError: khi chỉ định chapter nhưng KHÔNG có chỉ số nào hợp lệ. Cố ý
            báo lỗi thay vì im lặng xử cả video (user xin 3 phút mà nhận 65 phút).
    """
    def _result(ranges, label, ch_index=None, ch_indices=None, ch_title=None):
        norm = _normalize_ranges(ranges)
        return {
            "ranges": norm,
            "segment_label": label,
            "chapter_index": ch_index,
            "chapter_indices": ch_indices or [],
            "chapter_title": ch_title,
            "start_sec": norm[0][0] if norm else None,
            "end_sec": norm[-1][1] if norm else None,
        }

    start_sec = parse_time_str(start_time)
    end_sec = parse_time_str(end_time)

    # 1. Nếu chỉ định chapter
    if chapter is not None and chapters:
        idxs = parse_chapter_spec(chapter)

        if not idxs:
            # Không phải dạng số → khớp theo từ khóa trong tiêu đề chapter
            query = str(chapter).strip().lower()
            for idx, ch in enumerate(chapters, 1):
                if query and query in ch.get("title", "").lower():
                    idxs = [idx]
                    break

        valid = [i for i in idxs if 1 <= i <= len(chapters)]
        if idxs and not valid:
            raise ValueError(
                f"Video chỉ có {len(chapters)} chương, không tìm thấy chương "
                f"{compact_chapter_spec(idxs) or chapter}."
            )

        if valid:
            picked = [chapters[i - 1] for i in valid]
            ranges = [
                (float(ch.get("start_time", 0) or 0), float(ch.get("end_time", 0) or 0))
                for ch in picked
            ]
            titles = [
                clean_chapter_raw_title(ch.get("title", "") or f"Chương {i}")
                for ch, i in zip(picked, valid)
            ]

            if len(valid) == 1:
                ch_title = titles[0]
                label = f"Chương {valid[0]}: {ch_title}"
            elif _is_contiguous(valid):
                ch_title = f"{titles[0]} → {titles[-1]}"
                label = f"Chương {valid[0]}-{valid[-1]}: {ch_title}"
            else:
                ch_title = f"{titles[0]} + {len(valid) - 1} chương"
                label = f"Chương {', '.join(str(i) for i in valid)} ({len(valid)} chương)"

            return _result(ranges, label, ch_index=valid[0], ch_indices=valid, ch_title=ch_title)

    # 2. Nếu có start_time hoặc end_time
    if start_sec is not None or end_sec is not None:
        st_str = format_time_str(start_sec) if start_sec is not None else "00:00"
        et_str = format_time_str(end_sec) if end_sec is not None else "hết"
        return _result([(start_sec, end_sec)], f"Đoạn {st_str} - {et_str}")

    # 3. Fallback: Kiểm tra URL có chứa tham số ?t= không
    url_t = extract_time_from_url(url)
    if url_t is not None and url_t > 0:
        return _result([(url_t, None)], f"Từ mốc {format_time_str(url_t)}")

    return _result([], None)


def resolve_time_range(
    url: str,
    start_time: str | int | float | None = None,
    end_time: str | int | float | None = None,
    chapter: str | int | None = None,
    chapters: list[dict[str, Any]] | None = None,
) -> tuple[float | None, float | None, str | None, int | None, str | None]:
    """Bản tương thích ngược của resolve_time_ranges, gộp về một cửa sổ duy nhất.

    Trả về: (start_sec, end_sec, segment_label, chapter_index, chapter_title)
    """
    res = resolve_time_ranges(url, start_time, end_time, chapter, chapters)
    return (
        res["start_sec"],
        res["end_sec"],
        res["segment_label"],
        res["chapter_index"],
        res["chapter_title"],
    )


# ── 3. Tải và parse transcript (phụ đề) ──

# Ranh giới lượt nói được giữ lại trong transcript để tầng phân vai (speakers.py)
# nhận diện. Dùng đúng chuỗi ">>" mà phụ đề YouTube vốn dùng.
_TURN_MARKER = "\n>>"

# Auto-caption lặp dòng theo kiểu cuộn; bản lặp luôn nằm kề nhau nên cửa sổ nhỏ là đủ.
_DEDUP_WINDOW = 3


_SUBTITLE_LANG_PRIORITY = [
    "en",       # English manual
    "en-US",
    "en-GB",
    "en-orig",  # Auto-generated English (một số video dùng key này)
    "vi",       # Vietnamese nếu có
]


def _parse_timestamp_to_seconds(ts_str: str) -> float | None:
    """Chuyển đổi timestamp phụ đề (HH:MM:SS.mmm hoặc MM:SS.mmm) thành số giây."""
    parts = ts_str.strip().replace(",", ".").split(":")
    try:
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
    except ValueError:
        pass
    return None


_CUE_TS_RE = re.compile(
    r"((?:\d{2}:)?\d{2}:\d{2}[\.,]\d{3})\s*-->\s*((?:\d{2}:)?\d{2}:\d{2}[\.,]\d{3})"
)

# Ký tự kết câu — dùng để vá chỗ nối sau khi một đoạn bị cắt bỏ.
_SENTENCE_END = '.?!…"\''


def _parse_cue_blocks(
    raw_text: str,
    keep_ranges: Sequence[tuple[float | None, float | None]] | None = None,
    skip_ranges: Sequence[tuple[float | None, float | None]] | None = None,
    *,
    is_vtt: bool,
) -> str:
    """Parse khối cue của phụ đề VTT/SRT thành plain text.

    Một cue được giữ khi nó GIAO với keep_ranges (hoặc keep_ranges rỗng = giữ tất cả)
    VÀ điểm giữa của nó KHÔNG rơi vào skip_ranges. Hai bộ lọc độc lập, AND với nhau
    theo từng cue — không làm phép trừ khoảng.

    Khi có cue bị bỏ giữa chừng, hàm đánh dấu gap_pending để chấm câu cho dòng liền
    trước, tránh câu trước và câu sau chỗ cắt dính liền thành một. CỐ Ý không chèn
    marker ">>" giả: speakers.detect_speakers() coi ">>" là tín hiệu đổi người nói
    cứng, marker giả sẽ bịa ra một lần đổi vai và lệch pha toàn bộ phía sau.
    """
    blocks = re.split(r"\r?\n\r?\n", raw_text)
    text_lines: list[str] = []
    # Auto-caption lặp dòng theo kiểu cuộn (rolling), các bản lặp luôn NẰM KỀ NHAU.
    # Dùng cửa sổ trượt thay vì set toàn cục để không đánh rơi câu lặp lại hợp lệ.
    seen: deque[str] = deque(maxlen=_DEDUP_WINDOW)
    gap_pending = False

    def _mark_gap() -> None:
        """Ghi nhận có nội dung bị cắt ngay sau phần đã emit."""
        nonlocal gap_pending
        if text_lines:
            gap_pending = True
            # Xoá cửa sổ dedup để một dòng hợp lệ sau chỗ cắt không bị nuốt nhầm
            # vì trùng với dòng trước chỗ cắt.
            seen.clear()

    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue

        ts_match = None
        ts_line_idx = -1
        for idx, line in enumerate(lines):
            m = _CUE_TS_RE.search(line)
            if m:
                ts_match = m
                ts_line_idx = idx
                break

        if ts_match:
            c_start = _parse_timestamp_to_seconds(ts_match.group(1))
            c_end = _parse_timestamp_to_seconds(ts_match.group(2))

            if c_start is not None and c_end is not None:
                if keep_ranges and not _in_any_range(c_start, c_end, keep_ranges):
                    _mark_gap()
                    continue
                if skip_ranges and _midpoint_in_any(c_start, c_end, skip_ranges):
                    _mark_gap()
                    continue

        for idx, line in enumerate(lines):
            if idx <= ts_line_idx:
                continue
            if "-->" in line:
                continue
            if re.match(r"^\d+$", line):
                continue
            if is_vtt:
                if line.startswith(("WEBVTT", "NOTE", "STYLE", "Kind:", "Language:")):
                    continue
                if re.match(r"^(position|align|line|size):", line, re.I):
                    continue

            # Giảm thực thể HTML (&gt;&gt; -> >>)
            clean = html.unescape(line)
            # Loại bỏ HTML tags (<c>, </c>, <b>, etc.)
            clean = re.sub(r"<[^>]+>", "", clean)
            if is_vtt:
                # Loại bỏ VTT formatting tags {text}
                clean = re.sub(r"\{[^}]+\}", "", clean)
            # Marker đổi lượt nói ">>" của phụ đề: KHÔNG xoá, chuyển thành
            # ranh giới lượt để tầng phân vai người nói còn nhìn thấy.
            is_turn_start = bool(re.match(r"^>>", clean))
            clean = re.sub(r"^>>+\s*", "", clean).strip()

            if clean and clean not in seen:
                if gap_pending:
                    last = text_lines[-1].rstrip()
                    if last and last[-1] not in _SENTENCE_END:
                        text_lines[-1] = last + "."
                    gap_pending = False
                seen.append(clean)
                text_lines.append(f"{_TURN_MARKER} {clean}" if is_turn_start else clean)

    return " ".join(text_lines)


def _parse_vtt_content(
    vtt_text: str,
    start_sec: float | None = None,
    end_sec: float | None = None,
    keep_ranges: Sequence[tuple[float | None, float | None]] | None = None,
    skip_ranges: Sequence[tuple[float | None, float | None]] | None = None,
) -> str:
    """Parse nội dung file VTT thành plain text, hỗ trợ giữ/bỏ nhiều khoảng thời gian."""
    if keep_ranges is None and (start_sec is not None or end_sec is not None):
        keep_ranges = [(start_sec, end_sec)]
    return _parse_cue_blocks(vtt_text, keep_ranges, skip_ranges, is_vtt=True)


def _parse_srt_content(
    srt_text: str,
    start_sec: float | None = None,
    end_sec: float | None = None,
    keep_ranges: Sequence[tuple[float | None, float | None]] | None = None,
    skip_ranges: Sequence[tuple[float | None, float | None]] | None = None,
) -> str:
    """Parse nội dung file SRT thành plain text, hỗ trợ giữ/bỏ nhiều khoảng thời gian."""
    if keep_ranges is None and (start_sec is not None or end_sec is not None):
        keep_ranges = [(start_sec, end_sec)]
    return _parse_cue_blocks(srt_text, keep_ranges, skip_ranges, is_vtt=False)


def _reflow_block(raw_text: str, target_para_len: int = 400) -> str:
    """Gói các câu của một khối văn bản thành đoạn ~target_para_len ký tự."""
    text = re.sub(r"\s+", " ", raw_text).strip()
    if not text:
        return ""

    sentences = re.split(r"(?<=[.!?])\s+", text)

    paragraphs: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        current.append(sentence)
        current_len += len(sentence)
        if current_len >= target_para_len:
            paragraphs.append(" ".join(current))
            current = []
            current_len = 0

    if current:
        paragraphs.append(" ".join(current))

    return "\n\n".join(paragraphs)


def _reflow_transcript(raw_text: str) -> str:
    """Tái cấu trúc transcript thô thành các đoạn văn tự nhiên.

    Nếu phụ đề có marker đổi lượt ">>", mỗi lượt nói được reflow riêng và giữ
    nguyên marker ở đầu dòng, để `speakers.detect_speakers()` còn tách được vai.
    """
    if ">>" not in raw_text:
        return _reflow_block(raw_text)

    # Tách theo lượt nói, giữ lại marker
    turns = [t.strip() for t in re.split(r"\s*>>+\s*", raw_text)]
    out: list[str] = []
    for idx, turn in enumerate(turns):
        body = _reflow_block(turn)
        if not body:
            continue
        # Lượt đầu tiên có thể là phần mở đầu chưa gắn marker
        out.append(body if idx == 0 and not raw_text.lstrip().startswith(">>") else f">> {body}")

    return "\n\n".join(out)


def count_speaker_markers(raw_text: str) -> int:
    """Đếm số marker đổi lượt nói ">>" trong transcript."""
    return len(re.findall(r">>+", raw_text or ""))


def extract_youtube_transcript(
    url: str,
    start_time: str | int | float | None = None,
    end_time: str | int | float | None = None,
    chapter: str | int | None = None,
    skip_sponsors: bool = True,
    sponsor_categories: Sequence[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Tải phụ đề từ video YouTube và trả về (text, info_dict), hỗ trợ lọc phân đoạn.

    Args:
        url: Liên kết video YouTube.
        start_time: Mốc bắt đầu (chuỗi MM:SS, HH:MM:SS, hoặc số giây).
        end_time: Mốc kết thúc.
        chapter: Số chapter (1-based), dải "9-11", danh sách "2,5", hoặc tên chapter.
        skip_sponsors: Tự động cắt đoạn quảng cáo host tự đọc qua SponsorBlock.
        sponsor_categories: Nhóm đoạn cần cắt (mặc định: sponsor/selfpromo/interaction).

    Returns:
        tuple[str, dict]: (clean_text, video_info_dict)
    """
    if yt_dlp is None:
        raise ImportError("yt-dlp chưa được cài đặt. Chạy: pip install yt-dlp")

    with tempfile.TemporaryDirectory(prefix="yt_sub_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        output_template = str(tmp_path / "%(id)s.%(ext)s")

        info = None
        for lang_set in [["en", "en-US", "en-GB", "en-orig"], ["vi"]]:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": lang_set,
                "subtitlesformat": "vtt/srt/best",
                "outtmpl": output_template,
                "ignoreerrors": True,
            }

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    result = ydl.extract_info(url, download=True)
                    if result:
                        info = result
            except Exception:
                pass

            if info:
                video_id = info.get("id", "video")
                for lang in lang_set:
                    for ext in ("vtt", "srt"):
                        if (tmp_path / f"{video_id}.{lang}.{ext}").exists():
                            break
                    else:
                        continue
                    break
                else:
                    continue
                break

        if info is None:
            raise RuntimeError("Không thể truy cập video YouTube. Kiểm tra lại URL hoặc thử lại sau.")

        video_id = info.get("id", "video")
        title = info.get("title", "Untitled Video")
        channel = info.get("channel") or info.get("uploader") or "Unknown Channel"
        chapters = info.get("chapters") or []

        # Xác định khoảng thời gian cần trích xuất
        res = resolve_time_ranges(
            url=url,
            start_time=start_time,
            end_time=end_time,
            chapter=chapter,
            chapters=chapters,
        )
        keep_ranges = res["ranges"]
        segment_label = res["segment_label"]
        ch_idx = res["chapter_index"]
        ch_title = res["chapter_title"]
        start_sec = res["start_sec"]
        end_sec = res["end_sec"]

        # Cắt đoạn quảng cáo host tự đọc. video_id đã có sẵn nên không tốn thêm
        # lần gọi yt-dlp nào. Chỉ áp dụng (và chỉ báo cáo) segment thực sự giao
        # với phần video người dùng đã chọn.
        applied_sponsors = []
        skip_ranges: list[tuple[float, float]] = []
        if skip_sponsors:
            all_sponsors = fetch_sponsor_segments(video_id, sponsor_categories)
            applied_sponsors = segments_in_window(all_sponsors, keep_ranges)
            skip_ranges = merge_segments(applied_sponsors)
        sponsor_note = describe_segments_vi(applied_sponsors)

        sub_text = ""
        sub_lang_used = ""

        # Ưu tiên tìm file theo thứ tự ngôn ngữ
        for lang in _SUBTITLE_LANG_PRIORITY:
            for ext in ("vtt", "srt"):
                sub_file = tmp_path / f"{video_id}.{lang}.{ext}"
                if sub_file.exists():
                    raw = sub_file.read_text(encoding="utf-8", errors="ignore")
                    if ext == "vtt":
                        sub_text = _parse_vtt_content(raw, keep_ranges=keep_ranges, skip_ranges=skip_ranges)
                    else:
                        sub_text = _parse_srt_content(raw, keep_ranges=keep_ranges, skip_ranges=skip_ranges)
                    sub_lang_used = lang
                    break
            if sub_text:
                break

        # Fallback tìm bất kỳ file subtitle nào
        if not sub_text:
            for sub_file in sorted(tmp_path.glob(f"{video_id}.*")):
                if sub_file.suffix.lower() in (".vtt", ".srt"):
                    raw = sub_file.read_text(encoding="utf-8", errors="ignore")
                    if sub_file.suffix.lower() == ".vtt":
                        sub_text = _parse_vtt_content(raw, keep_ranges=keep_ranges, skip_ranges=skip_ranges)
                    else:
                        sub_text = _parse_srt_content(raw, keep_ranges=keep_ranges, skip_ranges=skip_ranges)
                    sub_lang_used = sub_file.stem.split(".")[-1] if "." in sub_file.stem else "unknown"
                    break

        if not sub_text or len(sub_text.strip()) < 30:
            range_info = f" ({segment_label})" if segment_label else ""
            sponsor_hint = ""
            if applied_sponsors:
                sponsor_hint = (
                    f" Lưu ý: đã cắt {len(applied_sponsors)} đoạn quảng cáo trong phân đoạn này — "
                    f"thử tắt bằng /sponsorblock off nếu muốn giữ lại."
                )
            raise RuntimeError(
                f"Video '{title}' không có phụ đề khả dụng hoặc phụ đề trong phân đoạn này quá ngắn{range_info}. "
                f"Hãy kiểm tra lại mốc thời gian hoặc chọn video có phụ đề CC.{sponsor_hint}"
            )

        clean_text = _reflow_transcript(sub_text)

        video_info = {
            "title": title,
            "channel": channel,
            "duration": info.get("duration", 0),
            "description": info.get("description", ""),
            "video_id": video_id,
            "url": info.get("webpage_url") or url,
            "upload_date": info.get("upload_date", ""),
            "subtitle_lang": sub_lang_used,
            "word_count": len(clean_text.split()),
            "chapters": chapters,
            "segment_label": segment_label,
            "chapter_index": ch_idx,
            "chapter_indices": res["chapter_indices"],
            "chapter_title": ch_title,
            "start_sec": start_sec,
            "end_sec": end_sec,
            "ranges": keep_ranges,
            "sponsor_removed_count": len(applied_sponsors),
            "sponsor_removed_sec": sum(s.duration for s in applied_sponsors),
            "sponsor_segments": [
                {"start": s.start, "end": s.end, "category": s.category}
                for s in applied_sponsors
            ],
            "sponsor_note": sponsor_note,
            "speaker_marker_count": count_speaker_markers(clean_text),
        }

        seg_display = f" [{segment_label}]" if segment_label else ""
        print(f"   ✅ Đã trích xuất phụ đề{seg_display} ({sub_lang_used}): {len(clean_text)} ký tự, ~{video_info['word_count']} từ")
        if sponsor_note:
            print(f"   🚫 SponsorBlock: đã cắt {sponsor_note}")
        if video_info["speaker_marker_count"]:
            print(f"   🗣️ Phát hiện {video_info['speaker_marker_count']} marker đổi lượt nói (>>) trong phụ đề")
        return clean_text, video_info


# ── 4. Chuyển đổi YouTube → ArticleContent ──

def youtube_to_article(
    url: str,
    start_time: str | int | float | None = None,
    end_time: str | int | float | None = None,
    chapter: str | int | None = None,
    skip_sponsors: bool = True,
    sponsor_categories: Sequence[str] | None = None,
) -> ArticleContent:
    """Chuyển đổi video YouTube thành ArticleContent tương thích pipeline dịch + TTS.

    Args:
        url: URL YouTube.
        start_time: Mốc bắt đầu (tùy chọn).
        end_time: Mốc kết thúc (tùy chọn).
        chapter: Số chapter, dải "9-11", danh sách "2,5", hoặc tên chapter (tùy chọn).
        skip_sponsors: Tự động cắt đoạn quảng cáo host tự đọc (mặc định bật).
        sponsor_categories: Nhóm đoạn cần cắt.
    """
    print(f"📺 Đang trích xuất phụ đề từ video YouTube...")

    transcript_text, info = extract_youtube_transcript(
        url,
        start_time=start_time,
        end_time=end_time,
        chapter=chapter,
        skip_sponsors=skip_sponsors,
        sponsor_categories=sponsor_categories,
    )

    video_title = info["title"]
    channel = info["channel"]
    ch_idx = info.get("chapter_index")
    ch_title = info.get("chapter_title")

    if ch_idx and ch_title:
        # Nếu chọn 1 chapter: tiêu đề bài viết đặt chuẩn xác theo tên chapter (không gắn tiền tố Chương X:)
        title = ch_title
    elif info.get("segment_label"):
        title = f"[{info['segment_label']}] {video_title}"
    else:
        title = video_title

    return ArticleContent(
        url=info.get("url", url),
        title=title,
        author=channel,
        domain="youtube.com",
        publish_date=info.get("upload_date", ""),
        text=transcript_text,
        html="",
        word_count=info.get("word_count", len(transcript_text.split())),
        video_title=video_title,
        chapter_title=ch_title,
        chapter_index=ch_idx,
        sponsor_note=info.get("sponsor_note", ""),
        sponsor_removed_count=info.get("sponsor_removed_count", 0),
    )
