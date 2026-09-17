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
)

ENV_PATH = PROJECT_DIR / ".env"
PODCASTS_DIR = PROJECT_DIR / "output" / "podcasts"

DEFAULT_VOICE = resolve_voice_code()


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
            f"6. ĐỊNH DẠNG ĐẦU RA: CHỈ XUẤT VĂN BẢN THUẦN ĐỂ ĐỌC (Plain text), không chứa các ký tự định dạng sân khấu như [Nhạc nền], (Host:), in đậm ** hay ký tự markdown #."
        )
        prompt = (
            f"Hãy chuyển đổi toàn bộ tài liệu sau của {topic_term} '{book_title}' thành một kịch bản Podcast Chuyên Sâu (Deep Dive) hoàn chỉnh (~6.500 - 8.500 ký tự) để máy đọc bằng giọng của Host {reader_name}:\n\n"
            f"{text[:35000]}"
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
            f"6. ĐỊNH DẠNG ĐẦU RA: CHỈ XUẤT VĂN BẢN THUẦN ĐỂ ĐỌC (Plain text), không chứa các ký tự định dạng sân khấu như [Nhạc nền], (Host nói:), in đậm ** hay # markdown."
        )
        prompt = (
            f"Hãy chuyển đổi tài liệu tóm tắt sau của {topic_term} '{book_title}' thành một kịch bản Podcast Tinh Gọn hoàn chỉnh để máy đọc bằng giọng nói của Host {reader_name}:\n\n"
            f"{text[:15000]}"
        )
        max_tokens = 4500

    script = llm.complete(system=system_prompt, messages=[{"role": "user", "content": prompt}], max_tokens=max_tokens)

    # Dọn dẹp các ký tự định dạng markdown thừa nếu có
    script = re.sub(r"\(.*?\)", "", script)
    script = script.replace("**", "").replace("##", "").replace("#", "").strip()
    return script


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
        clean_body = clean_text_for_tts(content)
        display_title = clean_title.replace("-", " ").title()
        spoken_intro = f"{display_title}.\n\n"
        spoken_outro = f"\n\nCảm ơn các bạn đã lắng nghe bản đọc {display_title}."
        script = spoken_intro + clean_body + spoken_outro
        script_file = PODCASTS_DIR / f"{clean_stem}_audio_script.txt"
        output_mp3 = PODCASTS_DIR / f"{clean_stem}_audio.mp3"
    elif is_deep:
        script = generate_podcast_script(content, book_title=clean_title, voice_code=target_voice, mode="deep", engine=active_engine)
        script_file = PODCASTS_DIR / f"{clean_stem}_podcast_chuyen_sau_script.txt"
        output_mp3 = PODCASTS_DIR / f"{clean_stem}_podcast_chuyen_sau.mp3"
    else:
        script = generate_podcast_script(content, book_title=clean_title, voice_code=target_voice, mode="quick", engine=active_engine)
        script_file = PODCASTS_DIR / f"{clean_stem}_podcast_tinh_gon_script.txt"
        output_mp3 = PODCASTS_DIR / f"{clean_stem}_podcast_tinh_gon.mp3"

    script_file.write_text(script, encoding="utf-8")
    print(f"📝 Đã lưu kịch bản {title_type} tại: {script_file}")

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

    create_podcast_for_book(
        input_file=input_path,
        voice=args.voice,
        engine=args.engine,
        speed=args.speed,
        ref_audio=args.ref_audio,
        send_telegram=args.telegram,
        mode=args.mode,
    )


if __name__ == "__main__":
    main()
