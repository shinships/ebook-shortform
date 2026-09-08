#!/usr/bin/env python3
"""send_weekly_recommendations.py — Gửi bản tin gợi ý sách mới & high-rating tuần này tới Telegram.

Cách dùng:
  ./scripts/send_weekly_recommendations.py                 # Gửi vào Chat & Topic mặc định trong .env
  ./scripts/send_weekly_recommendations.py --dry-run       # In nội dung ra màn hình, không gửi Telegram
  ./scripts/send_weekly_recommendations.py --force         # Bắt buộc tuyển chọn sách mới (bỏ qua cache)
  ./scripts/send_weekly_recommendations.py --category tech # Lọc theo danh mục (business, psychology, tech, productivity)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))
sys.path.insert(0, str(PROJECT_DIR / "src"))

from ebook_translator.recommender import CATEGORIES, book_recommender

ENV_PATH = PROJECT_DIR / ".env"
TELEGRAM_API_BASE = "https://api.telegram.org"


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


def build_recommendations_keyboard(issue: dict[str, Any]) -> dict[str, Any]:
    """Tạo bàn phím nút bấm tương tác 1-chạm cho bản tin gợi ý sách."""
    books = issue.get("books", [])
    keyboard: list[list[dict[str, str]]] = []

    # Hàng nút hành động cho từng cuốn sách (Tóm tắt nhanh & Lưu Wishlist)
    for i, b in enumerate(books, 1):
        b_id = b.get("id", f"b{i}")
        # Rút ngắn ID nếu quá dài để vừa giới hạn 64 bytes của Telegram callback_data
        short_id = b_id[:35]
        row = [
            {"text": f"⚡ Tóm tắt #{i}", "callback_data": f"rec:brief:{short_id}"},
            {"text": f"📌 Lưu #{i}", "callback_data": f"rec:wish:{short_id}"},
        ]
        keyboard.append(row)

    # Hàng chuyển đổi danh mục chủ đề
    cat_row = [
        {"text": "💼 Kinh doanh", "callback_data": "rec:cat:business"},
        {"text": "🧠 Tâm lý", "callback_data": "rec:cat:psychology"},
        {"text": "🤖 AI & Tech", "callback_data": "rec:cat:tech"},
    ]
    keyboard.append(cat_row)

    # Hàng tiện ích & đổi bộ gợi ý
    utility_row = [
        {"text": "⚡ Năng suất", "callback_data": "rec:cat:productivity"},
        {"text": "🌟 Tổng hợp", "callback_data": "rec:cat:all"},
        {"text": "🔄 Đổi bộ khác", "callback_data": "rec:refresh"},
    ]
    keyboard.append(utility_row)

    return {"inline_keyboard": keyboard}


def send_telegram_message(
    token: str,
    chat_id: int | str,
    text: str,
    thread_id: int | str | None = None,
    reply_markup: dict[str, Any] | None = None,
) -> bool:
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if thread_id and str(thread_id).isdigit() and int(thread_id) > 0:
        payload["message_thread_id"] = int(thread_id)
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        resp = requests.post(url, json=payload, timeout=20)
        data = resp.json()
        if data.get("ok"):
            return True
        print(f"❌ Lỗi gửi Telegram API: {data}", file=sys.stderr)
    except Exception as e:
        print(f"❌ Ngoại lệ gửi tin nhắn Telegram: {e}", file=sys.stderr)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Gửi bản tin gợi ý sách mới & high-rating tuần này tới Telegram.")
    parser.add_argument("--chat-id", help="Telegram Chat ID đích (mặc định đọc từ .env)")
    parser.add_argument("--topic-id", help="Telegram Topic ID đích (mặc định đọc từ .env)")
    parser.add_argument("--category", default="all", choices=list(CATEGORIES.keys()), help="Chuyên mục cần gợi ý")
    parser.add_argument("--force", action="store_true", help="Bắt buộc làm mới tuyển chọn từ AI")
    parser.add_argument("--dry-run", action="store_true", help="In ra terminal, không gửi Telegram")
    args = parser.parse_args()

    env = load_env(ENV_PATH)
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or env.get("TELEGRAM_BOT_TOKEN")
    raw_chat = args.chat_id or os.environ.get("TELEGRAM_CHAT_ID") or env.get("TELEGRAM_CHAT_ID")
    topic_id = args.topic_id or os.environ.get("TELEGRAM_TOPIC_ID") or env.get("TELEGRAM_TOPIC_ID")

    print(f"🔍 Đang tuyển chọn sách tuần này (chủ đề: {CATEGORIES.get(args.category, args.category)})...")
    issue = book_recommender.get_weekly_recommendations(
        category=args.category,
        force_refresh=args.force,
        count=3,
    )

    message_text = book_recommender.format_telegram_digest(issue)
    keyboard = build_recommendations_keyboard(issue)

    if args.dry_run:
        print("\n" + "=" * 60)
        print("📄 PREVIEW NỘI DUNG GỬI (DRY-RUN):")
        print("=" * 60)
        print(message_text)
        print("=" * 60)
        print("🔘 BÀN PHÍM TƯƠNG TÁC:", json.dumps(keyboard, ensure_ascii=False, indent=2))
        return

    if not token or not raw_chat:
        sys.exit("❌ Thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID trong .env")

    chat_id = int(raw_chat) if str(raw_chat).lstrip("-").isdigit() else raw_chat

    print(f"🚀 Đang gửi bản tin tới Chat: {chat_id} (Topic: {topic_id})...")
    ok = send_telegram_message(token, chat_id, message_text, thread_id=topic_id, reply_markup=keyboard)
    if ok:
        print("✅ Đã gửi bản tin Gợi ý Sách Tuần Này thành công!")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
