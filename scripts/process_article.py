#!/usr/bin/env python3
"""process_article.py — Tự động cào bài viết từ liên kết web (URL), dịch sang tiếng Việt và tạo Audio MP3 bản dịch.

Cách dùng:
  python scripts/process_article.py "https://paulgraham.com/foundermode.html"
  python scripts/process_article.py "https://paulgraham.com/foundermode.html" --audio
  python scripts/process_article.py "https://paulgraham.com/foundermode.html" --voice "Thái Sơn" --speed 1.15
  python scripts/process_article.py "https://paulgraham.com/foundermode.html" --audio --telegram
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from ebook_translator.core.article import (
    fetch_and_parse_article,
    generate_article_audio,
    translate_article,
)
from ebook_translator.core.llm import LLMClient
from ebook_translator.core.tags import format_tags, generate_topic_tags
from ebook_translator.core.youtube import (
    format_time_str,
    get_youtube_chapters,
    is_youtube_url,
    youtube_to_article,
)
from ebook_translator.core.tts import get_default_engine, get_reader_name, load_env, resolve_voice_code

ENV_PATH = PROJECT_DIR / ".env"
OUTPUT_DIR = PROJECT_DIR / "output" / "articles"


def send_article_to_telegram(
    audio_path: Path | None,
    md_path: Path | None,
    title: str,
    author: str,
    domain: str,
    summary: str,
    reader_name: str,
    speed: float = 1.15,
    tags: list[str] | None = None,
    engine_name: str = "ZeroTTS",
    video_title: str | None = None,
) -> None:
    """Gửi bản dịch bài viết và audio MP3 tới Telegram."""
    env = load_env(ENV_PATH)
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or env.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or env.get("TELEGRAM_CHAT_ID")
    topic_id = os.environ.get("TELEGRAM_TOPIC_ID") or env.get("TELEGRAM_TOPIC_ID")

    if not token or not chat_id:
        print("⚠️ Không tìm thấy cấu hình Telegram trong .env. Bỏ qua gửi Telegram.")
        return

    import requests

    api_url = f"https://api.telegram.org/bot{token}"
    tags_str = format_tags(tags) if tags else format_tags(generate_topic_tags(title, summary, domain, output_type="article"))

    # 1. Gửi file markdown bản dịch nếu có
    if md_path and md_path.exists():
        md_lines = [f"📰 <b>{title}</b>"]
        if video_title:
            md_lines.append(f"🎬 <b>Từ video:</b> <i>{video_title}</i>")
        md_lines.append(f"🔗: {author} • <b>Nguồn:</b> {domain}")
        md_lines.append(f"📝 <b>Tóm tắt:</b> <i>{summary}</i>")
        caption = "\n".join(md_lines)
        if tags_str:
            caption += f"\n\n{tags_str}"
        data = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
        if topic_id:
            data["message_thread_id"] = topic_id
        with open(md_path, "rb") as f:
            files = {"document": (md_path.name, f, "text/markdown")}
            requests.post(f"{api_url}/sendDocument", data=data, files=files, timeout=30)

    # 2. Gửi file Audio MP3 nếu có
    if audio_path and audio_path.exists():
        is_long = audio_path.stat().st_size > 3 * 1024 * 1024
        type_tag = "#podcast" if is_long else "#audio"
        raw_tags = tags if tags else generate_topic_tags(title, summary, domain, output_type="")
        keyword_candidates = [
            t for t in raw_tags
            if t.lower() not in ("#podcast", "#audio", "#short", "#dich", "#article", "#shortform", "#kienthuc")
        ]
        if not keyword_candidates:
            keyword_candidates = [t for t in raw_tags if t.lower() not in ("#podcast", "#audio", "#short", "#dich", "#article")]
        chosen_kw = keyword_candidates[0] if keyword_candidates else "#kienthuc"
        if not chosen_kw.startswith("#"):
            chosen_kw = f"#{chosen_kw}"
        desc_line = f"📝 {type_tag} {chosen_kw}"

        audio_lines = [f"🎙️ <b>{title}</b>"]
        if video_title:
            audio_lines.append(f"🎬 <b>Từ video:</b> <i>{video_title}</i>")
        audio_lines.append(f"🔗: {author} • <b>Nguồn:</b> {domain}")
        audio_lines.append(f"🎧 {reader_name} ({engine_name}, {speed}x)")
        audio_lines.append(desc_line)
        caption = "\n".join(audio_lines)
        data = {
            "chat_id": chat_id,
            "caption": caption,
            "parse_mode": "HTML",
            "title": f"[Dịch] {title}",
            "performer": f"{author} • {reader_name}",
        }
        if topic_id:
            data["message_thread_id"] = topic_id
        with open(audio_path, "rb") as f:
            files = {"audio": (audio_path.name, f, "audio/mpeg")}
            res = requests.post(f"{api_url}/sendAudio", data=data, files=files, timeout=120)
            if res.status_code == 200:
                print(f"✅ Đã gửi Audio bài viết sang Telegram: {audio_path.name}")
            else:
                print(f"❌ Lỗi gửi Audio Telegram: {res.text}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Cào, dịch và tạo Audio bản dịch bài viết từ URL web")
    parser.add_argument("url", help="Đường dẫn bài viết web (URL)")
    parser.add_argument("--no-audio", action="store_true", help="Chỉ dịch văn bản, không sinh Audio")
    parser.add_argument("--engine", choices=["zerotts", "vieneu", "vbee"], default=None, help="TTS Engine (zerotts, vieneu, vbee)")
    parser.add_argument("--voice", default=None, help="Giọng đọc AI (maichi, giahuy, Minh Quân, Thái Sơn...)")
    parser.add_argument("--speed", type=float, default=1.15, help="Tốc độ đọc Audio (Mặc định: 1.15x)")
    parser.add_argument("--model", default=None, help="Mô hình LLM dịch bài (Mặc định: gemini-3.7-flash)")
    parser.add_argument("--telegram", action="store_true", help="Tự động gửi bản dịch và Audio sang Telegram")
    parser.add_argument("--start", default=None, help="Mốc thời gian bắt đầu (ví dụ: 45:48, 01:15:30, 2748)")
    parser.add_argument("--end", default=None, help="Mốc thời gian kết thúc (ví dụ: 53:04, 01:25:00)")
    parser.add_argument("--chapter", default=None, help="Số chương (8), dải chương (9-11), danh sách (2,5) hoặc từ khóa tên chapter")
    parser.add_argument("--list-chapters", action="store_true", help="Liệt kê danh sách các Chapter của video YouTube rồi thoát")
    parser.add_argument("--no-sponsorblock", action="store_true", help="Giữ nguyên đoạn quảng cáo host tự đọc (mặc định: tự động cắt)")
    parser.add_argument("--sponsor-categories", default=None, help="Nhóm đoạn cần cắt, phân cách bằng dấu phẩy (mặc định: sponsor,selfpromo,interaction)")
    args = parser.parse_args()

    url = args.url.strip()
    if not url.startswith(("http://", "https://")):
        sys.exit("❌ URL không hợp lệ! Vui lòng cung cấp link bắt đầu bằng http:// hoặc https://")

    # Xử lý lệnh liệt kê Chapter
    if args.list_chapters:
        if not is_youtube_url(url):
            sys.exit("❌ Lệnh --list-chapters chỉ áp dụng cho liên kết YouTube.")
        print(f"\n📺 Đang tải danh sách Chapter từ: {url}")
        chapters = get_youtube_chapters(url)
        if not chapters:
            print("ℹ️ Video này không có danh sách Chapter được định nghĩa sẵn.")
            sys.exit(0)
        print(f"\n📑 Danh sách {len(chapters)} Chapters:")
        print("─" * 65)
        for i, ch in enumerate(chapters, 1):
            st = format_time_str(ch.get("start_time", 0))
            et = format_time_str(ch.get("end_time", 0))
            print(f"  [{i:02d}] {st} - {et} : {ch.get('title')}")
        print("─" * 65)
        print("💡 Gợi ý: --chapter 8 (một chương) · --chapter 9-11 (dải chương) · --chapter 2,5 (chương rời rạc).")
        sys.exit(0)

    # Phân luồng: YouTube → trích xuất transcript, Website → cào nội dung
    if is_youtube_url(url):
        print(f"\n📺 [1/3] Đang trích xuất phụ đề từ video YouTube: {url}")
        try:
            sponsor_cats = None
            if args.sponsor_categories:
                sponsor_cats = [c.strip() for c in args.sponsor_categories.split(",") if c.strip()]
            article = youtube_to_article(
                url,
                start_time=args.start,
                end_time=args.end,
                chapter=args.chapter,
                skip_sponsors=not args.no_sponsorblock,
                sponsor_categories=sponsor_cats,
            )
        except Exception as e:
            sys.exit(f"❌ Lỗi khi trích xuất phụ đề YouTube: {e}")

        if article.sponsor_note:
            print(f"   🚫 Đã cắt {article.sponsor_note}")
    else:
        print(f"\n🌐 [1/3] Đang cào và phân tích bài viết từ: {url}")
        try:
            article = fetch_and_parse_article(url)
        except Exception as e:
            sys.exit(f"❌ Lỗi khi tải bài viết: {e}")

    print(f"   ✅ Tiêu đề: {article.title}")
    print(f"   👤 Tác giả / Kênh: {article.author}")
    print(f"   🌐 Nguồn: {article.domain} ({article.word_count} từ)")
    if article.word_count < 50:
        print("⚠️ Cảnh báo: Nội dung rất ít. Video có thể không có phụ đề hoặc bài viết bị chặn.")

    print(f"\n🧠 [2/3] Đang dịch bài viết sang tiếng Việt...")
    llm = LLMClient(model=args.model)
    translated = translate_article(article, llm=llm)
    print(f"   ✅ Đã dịch xong: '{translated.title_vi}' ({translated.char_count} ký tự)")
    print(f"   💡 Tóm tắt cốt lõi: {translated.summary_vi}")
    if translated.md_path:
        print(f"   📝 File bản dịch: {translated.md_path}")

    audio_path = None
    env = load_env(ENV_PATH)
    active_engine = (args.engine or os.environ.get("TTS_ENGINE") or env.get("TTS_ENGINE") or "zerotts").lower().strip()
    target_voice = resolve_voice_code(args.voice, engine=active_engine)
    reader_name = get_reader_name(target_voice, engine=active_engine)
    engine_label = "ZeroTTS" if active_engine == "zerotts" else ("Vieneu" if active_engine == "vieneu" else "Vbee")

    if not args.no_audio:
        print(f"\n🎙️ [3/3] Đang tạo Audio bản dịch bằng {engine_label} 48kHz (Host: {reader_name}, {args.speed}x)...")
        audio_path = generate_article_audio(
            translated=translated,
            voice=target_voice,
            speed=args.speed,
            engine=active_engine,
        )
        if audio_path and audio_path.exists():
            pod_dir = PROJECT_DIR / "output" / "podcasts"
            pod_dir.mkdir(parents=True, exist_ok=True)
            pod_dest = pod_dir / audio_path.name
            try:
                import shutil
                shutil.copy2(audio_path, pod_dest)
                from ebook_translator.core.podcast_rss import update_podcast_feed
                update_podcast_feed()
                print(f"📡 Đã đồng bộ vào Apple Podcasts: {pod_dest.name}")
            except Exception as e:
                print(f"⚠️ Không thể đồng bộ podcast feed: {e}")

    if args.telegram:
        print("\n📢 Đang gửi ấn phẩm sang Telegram...")
        send_article_to_telegram(
            audio_path=audio_path,
            md_path=translated.md_path,
            title=translated.title_vi,
            author=article.author,
            domain=article.domain,
            summary=translated.summary_vi,
            reader_name=reader_name,
            speed=args.speed,
            tags=translated.tags,
            engine_name=engine_label,
            video_title=article.video_title,
        )

    print("\n🎉 HOÀN TẤT TÁC VỤ!")
    if translated.md_path:
        print(f"• Bản dịch Markdown: {translated.md_path}")
    if audio_path:
        print(f"• Audio MP3: {audio_path}")


if __name__ == "__main__":
    main()
