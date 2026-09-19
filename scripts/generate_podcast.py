#!/usr/bin/env python3
"""generate_podcast.py — Tự động tạo audio tóm tắt sách phong cách Podcast bằng ZeroTTS (mặc định), VieNeu-TTS v3 Turbo hoặc Vbee API.

Quy trình:
1. Nhận file tóm tắt (Markdown / Text / Ebook).
2. Dùng Gemini biên soạn kịch bản Podcast tự nhiên, đàm thoại lôi cuốn.
3. Chuyển đổi thành file âm thanh giọng đọc tiếng Việt chân thực (MP3 48kHz).
   - Mặc định: ZeroTTS (chạy real-time trên CPU, độ trễ cực thấp, WER 1.03%, âm thanh 48kHz).
   - Tùy chọn: VieNeu-TTS v3 Turbo (on-device 48kHz, voice cloning).
   - Tùy chọn: Vbee AIVoice API (Cloud).
4. Lưu trữ vào output/podcasts/.
5. (Tùy chọn) Gửi file Podcast MP3 qua Telegram.

Cách dùng:
  python scripts/generate_podcast.py Remote_Office_Not_Required_short.md
  python scripts/generate_podcast.py Remote_Office_Not_Required_short.md --voice "maichi"
  python scripts/generate_podcast.py Remote_Office_Not_Required_short.md --voice "giahuy" --speed 1.15 --telegram
  python scripts/generate_podcast.py Remote_Office_Not_Required_short.md --engine vieneu --voice "Thái Sơn"
  python scripts/generate_podcast.py Remote_Office_Not_Required_short.md --engine vbee --voice maiphuong
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

import requests

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from ebook_translator.core.llm import LLMClient
from ebook_translator.core.tts import (
    DEFAULT_FALLBACK_VOICE_VIENEU,
    DEFAULT_FALLBACK_VOICE_ZEROTTS,
    POPULAR_VOICES,
    VBEE_POPULAR_VOICES,
    VBEE_TTS_URL,
    VIENEU_POPULAR_VOICES,
    VOICE_ALIASES,
    VOICE_TO_READER_NAME,
    ZEROTTS_POPULAR_VOICES,
    call_tts,
    call_vbee_tts,
    call_vieneu_tts,
    call_zerotts_tts,
    get_default_engine,
    get_reader_name,
    load_env,
    print_available_voices,
    resolve_voice_code,
    pick_voices_for_cast,
    describe_cast,
    render_multivoice,
    split_into_episodes,
    episode_output_paths,
    ensure_under_telegram_limit,
    estimate_duration_sec,
)
from ebook_translator.core.speakers import SpeakerPlan, detect_speakers

ENV_PATH = PROJECT_DIR / ".env"
PODCASTS_DIR = PROJECT_DIR / "output" / "podcasts"

DEFAULT_VOICE = resolve_voice_code()


# ── Dọn dẹp chỉ dẫn sân khấu ──

# Chỉ xoá đúng các chỉ dẫn sân khấu, KHÔNG xoá mọi cụm trong ngoặc. Bản cũ dùng
# re.sub(r"\(.*?\)", "", script) nên nuốt luôn các chú giải thuật ngữ tiếng Anh
# mà ARTICLE_TRANSLATE_SYSTEM_PROMPT cố ý yêu cầu chèn vào, ví dụ
# "Chế độ Nhà sáng lập (Founder Mode)" -> "Chế độ Nhà sáng lập".
_STAGE_CUE_WORDS = (
    r"nhạc\s*nền|nhạc\s*hiệu|intro|outro|sfx|sound|bgm|music|"
    r"host|mc|người\s*dẫn|dẫn\s*chuyện|narrator|"
    r"ngưng|ngừng|ngắt\s*nghỉ|pause|beat|cười|laugh|thở|"
    r"chuyển\s*cảnh|fade|transition|hiệu\s*ứng"
)

_STAGE_PATTERNS = [
    # [bất kỳ thứ gì trong ngoặc vuông] — luôn là chỉ dẫn sân khấu
    re.compile(r"\[[^\]\n]{0,120}\]"),
    # (Host:) (MC nói:) (Nhạc nền lên) ...
    re.compile(rf"\(\s*(?:{_STAGE_CUE_WORDS})\b[^)\n]{{0,80}}\)", re.IGNORECASE),
    # (...: nội dung) — dấu hai chấm trong ngoặc gần như luôn là chỉ dẫn
    re.compile(r"\(\s*[^)\n]{0,30}:\s*[^)\n]{0,80}\)"),
]


def strip_stage_directions(text: str) -> str:
    """Xoá chỉ dẫn sân khấu nhưng GIỮ chú giải thuật ngữ trong ngoặc đơn."""
    if not text:
        return ""
    for pattern in _STAGE_PATTERNS:
        text = pattern.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +([,.!?;:])", r"\1", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ── Chương phản biện & mở rộng ──

_CRITIQUE_SYSTEM = (
    "Bạn là một nhà phân tích phản biện sắc sảo và là Host podcast chuyên về kinh doanh, "
    "tư duy hệ thống và công nghệ.\n"
    "Tên của bạn (Host) là: {reader_name}.\n"
    "Người nghe VỪA NGHE XONG toàn văn nội dung gốc. Nhiệm vụ của bạn KHÔNG phải tóm tắt lại, "
    "mà là bổ sung một CHƯƠNG BÌNH LUẬN PHẢN BIỆN & MỞ RỘNG (~{target_chars} ký tự tiếng Việt).\n\n"
    "Nguyên tắc biên kịch:\n"
    "1. KHÔNG TÓM TẮT LẠI: Tuyệt đối không kể lại nội dung người nghe vừa nghe. Chỉ nhắc lại một "
    "luận điểm ở mức đủ để người nghe định vị, rồi đi thẳng vào phần mổ xẻ.\n"
    "2. PHẢN BIỆN & ĐỐI CHIẾU ĐA CHIỀU: Mổ xẻ các điểm mù và giới hạn của tác giả/diễn giả — "
    "khi nào lời khuyên này phản tác dụng? Nó ngầm giả định điều gì về bối cảnh, nguồn lực, "
    "quy mô? Liên kết đối chiếu với các trường phái tư duy khác và đánh giá tính ứng dụng "
    "trong kỷ nguyên số & AI hiện nay.\n"
    "3. KHUNG THỰC THI & HEURISTICS (DO & DON'T): Phân tích cụ thể 'Việc CẦN LÀM' vs "
    "'Bẫy CẦN TRÁNH', kèm câu hỏi tự kiểm toán (Self-Audit) để người nghe soi chiếu vào "
    "công việc và đời sống của chính mình.\n"
    "4. TRUNG THỰC TRÍ TUỆ: Nêu cả chỗ bạn thấy lập luận gốc thực sự vững, không phản biện "
    "lấy được. Nếu một luận điểm đúng, hãy nói rõ vì sao nó đúng.\n"
    "5. VĂN NÓI LIỀN MẠCH: Xưng 'tôi là {reader_name}' và 'các bạn'. Tuyệt đối KHÔNG đọc gạch "
    "đầu dòng hay số thứ tự máy móc; dùng câu chuyển tiếp mềm mại.\n"
    "6. LOẠI BỎ QUẢNG CÁO & KÊU GỌI: Bỏ hoàn toàn mọi đoạn quảng cáo nhà tài trợ, mã giảm giá, lời mời dùng thử sản phẩm, kêu gọi like/subscribe/bấm chuông, quảng bá khóa học hay newsletter của diễn giả, và lời nhắc link trong phần mô tả. Không nhắc lại tên nhà tài trợ. Nếu một đoạn chỉ là quảng cáo, bỏ hẳn và nối mạch nội dung liền trước và sau cho tự nhiên.\n"
    "7. ĐỊNH DẠNG: CHỈ xuất văn bản thuần để máy đọc. Không markdown, không [Nhạc nền], "
    "không (Host:)."
)


def generate_critique_section(
    content_vi: str,
    book_title: str,
    reader_name: str,
    video_info: dict | None = None,
    part_label: str | None = None,
    is_final: bool = True,
    llm: LLMClient | None = None,
    target_chars: int = 3500,
) -> str:
    """Sinh chương 'Góc nhìn phản biện & mở rộng' nối vào cuối một tập.

    Chạy theo TỪNG TẬP với nội dung của chính tập đó, nên mỗi tập tự đứng được.
    Tập cuối bổ sung thêm phần tổng kết toàn bộ nội dung.
    """
    llm = llm or LLMClient()
    video_info = video_info or {}

    scope = f" (phần {part_label})" if part_label else ""
    closing = (
        "Đây là phần CUỐI CÙNG của loạt tập này, nên hãy khép lại bằng một đúc kết xuyên suốt "
        "toàn bộ nội dung và một lời chào ấm áp.\n"
        if is_final
        else "Đây CHƯA phải phần cuối; hãy kết thúc bằng một câu dẫn dắt sang phần tiếp theo, "
        "không chào tạm biệt.\n"
    )

    source_line = ""
    if video_info.get("channel"):
        source_line = f"Nguồn: kênh {video_info['channel']}\n"

    # Nội dung dài thì lấy mẫu đầu + cuối, NHƯNG có cảnh báo rõ ràng (khác hẳn
    # bản deep cũ vốn cắt âm thầm ở generate_podcast.py:98).
    max_input = 60000
    if len(content_vi) > max_input:
        head, tail = int(max_input * 0.6), int(max_input * 0.4)
        print(
            f"ℹ️ [phản biện] Nội dung tập dài {len(content_vi)} ký tự, lấy mẫu "
            f"{head} đầu + {tail} cuối để viết bình luận (phần thân podcast vẫn ĐẦY ĐỦ).",
            file=sys.stderr,
        )
        sample = content_vi[:head] + "\n\n[... phần giữa ...]\n\n" + content_vi[-tail:]
    else:
        sample = content_vi

    system_prompt = _CRITIQUE_SYSTEM.format(reader_name=reader_name, target_chars=target_chars)
    prompt = (
        f"Nội dung gốc mà người nghe vừa nghe xong{scope}: '{book_title}'\n"
        f"{source_line}{closing}\n"
        f"Hãy viết chương bình luận phản biện & mở rộng (~{target_chars} ký tự) để máy đọc "
        f"bằng giọng Host {reader_name}:\n\n{sample}"
    )

    print(f"🧠 Đang dùng AI ({llm.model}) viết chương Phản Biện & Mở Rộng{scope} với Host [{reader_name}]...")
    raw = llm.complete(
        system=system_prompt,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=6000,
    )

    script = strip_stage_directions(raw)
    script = script.replace("**", "").replace("##", "").replace("#", "").strip()
    from ebook_translator.core.tts import sanitize_repetitions

    clean = sanitize_repetitions(script)
    if len(script) - len(clean) > 50:
        print(
            f"⚠️ Đã khử {len(script) - len(clean)} ký tự lặp trong chương phản biện.",
            file=sys.stderr,
        )
    return clean


# ── Dựng kịch bản Podcast Đầy Đủ (toàn văn + phản biện) ──

HOST_SPEAKER_ID = "HOST"


def build_full_podcast_turns(
    vi_turns: list[tuple[str, str]],
    title_vi: str,
    reader_name: str,
    speaker_plan: SpeakerPlan | None = None,
    video_info: dict | None = None,
    engine: str | None = None,
    speed: float = 1.15,
    target_minutes: float = 40.0,
    max_mb: float = 45.0,
    bitrate_kbps: int = 192,
    llm: LLMClient | None = None,
    add_critique: bool = True,
) -> list[list[tuple[str, str]]]:
    """Dựng danh sách tập, mỗi tập là [(speaker_id, text)] sẵn sàng render.

    Cấu trúc mỗi tập: lời dẫn của Host → TOÀN VĂN nội dung gốc (không cắt xén,
    đúng giọng từng người nói) → chương 'Góc nhìn phản biện & mở rộng' đọc bằng
    giọng Host.

    Chương phản biện được đặt ở CUỐI mỗi tập với nội dung của chính tập đó, để
    mỗi tập tự đứng được; tập cuối bổ sung phần tổng kết toàn bộ.
    """
    video_info = video_info or {}
    from ebook_translator.core.article import clean_text_for_tts

    body = [
        (sid, clean_text_for_tts(text).strip())
        for sid, text in vi_turns
        if text and text.strip()
    ]
    body = [(sid, text) for sid, text in body if text]
    if not body:
        return []

    # Cắt tập dựa trên phần THÂN; chương phản biện cộng thêm sau nên trừ hao
    # khoảng 15% trần để tập cuối cùng không vượt hạn mức Telegram.
    body_target = target_minutes * 0.85 if add_critique else target_minutes
    body_max_mb = max_mb * 0.85 if add_critique else max_mb

    episodes = split_into_episodes(
        body,
        target_minutes=body_target,
        max_mb=body_max_mb,
        engine=engine,
        speed=speed,
        bitrate_kbps=bitrate_kbps,
    )
    if not episodes:
        return []

    n = len(episodes)
    channel = video_info.get("channel") or ""
    out: list[list[tuple[str, str]]] = []

    for i, ep_body in enumerate(episodes, 1):
        part_label = f"{i}/{n}" if n > 1 else None
        turns: list[tuple[str, str]] = []

        # 1. Lời dẫn mở đầu (giọng Host)
        part_phrase = f", phần {i} trên {n}" if n > 1 else ""
        cast_line = ""
        if speaker_plan and speaker_plan.is_multi:
            names = [s.display for s in speaker_plan.speakers]
            if len(names) == 2:
                cast_line = f" Nội dung là cuộc trò chuyện giữa {names[0]} và {names[1]}."
            elif names:
                cast_line = f" Nội dung có sự tham gia của {', '.join(names)}."
        source_line = f" từ kênh {channel}" if channel else ""
        intro = (
            f"Xin chào các bạn, tôi là {reader_name}. "
            f"Hôm nay chúng ta cùng nghe trọn vẹn nội dung: {title_vi}{part_phrase}{source_line}."
            f"{cast_line} "
            f"Phần đầu là toàn văn nội dung gốc, và ở cuối tập tôi sẽ chia sẻ một vài "
            f"góc nhìn phản biện cùng những mở rộng đáng suy ngẫm. Chúng ta bắt đầu."
        )
        turns.append((HOST_SPEAKER_ID, intro))

        # 2. Toàn văn nội dung gốc, giữ đúng giọng từng người nói
        turns.extend(ep_body)

        # 3. Chương phản biện & mở rộng (giọng Host)
        if add_critique:
            ep_text = "\n\n".join(t for _, t in ep_body)
            transition = (
                f"Vâng, đó là toàn bộ nội dung gốc"
                f"{' của phần ' + str(i) if n > 1 else ''}. "
                f"Bây giờ, tôi là {reader_name}, xin chia sẻ vài góc nhìn phản biện và mở rộng."
            )
            turns.append((HOST_SPEAKER_ID, transition))
            try:
                critique = generate_critique_section(
                    content_vi=ep_text,
                    book_title=title_vi,
                    reader_name=reader_name,
                    video_info=video_info,
                    part_label=part_label,
                    is_final=(i == n),
                    llm=llm,
                )
                if critique:
                    turns.append((HOST_SPEAKER_ID, critique))
            except Exception as e:
                print(
                    f"⚠️ Không viết được chương phản biện cho tập {i}/{n}: {e}. "
                    f"Tập vẫn được xuất với đầy đủ nội dung gốc.",
                    file=sys.stderr,
                )

        out.append(turns)

    return out


def render_full_podcast_episodes(
    episodes: list[list[tuple[str, str]]],
    base_output: Path,
    voice_map: dict[str, str],
    engine: str | None = None,
    speed: float = 1.15,
    bitrate: str = "192k",
    progress: Callable[[int, int, int, int], None] | None = None,
) -> list[Path]:
    """Render từng tập ra MP3, trả về danh sách file đã tạo thành công."""
    paths = episode_output_paths(Path(base_output), len(episodes))
    done: list[Path] = []

    for i, (turns, out_path) in enumerate(zip(episodes, paths), 1):
        chars = sum(len(t) for _, t in turns)
        est = estimate_duration_sec("x" * chars, engine, speed) / 60
        print(f"\n🎬 [{i}/{len(episodes)}] Đang render '{out_path.name}' — {len(turns)} lượt, ~{est:.1f} phút...")

        def _prog(cur: int, total: int, sid: str, _i=i) -> None:
            if progress:
                progress(_i, len(episodes), cur, total)

        ok = render_multivoice(
            turns=turns,
            output_path=out_path,
            voice_map=voice_map,
            engine=engine,
            speed=speed,
            bitrate=bitrate,
            fallback=True,
            progress=_prog if progress else None,
        )
        if ok and out_path.exists():
            ensure_under_telegram_limit(out_path)
            done.append(out_path)
        else:
            print(f"❌ Không render được tập {i}/{len(episodes)}: {out_path.name}", file=sys.stderr)

    return done


def generate_podcast_script(
    text: str,
    book_title: str,
    voice_code: str | None = None,
    mode: str = "quick",
    engine: str | None = None,
) -> str:
    """Sử dụng mô hình AI để chuyển nội dung tóm tắt thành kịch bản Podcast đàm thoại hấp dẫn.
    
    Hỗ trợ 2 chế độ:
    - quick: Podcast Tinh Gọn (8–12 phút, ~2.500 – 3.500 ký tự)
    - deep:  Podcast Chuyên Sâu (20–30 phút, ~6.500 – 8.500 ký tự)
    """
    reader_name = get_reader_name(voice_code, engine=engine)
    llm = LLMClient()
    topic_term = "phân đoạn / chủ đề" if any(k in book_title.lower() for k in ["chương", "chapter", "đoạn", "bài viết"]) else "cuốn sách"

    if mode == "deep":
        print(f"🧠 Đang dùng AI ({llm.model}) biên soạn Kịch bản Podcast Chuyên Sâu (Deep Dive, 20-30p) với Host [{reader_name}]...")
        system_prompt = (
            f"Bạn là một Podcast Producer và Host chuyên nghiệp hàng đầu về kinh doanh, phát triển bản thân và tư duy học thuật.\n"
            f"Tên của bạn (Host / Người dẫn chương trình) là: {reader_name}.\n"
            f"Nhiệm vụ của bạn là chuyển đổi toàn bộ tài liệu sau thành một KỊCH BẢN NÓI ĐƠN THOẠI CHUYÊN SÂU (DEEP-DIVE PODCAST SCRIPT) kéo dài khoảng 20-30 phút nghe (khoảng 6.500 - 8.500 ký tự tiếng Việt).\n\n"
            f"Nguyên tắc biên kịch Podcast Chuyên Sâu:\n"
            f"1. VĂN NÓI ĐỐI THOẠI & ĐẬM CHẤT HOST: Xưng hô 'tôi là {reader_name}' và 'các bạn' hoặc 'bạn'. Có nhịp điệu phong phú, câu hỏi phản tư, ngắt nghỉ tự nhiên. Mở đầu bằng lời chào ấm áp, giới thiệu tên mình ({reader_name}) và dẫn dắt vào chủ đề một cách lôi cuốn. Kết thúc cũng nhắc lại tên ({reader_name}) và chào tạm biệt.\n"
            f"2. BẺ KHÓA TOÀN DIỆN CÁC TRỤ CỘT TƯ DUY: Tuyệt đối không tóm tắt hời hợt; hãy lần lượt đi sâu vào từng luận điểm và bài học lớn của {topic_term} '{book_title}'. Giải thích cặn kẽ: CƠ CHẾ hoạt động (Mechanism), VÌ SAO nó đúng, và các VÍ DỤ / CASE-STUDY thực tế từ tài liệu.\n"
            f"3. TÍCH HỢP SHORTFORM NOTES (PHẢN BIỆN & ĐỐI CHIẾU ĐA CHIỀU): Mổ xẻ các điểm mù/giới hạn của tác giả/diễn giả (khi nào lời khuyên này phản tác dụng?), liên kết đối chiếu với các trường phái khác, và đánh giá tính ứng dụng trong kỷ nguyên số & AI hiện nay.\n"
            f"4. KHUNG THỰC THI & HEURISTICS (DO & DON'T): Phân tích cụ thể các nguyên tắc 'Việc CẦN LÀM' vs 'Bẫy CẦN TRÁNH' và đặt ra câu hỏi tự kiểm toán (Self-Audit) để người nghe tự soi chiếu vào công việc và đời sống.\n"
            f"5. MẠCH KỂ CHUYỆN LIỀN MẠCH (NARRATIVE FLOW): Tuyệt đối KHÔNG đọc gạch đầu dòng khô khan hay số thứ tự máy móc. Dùng nghệ thuật dẫn chuyện và câu chuyển tiếp mềm mại để xâu chuỗi toàn bộ tập Podcast thành một dòng chảy tri thức hấp dẫn từ đầu đến cuối.\n"
            f"6. LOẠI BỎ QUẢNG CÁO & KÊU GỌI: Bỏ hoàn toàn mọi đoạn quảng cáo nhà tài trợ, mã giảm giá, lời mời dùng thử sản phẩm, kêu gọi like/subscribe/bấm chuông, quảng bá khóa học hay newsletter của diễn giả, và lời nhắc link trong phần mô tả. Không nhắc lại tên nhà tài trợ. Nếu một đoạn chỉ là quảng cáo, bỏ hẳn và nối mạch nội dung liền trước và sau cho tự nhiên.\n"
            f"7. ĐỊNH DẠNG ĐẦU RA: CHỈ XUẤT VĂN BẢN THUẦN ĐỂ ĐỌC (Plain text), không chứa các ký tự định dạng sân khấu như [Nhạc nền], (Host:), in đậm ** hay ký tự markdown #."
        )
        # Nếu tài liệu rất dài (như toàn bộ ebook > 80.000 ký tự), lấy mẫu thông minh đầu và cuối để bao quát
        sample_text = text[:80000] if len(text) <= 80000 else text[:45000] + "\n\n[... các chương tiếp theo ...]\n\n" + text[-35000:]
        prompt = (
            f"Hãy chuyển đổi toàn bộ tài liệu sau của {topic_term} '{book_title}' thành một kịch bản Podcast Chuyên Sâu (Deep Dive) hoàn chỉnh (~6.500 - 8.500 ký tự) để máy đọc bằng giọng của Host {reader_name}:\n\n"
            f"{sample_text}"
        )
        max_tokens = 8192
    else:
        print(f"🧠 Đang dùng AI ({llm.model}) biên soạn Kịch bản Podcast Tinh Gọn (Quick Listen, 8-12p) với Host [{reader_name}]...")
        system_prompt = (
            f"Bạn là một Podcast Producer và Host chuyên nghiệp hàng đầu về kinh doanh, phát triển bản thân và tư duy.\n"
            f"Tên của bạn (Host / Người dẫn chương trình) là: {reader_name}.\n"
            f"Nhiệm vụ của bạn là chuyển đổi bản tóm tắt thành một KỊCH BẢN NÓI ĐƠN THOẠI TINH GỌN (EXPRESS PODCAST SCRIPT) kéo dài khoảng 8-12 phút nghe (tầm 2.500 - 3.500 ký tự tiếng Việt).\n\n"
            f"Nguyên tắc biên kịch Podcast Tinh Gọn:\n"
            f"1. VĂN NÓI TỰ NHIÊN & GIỚI THIỆU TÊN: Sử dụng văn phong gần gũi, xưng hô 'tôi là {reader_name}' và 'các bạn' hoặc 'bạn'. Có nhịp thở, câu cảm thán, câu hỏi tu từ. Ngay phần mở đầu hãy giới thiệu tên mình một cách tự nhiên (ví dụ: 'Chào mừng các bạn đã quay trở lại... Tôi là {reader_name}...'). Ở phần kết thúc cũng nhắc lại tên ({reader_name}) và chào tạm biệt.\n"
            f"2. BÁM SÁT NỘI DUNG CHÍNH: Tập trung 100% vào nội dung cốt lõi của {topic_term} '{book_title}', đúc kết 2-3 bài học đắt giá nhất. Tuyệt đối KHÔNG đưa vào các ví dụ lạc đề không liên quan.\n"
            f"3. HOOK MỞ ĐẦU: Bắt đầu bằng một câu hỏi gợi mở, một nghịch lý hoặc câu chuyện gây tò mò thay vì đọc tiêu đề khô khan.\n"
            f"4. ĐIỂM CHẠM THỰC TẾ: Không đọc danh sách gạch đầu dòng; hãy xâu chuỗi các ý tưởng thành một câu chuyện có dòng chảy mạch lạc.\n"
            f"5. KẾT THÚC HÀNH ĐỘNG: Đúc kết 1 hành vi cụ thể có thể làm ngay hôm nay kèm lời chào ấm áp, truyền cảm hứng từ {reader_name}.\n"
            f"6. LOẠI BỎ QUẢNG CÁO & KÊU GỌI: Bỏ hoàn toàn mọi đoạn quảng cáo nhà tài trợ, mã giảm giá, lời mời dùng thử sản phẩm, kêu gọi like/subscribe/bấm chuông, quảng bá khóa học hay newsletter của diễn giả, và lời nhắc link trong phần mô tả. Không nhắc lại tên nhà tài trợ. Nếu một đoạn chỉ là quảng cáo, bỏ hẳn và nối mạch nội dung liền trước và sau cho tự nhiên.\n"
            f"7. ĐỊNH DẠNG ĐẦU RA: CHỈ XUẤT VĂN BẢN THUẦN ĐỂ ĐỌC (Plain text), không chứa các ký tự định dạng sân khấu như [Nhạc nền], (Host nói:), in đậm ** hay # markdown."
        )
        prompt = (
            f"Hãy chuyển đổi tài liệu tóm tắt sau của {topic_term} '{book_title}' thành một kịch bản Podcast Tinh Gọn hoàn chỉnh để máy đọc bằng giọng nói của Host {reader_name}:\n\n"
            f"{text[:15000]}"
        )
        max_tokens = 4500

    script = llm.complete(system=system_prompt, messages=[{"role": "user", "content": prompt}], max_tokens=max_tokens)

    # Dọn dẹp các ký tự định dạng markdown thừa nếu có
    script = strip_stage_directions(script)
    script = script.replace("**", "").replace("##", "").replace("#", "").strip()

    # Khử hiện tượng lặp từ / lặp câu (hallucination loop) do LLM sinh ra
    from ebook_translator.core.tts import sanitize_repetitions
    clean_script = sanitize_repetitions(script)
    if len(script) - len(clean_script) > 50:
        print(f"⚠️ Phát hiện vòng lặp suy thoái (repetition loop) trong kịch bản do LLM sinh ra! Đã khử {len(script) - len(clean_script)} ký tự lặp.", file=sys.stderr)
    return clean_script


def call_vieneu_tts(
    text: str,
    output_path: Path,
    voice: str = "Minh Quân",
    ref_audio: str | Path | None = None,
    speed: float = 1.0,
) -> bool:
    """Tổng hợp giọng nói bằng VieNeu-TTS v3 Turbo on-device (48kHz) và xuất file MP3 studio chất lượng cao."""
    try:
        from vieneu import Vieneu
    except ImportError:
        print("❌ Chưa cài đặt thư viện vieneu. Vui lòng chạy: pip install vieneu", file=sys.stderr)
        return False

    print(f"🎙️ [VieNeu-TTS v3 Turbo] Khởi tạo mô hình on-device (48kHz)...")
    try:
        vieneu_model = Vieneu()
    except Exception as e:
        print(f"❌ Lỗi khởi tạo VieNeu model: {e}", file=sys.stderr)
        return False

    target_voice = resolve_voice_code(voice, engine="vieneu")
    print(f"🔊 [VieNeu] Đang tổng hợp giọng nói ({len(text)} ký tự) với Host [{target_voice}]...")
    start_t = time.time()

    try:
        if ref_audio and Path(ref_audio).exists():
            print(f"🧬 [VieNeu] Đang thực hiện Voice Cloning tức thì từ audio mẫu: {ref_audio}")
            audio = vieneu_model.infer(text, ref_audio=str(ref_audio), denoise=True)
        else:
            audio = vieneu_model.infer(text, voice=target_voice)
    except Exception as e:
        print(f"❌ Lỗi khi thực hiện infer trên VieNeu: {e}", file=sys.stderr)
        return False

    elapsed = time.time() - start_t
    audio_dur = len(audio) / 48000
    rtf = elapsed / audio_dur if audio_dur > 0 else 0
    print(f"⚡ [VieNeu] Xử lý xong {audio_dur:.1f}s audio trong {elapsed:.2f}s (RTF: {rtf:.3f})")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
        tmp_wav_path = Path(tmp_wav.name)

    try:
        vieneu_model.save(audio, str(tmp_wav_path))

        # Sử dụng FFmpeg để chuẩn hóa, lọc âm và áp dụng tốc độ (atempo) sang MP3 192k 48kHz
        cmd = ["ffmpeg", "-y", "-i", str(tmp_wav_path)]
        if abs(speed - 1.0) > 0.01:
            cmd.extend(["-filter:a", f"atempo={speed}"])
        cmd.extend(["-b:a", "192k", "-ar", "48000", str(output_path)])

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"⚠️ Cảnh báo FFmpeg: {proc.stderr}. Đang lưu trực tiếp file WAV...", file=sys.stderr)
            vieneu_model.save(audio, str(output_path.with_suffix(".wav")))
            return True

        print(f"🎉 Đã tạo thành công file Podcast MP3 (VieNeu 48kHz, {speed}x): {output_path}")
        return True
    finally:
        tmp_wav_path.unlink(missing_ok=True)


def call_vbee_tts(
    text: str,
    output_path: Path,
    app_id: str,
    token: str,
    voice_code: str = "hn_female_maiphuong_vdts_48k-fhg",
    speed: float = 1.15,
) -> bool:
    """Gọi Vbee TTS API để sinh file âm thanh MP3 (Dự phòng cloud)."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    payload = {
        "app_id": app_id,
        "response_type": "indirect",
        "callback_url": "https://httpbin.org/post",
        "input_text": text,
        "voice_code": voice_code,
        "audio_type": "mp3",
        "bitrate": 128,
        "speed_rate": speed,
    }

    print(f"🎙️ Đang gửi kịch bản ({len(text)} ký tự) tới Vbee API với giọng [{voice_code}]...")
    try:
        res = requests.post(VBEE_TTS_URL, headers=headers, json=payload, timeout=30)
        data = res.json()
    except Exception as e:
        print(f"❌ Lỗi kết nối tới Vbee API: {e}", file=sys.stderr)
        return False

    if data.get("status") != 1 or "result" not in data or "request_id" not in data["result"]:
        print(f"❌ Vbee API trả về lỗi: {data}", file=sys.stderr)
        return False

    request_id = data["result"]["request_id"]
    print(f"⏳ Đang render audio trên Vbee (Request ID: {request_id})...")

    status_url = f"{VBEE_TTS_URL}/{request_id}"
    start_time = time.time()

    while time.time() - start_time < 360:
        time.sleep(6)
        try:
            status_res = requests.get(status_url, headers=headers, timeout=15)
            status_data = status_res.json()
        except Exception:
            continue

        res_info = status_data.get("result", {})
        status = res_info.get("status")
        progress = res_info.get("progress", 0)

        if status == "SUCCESS":
            audio_url = res_info.get("audio_link")
            print(f"✅ Render Vbee thành công! Đang tải file MP3...")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with requests.get(audio_url, stream=True) as stream_res:
                stream_res.raise_for_status()
                with open(output_path, "wb") as f:
                    for chunk in stream_res.iter_content(chunk_size=16384):
                        f.write(chunk)
            print(f"🎉 Đã lưu file Podcast MP3: {output_path}")
            return True
        elif status == "FAILED":
            print(f"❌ Render thất bại từ Vbee: {status_data}", file=sys.stderr)
            return False
        else:
            print(f"   ⏳ Đang xử lý âm thanh ({progress}%)...")

    print("❌ Hết thời gian chờ kết quả từ Vbee.", file=sys.stderr)
    return False


