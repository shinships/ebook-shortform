"""Module phân vai người nói (speaker attribution) cho transcript video.

Nguyên tắc cốt lõi: **LLM không bao giờ được viết lại transcript.** LLM chỉ trả
về *chỉ số câu* nơi đổi người nói, còn việc tái dựng lượt nói là thuần tất định.
Nhờ vậy tổng số ký tự sau khi phân vai luôn bằng transcript gốc — điều kiện bắt
buộc của chế độ "podcast đầy đủ theo script gốc".

Bốn nhánh nhận diện (thử theo thứ tự, dừng ở nhánh đầu tiên khớp):
1. `name_labels` — script gốc do người dùng soạn sẵn dạng "Tên: nội dung" ở đầu
   mỗi lượt (không cần LLM để tách lượt). Nhãn tên bị BỎ khỏi nội dung đọc —
   đây là điểm khác biệt duy nhất so với bất biến "không mất chữ" gốc, được
   bù trừ tường minh (xem `_turns_from_name_labels`).
2. `markers` — phụ đề có marker đổi lượt ">>" (giữ lại bởi youtube.py). Chắc chắn
   nhất; LLM chỉ được gọi để đặt tên và suy giới tính cho từng vai.
3. `llm`     — không có marker/nhãn: đánh số câu rồi hỏi LLM chỗ nào đổi vai.
4. `single`  — chỉ một người nói; bỏ qua toàn bộ logic casting.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any

from ebook_translator.core.llm import LLMClient

# Cửa sổ đưa vào LLM mỗi lần (ký tự). Đủ nhỏ để model giữ được độ chính xác về
# chỉ số câu, đủ lớn để không tốn quá nhiều lượt gọi.
_WINDOW_CHARS = 15000

# Dưới ngưỡng này thì coi như độc thoại, không bõ công phân vai.
_MIN_TURNS_FOR_MULTI = 2

_HOST_HINTS = ("host", "mc", "người dẫn", "interviewer", "dẫn chương trình")


@dataclass
class Speaker:
    """Một người nói trong video."""

    id: str
    name: str = ""
    gender: str = "unknown"      # "male" | "female" | "unknown"
    role: str = "guest"          # "host" | "guest"
    confidence: float = 0.0

    @property
    def display(self) -> str:
        return self.name or self.id


@dataclass
class SpeakerPlan:
    """Kết quả phân vai: danh sách vai + các lượt nói đã tách."""

    speakers: list[Speaker] = field(default_factory=list)
    turns: list[tuple[str, str]] = field(default_factory=list)
    source: str = "single"       # "markers" | "llm" | "single"

    @property
    def is_multi(self) -> bool:
        return len(self.speakers) > 1

    def by_id(self, speaker_id: str) -> Speaker | None:
        return next((s for s in self.speakers if s.id == speaker_id), None)

    def gender_roster(self, default_gender: str = "male") -> dict[str, str]:
        """Roster {speaker_id: gender} để đưa thẳng vào tts.pick_voices_for_cast()."""
        return {
            s.id: (s.gender if s.gender in ("male", "female") else default_gender)
            for s in self.speakers
        }

    def total_chars(self) -> int:
        return sum(len(t) for _, t in self.turns)


# ── Tiện ích ──

def _split_sentences(text: str) -> list[str]:
    """Tách câu, giữ nguyên toàn bộ ký tự (không nuốt chữ)."""
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return [p for p in (s.strip() for s in parts) if p]


def _strip_json_fence(raw: str) -> str:
    """Bóc rào ```json ... ``` nếu model trả kèm."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    text = _strip_json_fence(raw)
    try:
        return json.loads(text)
    except Exception:
        pass
    # Cứu vãn: lấy khối {...} dài nhất trong output
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return None
    return None


def _normalize_gender(value: Any) -> str:
    val = str(value or "").strip().lower()
    if val in ("male", "nam", "m", "man"):
        return "male"
    if val in ("female", "nữ", "nu", "f", "woman"):
        return "female"
    return "unknown"


