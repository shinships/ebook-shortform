#!/usr/bin/env python3
"""generate_podcast.py — Tự động tạo audio tóm tắt sách phong cách Podcast bằng VieNeu-TTS v3 Turbo (mặc định) hoặc Vbee API.

Quy trình:
1. Nhận file tóm tắt (Markdown / Text / Ebook).
2. Dùng Gemini biên soạn kịch bản Podcast tự nhiên, đàm thoại lôi cuốn.
3. Chuyển đổi thành file âm thanh giọng đọc tiếng Việt chân thực (MP3 48kHz).
   - Mặc định: VieNeu-TTS v3 Turbo (chạy trực tiếp trên máy, âm thanh 48kHz, đọc tiếng Anh mượt mà, hỗ trợ Voice Cloning).
   - Tùy chọn: Vbee AIVoice API (Cloud).
4. Lưu trữ vào output/podcasts/.
5. (Tùy chọn) Gửi file Podcast MP3 qua Telegram.

Cách dùng:
  python scripts/generate_podcast.py Remote_Office_Not_Required_short.md
  python scripts/generate_podcast.py Remote_Office_Not_Required_short.md --voice "Thái Sơn"
  python scripts/generate_podcast.py Remote_Office_Not_Required_short.md --voice "Anh Khôi" --speed 1.1 --telegram
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

ENV_PATH = PROJECT_DIR / ".env"
PODCASTS_DIR = PROJECT_DIR / "output" / "podcasts"

# Endpoint Vbee AIVoice API (Dự phòng)
VBEE_TTS_URL = "https://vbee.vn/api/v1/tts"

# ── 1. Danh sách giọng đọc VieNeu-TTS v3 Turbo (Mặc định) ──
VIENEU_POPULAR_VOICES = {
    "Minh Quân": "HN - Minh Quân (Nam Bắc, tự nhiên, sinh động - Mặc định)",
    "Thái Sơn": "SG - Thái Sơn (Nam Nam, kể chuyện / audiobook truyền cảm)",
    "Anh Khôi": "HN - Anh Khôi (Nam Bắc, kể chuyện / sách nói trầm ấm)",
    "Quỳnh Anh": "HN - Quỳnh Anh (Nữ Bắc, đọc truyện / diễn cảm sâu lắng)",
    "Ngọc Huyền": "HN - Ngọc Huyền (Nữ Bắc, tự nhiên, phong cách podcast)",
    "Thanh Bình": "HN - Thanh Bình (Nam Bắc, kể chuyện, mạch lạc)",
    "Ngọc Linh": "HN - Ngọc Linh (Nữ Bắc, kể chuyện nhẹ nhàng)",
    "Thục Đoan": "SG - Thục Đoan (Nữ Nam, kể chuyện dịu dàng)",
    "Trúc Ly": "HN - Trúc Ly (Nữ Bắc, tự nhiên, trong trẻo)",
    "Phạm Tuyên": "HN - Phạm Tuyên (Nam Bắc, tự nhiên, gần gũi)",
    "Mỹ Duyên": "SG - Mỹ Duyên (Nữ Nam, đọc truyện ấm áp)",
    "Quang Sơn": "Huế - Quang Sơn (Nam Trung, tự nhiên)",
    "Ngọc Trân": "Huế - Ngọc Trân (Nữ Trung, tự nhiên ngọt ngào)",
    "Đoan Trang": "HN - Đoan Trang (Nữ Bắc, tự nhiên)",
    "Mai Anh": "HN - Mai Anh (Nữ Bắc, phong cách tin tức)",
    "Minh Đức": "HN - Minh Đức (Nam Bắc, phong cách tin tức)",
    "Adam": "SG - Adam (Nam Nam, hiện đại, tự nhiên)",
    "Đức Trí": "SG - Đức Trí (Nam Nam, đọc truyện)",
    "Kim Thanh": "SG - Kim Thanh (Nữ Nam, đọc truyện)",
    "Minh Triết": "SG - Minh Triết (Nam Nam, tin tức)",
    "Thùy Dung": "SG - Thùy Dung (Nữ Nam, tin tức)",
    "Xuân Vĩnh": "HN - Xuân Vĩnh (Nam Bắc, tự nhiên)",
}

# ── 2. Danh sách giọng đọc Vbee AIVoice (Dự phòng / Thay thế) ──
VBEE_POPULAR_VOICES = {
    "hn_female_maiphuong_vdts_48k-fhg": "HN - Mai Phương (Nữ Bắc, nhẹ nhàng)",
    "hn_male_manhdung_news_48k-fhg": "HN - Mạnh Dũng (Nam Bắc, thời sự)",
    "hn_female_ngochuyen_full_48k-fhg": "HN - Ngọc Huyền (Nữ Bắc, podcast)",
    "hn_male_thanhlong_talk_48k-fhg": "HN - Thanh Long (Nam Bắc, đàm thoại)",
    "hn_male_phuthang_stor80dt_48k-fhg": "HN - Anh Khôi (Nam Bắc, đọc truyện)",
    "hn_male_minhquan_yt-stable": "HN - Minh Quân (Nam Bắc, trẻ trung)",
    "sg_female_lantrinh_vdts_48k-fhg": "SG - Lan Trinh (Nữ Nam, dịu dàng)",
    "sg_female_thaotrinh_full_48k-fhg": "SG - Thảo Trinh (Nữ Nam, truyền cảm)",
    "sg_male_trungkien_vdts_48k-fhg": "SG - Trung Kiên (Nam Nam, ấm)",
    "sg_male_minhhoang_full_48k-fhg": "SG - Minh Hoàng (Nam Nam, hiện đại)",
    "hue_female_huonggiang_full_48k-fhg": "Huế - Hương Giang (Nữ Huế, ngọt ngào)",
    "hue_male_duyphuong_full_48k-fhg": "Huế - Duy Phương (Nam Huế, sâu lắng)",
}

# Tổng hợp để tương thích tra cứu
POPULAR_VOICES = {**VIENEU_POPULAR_VOICES, **VBEE_POPULAR_VOICES}

VOICE_ALIASES = {
    # VieNeu Aliases
    "minhquan": "Minh Quân",
    "minh quân": "Minh Quân",
    "thaison": "Thái Sơn",
    "thái sơn": "Thái Sơn",
    "anhkhoi": "Anh Khôi",
    "anh khôi": "Anh Khôi",
    "quynhanh": "Quỳnh Anh",
    "quỳnh anh": "Quỳnh Anh",
    "ngochuyen": "Ngọc Huyền",
    "ngọc huyền": "Ngọc Huyền",
    "thanhbinh": "Thanh Bình",
    "thanh bình": "Thanh Bình",
    "ngoclinh": "Ngọc Linh",
    "ngọc linh": "Ngọc Linh",
    "thucdoan": "Thục Đoan",
    "thục đoan": "Thục Đoan",
    "trucly": "Trúc Ly",
    "trúc ly": "Trúc Ly",
    "phamtuyen": "Phạm Tuyên",
    "phạm tuyên": "Phạm Tuyên",
    "myduyen": "Mỹ Duyên",
    "mỹ duyên": "Mỹ Duyên",
    "quangson": "Quang Sơn",
    "quang sơn": "Quang Sơn",
    "ngoctran": "Ngọc Trân",
    "ngọc trân": "Ngọc Trân",
    "doantrang": "Đoan Trang",
    "đoan trang": "Đoan Trang",
    "maianh": "Mai Anh",
    "mai anh": "Mai Anh",
    "minhduc": "Minh Đức",
    "minh đức": "Minh Đức",
    "adam": "Adam",
    "xuanvinh": "Xuân Vĩnh",
    "xuân vĩnh": "Xuân Vĩnh",
    # Vbee Aliases (vẫn hỗ trợ nếu chọn engine vbee)
    "maiphuong": "hn_female_maiphuong_vdts_48k-fhg",
    "mai phương": "hn_female_maiphuong_vdts_48k-fhg",
    "manhdung": "hn_male_manhdung_news_48k-fhg",
    "mạnh dũng": "hn_male_manhdung_news_48k-fhg",
    "thanhlong": "hn_male_thanhlong_talk_48k-fhg",
    "thanh long": "hn_male_thanhlong_talk_48k-fhg",
    "lantrinh": "sg_female_lantrinh_vdts_48k-fhg",
    "lan trinh": "sg_female_lantrinh_vdts_48k-fhg",
    "thaotrinh": "sg_female_thaotrinh_full_48k-fhg",
    "thảo trinh": "sg_female_thaotrinh_full_48k-fhg",
    "trungkien": "sg_male_trungkien_vdts_48k-fhg",
    "trung kiên": "sg_male_trungkien_vdts_48k-fhg",
    "minhhoang": "sg_male_minhhoang_full_48k-fhg",
    "minh hoàng": "sg_male_minhhoang_full_48k-fhg",
    "huonggiang": "hue_female_huonggiang_full_48k-fhg",
    "hương giang": "hue_female_huonggiang_full_48k-fhg",
    "duyphuong": "hue_male_duyphuong_full_48k-fhg",
    "duy phương": "hue_male_duyphuong_full_48k-fhg",
}

DEFAULT_FALLBACK_VOICE = "Minh Quân"

VOICE_TO_READER_NAME = {
    # VieNeu (Tên hiển thị trực tiếp)
    "Minh Quân": "Minh Quân",
    "Thái Sơn": "Thái Sơn",
    "Anh Khôi": "Anh Khôi",
    "Quỳnh Anh": "Quỳnh Anh",
    "Ngọc Huyền": "Ngọc Huyền",
    "Thanh Bình": "Thanh Bình",
    "Ngọc Linh": "Ngọc Linh",
    "Thục Đoan": "Thục Đoan",
    "Trúc Ly": "Trúc Ly",
    "Phạm Tuyên": "Phạm Tuyên",
    "Mỹ Duyên": "Mỹ Duyên",
    "Quang Sơn": "Quang Sơn",
    "Ngọc Trân": "Ngọc Trân",
    "Đoan Trang": "Đoan Trang",
    "Mai Anh": "Mai Anh",
    "Minh Đức": "Minh Đức",
    "Adam": "Adam",
    "Đức Trí": "Đức Trí",
    "Kim Thanh": "Kim Thanh",
    "Minh Triết": "Minh Triết",
    "Thùy Dung": "Thùy Dung",
    "Xuân Vĩnh": "Xuân Vĩnh",
    # Vbee
    "hn_female_maiphuong_vdts_48k-fhg": "Mai Phương",
    "hn_male_manhdung_news_48k-fhg": "Mạnh Dũng",
    "hn_female_ngochuyen_full_48k-fhg": "Ngọc Huyền",
    "hn_male_thanhlong_talk_48k-fhg": "Thanh Long",
    "hn_male_phuthang_stor80dt_48k-fhg": "Anh Khôi",
    "hn_male_minhquan_yt-stable": "Minh Quân",
    "sg_female_lantrinh_vdts_48k-fhg": "Lan Trinh",
    "sg_female_thaotrinh_full_48k-fhg": "Thảo Trinh",
    "sg_male_trungkien_vdts_48k-fhg": "Trung Kiên",
    "sg_male_minhhoang_full_48k-fhg": "Minh Hoàng",
    "hue_female_huonggiang_full_48k-fhg": "Hương Giang",
    "hue_male_duyphuong_full_48k-fhg": "Duy Phương",
}


def load_env(path: Path) -> dict[str, str]:
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def get_default_engine() -> str:
    """Xác định TTS engine mặc định: vieneu (ưu tiên) hoặc vbee."""
    env = load_env(ENV_PATH)
    engine = os.environ.get("TTS_ENGINE") or env.get("TTS_ENGINE") or "vieneu"
    return engine.lower().strip()


def resolve_voice_code(voice_input: str | None = None, engine: str | None = None) -> str:
    """Xác định mã giọng đọc chuẩn từ tên trực tiếp, alias, hoặc cấu hình .env."""
    if not engine:
        engine = get_default_engine()

    if not voice_input:
        env = load_env(ENV_PATH)
        if engine == "vbee":
            voice_input = os.environ.get("VBEE_VOICE") or env.get("VBEE_VOICE") or "hn_female_maiphuong_vdts_48k-fhg"
        else:
            voice_input = os.environ.get("VIENEU_VOICE") or env.get("VIENEU_VOICE") or DEFAULT_FALLBACK_VOICE

    raw = str(voice_input).strip()
    if raw in POPULAR_VOICES:
        return raw

    norm = raw.lower().replace(" ", "").replace("_", "").replace("-", "")
    for alias, code in VOICE_ALIASES.items():
        alias_norm = alias.replace(" ", "").replace("_", "").replace("-", "")
        if norm == alias_norm or alias_norm in norm:
            return code

    return raw


def get_reader_name(voice_code: str | None = None) -> str:
    """Lấy tên người đọc thân thiện tương ứng với mã giọng."""
    target_code = resolve_voice_code(voice_code)
    return VOICE_TO_READER_NAME.get(target_code, target_code)


DEFAULT_VOICE = resolve_voice_code()


def generate_podcast_script(text: str, book_title: str, voice_code: str | None = None) -> str:
    """Sử dụng Gemini để chuyển nội dung tóm tắt thành kịch bản Podcast đàm thoại hấp dẫn."""
    reader_name = get_reader_name(voice_code)
    print(f"🧠 Đang dùng Gemini để biên soạn kịch bản Podcast với người dẫn chuyện [{reader_name}]...")
    llm = LLMClient()

    system_prompt = (
        f"Bạn là một Podcast Producer và Host chuyên nghiệp hàng đầu về sách kinh doanh, phát triển bản thân và tư duy.\n"
        f"Tên của bạn (Host / Người dẫn chương trình) là: {reader_name}.\n"
        f"Nhiệm vụ của bạn là chuyển đổi bản tóm tắt sách thành một KỊCH BẢN NÓI ĐƠN THOẠI (SOLO PODCAST SCRIPT) kéo dài khoảng 8-12 phút nghe (tầm 2.500 - 3.500 ký tự).\n\n"
        f"Nguyên tắc biên kịch Podcast:\n"
        f"1. VĂN NÓI TỰ NHIÊN & GIỚI THIỆU TÊN: Sử dụng văn phong gần gũi, xưng hô 'tôi là {reader_name}' và 'các bạn' hoặc 'bạn'. Có nhịp thở, câu cảm thán, câu hỏi tu từ. Ngay phần mở đầu hãy giới thiệu tên mình một cách tự nhiên (ví dụ: 'Chào mừng các bạn đã quay trở lại... Tôi là {reader_name}...'). Ở phần kết thúc cũng nhắc lại tên ({reader_name}) và chào tạm biệt.\n"
        f"2. BÁM SÁT NỘI DUNG SÁCH: Tập trung 100% vào nội dung cốt lõi của cuốn sách '{book_title}' và các bài học trong tài liệu. Tuyệt đối KHÔNG đưa vào các ví dụ lạc đề không liên quan.\n"
        f"3. HOOK MỞ ĐẦU: Bắt đầu bằng một câu hỏi gợi mở, một nghịch lý hoặc câu chuyện gây tò mò thay vì đọc tiêu đề khô khan.\n"
        f"4. ĐIỂM CHẠM THỰC TẾ: Không đọc danh sách gạch đầu dòng; hãy xâu chuỗi các ý tưởng thành một câu chuyện có dòng chảy mạch lạc.\n"
        f"5. KẾT THÚC HÀNH ĐỘNG: Đúc kết 1-2 hành vi cụ thể có thể làm ngay hôm nay kèm lời chào ấm áp, truyền cảm hứng từ {reader_name}.\n"
        f"6. ĐỊNH DẠNG ĐẦU RA: CHỈ XUẤT VĂN BẢN THUẦN ĐỂ ĐỌC (Plain text), không chứa các ký tự định dạng sân khấu như [Nhạc nền], (Host nói:), **in đậm** hay # markdown. (Riêng các tag cảm xúc nhỏ như [cười] hoặc [thở dài] có thể dùng một cách tinh tế nếu hợp ngữ cảnh)."
    )

    prompt = (
        f"Hãy chuyển đổi tài liệu tóm tắt sau của cuốn sách '{book_title}' thành một kịch bản Podcast hoàn chỉnh để máy đọc bằng giọng nói của Host {reader_name}:\n\n"
        f"{text[:14000]}"
    )

    script = llm.complete(system=system_prompt, messages=[{"role": "user", "content": prompt}], max_tokens=4500)

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
    speed: float = 1.1,
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


def create_podcast_for_book(
    input_file: Path | str,
    voice: str | None = None,
    engine: str | None = None,
    speed: float = 1.1,
    ref_audio: str | Path | None = None,
    send_telegram: bool = False,
) -> Path | None:
    """Tạo tập podcast từ file tóm tắt và trả về đường dẫn file MP3 hoàn thành."""
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"❌ Không tìm thấy file: {input_path}", file=sys.stderr)
        return None

    env = load_env(ENV_PATH)
    active_engine = (engine or os.environ.get("TTS_ENGINE") or env.get("TTS_ENGINE") or "vieneu").lower().strip()
    target_voice = resolve_voice_code(voice, engine=active_engine)
    voice_desc = POPULAR_VOICES.get(target_voice, target_voice)
    reader_name = get_reader_name(target_voice)

    engine_label = "VieNeu-TTS v3 Turbo (On-device 48kHz)" if active_engine == "vieneu" else "Vbee AIVoice (Cloud)"
    print(f"🎙️ Engine TTS: {engine_label}")
    print(f"🗣️ Giọng đọc: {voice_desc} (Mã: {target_voice}, Người đọc: {reader_name})")

    stem = input_path.stem
    PODCASTS_DIR.mkdir(parents=True, exist_ok=True)

    content = extract_text_from_file(input_path)
    script = generate_podcast_script(content, book_title=stem, voice_code=target_voice)
    script_file = PODCASTS_DIR / f"{stem}_podcast_script.txt"
    script_file.write_text(script, encoding="utf-8")
    print(f"📝 Đã lưu kịch bản Podcast tại: {script_file}")

    output_mp3 = PODCASTS_DIR / f"{stem}_podcast.mp3"
    success = False

    if active_engine == "vieneu":
        clone_ref = ref_audio or os.environ.get("VIENEU_REF_AUDIO") or env.get("VIENEU_REF_AUDIO")
        success = call_vieneu_tts(
            text=script,
            output_path=output_mp3,
            voice=target_voice,
            ref_audio=clone_ref,
            speed=speed,
        )
        if not success:
            # Fallback sang Vbee nếu có cấu hình
            app_id = os.environ.get("VBEE_APP_ID") or env.get("VBEE_APP_ID")
            token = os.environ.get("VBEE_TOKEN") or env.get("VBEE_TOKEN")
            if app_id and token:
                print("🔄 Thử fallback sang Vbee Cloud API...", file=sys.stderr)
                vbee_voice = resolve_voice_code(voice, engine="vbee")
                success = call_vbee_tts(
                    text=script,
                    output_path=output_mp3,
                    app_id=app_id,
                    token=token,
                    voice_code=vbee_voice,
                    speed=speed,
                )
    else:
        app_id = os.environ.get("VBEE_APP_ID") or env.get("VBEE_APP_ID")
        token = os.environ.get("VBEE_TOKEN") or env.get("VBEE_TOKEN")
        if not app_id or not token:
            print("❌ Thiếu VBEE_APP_ID hoặc VBEE_TOKEN trong .env!", file=sys.stderr)
            return None
        success = call_vbee_tts(
            text=script,
            output_path=output_mp3,
            app_id=app_id,
            token=token,
            voice_code=target_voice,
            speed=speed,
        )

    if success:
        if send_telegram:
            telegram_script = PROJECT_DIR / "scripts" / "send_to_telegram.py"
            if telegram_script.exists():
                caption = (
                    f"🎙️ <b>Podcast Tóm Tắt: {stem}</b>\n"
                    f"🗣️ <b>Người đọc:</b> {reader_name} ({'VieNeu 48kHz' if active_engine == 'vieneu' else 'Vbee AI'} • {speed}x)"
                )
                subprocess.run([sys.executable, str(telegram_script), str(output_mp3), "--caption", caption])
        return output_mp3
    return None


def print_available_voices():
    """In danh sách các giọng đọc hỗ trợ tốt nhất."""
    current_default = resolve_voice_code()
    print("\n🎧 DANH SÁCH GIỌNG ĐỌC KHUYÊN DÙNG CHO AUDIOBOOK / PODCAST:")
    print("=" * 80)
    print("🦜 1. VIENEU-TTS v3 Turbo (MẶC ĐỊNH - On-device 48kHz, phát âm Anh-Việt chuẩn):")
    print("-" * 80)
    for code, desc in VIENEU_POPULAR_VOICES.items():
        is_cur = " [ĐANG CHỌN MẶC ĐỊNH]" if code == current_default else ""
        print(f"• {code:25} -> {desc}{is_cur}")

    print("\n☁️ 2. VBEE AIVoice (Cloud API dự phòng):")
    print("-" * 80)
    for code, desc in VBEE_POPULAR_VOICES.items():
        is_cur = " [ĐANG CHỌN MẶC ĐỊNH]" if code == current_default else ""
        print(f"• {code:35} -> {desc}{is_cur}")
    print("=" * 80)
    print(f"💡 Cấu hình giọng mặc định lâu dài: Thêm vào file .env:")
    print(f"   TTS_ENGINE=\"vieneu\"")
    print(f"   VIENEU_VOICE=\"Minh Quân\"\n")


def main():
    parser = argparse.ArgumentParser(description="Tạo Podcast audio tóm tắt sách bằng VieNeu v3 Turbo hoặc Vbee API")
    parser.add_argument("input_file", nargs="?", help="Đường dẫn file tóm tắt (.epub, .md hoặc .txt)")
    parser.add_argument("--engine", choices=["vieneu", "vbee"], default=None, help="Chọn TTS engine (Mặc định: vieneu)")
    parser.add_argument("--voice", default=None, help="Tên giọng đọc hoặc alias (Mặc định: Minh Quân)")
    parser.add_argument("--ref-audio", default=None, help="File âm thanh mẫu (3-8s) để clone giọng tức thì (chỉ VieNeu)")
    parser.add_argument("--speed", type=float, default=1.1, help="Tốc độ đọc (Mặc định: 1.1)")
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
    )


if __name__ == "__main__":
    main()
