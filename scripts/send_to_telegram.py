#!/usr/bin/env python3
"""Script gửi file tới nhóm/topic Telegram qua Bot API.

Cách dùng:
  python scripts/send_to_telegram.py file1.epub [file2.epub ...]
  python scripts/send_to_telegram.py file.epub --caption "Mô tả sách"
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

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
) -> bool:
    if not file_path.exists():
        print(f"❌ Không tìm thấy file: {file_path}", file=sys.stderr)
        return False

    cmd = [
        "curl",
        "-s",
        "-F", f"chat_id={chat_id}",
        "-F", f"document=@{file_path.resolve()}",
    ]
    if topic_id:
        cmd.extend(["-F", f"message_thread_id={topic_id}"])
    if caption:
        cmd.extend(["-F", f"caption={caption}", "-F", "parse_mode=HTML"])

    cmd.append(f"https://api.telegram.org/bot{bot_token}/sendDocument")

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0 and '"ok":true' in res.stdout:
        print(f"  ✅ Đã gửi thành công: {file_path.name}")
        return True
    else:
        print(f"  ❌ Lỗi khi gửi {file_path.name}: {res.stdout or res.stderr}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Gửi file tới nhóm/topic Telegram")
    parser.add_argument("files", nargs="+", help="Đường dẫn file cần gửi")
    parser.add_argument("--caption", help="Caption đi kèm (HTML hỗ trợ)")
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
        send_document(token, chat_id, p, topic_id=topic_id, caption=args.caption)


if __name__ == "__main__":
    main()