def _merge_adjacent(turns: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Gộp các lượt liền kề của cùng một người nói."""
    merged: list[tuple[str, str]] = []
    for speaker_id, text in turns:
        text = text.strip()
        if not text:
            continue
        if merged and merged[-1][0] == speaker_id:
            merged[-1] = (speaker_id, f"{merged[-1][1]} {text}".strip())
        else:
            merged.append((speaker_id, text))
    return merged


# ── Nhánh 1: tách theo marker ">>" ──

def _turns_from_markers(text: str) -> list[tuple[str, str]]:
    """Tách lượt nói theo marker ">>", gán luân phiên S1/S2/... theo thứ tự xuất hiện.

    Marker chỉ cho biết "đổi người nói", không cho biết là AI. Với hội thoại 2
    người (đại đa số phỏng vấn) thì luân phiên là đúng; LLM ở bước sau sẽ gộp lại
    nếu thực tế chỉ có 1 người hoặc nhiều hơn 2.
    """
    chunks = [c.strip() for c in re.split(r"\s*>>+\s*", text) if c.strip()]
    if len(chunks) < _MIN_TURNS_FOR_MULTI:
        return []
    return [(f"S{(i % 2) + 1}", chunk) for i, chunk in enumerate(chunks)]


# ── Nhánh 0: tách theo nhãn "Tên: nội dung" (script gốc soạn sẵn) ──

# Nhãn tên: tối đa 40 ký tự trước dấu ':', không xuống dòng bên trong nhãn.
_NAME_LABEL_RE = re.compile(r"^\s*([^\n:：]{1,40}):\s*(.+)$", re.DOTALL)
_MAX_LABEL_CAST = 6           # quá số này là header/mục lục, không phải dàn diễn viên
_MIN_LABELED_TURNS = 3         # cần đủ lượt để chắc đây là hội thoại, không phải tiêu đề lẻ


def _turns_from_name_labels(text: str) -> tuple[list[tuple[str, str]], int, dict[str, str]]:
    """Tách lượt nói theo nhãn 'Tên: nội dung' ở đầu mỗi đoạn.

    Khác với nhánh markers/llm, nhánh này CHỦ ĐỘNG bỏ nhãn tên khỏi nội dung —
    đúng yêu cầu "đọc script gốc nhưng bỏ tên người ở đầu mỗi lượt". Trả thêm
    số ký tự nhãn đã lược bỏ để detect_speakers() cộng bù khi kiểm tra bất biến
    không mất chữ (nhãn không phải nội dung mất mát, mà là nhãn được lược có
    chủ đích), cùng ánh xạ speaker_id → tên hiển thị gốc (khỏi phải hỏi lại LLM).
    """
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if len(blocks) < _MIN_TURNS_FOR_MULTI:
        blocks = [b.strip() for b in text.split("\n") if b.strip()]
    if len(blocks) < _MIN_TURNS_FOR_MULTI:
        return [], 0, {}

    parsed: list[tuple[str, str]] = []   # (nhãn hoặc "", nội dung)
    for block in blocks:
        m = _NAME_LABEL_RE.match(block)
        if m:
            parsed.append((m.group(1).strip(), m.group(2).strip()))
        else:
            parsed.append(("", block))

    labeled = [(label, body) for label, body in parsed if label]
    if len(labeled) < _MIN_LABELED_TURNS or len(labeled) < int(len(parsed) * 0.6):
        return [], 0, {}

    names_seen: list[str] = []
    for label, _ in labeled:
        key = label.lower()
        if key not in names_seen:
            names_seen.append(key)
    if len(names_seen) > _MAX_LABEL_CAST or len(names_seen) < _MIN_TURNS_FOR_MULTI:
        return [], 0, {}

    name_to_id: dict[str, str] = {}
    id_to_display: dict[str, str] = {}
    turns: list[tuple[str, str]] = []
    stripped_chars = 0
    for label, body in parsed:
        if not label:
            # Đoạn tiếp nối không có nhãn riêng (vd. xuống dòng giữa lượt nói) —
            # gộp vào lượt liền trước thay vì đánh rơi.
            if turns:
                prev_id, prev_body = turns[-1]
                turns[-1] = (prev_id, f"{prev_body} {body}".strip())
            continue
        key = label.lower()
        if key not in name_to_id:
            name_to_id[key] = f"S{len(name_to_id) + 1}"
            id_to_display[name_to_id[key]] = label
        sid = name_to_id[key]
        turns.append((sid, body))
        stripped_chars += len(label) + 1   # +1 cho dấu ':'

    return turns, stripped_chars, id_to_display


# ── Nhánh 2: hỏi LLM chỗ đổi vai ──

_SEGMENT_SYSTEM = (
    "Bạn là chuyên gia phân tích hội thoại. Nhiệm vụ: xác định các vị trí ĐỔI NGƯỜI NÓI "
    "trong một transcript đã được đánh số câu.\n\n"
    "QUY TẮC TUYỆT ĐỐI:\n"
    "1. KHÔNG viết lại, KHÔNG tóm tắt, KHÔNG sửa bất kỳ câu nào. Bạn chỉ trả về CHỈ SỐ CÂU.\n"
    "2. Chỉ đánh dấu câu MỞ ĐẦU một lượt nói mới. Câu đầu tiên (số 0) luôn là mở đầu một lượt.\n"
    "3. Dùng mã người nói ổn định: S1, S2, S3... S1 thường là người dẫn/phỏng vấn.\n"
    "4. Nếu thực tế chỉ có MỘT người nói (bài giảng, độc thoại), trả về đúng một mốc ở câu 0.\n"
    "5. CHỈ trả về JSON hợp lệ, không giải thích, không rào markdown."
)

_SEGMENT_SCHEMA = (
    '{"boundaries": [{"line": <số nguyên>, "speaker": "S1"}, ...]}'
)

_ROSTER_SYSTEM = (
    "Bạn là chuyên gia nhận diện nhân vật trong video phỏng vấn/hội thoại. "
    "Dựa vào tiêu đề, kênh, mô tả video và trích đoạn lời thoại của từng vai, hãy xác định "
    "TÊN THẬT và GIỚI TÍNH của từng người nói.\n\n"
    "QUY TẮC:\n"
    "1. gender chỉ nhận 'male', 'female', hoặc 'unknown'. Không chắc thì trả 'unknown' — "
    "đoán bừa tệ hơn là thừa nhận không biết.\n"
    "2. role nhận 'host' (người dẫn/phỏng vấn) hoặc 'guest' (khách mời).\n"
    "3. confidence là số thực 0.0-1.0.\n"
    "4. Nếu không xác định được tên thật, đặt name là mô tả ngắn tiếng Việt "
    "(ví dụ 'Người dẫn', 'Khách mời').\n"
    "5. CHỈ trả về JSON hợp lệ, không giải thích, không rào markdown."
)

_ROSTER_SCHEMA = (
    '{"speakers": [{"id": "S1", "name": "...", "gender": "male|female|unknown", '
    '"role": "host|guest", "confidence": 0.0}]}'
)


def _llm_segment_window(
    sentences: list[str],
    llm: LLMClient,
    offset: int,
    known_speakers: list[str],
) -> list[tuple[int, str]]:
    """Hỏi LLM các mốc đổi vai trong một cửa sổ câu. Trả [(chỉ_số_tuyệt_đối, speaker)]."""
    numbered = "\n".join(f"[{i}] {s}" for i, s in enumerate(sentences))
    carry = (
        f"\nCác mã người nói đã dùng ở phần trước (hãy tái sử dụng nếu cùng người): "
        f"{', '.join(known_speakers)}\n" if known_speakers else "\n"
    )
    prompt = (
        f"Transcript đã đánh số câu (từ 0 đến {len(sentences) - 1}):{carry}\n"
        f"{numbered}\n\n"
        f"Trả về JSON đúng cấu trúc: {_SEGMENT_SCHEMA}"
    )

    try:
        raw = llm.complete(
            system=_SEGMENT_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4000,
        )
    except Exception as e:
        print(f"⚠️ [speakers] LLM phân vai lỗi ở cửa sổ offset={offset}: {e}", file=sys.stderr)
        return []

    data = _parse_json_object(raw)
    if not data or not isinstance(data.get("boundaries"), list):
        print(f"⚠️ [speakers] LLM trả JSON không hợp lệ ở offset={offset}, bỏ qua cửa sổ.", file=sys.stderr)
        return []

    out: list[tuple[int, str]] = []
    for item in data["boundaries"]:
        if not isinstance(item, dict):
            continue
        try:
            line = int(item.get("line"))
        except (TypeError, ValueError):
            continue
        speaker = str(item.get("speaker") or "").strip() or "S1"
        # Kẹp chỉ số vào đúng phạm vi cửa sổ — model đôi khi trả số ngoài dải.
        if 0 <= line < len(sentences):
            out.append((offset + line, speaker))
    return sorted(set(out))


def _turns_from_llm(text: str, llm: LLMClient) -> list[tuple[str, str]]:
    """Phân vai bằng LLM, tái dựng TẤT ĐỊNH nên không mất một ký tự nào."""
    sentences = _split_sentences(text)
    if len(sentences) < 4:
        return []

    # Chia cửa sổ theo ký tự, cắt ở ranh giới câu
    windows: list[tuple[int, list[str]]] = []
    current: list[str] = []
    current_len = 0
    start_idx = 0
    for idx, sent in enumerate(sentences):
        if current and current_len + len(sent) > _WINDOW_CHARS:
            windows.append((start_idx, current))
            current, current_len, start_idx = [], 0, idx
        current.append(sent)
        current_len += len(sent)
    if current:
        windows.append((start_idx, current))

    boundaries: list[tuple[int, str]] = []
    known: list[str] = []
    for w_idx, (offset, window) in enumerate(windows, 1):
        print(f"   🗣️ [speakers] Phân vai cửa sổ {w_idx}/{len(windows)} ({len(window)} câu)...")
        found = _llm_segment_window(window, llm, offset, known)
        for _, speaker in found:
            if speaker not in known:
                known.append(speaker)
        boundaries.extend(found)

    if not boundaries:
        return []

    boundaries = sorted(set(boundaries))
    # Luôn có mốc ở câu 0 để không đánh rơi phần đầu.
    if boundaries[0][0] != 0:
        boundaries.insert(0, (0, boundaries[0][1]))

    turns: list[tuple[str, str]] = []
    for i, (line, speaker) in enumerate(boundaries):
        end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(sentences)
        body = " ".join(sentences[line:end]).strip()
        if body:
            turns.append((speaker, body))
    return turns


# ── Đặt tên + suy giới tính cho từng vai ──

def _build_roster(
    turns: list[tuple[str, str]],
    video_info: dict[str, Any],
    llm: LLMClient,
    known_names: dict[str, str] | None = None,
) -> list[Speaker]:
    """Hỏi LLM tên thật + giới tính cho từng mã người nói.

    known_names (id → tên hiển thị) dùng khi script gốc đã có nhãn tên tường
    minh (nhánh name_labels): tên đó ĐÁNG TIN hơn suy đoán của LLM nên luôn
    thắng, LLM chỉ còn việc suy giới tính + vai trò.
    """
    known_names = known_names or {}
    ids: list[str] = []
    for speaker_id, _ in turns:
        if speaker_id not in ids:
            ids.append(speaker_id)

    samples = []
    for sid in ids:
        excerpt = " ".join(t for s, t in turns if s == sid)[:900]
        samples.append(f"--- {sid} ---\n{excerpt}")

    prompt = (
        f"Tiêu đề video: {video_info.get('title', '')}\n"
        f"Kênh: {video_info.get('channel', '')}\n"
        f"Mô tả: {str(video_info.get('description', ''))[:1200]}\n\n"
        f"Trích đoạn lời thoại của từng người nói:\n\n"
        + "\n\n".join(samples)
        + f"\n\nTrả về JSON đúng cấu trúc: {_ROSTER_SCHEMA}"
    )

    fallback = [
        Speaker(id=sid, name=known_names.get(sid, sid), gender="unknown", role="guest")
        for sid in ids
    ]
    try:
        raw = llm.complete(
            system=_ROSTER_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1200,
        )
    except Exception as e:
        print(f"⚠️ [speakers] Không nhận diện được tên/giới tính: {e}", file=sys.stderr)
        return fallback

    data = _parse_json_object(raw)
    if not data or not isinstance(data.get("speakers"), list):
        print("⚠️ [speakers] LLM trả roster không hợp lệ, dùng mã mặc định.", file=sys.stderr)
        return fallback

    parsed: dict[str, Speaker] = {}
    for item in data["speakers"]:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or "").strip()
        if sid not in ids:
            continue
        role = str(item.get("role") or "guest").strip().lower()
        if role not in ("host", "guest"):
            role = "host" if any(h in role for h in _HOST_HINTS) else "guest"
        try:
            conf = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        parsed[sid] = Speaker(
            id=sid,
            # Tên trong script gốc (nếu có) luôn thắng suy đoán của LLM.
            name=known_names.get(sid) or str(item.get("name") or sid).strip() or sid,
            gender=_normalize_gender(item.get("gender")),
            role=role,
            confidence=max(0.0, min(1.0, conf)),
        )

    return [
        parsed.get(sid, Speaker(id=sid, name=known_names.get(sid, sid)))
        for sid in ids
    ]


# ── API chính ──

def detect_speakers(
    text: str,
    video_info: dict[str, Any] | None = None,
    llm: LLMClient | None = None,
    use_llm: bool = True,
    use_llm_segmentation: bool | None = None,
) -> SpeakerPlan:
    """Phân tích transcript thành các lượt nói kèm vai và giới tính.

    Bảo đảm KHÔNG MẤT CHỮ Ở PHẦN NỘI DUNG: tổng ký tự của các turn cộng với
    ký tự nhãn tên đã chủ động lược bỏ (nhánh name_labels) luôn bằng transcript
    gốc. Nếu phát hiện lệch ngoài dự tính, hàm tự quay về chế độ một người nói.

    Args:
        text: Transcript (có thể chứa marker ">>" do youtube.py giữ lại, hoặc
            nhãn "Tên: nội dung" ở đầu mỗi lượt do người dùng tự soạn).
        video_info: Dict metadata video (title, channel, description...).
        llm: LLMClient dùng lại; None thì tự tạo.
        use_llm: Tắt để không gọi LLM (kể cả đặt tên/giới tính) — test nhanh,
            không tốn token.
        use_llm_segmentation: Cho phép hỏi LLM tìm chỗ đổi vai khi văn bản
            KHÔNG có marker/nhãn cấu trúc sẵn (nhánh 3, tốn nhiều lượt gọi
            nhất vì phải quét theo cửa sổ). Mặc định theo `use_llm`; đặt False
            để chỉ chấp nhận multi-speaker khi có tín hiệu cấu trúc rẻ tiền
            (nhãn tên / marker ">>") — tránh gọi LLM dò mù trên văn bản độc
            thoại bình thường (vd. sách/bài viết đưa vào chế độ Podcast Chuyên
            Sâu) trong khi vẫn giữ nguyên khả năng suy giới tính qua LLM.
    """
    if use_llm_segmentation is None:
        use_llm_segmentation = use_llm
    video_info = video_info or {}
    text = (text or "").strip()
    if not text:
        return SpeakerPlan()

    def _single() -> SpeakerPlan:
        body = re.sub(r"\s*>>+\s*", " ", text).strip()
        return SpeakerPlan(
            speakers=[Speaker(id="S1", name="Người dẫn", gender="unknown", role="host", confidence=1.0)],
            turns=[("S1", body)],
            source="single",
        )

    stripped_chars = 0
    known_names: dict[str, str] = {}

    # Nhánh 0 — script gốc đã có nhãn "Tên: nội dung", tín hiệu rẻ và chắc nhất
    turns, stripped_chars, known_names = _turns_from_name_labels(text)
    source = "name_labels"

    # Nhánh 1 — marker ">>" là tín hiệu chắc chắn tiếp theo
    if not turns:
        turns = _turns_from_markers(text)
        source = "markers"

    # Nhánh 2 — không có marker/nhãn thì hỏi LLM dò mù (tốn kém, có thể tắt riêng)
    if not turns and use_llm_segmentation:
        if llm is None:
            llm = LLMClient()
        turns = _turns_from_llm(text, llm)
        source = "llm"

    turns = _merge_adjacent(turns)
    if len(turns) < _MIN_TURNS_FOR_MULTI:
        return _single()

    # Kiểm tra bất biến "không mất chữ" trước khi chấp nhận kết quả — cộng bù
    # phần nhãn tên đã chủ động lược bỏ (không phải nội dung bị mất).
    def _norm(s: str) -> str:
        return re.sub(r"\s+", "", re.sub(r">>+", "", s))

    original_chars = len(_norm(text))
    turn_chars = len(_norm(" ".join(t for _, t in turns))) + stripped_chars
    if turn_chars != original_chars:
        delta = original_chars - turn_chars
        print(
            f"⚠️ [speakers] Phân vai làm lệch {delta} ký tự so với bản gốc "
            f"({turn_chars} vs {original_chars}). Quay về chế độ một người nói để "
            f"đảm bảo không mất nội dung.",
            file=sys.stderr,
        )
        return _single()

    # Đặt tên + suy giới tính
    if use_llm:
        if llm is None:
            llm = LLMClient()
        speakers = _build_roster(turns, video_info, llm, known_names=known_names)
    else:
        speakers = [
            Speaker(id=sid, name=known_names.get(sid, sid))
            for sid in dict.fromkeys(s for s, _ in turns)
        ]

    # Người nói đầu tiên mặc định là host nếu LLM không chỉ ra ai cả
    if speakers and not any(s.role == "host" for s in speakers):
        speakers[0].role = "host"

    plan = SpeakerPlan(speakers=speakers, turns=turns, source=source)
    print(
        f"🗣️ [speakers] Nhận diện {len(speakers)} người nói qua '{source}': "
        + ", ".join(
            f"{s.id}={s.display} ({s.gender}, {s.role})" for s in speakers
        )
        + f" · {len(turns)} lượt nói"
    )
    return plan