def extract_text_from_file(path: Path) -> str:
    """Trích xuất nội dung văn bản từ file .epub, .md hoặc .txt."""
    ext = path.suffix.lower()
    if ext == ".epub":
        try:
            from ebook_translator.readers.epub_reader import read_epub
            book = read_epub(path)
            parts = []
            for ch in book.chapters:
                clean_txt = re.sub(r"<[^>]+>", " ", ch.html)
                clean_txt = " ".join(clean_txt.split())
                if clean_txt:
                    parts.append(clean_txt)
            return "\n\n".join(parts)
        except Exception as e:
            print(f"Lỗi đọc EPUB: {e}, thử đọc dạng text...", file=sys.stderr)
    return path.read_text(encoding="utf-8", errors="ignore")


def clean_text_for_tts(text: str) -> str:
    """Làm sạch các ký tự cú pháp markdown để giọng đọc AI đọc trơn tru và tự nhiên."""
    from ebook_translator.core.tts import sanitize_repetitions
    text = sanitize_repetitions(text)
    cleaned = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"^#{1,6}\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = cleaned.replace("**", "").replace("*", "").replace("`", "")
    cleaned = re.sub(r"^>\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^[-\*_]{3,}\s*$", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^[-•]\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\n{2,}", "\n\n", cleaned)
    return cleaned.strip()


def create_podcast_for_book(
    input_file: Path | str,
    voice: str | None = None,
    engine: str | None = None,
    speed: float = 1.15,
    ref_audio: str | Path | None = None,
    send_telegram: bool = False,
    mode: str = "quick",
    use_existing_script: Path | str | None = None,
    script_only: bool = False,
) -> Path | None:
    """Tạo tập podcast hoặc audio đọc toàn văn từ file tóm tắt / markdown và trả về đường dẫn file MP3 hoàn thành.
    
    mode:
    - 'direct': Đọc Toàn Văn (Toàn bộ nội dung file) -> <stem>_audio.mp3
    - 'quick':  Podcast Tinh Gọn (8–12 phút) -> <stem>_podcast_tinh_gon.mp3
    - 'deep':   Podcast Chuyên Sâu (20–30 phút) -> <stem>_podcast_chuyen_sau.mp3
    """
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"❌ Không tìm thấy file: {input_path}", file=sys.stderr)
        return None

    env = load_env(ENV_PATH)
    active_engine = (engine or os.environ.get("TTS_ENGINE") or env.get("TTS_ENGINE") or "zerotts").lower().strip()
    target_voice = resolve_voice_code(voice, engine=active_engine)
    voice_desc = POPULAR_VOICES.get(target_voice, target_voice)
    reader_name = get_reader_name(target_voice, engine=active_engine)

    is_direct = (mode == "direct")
    is_deep = (mode == "deep")

    if is_direct:
        title_type = "Bản Đọc Toàn Văn"
        desc_type = "Bản đọc âm thanh trọn vẹn toàn bộ nội dung bài viết"
    elif is_deep:
        title_type = "Podcast Chuyên Sâu"
        desc_type = (
            "Bản Nghe Masterclass (20–30 phút) — Phân tích chi tiết cơ chế, phản biện đa chiều và ma trận hành động"
        )
    else:
        title_type = "Podcast Tinh Gọn"
        desc_type = "Bản Nghe Tinh Cất (8–12 phút) — Nắm bắt nhanh luận đề và bài học cốt lõi"

    if active_engine == "zerotts":
        engine_label = "ZeroTTS (CPU Real-Time 48kHz)"
    elif active_engine == "vieneu":
        engine_label = "VieNeu-TTS v3 Turbo (On-device 48kHz)"
    else:
        engine_label = "Vbee AIVoice (Cloud)"

    print(f"🎙️ Định dạng: {title_type} ({'Direct Narration' if is_direct else ('Deep Dive' if is_deep else 'Express')})")
    print(f"🎙️ Engine TTS: {engine_label}")
    print(f"🗣️ Giọng đọc: {voice_desc} (Mã: {target_voice}, Người đọc: {reader_name})")

    stem = input_path.stem
    clean_stem = (
        stem.replace("_shortform", "")
        .replace("_short", "")
        .replace("_vn", "")
        .replace("_VN", "")
        .replace(".vi", "")
        .replace("_vi", "")
    )
    PODCASTS_DIR.mkdir(parents=True, exist_ok=True)

    content = extract_text_from_file(input_path)
    clean_title = clean_stem.replace("_", " ").strip()
    if is_direct:
        script_file = PODCASTS_DIR / f"{clean_stem}_audio_script.txt"
        output_mp3 = PODCASTS_DIR / f"{clean_stem}_audio.mp3"
        if use_existing_script and Path(use_existing_script).exists():
            print(f"📄 Dùng trực tiếp kịch bản có sẵn: {use_existing_script}")
            script = Path(use_existing_script).read_text(encoding="utf-8")
        else:
            clean_body = clean_text_for_tts(content)
            display_title = clean_title.replace("-", " ").title()
            spoken_intro = f"{display_title}.\n\n"
            spoken_outro = f"\n\nCảm ơn các bạn đã lắng nghe bản đọc {display_title}."
            script = spoken_intro + clean_body + spoken_outro
    elif is_deep:
        script_file = PODCASTS_DIR / f"{clean_stem}_podcast_chuyen_sau_script.txt"
        output_mp3 = PODCASTS_DIR / f"{clean_stem}_podcast_chuyen_sau.mp3"
        if use_existing_script and Path(use_existing_script).exists():
            print(f"📄 Dùng trực tiếp kịch bản có sẵn: {use_existing_script}")
            script = Path(use_existing_script).read_text(encoding="utf-8")
        else:
            script = generate_podcast_script(content, book_title=clean_title, voice_code=target_voice, mode="deep", engine=active_engine)
    else:
        script_file = PODCASTS_DIR / f"{clean_stem}_podcast_tinh_gon_script.txt"
        output_mp3 = PODCASTS_DIR / f"{clean_stem}_podcast_tinh_gon.mp3"
        if use_existing_script and Path(use_existing_script).exists():
            print(f"📄 Dùng trực tiếp kịch bản có sẵn: {use_existing_script}")
            script = Path(use_existing_script).read_text(encoding="utf-8")
        else:
            script = generate_podcast_script(content, book_title=clean_title, voice_code=target_voice, mode="quick", engine=active_engine)

    script_file.write_text(script, encoding="utf-8")
    print(f"📝 Đã lưu kịch bản {title_type} tại: {script_file}")

    if script_only:
        print("🛑 Chế độ --script-only: Hoàn tất lưu kịch bản, bỏ qua tổng hợp âm thanh.")
        return script_file

    success = call_tts(
        text=script,
        output_path=output_mp3,
        voice=target_voice,
        engine=active_engine,
        speed=speed,
        ref_audio=ref_audio,
        fallback=True,
    )

    if success:

        # Tự động trích xuất bìa sách tiếng Anh và nhúng vào file MP3
        try:
            from update_podcast_episode_covers import (
                extract_pdf_cover,
                extract_epub_cover,
                make_square_podcast_artwork,
                embed_cover_to_mp3,
                COVERS_DIR,
            )
            cov_img = None
            if input_path.suffix.lower() == ".pdf":
                cov_img = extract_pdf_cover(input_path)
            elif input_path.suffix.lower() == ".epub":
                cov_img = extract_epub_cover(input_path)

            if cov_img:
                ep_cov_path = COVERS_DIR / f"{clean_stem}.jpg"
                art = make_square_podcast_artwork(cov_img)
                art.save(ep_cov_path, "JPEG", quality=92)
                embed_cover_to_mp3(output_mp3, ep_cov_path)
                print(f"🎨 Đã tạo và nhúng bìa sách tiếng Anh vào MP3: {ep_cov_path.name}")
        except Exception as e:
            print(f"⚠️ Không thể trích xuất bìa sách: {e}")

        # Tự động cập nhật Private RSS Feed cho Apple Podcasts & CarPlay
        try:
            from ebook_translator.core.podcast_rss import update_podcast_feed
            update_podcast_feed()
        except Exception as e:
            print(f"⚠️ Không thể cập nhật Podcast RSS Feed: {e}")

        if send_telegram:
            telegram_script = PROJECT_DIR / "scripts" / "send_to_telegram.py"
            if telegram_script.exists():
                icon = "🎙️" if is_direct else ("🧠🎙️" if mode == "deep" else "⚡🎙️")
                header = f"{icon} <b>{clean_title}</b>"
                try:
                    from ebook_translator.core.tags import generate_topic_tags
                    raw_tags = generate_topic_tags(clean_stem, output_type="")
                    keyword_candidates = [
                        t for t in raw_tags
                        if t.lower() not in ("#podcast", "#audio", "#short", "#dich", "#article", "#shortform", "#kienthuc")
                    ]
                    chosen_kw = keyword_candidates[0] if keyword_candidates else "#kienthuc"
                    if not chosen_kw.startswith("#"):
                        chosen_kw = f"#{chosen_kw}"
                except Exception:
                    chosen_kw = "#kienthuc"
                is_long = (output_mp3.stat().st_size > 3 * 1024 * 1024) or (mode == "deep")
                type_tag = "#podcast" if is_long else "#audio"
                desc_line = f"📝 {type_tag} {chosen_kw}"
                engine_tag = "ZeroTTS" if active_engine == "zerotts" else ("VieNeu" if active_engine == "vieneu" else "Vbee")
                caption = (
                    f"{header}\n"
                    f"🎧 {reader_name} ({engine_tag}, {speed}x)\n"
                    f"{desc_line}"
                )
                subprocess.run([sys.executable, str(telegram_script), str(output_mp3), "--caption", caption, "--title", clean_title, "--performer", reader_name])
        return output_mp3
    return None


def main():
    parser = argparse.ArgumentParser(description="Tạo Podcast audio tóm tắt sách bằng ZeroTTS (mặc định), VieNeu v3 Turbo hoặc Vbee API")
    parser.add_argument("input_file", nargs="?", help="Đường dẫn file tóm tắt (.epub, .md hoặc .txt)")
    parser.add_argument("--mode", choices=["quick", "deep", "direct"], default="quick", help="Loại Audio: quick (Podcast tinh gọn 8-12p), deep (Podcast chuyên sâu 20-30p), hoặc direct (Đọc toàn văn file)")
    parser.add_argument("--deep", action="store_const", dest="mode", const="deep", help="Phím tắt: Tạo Podcast Chuyên Sâu (20-30p)")
    parser.add_argument("--quick", action="store_const", dest="mode", const="quick", help="Phím tắt: Tạo Podcast Tinh Gọn (8-12p)")
    parser.add_argument("--direct", action="store_const", dest="mode", const="direct", help="Phím tắt: Đọc toàn văn trực tiếp file markdown/văn bản")
    parser.add_argument("--engine", choices=["zerotts", "vieneu", "vbee"], default=None, help="Chọn TTS engine (Mặc định: zerotts hoặc cấu hình trong .env)")
    parser.add_argument("--voice", default=None, help="Tên giọng đọc hoặc alias (Mặc định: theo engine trong .env)")
    parser.add_argument("--ref-audio", default=None, help="File âm thanh mẫu (3-8s) để clone giọng tức thì (chỉ VieNeu)")
    parser.add_argument("--speed", type=float, default=1.15, help="Tốc độ đọc (Mặc định: 1.15)")
    parser.add_argument("--telegram", action="store_true", help="Tự động gửi file Podcast sang Telegram khi hoàn thành")
    parser.add_argument("--script-only", action="store_true", help="Chỉ tạo kịch bản Podcast văn bản, không gọi TTS")
    parser.add_argument("--use-existing-script", help="Dùng trực tiếp file script có sẵn thay vì sinh mới bằng LLM")
    parser.add_argument("--list-voices", action="store_true", help="Hiển thị danh sách giọng đọc có sẵn")
    args = parser.parse_args()

    if args.list_voices:
        print_available_voices()
        return

    if not args.input_file:
        parser.print_help()
        sys.exit("\n❌ Vui lòng cung cấp đường dẫn file sách hoặc tóm tắt!")

    input_path = Path(args.input_file)
    if not input_path.exists():
        sys.exit(f"❌ Không tìm thấy file: {input_path}")

    try:
        create_podcast_for_book(
            input_file=input_path,
            voice=args.voice,
            engine=args.engine,
            speed=args.speed,
            ref_audio=args.ref_audio,
            send_telegram=args.telegram,
            mode=args.mode,
            use_existing_script=args.use_existing_script,
            script_only=args.script_only,
        )
    except Exception as e:
        import traceback
        tb_str = traceback.format_exc()
        print(f"\n🚨 [PodcastPipeline] Gặp lỗi nghiêm trọng: {e}\n{tb_str}", file=sys.stderr)
        try:
            sys.path.insert(0, str(PROJECT_DIR / "scripts"))
            from antigravity_healer import heal_error

            def retry():
                print("\n🔄 [PodcastPipeline] Đang chạy lại tạo podcast sau khi Agent sửa lỗi...")
                create_podcast_for_book(
                    input_file=input_path,
                    voice=args.voice,
                    engine=args.engine,
                    speed=args.speed,
                    ref_audio=args.ref_audio,
                    send_telegram=args.telegram,
                    mode=args.mode,
                    use_existing_script=args.use_existing_script,
                    script_only=args.script_only,
                )

            heal_error(
                error=e,
                traceback_str=tb_str,
                failing_file="scripts/generate_podcast.py",
                context_info=f"Input: {input_path.name}, Mode: {args.mode}, Engine: {args.engine or 'default'}",
                retry_fn=retry,
            )
        except Exception as heal_err:
            print(f"⚠️ Không thể gọi Antigravity Healer: {heal_err}", file=sys.stderr)
            raise e


if __name__ == "__main__":
    main()
