#!/usr/bin/env python3
"""telegram_inbound_bot.py — Trợ lý AI Chuyên Biệt Dịch Thuật & Tóm Tắt Sách (Telegram Daemon).

Nhiệm vụ cốt lõi:
1. DỊCH SÁCH TOÀN VĂN (Full Translation): Dịch trung thực toàn bộ sách tiếng Anh
   sang tiếng Việt với hệ thống thuật ngữ chuyên ngành (Glossary), chuẩn EPUB3 (.vi.epub).
2. TÓM TẮT SÁCH CHUYÊN SÂU (Shortform Method): Phân tích luận đề cốt lõi, 3 trụ cột tư duy,
   hệ thống Shortform Notes (liên kết chéo, phản biện, bối cảnh đương đại) và bảng Heuristics (_short.epub).
3. MENU TƯƠNG TÁC 1-CHẠM (Inline Keyboard): Khi nhận file .epub/.pdf, bot gửi menu nút bấm
   để người dùng chọn ngay tác vụ mong muốn mà không cần gõ lệnh.
4. INSTANT 1-PAGE BRIEF: Trích xuất và gửi bản tóm tắt điều hành 1 trang trực tiếp vào chat
   để đọc lướt trong 2 phút trên điện thoại.
5. THƯ VIỆN & TRUY VẤN: Lệnh /library, /quick, /glossary, /ask để tra cứu và khai thác tri thức.
6. THEO DÕI OUTPUT: Tự động phát hiện và đồng bộ file dịch/tóm tắt mới từ máy tính sang nhóm Telegram.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import requests

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))
sys.path.insert(0, str(PROJECT_DIR / "src"))

ENV_PATH = PROJECT_DIR / ".env"
INBOX_DIR = PROJECT_DIR / "inbox"
OUTPUT_DIR = PROJECT_DIR / "output"
ORIGINALS_DIR = OUTPUT_DIR / "originals"
PROCESSING_DIR = PROJECT_DIR / "processing"
LOGS_DIR = PROJECT_DIR / "logs"
COVERS_DIR = PROJECT_DIR / "covers"

VENV_BIN = PROJECT_DIR / ".venv-mac" / "bin"
VENV_SUMMARIZE = VENV_BIN / "ebook-summarize"
VENV_TRANSLATE = VENV_BIN / "ebook-translate"
VENV_PYTHON = VENV_BIN / "python"

SETTINGS_PATH = LOGS_DIR / ".bot_settings.json"
SENT_FILES_PATH = LOGS_DIR / ".sent_files.json"
PENDING_FILES_PATH = LOGS_DIR / ".pending_files.json"

TELEGRAM_API_BASE = "https://api.telegram.org"


def load_env(path: Path) -> dict[str, str]:
    """Tải biến môi trường từ file .env."""
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def clean_book_filename(filename: str) -> str:
    """
    Chuẩn hóa tên file sách sau khi nhận từ Telegram hoặc nguồn tải:
    Loại bỏ các hậu tố rác của thư viện tải sách (Z-Library, 1lib, libgen, v.v.)
    Ví dụ:
      'Power_and_Progress_Our_Thousand_Year_z_library_sk,_1lib_sk,.pdf'
      -> 'Power_and_Progress_Our_Thousand_Year.pdf'
    """
    if not filename:
        return "book.epub"

    path = Path(filename)
    stem = path.stem
    ext = path.suffix.lower() if path.suffix else ".epub"

    # Cắt bỏ từ cụm z_library, 1lib, z-lib, zlib, libgen và toàn bộ phần sau nó
    pattern = r"([_,\s\.\-]+|\b)[\(\[]?(?:z[-_]?library|1lib|z-lib|zlib|libgen)[\s\S]*$"
    cleaned_stem = re.sub(pattern, "", stem, flags=re.IGNORECASE)

    if not cleaned_stem.strip():
        cleaned_stem = stem

    # Loại bỏ các ký tự phân cách/ngoặc thừa ở cuối chuỗi
    cleaned_stem = re.sub(r"[\s_,\.\-\(\)\[\]]+$", "", cleaned_stem).strip()

    clean_name = cleaned_stem if cleaned_stem else "book"
    return f"{clean_name}{ext}"


class TelegramBookBot:
    def __init__(self):
        self.env = load_env(ENV_PATH)
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN") or self.env.get("TELEGRAM_BOT_TOKEN")
        if not self.token:
            raise ValueError("Không tìm thấy TELEGRAM_BOT_TOKEN trong .env hoặc biến môi trường.")

        # Cấu hình Chat ID và Topic ID đồng bộ (Nhóm chính)
        raw_chat = os.environ.get("TELEGRAM_CHAT_ID") or self.env.get("TELEGRAM_CHAT_ID", "-1003879100454")
        self.default_chat_id = int(raw_chat) if raw_chat.lstrip("-").isdigit() else -1003879100454
        self.default_topic_id = self.env.get("TELEGRAM_TOPIC_ID", "365")

        # Whitelist người dùng bảo mật
        allowed_str = self.env.get("TELEGRAM_ALLOWED_USERS", "1563046373")
        self.allowed_users = {
            int(u.strip()) for u in allowed_str.split(",") if u.strip().isdigit()
        }

        self.api_url = f"{TELEGRAM_API_BASE}/bot{self.token}"
        self.file_api_url = f"{TELEGRAM_API_BASE}/file/bot{self.token}"

        self.job_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.running = True
        self.offset = 0

        # Cài đặt cấu hình bot (lưu file json để duy trì sau khởi động lại)
        self.settings: dict[str, Any] = self._load_settings()

        # Bộ nhớ tạm lưu file đang chờ người dùng bấm nút tương tác (lưu file để bảo toàn khi bot restart)
        self.pending_files: dict[str, dict[str, Any]] = self._load_pending_files()

        # Quản lý theo dõi file output được tạo từ máy tính
        self.bot_processed_files: dict[str, float] = {}
        self.sent_files: dict[str, float] = self._load_sent_files()

        # Đảm bảo các thư mục cần thiết tồn tại
        for d in (INBOX_DIR, OUTPUT_DIR, ORIGINALS_DIR, PROCESSING_DIR, LOGS_DIR, COVERS_DIR):
            d.mkdir(parents=True, exist_ok=True)

    # ── 1. Quản lý cài đặt & Cấu hình ──
    def _load_settings(self) -> dict[str, Any]:
        default = {
            "default_mode": "ask",  # "ask", "summarize", "translate", "both"
            "model": "gemini-3.7-flash",  # "gemini-3.7-flash", "gemini-2.5-pro"
        }
        if SETTINGS_PATH.exists():
            try:
                data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    default.update(data)
            except Exception:
                pass
        return default

    def _save_settings(self) -> None:
        try:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_PATH.write_text(json.dumps(self.settings, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[Settings] Lỗi lưu settings: {e}", file=sys.stderr)

    def _load_pending_files(self) -> dict[str, dict[str, Any]]:
        if PENDING_FILES_PATH.exists():
            try:
                data = json.loads(PENDING_FILES_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    now = time.time()
                    # Giữ các yêu cầu trong vòng 48 giờ
                    return {k: v for k, v in data.items() if now - v.get("timestamp", 0) < 172800}
            except Exception:
                pass
        return {}

    def _save_pending_files(self) -> None:
        try:
            PENDING_FILES_PATH.parent.mkdir(parents=True, exist_ok=True)
            PENDING_FILES_PATH.write_text(json.dumps(self.pending_files, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[Pending] Lỗi lưu pending_files: {e}", file=sys.stderr)

    def _load_sent_files(self) -> dict[str, float]:
        if SENT_FILES_PATH.exists():
            try:
                data = json.loads(SENT_FILES_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass

        initial_map: dict[str, float] = {}
        if OUTPUT_DIR.exists():
            for p in OUTPUT_DIR.glob("*.epub"):
                if not p.name.startswith("."):
                    initial_map[p.name] = p.stat().st_mtime
        return initial_map

    def _save_sent_files(self, data: dict[str, float]) -> None:
        try:
            SENT_FILES_PATH.parent.mkdir(parents=True, exist_ok=True)
            SENT_FILES_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[Watcher] Lỗi lưu sent_files: {e}", file=sys.stderr)

    # ── 2. Các hàm giao tiếp Telegram API ──
    def is_authorized(self, from_user: dict[str, Any], chat: dict[str, Any]) -> bool:
        user_id = from_user.get("id")
        chat_id = chat.get("id")
        if user_id in self.allowed_users:
            return True
        if self.default_chat_id and chat_id == self.default_chat_id:
            return True
        return False

    def send_message(
        self,
        chat_id: int | str,
        text: str,
        reply_to_message_id: int | None = None,
        thread_id: int | str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        if thread_id:
            payload["message_thread_id"] = thread_id
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            r = requests.post(f"{self.api_url}/sendMessage", json=payload, timeout=20)
            return r.json()
        except Exception as e:
            print(f"[Telegram] Lỗi sendMessage: {e}", file=sys.stderr)
            return None

    def edit_message_text(
        self,
        chat_id: int | str,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        try:
            r = requests.post(f"{self.api_url}/editMessageText", json=payload, timeout=20)
            res = r.json()
            if not res.get("ok"):
                print(f"[Telegram] editMessageText thất bại: {res}", file=sys.stderr)
            return res
        except Exception as e:
            print(f"[Telegram] Lỗi editMessageText: {e}", file=sys.stderr)
            return None

    def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> bool:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
            payload["show_alert"] = show_alert
        try:
            r = requests.post(f"{self.api_url}/answerCallbackQuery", json=payload, timeout=10)
            res = r.json()
            if not res.get("ok"):
                print(f"[Telegram] answerCallbackQuery thất bại: {res}", file=sys.stderr)
            return bool(res.get("ok"))
        except Exception as e:
            print(f"[Telegram] Lỗi answerCallbackQuery: {e}", file=sys.stderr)
            return False

    def send_document(
        self,
        chat_id: int | str,
        file_path: Path,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
        thread_id: int | str | None = None,
    ) -> bool:
        if not file_path.exists():
            return False

        data: dict[str, Any] = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
            data["parse_mode"] = "HTML"
        if reply_to_message_id:
            data["reply_to_message_id"] = reply_to_message_id
        if thread_id:
            data["message_thread_id"] = thread_id

        try:
            with open(file_path, "rb") as f:
                files = {"document": (file_path.name, f)}
                r = requests.post(
                    f"{self.api_url}/sendDocument",
                    data=data,
                    files=files,
                    timeout=180,
                )
            res = r.json()
            return bool(res.get("ok"))
        except Exception as e:
            print(f"[Telegram] Lỗi sendDocument: {e}", file=sys.stderr)
            return False

    def download_file(self, file_id: str, dest_path: Path) -> bool:
        try:
            r = requests.get(f"{self.api_url}/getFile", params={"file_id": file_id}, timeout=25)
            res = r.json()
            if not res.get("ok"):
                print(f"[Telegram] getFile thất bại: {res}", file=sys.stderr)
                return False

            rel_path = res["result"]["file_path"]
            download_url = f"{self.file_api_url}/{rel_path}"

            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with requests.get(download_url, stream=True, timeout=240) as stream_res:
                stream_res.raise_for_status()
                with open(dest_path, "wb") as f:
                    for chunk in stream_res.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
            return True
        except Exception as e:
            print(f"[Telegram] Lỗi download file: {e}", file=sys.stderr)
            return False

    # ── 3. Xử lý nhận file & Tạo nút tương tác 1-chạm ──
    def handle_incoming_document(self, message: dict[str, Any]) -> None:
        chat = message.get("chat", {})
        from_user = message.get("from", {})
        chat_id = chat.get("id")
        msg_id = message.get("message_id")
        thread_id = message.get("message_thread_id")

        if not self.is_authorized(from_user, chat):
            self.send_message(
                chat_id,
                "🔒 Rất tiếc, bạn chưa có quyền sử dụng bot này. Vui lòng liên hệ quản trị viên.",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
            return

        doc = message.get("document", {})
        raw_file_name = doc.get("file_name", "unknown.epub")
        file_name = clean_book_filename(raw_file_name)
        if raw_file_name != file_name:
            print(f"[Telegram] Chuẩn hóa tên file: '{raw_file_name}' -> '{file_name}'")
        file_id = doc.get("file_id")
        file_size = doc.get("file_size", 0)
        ext = Path(file_name).suffix.lower()

        if ext not in (".epub", ".pdf"):
            self.send_message(
                chat_id,
                f"⚠️ Định dạng <code>{ext}</code> chưa được hỗ trợ. Vui lòng gửi file <b>.epub</b> hoặc <b>.pdf</b>.",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
            return

        if file_size > 20 * 1024 * 1024:
            size_mb = file_size / (1024 * 1024)
            self.send_message(
                chat_id,
                f"❌ File <b>{file_name}</b> quá lớn ({size_mb:.1f} MB).\n"
                f"Telegram Bot API giới hạn tải file dưới 20 MB. "
                f"Với sách lớn hơn, vui lòng copy trực tiếp vào thư mục <code>inbox/</code> trên máy Mac.",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
            return

        first_name = from_user.get("first_name", "")
        last_name = from_user.get("last_name", "")
        sender_name = f"{first_name} {last_name}".strip() or from_user.get("username") or "Bạn đọc"

        size_str = (
            f"{file_size / (1024 * 1024):.1f} MB"
            if file_size > 1024 * 1024
            else f"{file_size / 1024:.0f} KB"
        )

        caption_lower = (message.get("caption") or "").lower().strip()

        # Kiểm tra caption người dùng nhập kèm
        chosen_action: str | None = None
        if any(w in caption_lower for w in ["dịch", "dich", "translate", "toàn văn", "full"]):
            chosen_action = "translate"
        elif any(w in caption_lower for w in ["tóm tắt", "tom tat", "shortform", "summary", "brief"]):
            chosen_action = "summarize"
        elif any(w in caption_lower for w in ["cả hai", "ca hai", "both", "all"]):
            chosen_action = "both"
        elif any(w in caption_lower for w in ["thử", "thu", "preview", "sample"]):
            chosen_action = "preview"

        # Nếu không có caption, kiểm tra chế độ mặc định
        if not chosen_action:
            default_mode = self.settings.get("default_mode", "ask")
            if default_mode != "ask":
                chosen_action = default_mode

        # Nếu đã xác định được action (qua caption hoặc default_mode cố định):
        if chosen_action:
            action_names = {
                "summarize": "⚡ Tóm tắt Shortform",
                "translate": "📖 Dịch toàn bộ sách",
                "both": "🚀 Cả Dịch & Tóm tắt",
                "preview": "👁️ Đọc thử 1 chương dịch",
            }
            desc = action_names.get(chosen_action, chosen_action)
            ack_msg = (
                f"📥 <b>Đã nhận sách:</b> <code>{file_name}</code> ({size_str})\n"
                f"🎯 <b>Tác vụ tự động:</b> {desc}\n"
                f"⏳ Đang tải file về máy và đưa vào hàng đợi xử lý..."
            )
            self.send_message(chat_id, ack_msg, reply_to_message_id=msg_id, thread_id=thread_id)

            self.job_queue.put({
                "type": f"{chosen_action}_book" if chosen_action != "both" else "both",
                "action": chosen_action,
                "file_id": file_id,
                "file_name": file_name,
                "chat_id": chat_id,
                "thread_id": thread_id,
                "msg_id": msg_id,
                "sender_name": sender_name,
            })
            return

        file_token = uuid.uuid4().hex[:8]
        self.pending_files[file_token] = {
            "file_id": file_id,
            "file_name": file_name,
            "file_size": file_size,
            "chat_id": chat_id,
            "thread_id": thread_id,
            "msg_id": msg_id,
            "sender_name": sender_name,
            "timestamp": time.time(),
        }
        self._save_pending_files()

        inline_keyboard = {
            "inline_keyboard": [
                [
                    {"text": "⚡ Tóm tắt Shortform (3–5p)", "callback_data": f"sum:{file_token}"},
                    {"text": "📖 Dịch toàn bộ (~20p)", "callback_data": f"trans:{file_token}"},
                ],
                [
                    {"text": "🚀 Cả Dịch & Tóm tắt", "callback_data": f"both:{file_token}"},
                    {"text": "👁️ Đọc thử 1 chương", "callback_data": f"prev:{file_token}"},
                ],
            ]
        }

        prompt_text = (
            f"📥 <b>Đã nhận sách:</b> <code>{file_name}</code> ({size_str})\n"
            f"👤 <b>Người gửi:</b> {sender_name}\n\n"
            f"🎯 <b>Vui lòng chọn tác vụ xử lý bạn muốn:</b>\n"
            f"• <b>Tóm tắt Shortform:</b> Trích xuất luận đề, 3 trụ cột & Shortform Notes.\n"
            f"• <b>Dịch toàn bộ sách:</b> Dịch toàn văn giữ nguyên hình ảnh, thuật ngữ glossary.\n"
            f"• <b>Đọc thử 1 chương:</b> Dịch mẫu chương đầu để kiểm tra văn phong."
        )
        self.send_message(
            chat_id,
            prompt_text,
            reply_to_message_id=msg_id,
            thread_id=thread_id,
            reply_markup=inline_keyboard,
        )

    # ── 4. Xử lý Callback Query khi người dùng bấm nút ──
    def handle_callback_query(self, query: dict[str, Any]) -> None:
        query_id = query.get("id")
        from_user = query.get("from", {})
        message = query.get("message", {})
        data = (query.get("data") or "").strip()

        chat = message.get("chat", {})
        chat_id = chat.get("id")
        msg_id = message.get("message_id")
        thread_id = message.get("message_thread_id")

        if not self.is_authorized(from_user, chat):
            self.answer_callback_query(query_id, text="🔒 Bạn chưa có quyền thực hiện thao tác này.", show_alert=True)
            self.send_message(chat_id, "🔒 Bạn chưa có quyền thực hiện thao tác này.", thread_id=thread_id)
            return

        # ── Trường hợp chọn tác vụ cho file đang chờ: sum / trans / both / prev ──
        if ":" in data:
            action_code, token = data.split(":", 1)
            action_map = {
                "sum": ("summarize_book", "⚡ Tóm tắt Shortform chuyên sâu"),
                "trans": ("translate_book", "📖 Dịch toàn bộ sách"),
                "both": ("both", "🚀 Cả Dịch & Tóm tắt sách"),
                "prev": ("preview_book", "👁️ Đọc thử 1 chương dịch"),
            }

            if action_code in action_map:
                job_type, action_desc = action_map[action_code]
                info = self.pending_files.get(token)

                # Phục hồi ngữ cảnh: nếu token không có trong RAM (ví dụ file gửi trước khi restart),
                # tìm file đính kèm từ tin nhắn cha (reply_to_message)
                if not info:
                    reply_msg = message.get("reply_to_message", {})
                    doc = reply_msg.get("document") if reply_msg else None
                    if doc and doc.get("file_id"):
                        raw_fname = doc.get("file_name", "book.epub")
                        cleaned_fname = clean_book_filename(raw_fname)
                        from_u = reply_msg.get("from", {})
                        s_name = (
                            f"{from_u.get('first_name', '')} {from_u.get('last_name', '')}".strip()
                            or from_u.get("username")
                            or "Bạn đọc"
                        )
                        info = {
                            "file_id": doc["file_id"],
                            "file_name": cleaned_fname,
                            "file_size": doc.get("file_size", 0),
                            "chat_id": chat_id,
                            "thread_id": thread_id,
                            "msg_id": reply_msg.get("message_id", msg_id),
                            "sender_name": s_name,
                            "timestamp": time.time(),
                        }
                        print(f"[Callback] 🔄 Phục hồi thành công file từ reply_to_message: {cleaned_fname}")

                if not info:
                    self.answer_callback_query(query_id, text="⚠️ Yêu cầu đã hết hạn. Vui lòng gửi lại file sách!", show_alert=True)
                    self.edit_message_text(
                        chat_id,
                        msg_id,
                        "⚠️ <i>Yêu cầu này đã hết hạn. Vui lòng gửi lại file sách!</i>",
                        reply_markup={"inline_keyboard": []},
                    )
                    return

                # Phản hồi toast ngay lập tức trên giao diện Telegram
                self.answer_callback_query(query_id, text=f"✅ Đã nhận: {action_desc}!")

                file_name = info["file_name"]

                # Cập nhật tin nhắn để xóa các nút bấm và xác nhận tác vụ
                updated_text = (
                    f"📥 <b>Sách:</b> <code>{file_name}</code>\n"
                    f"✅ <b>Đã chọn:</b> {action_desc}\n"
                    f"⏳ Đang tải file về máy và đưa vào hàng đợi xử lý..."
                )
                self.edit_message_text(
                    chat_id,
                    msg_id,
                    updated_text,
                    reply_markup={"inline_keyboard": []},
                )

                self.job_queue.put({
                    "type": job_type,
                    "action": action_code,
                    "file_id": info["file_id"],
                    "file_name": file_name,
                    "chat_id": chat_id,
                    "thread_id": thread_id,
                    "msg_id": info.get("msg_id", msg_id),
                    "sender_name": info.get("sender_name", "Bạn đọc"),
                })

                # Xóa token khỏi bộ nhớ tạm và đồng bộ file
                self.pending_files.pop(token, None)
                self._save_pending_files()
                print(f"[Callback] Đã đưa vào hàng đợi: {action_code} cho '{file_name}'")
                return

            # ── Trường hợp chọn mode: setmode:<mode> ──
            elif action_code == "setmode":
                new_mode = token
                self.settings["default_mode"] = new_mode
                self._save_settings()
                mode_desc = {
                    "ask": "Hỏi qua nút bấm tương tác mỗi khi gửi sách",
                    "summarize": "Luôn tự động Tóm tắt Shortform",
                    "translate": "Luôn tự động Dịch toàn bộ",
                    "both": "Luôn tự động làm Cả hai",
                }.get(new_mode, new_mode)
                self.answer_callback_query(query_id, text=f"Đã lưu: {mode_desc}")
                self.edit_message_text(
                    chat_id,
                    msg_id,
                    f"⚙️ <b>Cài đặt chế độ xử lý mặc định:</b>\n👉 <b>{mode_desc}</b>",
                    reply_markup={"inline_keyboard": []},
                )
                return

            # ── Trường hợp chọn model: setmodel:<model> ──
            elif action_code == "setmodel":
                new_model = token
                self.settings["model"] = new_model
                self._save_settings()
                self.answer_callback_query(query_id, text=f"Đã đổi sang: {new_model}")
                self.edit_message_text(
                    chat_id,
                    msg_id,
                    f"🧠 <b>Đã chuyển đổi Model LLM sang:</b> <code>{new_model}</code>",
                    reply_markup={"inline_keyboard": []},
                )
                return

            # ── Trường hợp tải sách từ thư viện: get:<stem> ──
            elif action_code == "get":
                target_stem = token
                matches = [
                    f for f in OUTPUT_DIR.glob("*.epub")
                    if target_stem.lower() in f.stem.lower() and not f.name.startswith(".")
                ]
                if matches:
                    f = matches[0]
                    self.answer_callback_query(query_id, text="Đang gửi sách...")
                    self.send_document(chat_id, f, caption=f"📚 <b>{f.name}</b>", thread_id=thread_id)
                else:
                    self.answer_callback_query(query_id, text="Không tìm thấy sách!", show_alert=True)
                    self.send_message(chat_id, f"🔍 Không tìm thấy file <code>{target_stem}</code>", thread_id=thread_id)
                return

    # ── 5. Xử lý các lệnh Text (/menu, /start, /help, /library,...) ──
    def handle_message(self, message: dict[str, Any]) -> None:
        chat = message.get("chat", {})
        from_user = message.get("from", {})
        chat_id = chat.get("id")
        msg_id = message.get("message_id")
        thread_id = message.get("message_thread_id")

        if not chat_id:
            return

        first_name = from_user.get("first_name", "")
        last_name = from_user.get("last_name", "")
        sender_name = f"{first_name} {last_name}".strip() or from_user.get("username") or "Bạn đọc"

        # Nếu tin nhắn có đính kèm Document (.epub/.pdf)
        if message.get("document"):
            self.handle_incoming_document(message)
            return

        text = (message.get("text") or "").strip()
        if not text:
            return

        # ── Xử lý khi người dùng Reply bằng chữ (ví dụ: "tóm tắt", "dịch", "1", "2") ──
        reply_msg = message.get("reply_to_message", {})
        if reply_msg and not text.startswith("/"):
            doc = reply_msg.get("document")
            if not doc and reply_msg.get("reply_to_message"):
                doc = reply_msg.get("reply_to_message", {}).get("document")

            if doc and doc.get("file_name", "").lower().endswith((".epub", ".pdf")):
                if not self.is_authorized(from_user, chat):
                    self.send_message(chat_id, "🔒 Bạn chưa có quyền dùng bot.", thread_id=thread_id)
                    return

                txt_lower = text.lower()
                chosen = None
                if any(w in txt_lower for w in ["tóm tắt", "tom tat", "shortform", "summary", "brief", "1"]):
                    chosen = "summarize"
                elif any(w in txt_lower for w in ["dịch", "dich", "translate", "2"]):
                    chosen = "translate"
                elif any(w in txt_lower for w in ["cả hai", "ca hai", "both", "3"]):
                    chosen = "both"
                elif any(w in txt_lower for w in ["thử", "thu", "preview", "4"]):
                    chosen = "preview"

                if chosen:
                    file_name = clean_book_filename(doc["file_name"])
                    action_descs = {
                        "summarize": "⚡ Tóm tắt Shortform",
                        "translate": "📖 Dịch toàn bộ sách",
                        "both": "🚀 Cả Dịch & Tóm tắt",
                        "preview": "👁️ Đọc thử 1 chương",
                    }
                    self.send_message(
                        chat_id,
                        f"🎯 Đã nhận yêu cầu <b>{action_descs[chosen]}</b> cho sách: <code>{file_name}</code>!\n⏳ Đang đưa vào hàng đợi...",
                        reply_to_message_id=msg_id,
                        thread_id=thread_id,
                    )
                    self.job_queue.put({
                        "type": f"{chosen}_book" if chosen != "both" else "both",
                        "action": chosen,
                        "file_id": doc["file_id"],
                        "file_name": file_name,
                        "chat_id": chat_id,
                        "thread_id": thread_id,
                        "msg_id": msg_id,
                        "sender_name": sender_name,
                    })
                    return

        if not text.startswith("/"):
            return

        parts = text.split(maxsplit=1)
        cmd = parts[0].split("@")[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        # ── /start, /menu, /help ──
        if cmd in ("/start", "/menu", "/help"):
            active_model = self.settings.get("model", "gemini-3.7-flash")
            active_mode = self.settings.get("default_mode", "ask")
            mode_name = {
                "ask": "Hỏi qua nút bấm (Interactive)",
                "summarize": "Tự động Tóm tắt Shortform",
                "translate": "Tự động Dịch toàn văn",
                "both": "Tự động Cả hai",
            }.get(active_mode, active_mode)

            menu_text = (
                "📚 <b>TRỢ LÝ DỊCH THUẬT & TÓM TẮT SÁCH AI</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "Chào bạn! Bot chuyên biệt 100% cho việc tiếp nhận, dịch thuật toàn văn và tóm tắt sách chuyên sâu theo phương pháp Shortform.\n\n"
                "📖 <b>1. CÁCH GỬI SÁCH ĐỂ XỬ LÝ:</b>\n"
                "• Gửi trực tiếp file <code>.epub</code> hoặc <code>.pdf</code> (dưới 20 MB) vào chat này.\n"
                "• Bot sẽ hiển thị <b>menu nút bấm 1-chạm</b> để bạn chọn ngay:\n"
                "  ⚡ <b>Tóm tắt Shortform (3–5p):</b> Luận đề cốt lõi, 3 trụ cột, Shortform Notes & Heuristics.\n"
                "  📖 <b>Dịch toàn bộ sách (~20p):</b> Dịch trung thực toàn văn kèm thuật ngữ glossary chuẩn.\n"
                "  🚀 <b>Cả Dịch & Tóm tắt:</b> Xuất bản cả 2 file EPUB hoàn chỉnh.\n"
                "  👁️ <b>Đọc thử 1 chương:</b> Dịch mẫu chương đầu để kiểm tra chất lượng văn phong.\n"
                "• <i>Mẹo: Nhắn kèm caption 'dịch' hoặc 'tóm tắt' khi gửi file để bot tự động chạy!</i>\n\n"
                "🛠️ <b>2. CÁC LỆNH TÍNH NĂNG NỔI BẬT:</b>\n"
                "• <code>/library</code> — Mở Thư viện sách đã xử lý (xem & tải lại ngay).\n"
                "• <code>/quick &lt;tên sách&gt;</code> — Đọc ngay bản tóm tắt 1 trang trong tin nhắn.\n"
                "• <code>/glossary &lt;tên sách&gt;</code> — Tra cứu bảng thuật ngữ song ngữ Anh - Việt.\n"
                "• <code>/ask &lt;câu hỏi&gt;</code> — Reply file sách và hỏi đáp phản biện với nội dung.\n"
                "• <code>/mode</code> — Cài đặt chế độ xử lý mặc định khi nhận file.\n"
                "• <code>/model</code> — Chọn mô hình AI (Gemini Flash / Gemini Pro).\n"
                "• <code>/status</code> — Kiểm tra hàng đợi và trạng thái máy Mac.\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"⚙️ Chế độ hiện tại: <b>{mode_name}</b> | Model: <code>{active_model}</code>\n"
                f"📢 Nhóm đồng bộ: <code>{self.default_chat_id}</code> (topic {self.default_topic_id})"
            )
            self.send_message(chat_id, menu_text, reply_to_message_id=msg_id, thread_id=thread_id)
            return

        # ── /status ──
        elif cmd == "/status":
            q_size = self.job_queue.qsize()
            active_model = self.settings.get("model", "gemini-3.7-flash")
            active_mode = self.settings.get("default_mode", "ask")

            short_count = len([f for f in OUTPUT_DIR.glob("*_short.epub") if not f.name.startswith(".")])
            trans_count = len([f for f in OUTPUT_DIR.glob("*.vi.epub") if not f.name.startswith(".")])

            status_text = (
                f"🟢 <b>HỆ THỐNG ĐANG HOẠT ĐỘNG BÌNH THƯỜNG</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"• Hàng đợi đang chờ: <b>{q_size}</b> tác vụ\n"
                f"• Mô hình LLM đang dùng: <code>{active_model}</code>\n"
                f"• Chế độ nhận sách: <b>{active_mode}</b>\n"
                f"• Thư mục output: <code>{OUTPUT_DIR}</code>\n"
                f"• Nhóm đồng bộ: <code>{self.default_chat_id}</code> (topic {self.default_topic_id})\n\n"
                f"📊 <b>Thống kê Thư viện hiện tại:</b>\n"
                f"⚡ Sách Tóm tắt Shortform: <b>{short_count}</b> cuốn\n"
                f"📖 Sách Dịch toàn văn: <b>{trans_count}</b> cuốn"
            )
            self.send_message(chat_id, status_text, reply_to_message_id=msg_id, thread_id=thread_id)
            return

        # ── /mode ──
        elif cmd == "/mode":
            if not self.is_authorized(from_user, chat):
                self.send_message(chat_id, "🔒 Bạn chưa có quyền dùng bot.", thread_id=thread_id)
                return

            if arg.lower() in ("ask", "summarize", "translate", "both"):
                self.settings["default_mode"] = arg.lower()
                self._save_settings()
                self.send_message(
                    chat_id,
                    f"✅ Đã đổi chế độ xử lý mặc định sang: <b>{arg.lower()}</b>",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                )
                return

            keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "❓ Hỏi qua nút bấm (Mặc định)", "callback_data": "setmode:ask"},
                    ],
                    [
                        {"text": "⚡ Luôn Tóm tắt Shortform", "callback_data": "setmode:summarize"},
                        {"text": "📖 Luôn Dịch toàn bộ", "callback_data": "setmode:translate"},
                    ],
                    [
                        {"text": "🚀 Luôn làm Cả hai", "callback_data": "setmode:both"},
                    ],
                ]
            }
            curr = self.settings.get("default_mode", "ask")
            self.send_message(
                chat_id,
                f"⚙️ <b>Cấu hình Chế độ Xử lý Khi Nhận Sách</b>\n"
                f"Chế độ hiện tại: <b>{curr}</b>\n\n"
                f"Chọn chế độ bạn muốn áp dụng tự động:",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
                reply_markup=keyboard,
            )
            return

        # ── /model ──
        elif cmd == "/model":
            if not self.is_authorized(from_user, chat):
                self.send_message(chat_id, "🔒 Bạn chưa có quyền dùng bot.", thread_id=thread_id)
                return

            if arg.lower() in ("flash", "pro", "gemini-3.7-flash", "gemini-2.5-pro"):
                model_name = "gemini-3.7-flash" if "flash" in arg.lower() else "gemini-2.5-pro"
                self.settings["model"] = model_name
                self._save_settings()
                self.send_message(
                    chat_id,
                    f"✅ Đã cấu hình Model LLM sang: <code>{model_name}</code>",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                )
                return

            keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "⚡ Gemini 3.7 Flash (Siêu nhanh, chuẩn)", "callback_data": "setmodel:gemini-3.7-flash"},
                    ],
                    [
                        {"text": "🧠 Gemini 2.5 Pro (Học thuật, chuyên sâu)", "callback_data": "setmodel:gemini-2.5-pro"},
                    ],
                ]
            }
            curr = self.settings.get("model", "gemini-3.7-flash")
            self.send_message(
                chat_id,
                f"🧠 <b>Chọn Mô hình AI (Gemini Engine)</b>\n"
                f"Model hiện tại: <code>{curr}</code>\n\n"
                f"• <b>Flash:</b> Tốc độ cao (~3–5 phút), tối ưu chi phí token, lập luận mạch lạc.\n"
                f"• <b>Pro:</b> Khả năng lý luận triết học, phản biện đa chiều và dịch thuật văn học tinh tế nhất.",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
                reply_markup=keyboard,
            )
            return

        # ── /library hoặc /books ──
        elif cmd in ("/library", "/books"):
            epubs = [f for f in OUTPUT_DIR.glob("*.epub") if not f.name.startswith(".")]
            if not epubs:
                self.send_message(
                    chat_id,
                    "📚 Thư viện hiện chưa có sách nào. Hãy gửi một file <code>.epub</code> hoặc <code>.pdf</code> vào đây nhé!",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                )
                return

            short_books = [f for f in epubs if "_short" in f.stem]
            trans_books = [f for f in epubs if ".vi" in f.stem or "_vi" in f.stem or "_short" not in f.stem]

            lines = ["📚 <b>THƯ VIỆN SÁCH ĐÃ XỬ LÝ</b>\n━━━━━━━━━━━━━━━━━━━━\n"]
            if short_books:
                lines.append(f"⚡ <b>Bản Tóm Tắt Shortform ({len(short_books)} cuốn):</b>")
                for i, f in enumerate(short_books[:10], 1):
                    clean = f.stem.replace("_short", "").replace("_", " ")
                    lines.append(f"{i}. <b>{clean}</b> ➔ <code>/get {f.stem}</code> (hoặc <code>/quick {f.stem}</code>)")
                lines.append("")

            if trans_books:
                lines.append(f"📖 <b>Bản Dịch Tiếng Việt ({len(trans_books)} cuốn):</b>")
                for i, f in enumerate(trans_books[:10], 1):
                    clean = f.stem.replace(".vi", "").replace("_vi", "").replace("_", " ")
                    lines.append(f"{i}. <b>{clean}</b> ➔ <code>/get {f.stem}</code>")
                lines.append("")

            lines.append("━━━━━━━━━━━━━━━━━━━━")
            lines.append("💡 <i>Gõ lệnh <code>/get &lt;tên sách&gt;</code> để nhận file EPUB ngay tức thì!</i>")
            self.send_message(chat_id, "\n".join(lines), reply_to_message_id=msg_id, thread_id=thread_id)
            return

        # ── /get <tên sách> ──
        elif cmd == "/get":
            if not arg:
                self.send_message(chat_id, "💡 Cách dùng: <code>/get &lt;tên sách&gt;</code>", reply_to_message_id=msg_id, thread_id=thread_id)
                return

            matches = [
                f for f in OUTPUT_DIR.glob("*.epub")
                if arg.lower() in f.stem.lower() and not f.name.startswith(".")
            ]
            if matches:
                target = matches[0]
                self.send_document(chat_id, target, caption=f"📚 <b>{target.name}</b>", reply_to_message_id=msg_id, thread_id=thread_id)
            else:
                self.send_message(chat_id, f"🔍 Không tìm thấy cuốn sách nào khớp với từ khóa <i>'{arg}'</i>.", reply_to_message_id=msg_id, thread_id=thread_id)
            return

        # ── /quick <tên sách> (1-Page Brief trực tiếp trong chat) ──
        elif cmd in ("/quick", "/brief"):
            if not arg:
                # Nếu người dùng reply vào tin nhắn
                reply_msg = message.get("reply_to_message", {})
                reply_doc = reply_msg.get("document", {})
                if reply_doc:
                    arg = Path(clean_book_filename(reply_doc.get("file_name", ""))).stem

            if not arg:
                self.send_message(
                    chat_id,
                    "💡 Cách dùng: <code>/quick &lt;tên sách&gt;</code> (hoặc Reply tin nhắn sách và gõ /quick) để xem bản tóm tắt 1 trang.",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                )
                return

            # Tìm file analysis.json tương ứng
            json_matches = [
                f for f in OUTPUT_DIR.glob("*.analysis.json")
                if arg.lower() in f.stem.lower() and not f.name.startswith(".")
            ]
            if json_matches:
                brief = self.format_executive_brief(json_matches[0], json_matches[0].stem.replace(".analysis", ""))
                if brief:
                    self.send_message(chat_id, brief, reply_to_message_id=msg_id, thread_id=thread_id)
                    return

            self.send_message(
                chat_id,
                f"🔍 Chưa có bản phân tích tóm tắt sẵn cho từ khóa <i>'{arg}'</i>. Bạn hãy gửi file sách để bot tóm tắt nhé!",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
            return

        # ── /glossary <tên sách> ──
        elif cmd == "/glossary":
            if not arg:
                self.send_message(chat_id, "💡 Cách dùng: <code>/glossary &lt;tên sách&gt;</code> để tra cứu thuật ngữ.", reply_to_message_id=msg_id, thread_id=thread_id)
                return

            matches = [
                f for f in OUTPUT_DIR.glob("*.glossary.json")
                if arg.lower() in f.stem.lower() and not f.name.startswith(".")
            ]
            if matches:
                g_file = matches[0]
                try:
                    g_data = json.loads(g_file.read_text(encoding="utf-8"))
                    terms = g_data.get("terms", {})
                    if terms:
                        lines = [f"📚 <b>BẢNG THUẬT NGỮ CHUYÊN NGÀNH: {g_file.stem.replace('.vi.glossary', '')}</b>\n━━━━━━━━━━━━━━━━━━━━\n"]
                        for en, vi in sorted(terms.items())[:25]:
                            lines.append(f"• <b>{en}</b> ➔ <i>{vi}</i>")
                        lines.append(f"\n<i>(Tổng cộng: {len(terms)} thuật ngữ đã chuẩn hóa)</i>")
                        self.send_message(chat_id, "\n".join(lines), reply_to_message_id=msg_id, thread_id=thread_id)
                        return
                except Exception as e:
                    print(f"Lỗi đọc glossary: {e}", file=sys.stderr)

            self.send_message(chat_id, f"🔍 Chưa tìm thấy file glossary nào cho <i>'{arg}'</i>.", reply_to_message_id=msg_id, thread_id=thread_id)
            return

        # ── /ask <câu hỏi> ──
        elif cmd == "/ask":
            if not arg:
                self.send_message(chat_id, "💡 Cách dùng: Reply vào file sách hoặc gõ <code>/ask &lt;câu hỏi về cuốn sách&gt;</code>", reply_to_message_id=msg_id, thread_id=thread_id)
                return

            # Xác định ngữ cảnh cuốn sách
            book_context_text = ""
            reply_msg = message.get("reply_to_message", {})
            reply_doc = reply_msg.get("document", {})
            book_stem = ""
            if reply_doc:
                book_stem = Path(clean_book_filename(reply_doc.get("file_name", ""))).stem

            if book_stem:
                # Tìm analysis
                analyses = [f for f in OUTPUT_DIR.glob(f"*{book_stem}*.analysis.json")]
                if analyses:
                    try:
                        an_data = json.loads(analyses[0].read_text(encoding="utf-8"))
                        book_context_text = f"Sách: {an_data.get('title_vi', book_stem)}\nBối cảnh: {an_data.get('book_context', '')}\nTrụ cột: {an_data.get('themes', [])}"
                    except Exception:
                        pass

            self.send_message(chat_id, "🧠 <i>Đang phân tích và đúc kết câu trả lời theo phương pháp Shortform...</i>", reply_to_message_id=msg_id, thread_id=thread_id)

            # Gọi Gemini trả lời
            try:
                from ebook_translator.core.llm import LLMClient
                active_model = self.settings.get("model", "gemini-3.7-flash")
                llm = LLMClient(model=active_model)
                system_prompt = (
                    "Bạn là chuyên gia phân tích tri thức và phản biện sách theo phong cách Shortform. "
                    "Trả lời câu hỏi của người đọc một cách súc tích (dưới 300 từ), gãy gọn, sắc sảo, có tính hành động cao."
                )
                user_prompt = f"Ngữ cảnh sách:\n{book_context_text}\n\nCâu hỏi của người đọc:\n{arg}"
                resp = llm.complete(system=system_prompt, messages=[{"role": "user", "content": user_prompt}])
                self.send_message(chat_id, f"💡 <b>Góc Nhìn Shortform:</b>\n\n{resp}", reply_to_message_id=msg_id, thread_id=thread_id)
            except Exception as e:
                self.send_message(chat_id, f"❌ Lỗi khi trả lời: {e}", reply_to_message_id=msg_id, thread_id=thread_id)
            return

        # ── /translate hoặc /dich ──
        elif cmd in ("/translate", "/dich"):
            if not self.is_authorized(from_user, chat):
                self.send_message(chat_id, "🔒 Bạn chưa có quyền dùng bot.", thread_id=thread_id)
                return

            reply_msg = message.get("reply_to_message", {})
            doc = reply_msg.get("document") if reply_msg else None
            if not doc and reply_msg and reply_msg.get("reply_to_message"):
                doc = reply_msg.get("reply_to_message", {}).get("document")
            if doc and doc.get("file_name", "").lower().endswith((".epub", ".pdf")):
                raw_file_name = doc["file_name"]
                file_name = clean_book_filename(raw_file_name)
                self.send_message(
                    chat_id,
                    f"📖 Đã nhận yêu cầu <b>Dịch Toàn Bộ Sách</b> cho: <code>{file_name}</code>!\n⏳ Đang đưa vào hàng đợi...",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                )
                self.job_queue.put({
                    "type": "translate_book",
                    "file_id": doc["file_id"],
                    "file_name": file_name,
                    "chat_id": chat_id,
                    "thread_id": thread_id,
                    "msg_id": msg_id,
                    "sender_name": sender_name,
                })
                return

            self.send_message(
                chat_id,
                "📖 <b>Dịch Sách Toàn Văn Sang Tiếng Việt</b>\n"
                "• Gửi file <code>.epub</code> hoặc <code>.pdf</code> kèm caption <i>'dịch'</i>.\n"
                "• Hoặc Reply vào file sách bất kỳ trong chat và gõ lệnh <code>/translate</code>!",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
            return

        # ── /summarize hoặc /tomtat ──
        elif cmd in ("/summarize", "/tomtat"):
            if not self.is_authorized(from_user, chat):
                self.send_message(chat_id, "🔒 Bạn chưa có quyền dùng bot.", thread_id=thread_id)
                return

            reply_msg = message.get("reply_to_message", {})
            doc = reply_msg.get("document") if reply_msg else None
            if not doc and reply_msg and reply_msg.get("reply_to_message"):
                doc = reply_msg.get("reply_to_message", {}).get("document")
            if doc and doc.get("file_name", "").lower().endswith((".epub", ".pdf")):
                raw_file_name = doc["file_name"]
                file_name = clean_book_filename(raw_file_name)
                self.send_message(
                    chat_id,
                    f"⚡ Đã nhận yêu cầu <b>Tóm Tắt Shortform</b> cho: <code>{file_name}</code>!\n⏳ Đang đưa vào hàng đợi...",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                )
                self.job_queue.put({
                    "type": "summarize_book",
                    "file_id": doc["file_id"],
                    "file_name": file_name,
                    "chat_id": chat_id,
                    "thread_id": thread_id,
                    "msg_id": msg_id,
                    "sender_name": sender_name,
                })
                return

            self.send_message(
                chat_id,
                "⚡ <b>Tóm Tắt Sách Chuyên Sâu (Shortform Method)</b>\n"
                "• Gửi file <code>.epub</code> hoặc <code>.pdf</code> kèm caption <i>'tóm tắt'</i>.\n"
                "• Hoặc Reply vào file sách bất kỳ trong chat và gõ lệnh <code>/summarize</code>!",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
            return

    # ── 6. Helper định dạng Bản tóm tắt điều hành 1 trang ──
    def format_executive_brief(self, analysis_path: Path, stem: str) -> str:
        try:
            data = json.loads(analysis_path.read_text(encoding="utf-8"))
            title_vi = data.get("title_vi") or stem.replace("_", " ")
            context = (data.get("book_context") or "").strip()
            audience = (data.get("audience") or "").strip()
            themes = data.get("themes") or []

            lines = [
                "⚡ <b>BẢN TÓM TẮT ĐIỀU HÀNH (1-PAGE BRIEF)</b>",
                f"📖 <b>{title_vi}</b>",
                "━━━━━━━━━━━━━━━━━━━━",
            ]
            if context:
                lines.append(f"🎯 <b>Bối cảnh & Luận đề cốt lõi:</b>\n{context}\n")
            if audience:
                lines.append(f"👥 <b>Độc giả mục tiêu:</b>\n{audience}\n")
            if themes:
                lines.append("📌 <b>3–5 Trụ cột tư duy chính:</b>")
                for i, theme in enumerate(themes[:5], 1):
                    lines.append(f"{i}. {theme}")
                lines.append("")
            lines.append("━━━━━━━━━━━━━━━━━━━━")
            lines.append("📱 <i>Bản phân tích chuyên sâu đầy đủ (Shortform Notes, Phản biện đa chiều & Bảng Heuristics) được đính kèm trong file EPUB bên dưới:</i>")
            return "\n".join(lines)
        except Exception as e:
            print(f"Lỗi format 1-page brief: {e}", file=sys.stderr)
            return ""

    def find_cover(self, stem: str) -> Path | None:
        """Tìm ảnh bìa tùy chỉnh trong covers/."""
        for ext in ("jpg", "jpeg", "png", "webp", "gif"):
            c_file = COVERS_DIR / f"{stem}.{ext}"
            if c_file.exists():
                return c_file
        return None

    # ── 7. Worker Loop xử lý hàng đợi ngầm ──
    def worker_loop(self) -> None:
        while self.running:
            try:
                job = self.job_queue.get(timeout=2)
            except queue.Empty:
                continue

            try:
                self.process_job(job)
            except Exception as e:
                print(f"[Worker] Ngoại lệ khi xử lý job: {e}", file=sys.stderr)
            finally:
                self.job_queue.task_done()

    def process_job(self, job: dict[str, Any]) -> None:
        job_type = job.get("type", "summarize_book")
        chat_id = job["chat_id"]
        thread_id = job.get("thread_id")
        msg_id = job.get("msg_id")
        sender_name = job.get("sender_name", "Bạn đọc")
        file_id = job.get("file_id")
        file_name = clean_book_filename(job.get("file_name", "book.epub"))

        target_group_chat = self.default_chat_id or -1003879100454
        target_group_topic = self.default_topic_id or "365"
        is_already_in_group = (
            chat_id == target_group_chat
            and str(thread_id or "") == str(target_group_topic)
        )

        # 1. Tải file về máy nếu có file_id
        target_path = INBOX_DIR / file_name
        if file_id:
            if not self.download_file(file_id, target_path):
                self.send_message(chat_id, f"❌ Tải file <b>{file_name}</b> thất bại. Vui lòng thử lại!", reply_to_message_id=msg_id, thread_id=thread_id)
                return

        stem = Path(file_name).stem
        active_model = self.settings.get("model", "gemini-3.7-flash")

        # ── TRƯỜNG HỢP A: TÓM TẮT SHORTFORM ──
        if job_type in ("summarize_book", "both"):
            self.send_message(
                chat_id,
                f"⚙️ <b>Bắt đầu tóm tắt Shortform:</b> <code>{file_name}</code>\n"
                f"🧠 Gemini (<code>{active_model}</code>) đang phân tích cấu trúc, trích xuất luận đề & biên soạn các bài học chuyên sâu...\n"
                f"<i>(Thời gian xử lý: khoảng 3 – 8 phút)</i>",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )

            start_time = time.time()
            output_epub = OUTPUT_DIR / f"{stem}_short.epub"
            analysis_json = OUTPUT_DIR / f"{stem}_short.analysis.json"
            workdir = PROCESSING_DIR / f"{stem}_short.workdir"

            cmd = [
                str(VENV_SUMMARIZE),
                str(target_path),
                "-o", str(output_epub),
                "--model", active_model,
                "--keep-workdir",
            ]
            cover_file = self.find_cover(stem)
            if cover_file:
                cmd.extend(["--cover", str(cover_file)])

            res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PROJECT_DIR))
            dur = int(time.time() - start_time)
            dur_m, dur_s = dur // 60, dur % 60

            if res.returncode == 0 and output_epub.exists():
                self.bot_processed_files[output_epub.name] = time.time()
                self.sent_files[output_epub.name] = output_epub.stat().st_mtime
                self._save_sent_files(self.sent_files)

                # Di chuyển file analysis vào output/ nếu nằm ở nơi khác
                cand_analysis = Path(str(output_epub).replace(".epub", ".analysis.json"))
                if cand_analysis.exists() and cand_analysis != analysis_json:
                    try:
                        shutil.copy(cand_analysis, analysis_json)
                    except Exception:
                        pass

                # Trích xuất và gửi 1-Page Brief trước
                if analysis_json.exists():
                    brief_text = self.format_executive_brief(analysis_json, stem)
                    if brief_text:
                        self.send_message(chat_id, brief_text, reply_to_message_id=msg_id, thread_id=thread_id)

                # Gửi file EPUB
                caption = (
                    f"⚡ <b>{stem}</b>\n"
                    f"✨ <i>Bản tóm tắt chuyên sâu phong cách Shortform</i>\n\n"
                    f"⏱️ <b>Thời gian xử lý:</b> {dur_m}m{dur_s}s\n"
                    f"📱 <i>Đã đóng gói chuẩn EPUB3, tương thích Apple Books, Kindle, Kobo!</i>"
                )
                self.send_document(chat_id, output_epub, caption=caption, reply_to_message_id=msg_id, thread_id=thread_id)

                if not is_already_in_group:
                    group_caption = (
                        f"⚡ <b>{stem}</b>\n"
                        f"✨ <i>Bản tóm tắt chuyên sâu phong cách Shortform</i>\n\n"
                        f"👤 <b>Yêu cầu bởi:</b> {sender_name}\n"
                        f"⏱️ <b>Thời gian xử lý:</b> {dur_m}m{dur_s}s\n"
                        f"📱 <i>Đã đóng gói chuẩn EPUB3!</i>"
                    )
                    self.send_document(target_group_chat, output_epub, caption=group_caption, thread_id=target_group_topic)

                shutil.rmtree(workdir, ignore_errors=True)
            else:
                print(f"[Worker] Lỗi tóm tắt {file_name}:\n{res.stderr}\n{res.stdout}", file=sys.stderr)
                self.send_message(
                    chat_id,
                    f"❌ Có lỗi xảy ra khi tóm tắt <b>{file_name}</b>.\n"
                    f"Vui lòng kiểm tra log trên máy tính hoặc thử lại.",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                )
                if job_type == "both":
                    return

        # ── TRƯỜNG HỢP B: DỊCH TOÀN BỘ SÁCH HOẶC ĐỌC THỬ CHƯƠNG ĐẦU ──
        if job_type in ("translate_book", "preview_book", "both"):
            is_preview = (job_type == "preview_book")
            out_suffix = "_preview.vi.epub" if is_preview else ".vi.epub"
            output_epub = OUTPUT_DIR / f"{stem}{out_suffix}"
            workdir = PROCESSING_DIR / f"{stem}_trans.workdir"

            status_desc = "dịch thử 1 chương đầu" if is_preview else "dịch toàn bộ cuốn sách"
            est_time = "~1 – 2 phút" if is_preview else "~15 – 25 phút"

            self.send_message(
                chat_id,
                f"📖 <b>Bắt đầu {status_desc}:</b> <code>{file_name}</code>\n"
                f"🧠 Gemini (<code>{active_model}</code>) đang trích xuất bảng thuật ngữ Glossary & tiến hành dịch...\n"
                f"<i>(Thời gian xử lý: {est_time})</i>",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )

            start_time = time.time()
            cmd = [
                str(VENV_TRANSLATE),
                str(target_path),
                "-o", str(output_epub),
                "--model", active_model,
                "--keep-workdir",
            ]
            if is_preview:
                cmd.extend(["--max-chapters", "1"])
            cover_file = self.find_cover(stem)
            if cover_file:
                cmd.extend(["--cover", str(cover_file)])

            res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PROJECT_DIR))
            dur = int(time.time() - start_time)
            dur_m, dur_s = dur // 60, dur % 60

            if res.returncode == 0 and output_epub.exists():
                self.bot_processed_files[output_epub.name] = time.time()
                self.sent_files[output_epub.name] = output_epub.stat().st_mtime
                self._save_sent_files(self.sent_files)

                # Kiểm tra glossary
                glossary_file = Path(str(output_epub).replace(".epub", ".glossary.json"))
                term_count = 0
                if glossary_file.exists():
                    try:
                        g_data = json.loads(glossary_file.read_text(encoding="utf-8"))
                        term_count = len(g_data.get("terms", {}))
                    except Exception:
                        pass

                term_note = f"\n📚 <b>Thuật ngữ chuẩn hóa:</b> {term_count} từ" if term_count > 0 else ""
                preview_note = "\n💡 <i>Gõ /translate để dịch trọn vẹn cả cuốn sách!</i>" if is_preview else ""

                caption = (
                    f"📖 <b>{stem}</b>\n"
                    f"✨ <i>{'Bản dịch thử chương 1' if is_preview else 'Bản dịch tiếng Việt toàn văn chất lượng cao'}</i>\n"
                    f"{term_note}\n"
                    f"⏱️ <b>Thời gian xử lý:</b> {dur_m}m{dur_s}s\n"
                    f"📱 <i>Đã đóng gói chuẩn EPUB3!</i>{preview_note}"
                )

                self.send_document(chat_id, output_epub, caption=caption, reply_to_message_id=msg_id, thread_id=thread_id)

                if not is_already_in_group and not is_preview:
                    group_caption = (
                        f"📖 <b>{stem}</b>\n"
                        f"✨ <i>Bản dịch tiếng Việt toàn văn chất lượng cao</i>\n"
                        f"👤 <b>Yêu cầu bởi:</b> {sender_name}\n"
                        f"⏱️ <b>Thời gian xử lý:</b> {dur_m}m{dur_s}s\n"
                        f"📱 <i>Đã đóng gói chuẩn EPUB3!</i>"
                    )
                    self.send_document(target_group_chat, output_epub, caption=group_caption, thread_id=target_group_topic)

                shutil.rmtree(workdir, ignore_errors=True)
            else:
                print(f"[Worker] Lỗi dịch {file_name}:\n{res.stderr}\n{res.stdout}", file=sys.stderr)
                self.send_message(
                    chat_id,
                    f"❌ Quá trình dịch <b>{file_name}</b> gặp lỗi.\n"
                    f"Vui lòng kiểm tra log trên máy Mac để biết chi tiết.",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                )

        # Lưu bản gốc vào originals/
        if target_path.exists():
            dest_orig = ORIGINALS_DIR / target_path.name
            try:
                shutil.move(str(target_path), str(dest_orig))
            except Exception:
                pass

    # ── 8. Theo dõi thư mục output/ từ máy tính ──
    def output_watcher_loop(self) -> None:
        print("👀 Đang theo dõi thư mục output/ để tự động thông báo khi có file mới từ máy tính...")
        while self.running:
            try:
                time.sleep(4)
                if not self.default_chat_id:
                    continue

                candidates: list[Path] = []
                if OUTPUT_DIR.exists():
                    for f in OUTPUT_DIR.glob("*.epub"):
                        if not f.name.startswith(".") and f.is_file():
                            candidates.append(f)

                for file_path in candidates:
                    fname = file_path.name
                    try:
                        mtime = file_path.stat().st_mtime
                        size = file_path.stat().st_size
                    except OSError:
                        continue

                    if size == 0:
                        continue

                    last_sent_mtime = self.sent_files.get(fname)
                    if last_sent_mtime is None or mtime > last_sent_mtime + 3.0:
                        time.sleep(2.0)
                        try:
                            new_size = file_path.stat().st_size
                        except OSError:
                            continue
                        if new_size != size:
                            continue

                        # Nếu file do bot vừa tạo trong 90s qua thì bỏ qua
                        if time.time() - self.bot_processed_files.get(fname, 0) < 90:
                            self.sent_files[fname] = mtime
                            self._save_sent_files(self.sent_files)
                            continue

                        # ĐÂY LÀ FILE DO USER TẠO TỪ MÁY MAC
                        is_short = "_short" in fname
                        type_tag = "⚡ [Tóm Tắt Shortform]" if is_short else "📖 [Bản Dịch Toàn Văn]"
                        clean_stem = (
                            file_path.stem.replace("_short", "")
                            .replace(".vi", "")
                            .replace("_vi", "")
                            .replace("_", " ")
                        )
                        size_str = (
                            f"{new_size / (1024 * 1024):.1f} MB"
                            if new_size > 1024 * 1024
                            else f"{new_size / 1024:.0f} KB"
                        )
                        curr_time = time.strftime("%H:%M:%S %d/%m/%Y")

                        caption = (
                            f"🖥️ <b>[Xuất bản từ máy tính]</b> {type_tag}\n"
                            f"📚 <b>{clean_stem}</b>\n\n"
                            f"📁 <b>Tệp:</b> <code>{fname}</code> ({size_str})\n"
                            f"⏱️ <b>Hoàn tất lúc:</b> {curr_time}\n"
                            f"📱 <i>Đã đóng gói chuẩn EPUB3, tương thích Apple Books, Kindle, Kobo!</i>"
                        )

                        print(f"[Watcher] Phát hiện file mới từ máy tính: {fname}. Đang gửi tới nhóm...")
                        sent = self.send_document(
                            self.default_chat_id,
                            file_path,
                            caption=caption,
                            thread_id=self.default_topic_id,
                        )
                        if sent:
                            print(f"[Watcher] ✅ Đã đồng bộ file thành công: {fname}")
                            self.sent_files[fname] = mtime
                            self._save_sent_files(self.sent_files)

            except Exception as e:
                print(f"[Watcher] Lỗi vòng lặp watcher: {e}", file=sys.stderr)

    # ── 9. Đăng ký menu lệnh với Telegram API ──
    def register_bot_commands(self) -> None:
        commands = [
            {"command": "menu", "description": "📖 Menu & Hướng dẫn sử dụng bot"},
            {"command": "library", "description": "📚 Thư viện sách đã dịch & tóm tắt"},
            {"command": "translate", "description": "📖 Dịch sách toàn văn sang tiếng Việt"},
            {"command": "summarize", "description": "⚡ Tóm tắt sách chuyên sâu kiểu Shortform"},
            {"command": "quick", "description": "⚡ Đọc ngay bản tóm tắt điều hành 1 trang"},
            {"command": "glossary", "description": "📑 Tra cứu bảng thuật ngữ song ngữ"},
            {"command": "ask", "description": "🧠 Hỏi đáp phản biện với nội dung sách"},
            {"command": "mode", "description": "⚙️ Cài đặt chế độ xử lý mặc định"},
            {"command": "model", "description": "🧠 Chọn mô hình AI (Flash / Pro)"},
            {"command": "status", "description": "🟢 Kiểm tra hàng đợi & hệ thống"},
            {"command": "help", "description": "❓ Trợ giúp nhanh cách gửi sách"},
        ]
        try:
            r = requests.post(f"{self.api_url}/setMyCommands", json={"commands": commands}, timeout=10)
            if r.status_code == 200 and r.json().get("ok"):
                print("📋 Đã đồng bộ menu lệnh Dịch & Tóm Tắt Sách với Telegram (gợi ý tự động khi gõ '/')")
            else:
                print(f"[Telegram] setMyCommands trả về lỗi: {r.text}", file=sys.stderr)
        except Exception as e:
            print(f"[Telegram] Lỗi setMyCommands: {e}", file=sys.stderr)

    # ── 10. Vòng lặp chính Long-polling ──
    def run(self) -> None:
        print("🤖 Telegram Book Bot (Dịch thuật & Tóm tắt Shortform) đang khởi động...")
        try:
            r = requests.get(f"{self.api_url}/getMe", timeout=12)
            me = r.json()
            if not me.get("ok"):
                sys.exit(f"❌ Token bot không hợp lệ: {me}")
            bot_username = me["result"]["username"]
            print(f"✅ Đã kết nối thành công tới Bot: @{bot_username}")
        except Exception as e:
            sys.exit(f"❌ Không thể kết nối tới Telegram API: {e}")

        # Đồng bộ menu lệnh với Telegram
        self.register_bot_commands()

        worker = threading.Thread(target=self.worker_loop, daemon=True)
        worker.start()

        watcher = threading.Thread(target=self.output_watcher_loop, daemon=True)
        watcher.start()

        print("👂 Đang lắng nghe tài liệu, lệnh Dịch và Tóm tắt từ Telegram...")

        while self.running:
            try:
                params = {
                    "offset": self.offset,
                    "timeout": 25,
                }
                resp = requests.get(f"{self.api_url}/getUpdates", params=params, timeout=30)
                data = resp.json()

                if not data.get("ok"):
                    print(f"[Telegram] getUpdates lỗi: {data}", file=sys.stderr)
                    time.sleep(4)
                    continue

                for update in data.get("result", []):
                    self.offset = update["update_id"] + 1

                    # 1. Xử lý Callback Query (Nút bấm Inline Keyboard)
                    callback_query = update.get("callback_query")
                    if callback_query:
                        self.handle_callback_query(callback_query)
                        continue

                    # 2. Xử lý Tin nhắn thông thường
                    message = update.get("message")
                    if message:
                        self.handle_message(message)

            except requests.RequestException:
                time.sleep(3)
            except Exception as e:
                print(f"[MainLoop] Ngoại lệ không mong muốn: {e}", file=sys.stderr)
                time.sleep(2)


def main():
    bot = TelegramBookBot()
    bot.run()


if __name__ == "__main__":
    main()
