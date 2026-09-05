#!/usr/bin/env python3
"""generate_podcast.py — Tự động tạo audio tóm tắt sách phong cách Podcast bằng Vbee TTS API.

Quy trình:
1. Nhận file tóm tắt (Markdown / Text / Ebook).
2. Dùng Gemini biên soạn kịch bản Podcast tự nhiên, đàm thoại lôi cuốn.
3. Gọi Vbee TTS API chuyển đổi thành file âm thanh giọng đọc tiếng Việt chân thực (MP3).
4. Lưu trữ vào output/podcasts/.
5. (Tùy chọn) Gửi file Podcast MP3 qua Telegram.

Cách dùng:
  python scripts/generate_podcast.py Remote_Office_Not_Required_short.md
  python scripts/generate_podcast.py Remote_Office_Not_Required_short.md --voice hn_female_ngochuyen_full_48k-fhg
  python scripts/generate_podcast.py Remote_Office_Not_Required_short.md --telegram
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from ebook_translator.core.llm import LLMClient

ENV_PATH = PROJECT_DIR / ".env"
PODCASTS_DIR = PROJECT_DIR / "output" / "podcasts"

# Endpoint Vbee AIVoice API
VBEE_TTS_URL = "https://vbee.vn/api/v1/tts"

# Danh sách giọng đọc podcast tiêu biểu
POPULAR_VOICES = {
    "hn_female_maiphuong_vdts_48k-fhg": "HN - Mai Phương (Nữ Bắc, tự nhiên, nhẹ nhàng)",
    "hn_male_manhdung_news_48k-fhg": "HN - Mạnh Dũng (Nam Bắc, trang trọng, thời sự)",
    "hn_female_ngochuyen_full_48k-fhg": "HN - Ngọc Huyền (Nữ Bắc, truyền cảm, podcast)",
    "hn_male_thanhlong_talk_48k-fhg": "HN - Thanh Long (Nam Bắc, talkshow/đàm thoại)",
    "hn_male_phuthang_stor80dt_48k-fhg": "HN - Anh Khôi (Nam Bắc, trầm ấm, đọc truyện)",
    "hn_male_minhquan_yt-stable": "HN - Minh Quân (Nam Bắc, trẻ trung, review)",
    "sg_female_lantrinh_vdts_48k-fhg": "SG - Lan Trinh (Nữ Nam, dịu dàng, tự nhiên)",
    "sg_female_thaotrinh_full_48k-fhg": "SG - Thảo Trinh (Nữ Nam, truyền cảm)",
    "sg_male_trungkien_vdts_48k-fhg": "SG - Trung Kiên (Nam Nam, truyền cảm, ấm)",
    "sg_male_minhhoang_full_48k-fhg": "SG - Minh Hoàng (Nam Nam, hiện đại)",
    "hue_female_huonggiang_full_48k-fhg": "Huế - Hương Giang (Nữ Huế, ngọt ngào)",
    "hue_male_duyphuong_full_48k-fhg": "Huế - Duy Phương (Nam Huế, sâu lắng)",
}

VOICE_ALIASES = {
    "maiphuong": "hn_female_maiphuong_vdts_48k-fhg",
    "mai phương": "hn_female_maiphuong_vdts_48k-fhg",
    "manhdung": "hn_male_manhdung_news_48k-fhg",
    "mạnh dũng": "hn_male_manhdung_news_48k-fhg",
    "ngochuyen": "hn_female_ngochuyen_full_48k-fhg",
    "ngọc huyền": "hn_female_ngochuyen_full_48k-fhg",
    "thanhlong": "hn_male_thanhlong_talk_48k-fhg",
    "thanh long": "hn_male_thanhlong_talk_48k-fhg",
    "anhkhoi": "hn_male_phuthang_stor80dt_48k-fhg",
    "anh khôi": "hn_male_phuthang_stor80dt_48k-fhg",
    "minhquan": "hn_male_minhquan_yt-stable",
    "minh quân": "hn_male_minhquan_yt-stable",
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

DEFAULT_FALLBACK_VOICE = "hn_female_maiphuong_vdts_48k-fhg"

VOICE_TO_READER_NAME = {
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


def get_reader_name(voice_code: str | None = None) -> str:
    """Lấy tên người đọc tương ứng với mã giọng."""
    target_code = resolve_voice_code(voice_code)
    return VOICE_TO_READER_NAME.get(target_code, "Mai Phương")


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


def resolve_voice_code(voice_input: str | None = None) -> str:
    """Xác định mã giọng đọc Vbee chuẩn từ mã trực tiếp, alias tên gọi, hoặc cấu hình .env."""
    if not voice_input:
        env = load_env(ENV_PATH)
        voice_input = os.environ.get("VBEE_VOICE") or env.get("VBEE_VOICE") or DEFAULT_FALLBACK_VOICE

    raw = str(voice_input).strip()
    if raw in POPULAR_VOICES:
        return raw

    norm = raw.lower().replace(" ", "").replace("_", "").replace("-", "")
    for alias, code in VOICE_ALIASES.items():
        alias_norm = alias.replace(" ", "").replace("_", "").replace("-", "")
        if norm == alias_norm or alias_norm in norm:
            return code

    return raw


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
        f"2. BÁM SÁT NỘI DUNG SÁCH: Tập trung 100% vào nội dung cốt lõi của cuốn sách '{book_title}' và các bài học trong tài liệu. Tuyệt đối KHÔNG đưa vào các ví dụ lạc đề không liên quan (chẳng hạn như AI đặt lịch, CSKH phòng khám...).\n"
        f"3. HOOK MỞ ĐẦU: Bắt đầu bằng một câu hỏi gợi mở, một nghịch lý hoặc câu chuyện gây tò mò thay vì đọc tiêu đề khô khan.\n"
        f"4. ĐIỂM CHẠM THỰC TẾ: Không đọc danh sách gạch đầu dòng; hãy xâu chuỗi các ý tưởng thành một câu chuyện có dòng chảy mạch lạc.\n"
        f"5. KẾT THÚC HÀNH ĐỘNG: Đúc kết 1-2 hành vi cụ thể có thể làm ngay hôm nay kèm lời chào ấm áp, truyền cảm hứng từ {reader_name}.\n"
        f"6. ĐỊNH DẠNG ĐẦU RA: CHỈ XUẤT VĂN BẢN THUẦN ĐỂ ĐỌC (Plain text), không chứa các ký tự định dạng sân khấu như [Nhạc nền], [Cười], (Host nói:), **in đậm** hay # markdown."
    )

    prompt = (
        f"Hãy chuyển đổi tài liệu tóm tắt sau của cuốn sách '{book_title}' thành một kịch bản Podcast hoàn chỉnh để máy đọc bằng giọng nói của Host {reader_name}:\n\n"
        f"{text[:12000]}"
    )

    script = llm.complete(system=system_prompt, messages=[{"role": "user", "content": prompt}], max_tokens=4000)

    # Loại bỏ các tag sân khấu nếu có
    script = re.sub(r"\[.*?\]", "", script)
    script = re.sub(r"\(.*?\)", "", script)
    script = script.replace("**", "").replace("#", "").strip()
    return script


def call_vbee_tts(
    text: str,
    output_path: Path,
    app_id: str,
    token: str,
    voice_code: str = DEFAULT_VOICE,
    speed: float = 1.0,
) -> bool:
    """Gọi Vbee TTS API để sinh file âm thanh MP3."""
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

    # Polling chờ hoàn tất
    status_url = f"{VBEE_TTS_URL}/{request_id}"
    start_time = time.time()

    while time.time() - start_time < 360:  # Chờ tối đa 6 phút
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
            print(f"✅ Render thành công! Đang tải file MP3...")
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
    speed: float = 1.0,
    send_telegram: bool = False,
) -> Path | None:
    """Tạo tập podcast từ file tóm tắt và trả về đường dẫn file MP3 hoàn thành."""
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"❌ Không tìm thấy file: {input_path}", file=sys.stderr)
        return None

    env = load_env(ENV_PATH)
    app_id = os.environ.get("VBEE_APP_ID") or env.get("VBEE_APP_ID")
    token = os.environ.get("VBEE_TOKEN") or env.get("VBEE_TOKEN")
    if not app_id or not token:
        print("❌ Thiếu VBEE_APP_ID hoặc VBEE_TOKEN trong .env!", file=sys.stderr)
        return None

    target_voice = resolve_voice_code(voice)
    voice_desc = POPULAR_VOICES.get(target_voice, target_voice)
    reader_name = get_reader_name(target_voice)
    print(f"🎙️ Giọng đọc được chọn: {voice_desc} (Mã: {target_voice}, Người đọc: {reader_name})")

    stem = input_path.stem
    PODCASTS_DIR.mkdir(parents=True, exist_ok=True)

    content = extract_text_from_file(input_path)
    script = generate_podcast_script(content, book_title=stem, voice_code=target_voice)
    script_file = PODCASTS_DIR / f"{stem}_podcast_script.txt"
    script_file.write_text(script, encoding="utf-8")
    print(f"📝 Đã lưu kịch bản Podcast tại: {script_file}")

    output_mp3 = PODCASTS_DIR / f"{stem}_podcast.mp3"
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
                    f"🗣️ <b>Người đọc:</b> {reader_name} (AI Vbee - 128kbps)"
                )
                subprocess.run([sys.executable, str(telegram_script), str(output_mp3), "--caption", caption])
        return output_mp3
    return None


def print_available_voices():
    """In danh sách các giọng đọc Vbee hỗ trợ tốt nhất."""
    current_default = resolve_voice_code()
    print("\n🎧 DANH SÁCH GIỌNG ĐỌC VBEE KHUYÊN DÙNG CHO PODCAST:")
    print("=" * 75)
    for code, desc in POPULAR_VOICES.items():
        is_cur = " [ĐANG CHỌN MẶC ĐỊNH]" if code == current_default else ""
        print(f"• {code:35} -> {desc}{is_cur}")
    print("=" * 75)
    print(f"💡 Cấu hình giọng mặc định lâu dài: Thêm vào file .env:")
    print(f"   VBEE_VOICE=\"hn_female_maiphuong_vdts_48k-fhg\"\n")


def main():
    parser = argparse.ArgumentParser(description="Tạo Podcast audio tóm tắt sách bằng Vbee TTS API")
    parser.add_argument("input_file", nargs="?", help="Đường dẫn file tóm tắt (.epub, .md hoặc .txt)")
    parser.add_argument("--voice", default=DEFAULT_VOICE, help=f"Mã giọng đọc hoặc alias (Mặc định: {DEFAULT_VOICE})")
    parser.add_argument("--speed", type=float, default=1.0, help="Tốc độ đọc (Mặc định: 1.0)")
    parser.add_argument("--telegram", action="store_true", help="Tự động gửi file Podcast sang Telegram khi hoàn thành")
    parser.add_argument("--script-only", action="store_true", help="Chỉ tạo kịch bản Podcast văn bản, không gọi TTS")
    parser.add_argument("--use-existing-script", help="Dùng trực tiếp file script có sẵn thay vì sinh mới bằng LLM")
    parser.add_argument("--list-voices", action="store_true", help="Hiển thị danh sách giọng đọc Vbee có sẵn")
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

    target_voice = resolve_voice_code(args.voice)
    reader_name = get_reader_name(target_voice)
    voice_desc = POPULAR_VOICES.get(target_voice, target_voice)
    stem = input_path.stem
    PODCASTS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Lấy hoặc sinh kịch bản Podcast
    if args.use_existing_script and Path(args.use_existing_script).exists():
        script = Path(args.use_existing_script).read_text(encoding="utf-8")
        print(f"📄 Sử dụng kịch bản có sẵn từ: {args.use_existing_script}")
    else:
        content = extract_text_from_file(input_path)
        script = generate_podcast_script(content, book_title=stem, voice_code=target_voice)
        script_file = PODCASTS_DIR / f"{stem}_podcast_script.txt"
        script_file.write_text(script, encoding="utf-8")
        print(f"📝 Đã lưu kịch bản Podcast tại: {script_file}")

    if args.script_only:
        print("✅ Hoàn tất tạo kịch bản Podcast!")
        return

    # 2. Gọi Vbee TTS API
    env = load_env(ENV_PATH)
    app_id = os.environ.get("VBEE_APP_ID") or env.get("VBEE_APP_ID")
    token = os.environ.get("VBEE_TOKEN") or env.get("VBEE_TOKEN")
    if not app_id or not token:
        sys.exit("❌ Thiếu VBEE_APP_ID hoặc VBEE_TOKEN trong .env!")

    output_mp3 = PODCASTS_DIR / f"{stem}_podcast.mp3"
    success = call_vbee_tts(
        text=script,
        output_path=output_mp3,
        app_id=app_id,
        token=token,
        voice_code=target_voice,
        speed=args.speed,
    )

    # 3. Gửi sang Telegram nếu được yêu cầu
    if success and args.telegram:
        print("📤 Đang gửi Podcast MP3 tới Telegram Topic...")
        telegram_script = PROJECT_DIR / "scripts" / "send_to_telegram.py"
        if telegram_script.exists():
            caption = (
                f"🎙️ <b>Podcast Tóm Tắt: {stem}</b>\n"
                f"🗣️ <b>Người đọc:</b> {reader_name} (AI Vbee - 128kbps)"
            )
            subprocess.run([sys.executable, str(telegram_script), str(output_mp3), "--caption", caption])


if __name__ == "__main__":
    main()
