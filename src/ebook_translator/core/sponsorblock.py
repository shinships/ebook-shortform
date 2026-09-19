"""Module truy vấn SponsorBlock để loại bỏ đoạn quảng cáo do chính host đọc.

Cần phân biệt hai loại quảng cáo trên YouTube:
- Quảng cáo YouTube tự chèn (pre-roll, mid-roll): KHÔNG nằm trong file phụ đề,
  nên pipeline không cần làm gì cả.
- Sponsor read do host tự đọc trong video: nằm nguyên trong phụ đề và sẽ đi thẳng
  vào podcast nếu không cắt. Đây là thứ module này xử lý.

SponsorBlock (https://sponsor.ajay.app) là cơ sở dữ liệu cộng đồng lưu mốc giây của
các đoạn này. Module chỉ TRẢ VỀ danh sách khoảng cần bỏ; việc cắt do
youtube._parse_cue_blocks thực hiện ở tầng phụ đề.

Nguyên tắc: không bao giờ ném exception làm chết job. Mọi sự cố mạng, rate-limit
hay dữ liệu lạ đều quy về danh sách rỗng.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover - requests là dependency chuẩn của dự án
    requests = None  # type: ignore[assignment]


_API_URL = "https://sponsor.ajay.app/api/skipSegments"
_USER_AGENT = "ebook-shortform/0.1"

# Ba nhóm mặc định đều là nội dung phi-học-thuật thuần túy.
# CỐ Ý không bật intro/outro/preview: với video phỏng vấn và talk, "intro" thường
# chứa phần dẫn nhập chủ đề thật và "outro" thường chứa kết luận — cắt đi là làm
# hỏng đúng phần nội dung mà pipeline này sinh ra để phục vụ.
DEFAULT_CATEGORIES: tuple[str, ...] = ("sponsor", "selfpromo", "interaction")
ALL_CATEGORIES: tuple[str, ...] = DEFAULT_CATEGORIES + (
    "intro",
    "outro",
    "preview",
    "music_offtopic",
)

CATEGORY_LABELS_VI: dict[str, str] = {
    "sponsor": "quảng cáo tài trợ",
    "selfpromo": "tự quảng bá",
    "interaction": "kêu gọi like/sub",
    "intro": "nhạc hiệu đầu",
    "outro": "kết / nhạc hiệu cuối",
    "preview": "tóm lược đầu video",
    "music_offtopic": "nhạc xen",
}

# Một segment phủ quá tỉ lệ này của video gần như chắc chắn là phá hoại.
_VANDALISM_RATIO = 0.40
_CACHE_TTL = 3600.0
_CACHE: dict[str, tuple[float, list["SponsorSegment"]]] = {}


@dataclass
class SponsorSegment:
    """Một đoạn cần bỏ, theo mốc giây trong video gốc."""

    start: float
    end: float
    category: str = "sponsor"
    votes: int = 0
    uuid: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def label_vi(self) -> str:
        return CATEGORY_LABELS_VI.get(self.category, self.category)


def _warn(msg: str) -> None:
    print(f"[SponsorBlock] {msg}", file=sys.stderr)


def fetch_sponsor_segments(
    video_id: str,
    categories: Sequence[str] | None = None,
    timeout: float = 8.0,
    use_cache: bool = True,
) -> list[SponsorSegment]:
    """Lấy danh sách đoạn cần bỏ của một video. Lỗi bất kỳ đều trả về [].

    Lưu ý: video không có dữ liệu thì API trả HTTP 404 chứ không phải mảng rỗng —
    404 phải hiểu là "không có đoạn nào", không phải lỗi.
    """
    if not video_id:
        return []
    if requests is None:
        _warn("thiếu thư viện requests, bỏ qua bước cắt quảng cáo")
        return []

    cats = tuple(categories) if categories else DEFAULT_CATEGORIES
    cache_key = f"{video_id}|{','.join(sorted(cats))}"
    if use_cache:
        hit = _CACHE.get(cache_key)
        if hit and (time.time() - hit[0]) < _CACHE_TTL:
            return hit[1]

    segments: list[SponsorSegment] = []
    try:
        resp = requests.get(
            _API_URL,
            params={"videoID": video_id, "categories": json.dumps(list(cats))},
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT},
        )
        if resp.status_code == 404:
            segments = []
        elif resp.status_code != 200:
            _warn(f"API trả về HTTP {resp.status_code}, bỏ qua bước cắt quảng cáo")
            return []
        else:
            segments = _parse_api_payload(resp.json())
    except Exception as e:
        _warn(f"không gọi được API ({type(e).__name__}: {e}), bỏ qua bước cắt quảng cáo")
        return []

    _CACHE[cache_key] = (time.time(), segments)
    return segments


def _parse_api_payload(payload: Any) -> list[SponsorSegment]:
    """Chuyển JSON của API thành SponsorSegment đã lọc sạch."""
    if not isinstance(payload, list):
        return []

    out: list[SponsorSegment] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        # actionType "full" đánh dấu cả video là quảng cáo — bỏ qua, nếu không
        # sẽ xoá sạch transcript.
        if item.get("actionType", "skip") != "skip":
            continue

        seg = item.get("segment") or []
        if not isinstance(seg, (list, tuple)) or len(seg) != 2:
            continue
        try:
            start, end = float(seg[0]), float(seg[1])
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue

        votes = int(item.get("votes", 0) or 0)
        if votes < -1:
            continue

        duration = item.get("videoDuration") or 0
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            duration = 0.0
        if duration > 0:
            start = max(0.0, min(start, duration))
            end = max(0.0, min(end, duration))
            if end <= start:
                continue
            if (end - start) / duration > _VANDALISM_RATIO:
                _warn(
                    f"bỏ qua segment phủ {(end - start) / duration:.0%} thời lượng video "
                    f"(nghi phá hoại): {start:.0f}-{end:.0f}s"
                )
                continue

        out.append(
            SponsorSegment(
                start=start,
                end=end,
                category=str(item.get("category", "sponsor")),
                votes=votes,
                uuid=str(item.get("UUID", "")),
            )
        )

    out.sort(key=lambda s: s.start)
    return out


def merge_segments(segments: Sequence[SponsorSegment]) -> list[tuple[float, float]]:
    """Gộp các segment chồng lấn thành danh sách khoảng (start, end) rời nhau."""
    ordered = sorted((s.start, s.end) for s in segments)
    merged: list[tuple[float, float]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def segments_in_window(
    segments: Sequence[SponsorSegment],
    keep_ranges: Sequence[tuple[float | None, float | None]] | None,
) -> list[SponsorSegment]:
    """Lọc ra các segment thực sự giao với phần video người dùng đã chọn.

    Segment nằm ngoài cửa sổ vẫn được API trả về nhưng không được áp dụng và
    KHÔNG được báo cáo — nếu không người dùng sẽ tưởng có nội dung bị cắt mất.
    """
    if not keep_ranges:
        return list(segments)

    out: list[SponsorSegment] = []
    for seg in segments:
        for raw_lo, raw_hi in keep_ranges:
            lo = float("-inf") if raw_lo is None else raw_lo
            hi = float("inf") if raw_hi is None else raw_hi
            if seg.end > lo and seg.start < hi:
                out.append(seg)
                break
    return out


def describe_segments_vi(segments: Sequence[SponsorSegment]) -> str:
    """Mô tả tiếng Việt ngắn gọn cho người dùng cuối.

    Ví dụ: "2 đoạn quảng cáo tài trợ (~3 phút): 18:20-19:46, 42:25-44:01"
    """
    if not segments:
        return ""

    # Import muộn để tránh vòng lặp import (youtube.py import module này).
    from ebook_translator.core.youtube import format_time_str

    total = sum(s.duration for s in segments)
    kinds = sorted({s.label_vi for s in segments})
    kind_text = " + ".join(kinds)

    if total >= 60:
        dur_text = f"~{round(total / 60)} phút"
    else:
        dur_text = f"~{round(total)} giây"

    marks = ", ".join(
        f"{format_time_str(s.start)}-{format_time_str(s.end)}" for s in segments
    )
    return f"{len(segments)} đoạn {kind_text} ({dur_text}): {marks}"
