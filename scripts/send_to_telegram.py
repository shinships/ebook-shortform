#!/usr/bin/env python3
"""Script gửi file tới nhóm/topic Telegram qua Bot API.

Cách dùng:
  python scripts/send_to_telegram.py file1.epub [file2.epub ...]
  python scripts/send_to_telegram.py file.epub --caption "Mô tả sách"
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_env(env_path: Path) -> dict[str, str]:
    env = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def send_document(
    bot_token: str,
    chat_id: str,
    file_path: Path,
    topic_id: str | None = None,
    caption: str | None = None,
    title: str | None = None,
    performer: str | None = None,
) -> bool:
    if not file_path.exists():
        print(f"❌ Không tìm thấy file: {file_path}", file=sys.stderr)
        return False

    is_audio = file_path.suffix.lower() in (".mp3", ".m4a", ".wav")
    endpoint = "sendAudio" if is_audio else "sendDocument"
    url = f"https://api.telegram.org/bot{bot_token}/{endpoint}"

    data = {
        "chat_id": chat_id,
        "parse_mode": "HTML",
    }
    if topic_id and str(topic_id).isdigit():
        data["message_thread_id"] = int(topic_id)
    if caption:
        data["caption"] = caption

    if is_audio:
        clean_title = title or file_path.stem.replace("_audio", "").replace("_podcast", "").replace("-", " ").replace("_", " ").title()
        data["title"] = clean_title
        if performer:
            data["performer"] = performer

    field_name = "audio" if is_audio else "document"
    try:
        with open(file_path, "rb") as f:
            files = {field_name: (file_path.name, f)}
            res = requests.post(url, data=data, files=files, timeout=240)
            res_data = res.json()
            if res_data.get("ok"):
                print(f"  ✅ Đã gửi thành công: {file_path.name}")
                return True
            else:
                print(f"  ❌ Lỗi khi gửi {file_path.name}: {res_data}", file=sys.stderr)
                return False
    except Exception as e:
        print(f"  ❌ Ngoại lệ khi gửi {file_path.name}: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Gửi file tới nhóm/topic Telegram")
    parser.add_argument("files", nargs="+", help="Đường dẫn file cần gửi")
    parser.add_argument("--caption", help="Caption đi kèm (HTML hỗ trợ)")
    parser.add_argument("--title", help="Tiêu đề bài audio (chỉ dùng cho file âm thanh)")
    parser.add_argument("--performer", help="Tên người đọc/nghệ sĩ (chỉ dùng cho file âm thanh)")
    parser.add_argument("--token", help="Telegram Bot Token")
    parser.add_argument("--chat-id", help="Telegram Chat ID")
    parser.add_argument("--topic-id", help="Telegram Topic Thread ID")
    args = parser.parse_args()

    env = load_env(ENV_PATH)
    token = args.token or os.environ.get("TELEGRAM_BOT_TOKEN") or env.get("TELEGRAM_BOT_TOKEN")
    chat_id = args.chat_id or os.environ.get("TELEGRAM_CHAT_ID") or env.get("TELEGRAM_CHAT_ID")
    topic_id = args.topic_id or os.environ.get("TELEGRAM_TOPIC_ID") or env.get("TELEGRAM_TOPIC_ID")

    if not token or not chat_id:
        sys.exit("Thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID (kiểm tra .env hoặc biến môi trường).")

    for f in args.files:
        p = Path(f)
        send_document(
            token,
            chat_id,
            p,
            topic_id=topic_id,
            caption=args.caption,
            title=args.title,
            performer=args.performer,
        )


if __name__ == "__main__":
    main()
