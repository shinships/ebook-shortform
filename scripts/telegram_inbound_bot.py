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

import datetime
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))
sys.path.insert(0, str(PROJECT_DIR / "src"))

from ebook_translator.core.article import (
    ArticleContent,
    TranslatedArticle,
    fetch_and_parse_article,
    generate_article_audio,
    translate_article,
)
from ebook_translator.core.llm import LLMClient
from ebook_translator.core.tags import format_tags, generate_topic_tags
from ebook_translator.core.youtube import (
    extract_time_from_url,
    format_time_str,
    get_youtube_chapters,
    is_youtube_url,
    youtube_to_article,
)
from ebook_translator.recommender import CATEGORIES, SEED_RECOMMENDATIONS, book_recommender
from generate_podcast import (
    PODCASTS_DIR,
    POPULAR_VOICES,
    VIENEU_POPULAR_VOICES,
    VOICE_TO_READER_NAME,
    ZEROTTS_POPULAR_VOICES,
    call_tts,
    call_vieneu_tts,
    call_zerotts_tts,
    create_podcast_for_book,
    generate_podcast_script,
    get_reader_name,
    load_env,
    resolve_voice_code,
)
from send_weekly_recommendations import build_recommendations_keyboard

ENV_PATH = PROJECT_DIR / ".env"
INBOX_DIR = PROJECT_DIR / "inbox"
OUTPUT_DIR = PROJECT_DIR / "output"
ORIGINALS_DIR = OUTPUT_DIR / "originals"
PROCESSING_DIR = PROJECT_DIR / "processing"
LOGS_DIR = PROJECT_DIR / "logs"
COVERS_DIR = PROJECT_DIR / "covers"
ARTICLES_DIR = OUTPUT_DIR / "articles"

VENV_BIN = PROJECT_DIR / ".venv-mac" / "bin"
VENV_SUMMARIZE = VENV_BIN / "ebook-summarize"
VENV_TRANSLATE = VENV_BIN / "ebook-translate"
VENV_PYTHON = VENV_BIN / "python"

SETTINGS_PATH = LOGS_DIR / ".bot_settings.json"
SENT_FILES_PATH = LOGS_DIR / ".sent_files.json"
PENDING_FILES_PATH = LOGS_DIR / ".pending_files.json"
PENDING_REELS_PATH = LOGS_DIR / ".pending_reels.json"
PENDING_URLS_PATH = LOGS_DIR / ".pending_urls.json"

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


@dataclass
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


def render_progress_bar(current: int, total: int, width: int = 10) -> str:
    """Vẽ thanh trạng thái trực quan dạng [██████░░░░] 60%."""
    if total <= 0:
        empty = "░" * width
        return f"[{empty}] 0%"
    fraction = min(1.0, max(0.0, current / total))
    pct = int(fraction * 100)
    filled = int(round(fraction * width))
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {pct}%"


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
        self.pending_urls: dict[str, dict[str, Any]] = self._load_pending_urls()
        self.pending_reels: dict[str, dict[str, Any]] = self._load_pending_reels()

        # Quản lý theo dõi file output được tạo từ máy tính
        self.bot_processed_files: dict[str, float] = {}
        self.sent_files: dict[str, float] = self._load_sent_files()

        # Đảm bảo các thư mục cần thiết tồn tại
        for d in (INBOX_DIR, OUTPUT_DIR, ORIGINALS_DIR, PROCESSING_DIR, LOGS_DIR, COVERS_DIR, ARTICLES_DIR):
            d.mkdir(parents=True, exist_ok=True)

    # ── 1. Quản lý cài đặt & Cấu hình ──
    def _load_settings(self) -> dict[str, Any]:
        env = load_env(ENV_PATH)
        def_engine = os.environ.get("TTS_ENGINE") or env.get("TTS_ENGINE") or "zerotts"
        def_voice = "maichi" if def_engine == "zerotts" else "Minh Quân"
        default = {
            "default_mode": "ask",  # "ask", "summarize", "translate", "both"
            "model": "gemini-3.7-flash",  # "gemini-3.7-flash", "gemini-2.5-pro"
            "engine": def_engine,  # "zerotts" (CPU 48kHz), "vieneu", "vbee"
            "voice": def_voice,  # Giọng đọc mặc định (maichi cho ZeroTTS, Minh Quân cho VieNeu)
            "podcast_speed": 1.15,  # Tốc độ đọc mặc định 1.15x (cho phép 1.1x, 1.15x, 1.2x)
            "weekly_recommendation": {
                "enabled": True,
                "day": 0,  # 0: Thứ Hai
                "hour": 9,  # 09:00 sáng
                "last_sent_week": "",
            },
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

    def _load_pending_reels(self) -> dict[str, dict[str, Any]]:
        if PENDING_REELS_PATH.exists():
            try:
                data = json.loads(PENDING_REELS_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    now = time.time()
                    return {k: v for k, v in data.items() if now - v.get("timestamp", 0) < 172800}
            except Exception:
                pass
        return {}

    def _save_pending_reels(self) -> None:
        try:
            PENDING_REELS_PATH.parent.mkdir(parents=True, exist_ok=True)
            PENDING_REELS_PATH.write_text(json.dumps(self.pending_reels, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[Pending] Lỗi lưu pending_reels: {e}", file=sys.stderr)

    def _load_pending_urls(self) -> dict[str, dict[str, Any]]:
        if PENDING_URLS_PATH.exists():
            try:
                data = json.loads(PENDING_URLS_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    now = time.time()
                    return {k: v for k, v in data.items() if now - v.get("timestamp", 0) < 172800}
            except Exception:
                pass
        return {}

    def _save_pending_urls(self) -> None:
        try:
            PENDING_URLS_PATH.parent.mkdir(parents=True, exist_ok=True)
            PENDING_URLS_PATH.write_text(json.dumps(self.pending_urls, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[Pending] Lỗi lưu pending_urls: {e}", file=sys.stderr)

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

    def send_video(
        self,
        chat_id: int | str,
        file_path: Path,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
        thread_id: int | str | None = None,
    ) -> bool:
        """Gửi file Video MP4 chuẩn Telegram Video Player (dùng cho Short/Reel 9:16)."""
        if not file_path.exists():
            return False

        data: dict[str, Any] = {
            "chat_id": chat_id,
            "supports_streaming": True,
            "width": 1080,
            "height": 1920,
        }
        if caption:
            data["caption"] = caption
            data["parse_mode"] = "HTML"
        if reply_to_message_id:
            data["reply_to_message_id"] = reply_to_message_id
        if thread_id and str(thread_id).isdigit() and int(thread_id) > 0:
            data["message_thread_id"] = int(thread_id)

        try:
            with open(file_path, "rb") as f:
                files = {"video": (file_path.name, f, "video/mp4")}
                r = requests.post(
                    f"{self.api_url}/sendVideo",
                    data=data,
                    files=files,
                    timeout=240,
                )
            res = r.json()
            if not res.get("ok"):
                print(f"[Telegram] Lỗi sendVideo: {res}", file=sys.stderr)
            return bool(res.get("ok"))
        except Exception as e:
            print(f"[Telegram] Ngoại lệ sendVideo: {e}", file=sys.stderr)
            return False

    def send_audio(
        self,
        chat_id: int | str,
        file_path: Path,
        caption: str | None = None,
        title: str | None = None,
        performer: str | None = None,
        reply_to_message_id: int | None = None,
        thread_id: int | str | None = None,
        thumb: Path | None = None,
    ) -> dict[str, Any] | None:
        """Gửi file âm thanh MP3 chuẩn Telegram Audio Player (cho phép phát ngầm, 1.5x, 2x) kèm ảnh bìa tập."""
        if not file_path.exists():
            print(f"❌ Không tìm thấy file audio: {file_path}", file=sys.stderr)
            return None

        url = f"{self.api_url}/sendAudio"
        data: dict[str, Any] = {
            "chat_id": chat_id,
            "parse_mode": "HTML",
        }
        if reply_to_message_id:
            data["reply_to_message_id"] = reply_to_message_id
        if thread_id and str(thread_id).isdigit() and int(thread_id) > 0:
            data["message_thread_id"] = int(thread_id)
        if caption:
            data["caption"] = caption
        if title:
            data["title"] = title
        if performer:
            data["performer"] = performer

        # Tự động tìm ảnh bìa nếu chưa truyền vào
        if thumb is None:
            clean_stem = (
                file_path.stem
                .replace("_podcast_tinh_gon", "")
                .replace("_podcast_chuyen_sau", "")
                .replace("_podcast", "")
                .replace("_audio", "")
                .replace("_short_short", "")
                .replace("_short", "")
            )
            cand = PODCASTS_DIR / "episode_covers" / f"{clean_stem}.jpg"
            if cand.exists():
                thumb = cand

        try:
            with open(file_path, "rb") as f:
                files: dict[str, Any] = {"audio": (file_path.name, f, "audio/mpeg")}
                thumb_file = None
                if thumb and thumb.exists() and thumb.stat().st_size > 0:
                    try:
                        thumb_file = open(thumb, "rb")
                        files["thumbnail"] = (thumb.name, thumb_file, "image/jpeg")
                    except Exception:
                        pass

                try:
                    resp = requests.post(url, data=data, files=files, timeout=240)
                    res_data = resp.json()
                    if not res_data.get("ok"):
                        print(f"[Telegram] Lỗi sendAudio: {res_data}", file=sys.stderr)
                    return res_data
                finally:
                    if thumb_file:
                        thumb_file.close()
        except Exception as e:
            print(f"[Telegram] Ngoại lệ sendAudio: {e}", file=sys.stderr)
            return None

    def download_file(self, file_id: str, dest_path: Path) -> bool:
        if dest_path.exists() and dest_path.stat().st_size > 0:
            return True
        orig = ORIGINALS_DIR / dest_path.name
        if orig.exists() and orig.stat().st_size > 0:
            shutil.copy2(str(orig), str(dest_path))
            return True
        proc = PROCESSING_DIR / dest_path.name
        if proc.exists() and proc.stat().st_size > 0:
            shutil.copy2(str(proc), str(dest_path))
            return True

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

        if ext not in (".epub", ".pdf", ".md", ".markdown", ".txt"):
            self.send_message(
                chat_id,
                f"⚠️ Định dạng <code>{ext}</code> chưa được hỗ trợ. Vui lòng gửi file <b>.epub</b>, <b>.pdf</b> hoặc <b>.md</b>.",
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

        # 1. Phát hiện tin nhắn CHUYỂN TIẾP (Forwarded)
        is_forwarded = bool(
            message.get("forward_date")
            or message.get("forward_origin")
            or message.get("forward_from")
            or message.get("forward_from_chat")
            or message.get("forward_sender_name")
        )

        # 2. Phát hiện file ĐÃ ĐƯỢC XỬ LÝ (Tóm tắt _short.epub / _shortform.epub hoặc Dịch .vi.epub / _VN.epub)
        file_name_lower = file_name.lower()
        is_text_doc = ext in (".md", ".markdown", ".txt")
        is_already_short = "_short" in file_name_lower or "_shortform" in file_name_lower
        is_already_translated = (
            any(tag in file_name_lower for tag in (".vi.", "_vi.", ".vi_", ".vn.", "_vn.", ".vn_"))
            or file_name_lower.endswith((".vi.epub", "_vi.epub", ".vn.epub", "_vn.epub", "_vn.pdf"))
        )
        is_already_processed = is_already_short or is_already_translated

        # 3. Phân tích Caption
        caption_raw = (message.get("caption") or "").strip()
        # NGUYÊN TẮC VÀNG: Nếu là tin forward, caption là của bài đăng gốc, KHÔNG PHẢI chỉ thị của người gửi!
        caption_lower = "" if is_forwarded else caption_raw.lower()

        chosen_action: str | None = None
        if caption_lower:
            if is_text_doc:
                if any(w in caption_lower for w in ["podcast sâu", "chuyên sâu", "deep", "deepdive", "podcast dài"]):
                    chosen_action = "pod_deep"
                elif any(w in caption_lower for w in ["podcast tinh gọn", "tinh gọn", "quick"]):
                    chosen_action = "pod_quick"
                elif any(w in caption_lower for w in ["audio", "nghe", "đọc", "voice", "toàn văn", "mp3"]):
                    chosen_action = "pod_direct"
                elif any(w in caption_lower for w in ["podcast"]):
                    chosen_action = "pod_quick"
                elif any(w in caption_lower for w in ["dịch", "dich", "translate"]):
                    chosen_action = "translate"
                elif any(w in caption_lower for w in ["tóm tắt", "tom tat", "shortform", "summary", "brief"]):
                    chosen_action = "summarize"
            else:
                if any(w in caption_lower for w in ["podcast sâu", "chuyên sâu", "deep", "deepdive", "podcast dài"]):
                    chosen_action = "pod_deep"
                elif any(w in caption_lower for w in ["podcast", "audio", "nghe", "đọc", "vbee", "vieneu", "tinh gọn"]):
                    chosen_action = "pod_quick"
                elif any(w in caption_lower for w in ["dịch", "dich", "translate", "toàn văn", "full"]):
                    chosen_action = "translate"
                elif any(w in caption_lower for w in ["tóm tắt", "tom tat", "shortform", "summary", "brief"]):
                    chosen_action = "summarize"
                elif any(w in caption_lower for w in ["cả hai", "ca hai", "both", "all"]):
                    chosen_action = "both"
                elif any(w in caption_lower for w in ["thử", "thu", "preview", "sample"]):
                    chosen_action = "preview"

        # BẢO VỆ: Nếu file được FORWARD hoặc ĐÃ QUA XỬ LÝ (_short.epub, _shortform.epub, .vi.epub, _VN.epub),
        # TUYỆT ĐỐI KHÔNG tự động áp dụng default_mode! Luôn hiện menu để người dùng chọn.
        if not chosen_action and not is_forwarded and not is_already_processed:
            default_mode = self.settings.get("default_mode", "ask")
            if default_mode != "ask":
                chosen_action = default_mode

        # Nếu đã xác định được action rõ ràng:
        if chosen_action:
            action_names = {
                "podcast": "🎙️ Tạo Podcast Tinh Gọn",
                "pod_quick": "🎙️ Tạo Podcast Tinh Gọn",
                "pod_deep": "🎙️ Tạo Podcast Chuyên Sâu",
                "pod_direct": "🎙️ Đọc Audio Toàn Văn (MP3 48kHz)",
                "summarize": "📝 Tóm tắt Shortform",
                "translate": "📖 Dịch toàn bộ sách",
                "both": "🚀 Cả Dịch & Tóm tắt",
                "preview": "👁️ Đọc thử 1 chương",
            }
            job_types = {
                "podcast": "podcast_book_quick",
                "pod_quick": "podcast_book_quick",
                "pod_deep": "podcast_book_deep",
                "pod_direct": "podcast_book_direct",
                "summarize": "summarize_book",
                "translate": "translate_book",
                "both": "both",
                "preview": "preview_book",
            }
            desc = action_names.get(chosen_action, chosen_action)
            ack_msg = (
                f"📥 <b>Đã nhận tài liệu:</b> <code>{file_name}</code> ({size_str})\n"
                f"🎯 <b>Tác vụ tự động:</b> {desc}\n"
                f"⏳ Đang tải file về máy và đưa vào hàng đợi xử lý..."
            )
            self.send_message(chat_id, ack_msg, reply_to_message_id=msg_id, thread_id=thread_id)

            self.job_queue.put({
                "type": job_types.get(chosen_action, f"{chosen_action}_book"),
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

        # Menu 1-chạm tùy chỉnh thông minh theo loại file
        if is_text_doc:
            # Menu chuyên biệt cho file Markdown / Text
            inline_keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "🎙️ Đọc Toàn Văn", "callback_data": f"pod_direct:{file_token}"},
                    ],
                    [
                        {"text": "🎙️ Podcast Tinh Gọn", "callback_data": f"pod_quick:{file_token}"},
                        {"text": "🎙️ Podcast Chuyên Sâu", "callback_data": f"pod_deep:{file_token}"},
                    ],
                ]
            }
            prompt_text = (
                f"📄 <b>Đã nhận tài liệu:</b> <code>{file_name}</code> ({size_str})\n"
                f"✨ <i>Định dạng Markdown / Text</i>\n\n"
                f"🎯 <b>Chọn định dạng âm thanh bạn muốn tạo:</b>\n\n"
                f"• 🎙️ <b>Đọc Toàn Văn:</b> Đọc trọn vẹn 100% từng câu chữ của tài liệu bằng giọng AI 48kHz (không tóm tắt hay cắt gọt).\n\n"
                f"• 🎙️ <b>Podcast Tinh Gọn (8–12p):</b> AI cô đọng các ý chính cốt lõi thành bản audio ngắn gọn, dễ nghe.\n\n"
                f"• 🎙️ <b>Podcast Chuyên Sâu (20–30p):</b> AI mổ xẻ chi tiết luận điểm, cơ chế và phản biện theo phong cách thảo luận chuyên sâu."
            )
        elif is_already_short or is_already_processed:
            # Menu chuyên biệt cho file ĐÃ TÓM TẮT / ĐÃ DỊCH
            inline_keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "🎙️ Podcast Tinh Gọn", "callback_data": f"pod_quick:{file_token}"},
                        {"text": "🎙️ Podcast Chuyên Sâu", "callback_data": f"pod_deep:{file_token}"},
                    ],
                    [
                        {"text": "📝 Tóm tắt nhanh 1 trang", "callback_data": f"quick:{file_token}"},
                        {"text": "🧠 Hỏi đáp phản biện (/ask)", "callback_data": f"ask:{file_token}"},
                    ],
                    [
                        {"text": "📖 Dịch sang tiếng Việt", "callback_data": f"trans:{file_token}"},
                        {"text": "🔄 Tóm tắt lại từ đầu", "callback_data": f"sum:{file_token}"},
                    ],
                ]
            }
            origin_note = " <i>(Tin nhắn chuyển tiếp)</i>" if is_forwarded else ""
            status_tag = "bản tóm tắt Shortform hoàn chỉnh" if is_already_short else "bản dịch tiếng Việt"
            prompt_text = (
                f"📥 <b>Đã nhận sách:</b> <code>{file_name}</code> ({size_str}){origin_note}\n"
                f"✨ <i>Đây là {status_tag}.</i>\n\n"
                f"🎯 <b>Chọn tác vụ bạn muốn thực hiện:</b>\n"
                f"• 🎙️ <b>Podcast Tinh Gọn (8–12p):</b> Nắm bắt nhanh luận đề và 2–3 bài học cốt lõi.\n"
                f"• 🎙️ <b>Podcast Chuyên Sâu (20–30p):</b> Phân tích chi tiết cơ chế, phản biện đa chiều và ma trận hành động.\n"
                f"• 📝 <b>Tóm tắt nhanh:</b> Đọc bản tóm tắt điều hành 1 trang ngay trong chat.\n"
                f"• 🧠 <b>Hỏi đáp:</b> Đặt câu hỏi phản biện với nội dung cuốn sách."
            )
        else:
            # Menu cho file sách GỐC tiếng Anh (.epub / .pdf)
            inline_keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "📝 Tóm tắt Shortform", "callback_data": f"sum:{file_token}"},
                        {"text": "📖 Dịch toàn bộ", "callback_data": f"trans:{file_token}"},
                    ],
                    [
                        {"text": "🎙️ Podcast Tinh Gọn", "callback_data": f"pod_quick:{file_token}"},
                        {"text": "🎙️ Podcast Chuyên Sâu", "callback_data": f"pod_deep:{file_token}"},
                    ],
                    [
                        {"text": "📝🎙️ Tóm tắt + Pod Gọn", "callback_data": f"sum_pod:{file_token}"},
                        {"text": "📝🎙️ Tóm tắt + Pod Sâu", "callback_data": f"sum_deep_pod:{file_token}"},
                    ],
                    [
                        {"text": "🚀 Cả Dịch & Tóm tắt", "callback_data": f"both:{file_token}"},
                        {"text": "👁️ Đọc thử 1 chương", "callback_data": f"prev:{file_token}"},
                    ],
                ]
            }
            origin_note = " <i>(Tin nhắn chuyển tiếp)</i>" if is_forwarded else ""
            prompt_text = (
                f"📥 <b>Đã nhận sách:</b> <code>{file_name}</code> ({size_str}){origin_note}\n"
                f"👤 <b>Người gửi:</b> {sender_name}\n\n"
                f"🎯 <b>Vui lòng chọn tác vụ xử lý bạn muốn:</b>\n"
                f"• 📝 <b>Tóm tắt Shortform:</b> Trích xuất luận đề, 3 trụ cột & Shortform Notes (EPUB).\n"
                f"• 📖 <b>Dịch toàn bộ sách:</b> Dịch toàn văn giữ nguyên hình ảnh, thuật ngữ glossary.\n"
                f"• 🎙️ <b>Podcast Tinh Gọn (8–12p):</b> Audio cô đọng ý tưởng lớn, phù hợp nghe nhanh.\n"
                f"• 🎙️ <b>Podcast Chuyên Sâu (20–30p):</b> Masterclass mổ xẻ toàn diện cơ chế & phản biện.\n"
                f"• 📝🎙️ <b>Tóm tắt + Podcast:</b> Vừa tạo sách EPUB tóm tắt vừa tạo Audio Podcast.\n"
                f"• 👁️ <b>Đọc thử 1 chương:</b> Dịch mẫu chương đầu để kiểm tra văn phong."
            )

        self.send_message(
            chat_id,
            prompt_text,
            reply_to_message_id=msg_id,
            thread_id=thread_id,
            reply_markup=inline_keyboard,
        )

    @staticmethod
    def extract_url_from_message(msg: dict[str, Any] | None) -> str | None:
        """Trích xuất đường dẫn URL (YouTube / web) từ tin nhắn hoặc tin nhắn được reply."""
        if not msg:
            return None
        # 1. Tìm URL trong text hoặc caption
        for text_source in (msg.get("text") or "", msg.get("caption") or ""):
            urls = re.findall(r"https?://[^\s<>\"']+", text_source)
            if urls:
                return urls[0].strip().rstrip(".,;!?)<>\"'")
        # 2. Tìm trong entities / caption_entities
        entities = (msg.get("entities") or []) + (msg.get("caption_entities") or [])
        for ent in entities:
            if ent.get("type") == "text_link" and ent.get("url"):
                return ent["url"].strip().rstrip(".,;!?)<>\"'")
        # 3. Đệ quy kiểm tra reply_to_message nếu có
        if msg.get("reply_to_message"):
            return TelegramInboundBot.extract_url_from_message(msg["reply_to_message"])
        return None

    # ── 3b. Tiếp nhận và phân tích liên kết bài viết (URL) ──
    def handle_incoming_url(self, message: dict[str, Any], url: str) -> None:
        chat = message.get("chat", {})
        from_user = message.get("from", {})
        chat_id = chat.get("id")
        msg_id = message.get("message_id")
        thread_id = message.get("message_thread_id")

        if not self.is_authorized(from_user, chat):
            self.send_message(chat_id, "🔒 Bạn chưa có quyền dùng bot.", thread_id=thread_id)
            return

        first_name = from_user.get("first_name", "")
        last_name = from_user.get("last_name", "")
        sender_name = f"{first_name} {last_name}".strip() or from_user.get("username") or "Bạn đọc"

        url = url.strip().rstrip(".,;!?)")
        text_raw = (message.get("text") or message.get("caption") or "").lower()
        is_yt = is_youtube_url(url)

        # Nhận diện mốc thời gian / chapter nếu là YouTube
        yt_start: str | None = None
        yt_end: str | None = None
        yt_chapter: str | None = None

        if is_yt:
            # 1. Khoảng thời gian: "45:48-53:04", "45:48 - 53:04", "45:48 đến 53:04", "10:00 to 20:00"
            range_m = re.search(r"(\d{1,2}:\d{2}(?::\d{2})?)\s*(?:-|–|—|đến|to)\s*(\d{1,2}:\d{2}(?::\d{2})?)", text_raw)
            if range_m:
                yt_start = range_m.group(1)
                yt_end = range_m.group(2)
            else:
                # 2. Chapter: "ch 8", "ch8", "chapter 8", "chương 8"
                ch_m = re.search(r"(?:chapter|chương|ch)\s*(\d+)", text_raw)
                if ch_m:
                    yt_chapter = ch_m.group(1)
                else:
                    # 3. Mốc đơn lẻ: "từ 45:48", "từ 2748", "start 45:48", "mốc 45:48"
                    single_m = re.search(r"(?:từ|start|bắt đầu|mốc)\s*(\d{1,2}:\d{2}(?::\d{2})?|\d+s?)", text_raw)
                    if single_m:
                        yt_start = single_m.group(1)

            # 4. Nếu không có mốc trong văn bản, kiểm tra URL có param t= không
            if not yt_start and not yt_chapter:
                url_t = extract_time_from_url(url)
                if url_t and url_t > 0:
                    yt_start = format_time_str(url_t)

        seg_desc = ""
        if yt_chapter:
            seg_desc = f" (Chương {yt_chapter})"
        elif yt_start and yt_end:
            seg_desc = f" (Đoạn {yt_start} - {yt_end})"
        elif yt_start:
            seg_desc = f" (Từ {yt_start})"

        # Phân tích ý định từ văn bản đi kèm
        has_audio = any(w in text_raw for w in ["audio", "podcast", "nghe", "đọc", "voice", "phát âm"])
        has_trans = any(w in text_raw for w in ["dịch", "dich", "translate", "vietsub", "tiếng việt"])
        has_sum = any(w in text_raw for w in ["tóm tắt", "tom tat", "shortform", "summary", "brief"])
        has_deep = any(w in text_raw for w in ["sâu", "deep", "chuyên sâu", "deepdive", "podcast sâu", "podcast"])

        chosen_action: str | None = None
        if has_deep and has_audio:
            chosen_action = "yt_pod_deep" if is_yt else "art_sum_pod"
        elif has_trans and has_audio:
            chosen_action = "art_trans_pod"
        elif has_sum and has_audio:
            chosen_action = "art_sum_pod"
        elif has_trans:
            chosen_action = "art_trans"
        elif has_sum:
            chosen_action = "art_sum"
        elif has_audio:
            chosen_action = "art_trans_pod"

        action_names = {
            "art_trans_pod": "🎙️ Đọc Toàn Văn",
            "art_trans": "📝 Bản Dịch Chữ",
            "art_sum_pod": "🎙️ Podcast Tinh Gọn",
            "art_sum": "📝 Tóm tắt ngắn",
            "yt_pod_deep": "🎙️ Podcast Phân Tích",
        }
        job_types = {
            "art_trans_pod": "article_translate_audio",
            "art_trans": "article_translate",
            "art_sum_pod": "article_summarize_audio",
            "art_sum": "article_summarize",
            "yt_pod_deep": "article_yt_podcast_deep",
        }

        # Nếu đã có chỉ định rõ ràng qua lệnh hoặc từ khóa
        if chosen_action:
            desc = action_names[chosen_action]
            source_label = "video YouTube" if is_yt else "bài viết"
            ack_msg = (
                f"{'📺' if is_yt else '🌐'} <b>Đã nhận liên kết {source_label}:</b> <code>{url}</code>\n"
                f"🎯 <b>Tác vụ tự động:</b> {desc}{seg_desc}\n"
                f"⏳ Đang {'trích xuất phụ đề' if is_yt else 'tải nội dung bài viết'} và đưa vào hàng đợi xử lý..."
            )
            self.send_message(chat_id, ack_msg, reply_to_message_id=msg_id, thread_id=thread_id)
            self.job_queue.put({
                "type": job_types[chosen_action],
                "action": chosen_action,
                "url": url,
                "chat_id": chat_id,
                "thread_id": thread_id,
                "msg_id": msg_id,
                "sender_name": sender_name,
                "start_time": yt_start,
                "end_time": yt_end,
                "chapter": yt_chapter,
            })
            return

        # Nếu chỉ dán link trống: Tạo token và hiển thị menu tương tác 1-chạm
        url_token = uuid.uuid4().hex[:8]
        self.pending_urls[url_token] = {
            "url": url,
            "chat_id": chat_id,
            "thread_id": thread_id,
            "msg_id": msg_id,
            "sender_name": sender_name,
            "timestamp": time.time(),
            "start_time": yt_start,
            "end_time": yt_end,
            "chapter": yt_chapter,
        }
        self._save_pending_urls()

        parsed_url = urllib.parse.urlparse(url)
        domain = parsed_url.netloc.replace("www.", "")

        # Menu riêng cho YouTube vs Bài viết thường
        if is_yt:
            chapters = []
            try:
                chapters = get_youtube_chapters(url)
            except Exception:
                pass

            buttons = [
                [
                    {"text": f"🎙️ Đọc Toàn Văn{seg_desc}", "callback_data": f"art_trans_pod:{url_token}"},
                ],
                [
                    {"text": f"🎙️ Podcast Phân Tích{seg_desc}", "callback_data": f"yt_pod_deep:{url_token}"},
                ],
                [
                    {"text": f"📝 Bản Dịch Chữ{seg_desc}", "callback_data": f"art_trans:{url_token}"},
                ],
            ]
            if chapters and not (yt_chapter or yt_start):
                buttons.append([
                    {"text": f"📑 Chọn Chapter ({len(chapters)} chương)", "callback_data": f"yt_ch_list:{url_token}"}
                ])
            elif chapters:
                buttons.append([
                    {"text": f"📑 Đổi Chapter khác ({len(chapters)} chương)", "callback_data": f"yt_ch_list:{url_token}"}
                ])

            inline_keyboard = {"inline_keyboard": buttons}
            prompt_text = (
                f"📺 <b>TIẾP NHẬN VIDEO YOUTUBE</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🔗 <b>Đường dẫn:</b> <code>{url}</code>\n"
                f"📡 <b>Nguồn:</b> <code>{domain}</code>\n"
            )
            if seg_desc:
                prompt_text += f"⏱️ <b>Phân đoạn:</b> <code>{seg_desc.strip(' ()')}</code>\n"
            elif chapters:
                prompt_text += f"📑 <b>Video có {len(chapters)} Chapters</b> (Có thể bấm chọn chương bên dưới)\n"

            prompt_text += (
                f"\n🎧 <b>Chọn định dạng bạn muốn tạo:</b>\n\n"
                f"• 🎙️ <b>Đọc Toàn Văn:</b> Dịch và đọc trọn vẹn 100% phụ đề bằng giọng AI 48kHz.\n\n"
                f"• 🎙️ <b>Podcast Phân Tích:</b> AI chắt lọc luận điểm cốt lõi và đúc kết thành buổi đàm thoại podcast lôi cuốn.\n\n"
                f"• 📝 <b>Bản Dịch Chữ:</b> Chỉ dịch phụ đề sang tiếng Việt (văn bản)."
            )
            if not seg_desc:
                prompt_text += (
                    f"\n\n💡 <i>Mẹo: Gửi link kèm mốc (VD: <code>{url} 45:48-53:04</code> hoặc <code>ch8</code>) để cắt nhanh!</i>"
                )
        else:
            inline_keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "🎙️ Đọc Toàn Văn", "callback_data": f"art_trans_pod:{url_token}"},
                    ],
                    [
                        {"text": "🎙️ Podcast Tinh Gọn", "callback_data": f"art_sum_pod:{url_token}"},
                    ],
                    [
                        {"text": "📝 Bản Dịch Chữ", "callback_data": f"art_trans:{url_token}"},
                    ],
                ]
            }
            prompt_text = (
                f"🌐 <b>TIẾP NHẬN BÀI VIẾT TỪ LIÊN KẾT</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🔗 <b>Đường dẫn:</b> <code>{url}</code>\n"
                f"📡 <b>Nguồn:</b> <code>{domain}</code>\n\n"
                f"🎧 <b>Chọn định dạng bạn muốn tạo:</b>\n\n"
                f"• 🎙️ <b>Đọc Toàn Văn:</b> Dịch và đọc trọn vẹn 100% bài viết bằng giọng AI 48kHz.\n\n"
                f"• 🎙️ <b>Podcast Tinh Gọn:</b> AI cô đọng các ý chính quan trọng nhất thành bản audio ngắn gọn dễ nghe.\n\n"
                f"• 📝 <b>Bản Dịch Chữ:</b> Chỉ dịch bài viết sang tiếng Việt (văn bản)."
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

        # ── Trường hợp bấm nút "🎬 Tạo Video Reel" từ 1 tập Podcast đã có sẵn ──
        if data.startswith("make_reel:"):
            reel_token = data.split(":", 1)[1]
            info = self.pending_reels.get(reel_token)
            if not info:
                self.answer_callback_query(query_id, text="⚠️ Yêu cầu đã hết hạn. Vui lòng tạo Podcast mới!", show_alert=True)
                return

            self.answer_callback_query(query_id, text="🎬 Đã nhận yêu cầu! Đang tạo Video Reel...")
            self.edit_message_text(
                chat_id,
                msg_id,
                f"🎬 <b>{info.get('title', '')}</b>\n⏳ Đang tạo Video Reel 9:16 (bìa sách + phụ đề đồng bộ)... Vui lòng đợi 30–60 giây.",
                reply_markup={"inline_keyboard": []},
            )
            self.job_queue.put({
                "type": "make_reel_video",
                "chat_id": info.get("chat_id", chat_id),
                "thread_id": info.get("thread_id", thread_id),
                "msg_id": msg_id,
                "mp3_path": info.get("mp3_path"),
                "script_path": info.get("script_path"),
                "title": info.get("title"),
            })
            self.pending_reels.pop(reel_token, None)
            self._save_pending_reels()
            return

        # ── Trường hợp chọn tác vụ cho file đang chờ hoặc link bài viết ──
        if ":" in data:
            action_code, token = data.split(":", 1)

            # A. Xử lý bài viết từ liên kết URL
            article_action_map = {
                "art_trans_pod": ("article_translate_audio", "🎙️ Đọc Toàn Văn"),
                "art_trans": ("article_translate", "📝 Bản Dịch Chữ"),
                "art_sum_pod": ("article_summarize_audio", "🎙️ Podcast Tinh Gọn"),
                "art_sum": ("article_summarize", "📝 Tóm tắt ngắn"),
                "yt_pod_deep": ("article_yt_podcast_deep", "🎙️ Podcast Phân Tích"),
            }
            if action_code in article_action_map:
                job_type, action_desc = article_action_map[action_code]
                info = self.pending_urls.get(token)
                if not info:
                    self.answer_callback_query(query_id, text="⚠️ Yêu cầu đã hết hạn. Vui lòng gửi lại link!", show_alert=True)
                    self.edit_message_text(
                        chat_id,
                        msg_id,
                        "⚠️ <i>Yêu cầu này đã hết hạn. Vui lòng gửi lại liên kết bài viết!</i>",
                        reply_markup={"inline_keyboard": []},
                    )
                    return

                self.answer_callback_query(query_id, text=f"✅ Đã nhận: {action_desc}!")
                target_url = info["url"]
                seg_info = ""
                if info.get("chapter"):
                    ch_title_desc = f": {info['chapter_title']}" if info.get("chapter_title") else ""
                    seg_info = f"\n⏱️ <b>Phân đoạn:</b> Chương {info['chapter']}{ch_title_desc}"
                elif info.get("start_time") and info.get("end_time"):
                    seg_info = f"\n⏱️ <b>Phân đoạn:</b> {info['start_time']} - {info['end_time']}"
                elif info.get("start_time"):
                    seg_info = f"\n⏱️ <b>Phân đoạn:</b> Từ {info['start_time']}"

                updated_text = (
                    f"🌐 <b>Liên kết:</b> <code>{target_url}</code>{seg_info}\n"
                    f"✅ <b>Đã chọn:</b> {action_desc}\n"
                    f"⏳ Đang tải nội dung và đưa vào hàng đợi xử lý..."
                )
                self.edit_message_text(chat_id, msg_id, updated_text, reply_markup={"inline_keyboard": []})
                self.job_queue.put({
                    "type": job_type,
                    "action": action_code,
                    "url": target_url,
                    "chat_id": chat_id,
                    "thread_id": thread_id,
                    "msg_id": msg_id,
                    "sender_name": info.get("sender_name", "Bạn đọc"),
                    "start_time": info.get("start_time"),
                    "end_time": info.get("end_time"),
                    "chapter": info.get("chapter"),
                    "chapter_title": info.get("chapter_title"),
                })
                return

            # Xử lý xem danh sách chapters của video YouTube
            if action_code == "yt_ch_list":
                info = self.pending_urls.get(token)
                if not info:
                    self.answer_callback_query(query_id, text="⚠️ Yêu cầu đã hết hạn.", show_alert=True)
                    return
                self.answer_callback_query(query_id, text="📑 Đang tải danh sách Chapter...")
                chapters = get_youtube_chapters(info["url"])
                if not chapters:
                    self.answer_callback_query(query_id, text="ℹ️ Video này không có Chapters sẵn.", show_alert=True)
                    return

                ch_buttons = []
                for idx, ch in enumerate(chapters[:18], 1):
                    st = format_time_str(ch.get("start_time", 0))
                    title_clean = ch.get("title", f"Chương {idx}").strip()
                    title_short = (title_clean[:22] + "…") if len(title_clean) > 22 else title_clean
                    btn_text = f"[{idx:02d}] {st} {title_short}"
                    ch_buttons.append([{"text": btn_text, "callback_data": f"yt_ch_pick:{token}_{idx}"}])

                ch_buttons.append([{"text": "🔙 Quay lại menu chính", "callback_data": f"yt_back:{token}"}])

                self.edit_message_text(
                    chat_id,
                    msg_id,
                    f"📑 <b>Chọn Chapter bạn muốn xử lý:</b>\n"
                    f"🔗 <code>{info['url']}</code>\n"
                    f"📊 <i>Tổng cộng {len(chapters)} chương. Bấm vào một chương để xử lý:</i>",
                    reply_markup={"inline_keyboard": ch_buttons},
                )
                return

            # Xử lý khi bấm chọn một chapter cụ thể
            if action_code == "yt_ch_pick":
                if "_" in token:
                    url_tok, ch_num_str = token.rsplit("_", 1)
                else:
                    url_tok, ch_num_str = token, "1"
                info = self.pending_urls.get(url_tok)
                if not info:
                    self.answer_callback_query(query_id, text="⚠️ Yêu cầu đã hết hạn.", show_alert=True)
                    return

                # Lấy tên chapter thực tế từ metadata
                ch_title = f"Chương {ch_num_str}"
                try:
                    chapters = get_youtube_chapters(info["url"])
                    ch_idx_int = int(ch_num_str) if ch_num_str.isdigit() else 1
                    if 1 <= ch_idx_int <= len(chapters):
                        raw_t = chapters[ch_idx_int - 1].get("title", ch_title).strip()
                        from ebook_translator.core.youtube import clean_chapter_raw_title
                        ch_title = clean_chapter_raw_title(raw_t)
                except Exception:
                    pass

                info["chapter"] = ch_num_str
                info["chapter_title"] = ch_title
                self._save_pending_urls()

                self.answer_callback_query(query_id, text=f"✅ Đã chọn Chapter {ch_num_str}!")

                seg_label = f" (Chương {ch_num_str})"
                inline_keyboard = {
                    "inline_keyboard": [
                        [{"text": f"🎙️ Đọc Toàn Văn{seg_label}", "callback_data": f"art_trans_pod:{url_tok}"}],
                        [{"text": f"🎙️ Podcast Phân Tích{seg_label}", "callback_data": f"yt_pod_deep:{url_tok}"}],
                        [{"text": f"📝 Bản Dịch Chữ{seg_label}", "callback_data": f"art_trans:{url_tok}"}],
                        [{"text": "🔙 Chọn lại Chapter", "callback_data": f"yt_ch_list:{url_tok}"}],
                    ]
                }
                display_ch = f"Chương {ch_num_str}: {ch_title}" if ch_title and ch_title != f"Chương {ch_num_str}" else f"Chương {ch_num_str}"
                self.edit_message_text(
                    chat_id,
                    msg_id,
                    f"📺 <b>TIẾP NHẬN VIDEO YOUTUBE</b>\n"
                    f"🔗 <code>{info['url']}</code>\n"
                    f"🎯 <b>Phân đoạn đã chọn:</b> <code>{display_ch}</code>\n\n"
                    f"🎧 <b>Chọn định dạng muốn tạo:</b>",
                    reply_markup=inline_keyboard,
                )
                return

            if action_code == "yt_back":
                info = self.pending_urls.get(token)
                if not info:
                    self.answer_callback_query(query_id, text="⚠️ Yêu cầu đã hết hạn.", show_alert=True)
                    return
                self.answer_callback_query(query_id)
                
                fake_msg = dict(message)
                fake_msg["from"] = from_user
                fake_msg["message_id"] = info.get("msg_id", message.get("message_id"))
                fake_msg["text"] = info["url"]
                
                try:
                    requests.post(f"{self.api_url}/deleteMessage", json={"chat_id": chat_id, "message_id": message.get("message_id")}, timeout=10)
                except Exception:
                    pass
                
                self.handle_incoming_url(fake_msg, info["url"])
                return

            action_map = {
                "sum": ("summarize_book", "📝 Tóm tắt Shortform"),
                "trans": ("translate_book", "📖 Dịch toàn bộ sách"),
                "both": ("both", "🚀 Cả Dịch & Tóm tắt sách"),
                "prev": ("preview_book", "👁️ Đọc thử 1 chương"),
                "pod": ("podcast_book_quick", "🎙️ Podcast Tinh Gọn"),
                "pod_quick": ("podcast_book_quick", "🎙️ Podcast Tinh Gọn"),
                "pod_deep": ("podcast_book_deep", "🎙️ Podcast Chuyên Sâu"),
                "pod_direct": ("podcast_book_direct", "🎙️ Đọc Audio Toàn Văn"),
                "sum_pod": ("sum_and_pod_quick", "📝🎙️ Tóm tắt + Pod Gọn"),
                "sum_deep_pod": ("sum_and_pod_deep", "📝🎙️ Tóm tắt + Pod Sâu"),
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
                    "file_id": info.get("file_id"),
                    "file_name": file_name,
                    "chat_id": info.get("chat_id", chat_id),
                    "thread_id": info.get("thread_id", thread_id),
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
                self.answer_callback_query(query_id, text=f"Đã đổi model: {new_model}")
                self.edit_message_text(
                    chat_id,
                    msg_id,
                    f"🧠 <b>Mô hình AI hiện tại:</b>\n👉 <code>{new_model}</code>",
                    reply_markup={"inline_keyboard": []},
                )
                return


            # ── Trường hợp chọn Engine: setengine:<engine> ──
            elif action_code == "setengine":
                new_engine = token.lower().strip()
                self.settings["engine"] = new_engine
                if new_engine == "zerotts":
                    if self.settings.get("voice") not in ZEROTTS_POPULAR_VOICES:
                        self.settings["voice"] = "maichi"
                    eng_label = "⚡ ZeroTTS (48kHz Real-Time CPU)"
                elif new_engine == "vieneu":
                    if self.settings.get("voice") not in VIENEU_POPULAR_VOICES:
                        self.settings["voice"] = "Minh Quân"
                    eng_label = "🦜 VieNeu-TTS v3 Turbo (48kHz)"
                else:
                    self.settings["voice"] = "hn_female_maiphuong_vdts_48k-fhg"
                    eng_label = "☁️ Vbee AIVoice (Cloud)"
                self._save_settings()
                curr_voice = self.settings.get("voice")
                r_name = get_reader_name(curr_voice, engine=new_engine)
                self.answer_callback_query(query_id, text=f"✅ Đã chọn engine: {eng_label}")
                self.edit_message_text(
                    chat_id,
                    msg_id,
                    f"⚙️ <b>Đã chuyển TTS Engine sang:</b> <b>{eng_label}</b>\n"
                    f"🗣️ <b>Host mặc định:</b> {r_name} (<code>{curr_voice}</code>)\n\n"
                    f"💡 <i>Gõ <code>/voice</code> để đổi giọng đọc hoặc <code>/speed</code> để chỉnh tốc độ!</i>",
                    reply_markup={"inline_keyboard": []},
                )
                return

            # ── Trường hợp mở menu chọn engine: cmd_engine ──
            elif action_code == "cmd_engine":
                curr_engine = self.settings.get("engine", "zerotts")
                keyboard = {
                    "inline_keyboard": [
                        [{"text": f"{'✅ ' if curr_engine == 'zerotts' else ''}⚡ ZeroTTS (48kHz CPU, WER 1%)", "callback_data": "setengine:zerotts"}],
                        [{"text": f"{'✅ ' if curr_engine == 'vieneu' else ''}🦜 VieNeu-TTS (48kHz On-device)", "callback_data": "setengine:vieneu"}],
                        [{"text": f"{'✅ ' if curr_engine == 'vbee' else ''}☁️ Vbee AIVoice (Cloud API)", "callback_data": "setengine:vbee"}],
                    ]
                }
                self.answer_callback_query(query_id)
                self.edit_message_text(
                    chat_id,
                    msg_id,
                    "⚙️ <b>CHỌN CÔNG NGHỆ GIỌNG ĐỌC AI (TTS ENGINE)</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nBấm chọn engine bạn muốn dùng làm mặc định:",
                    reply_markup=keyboard,
                )
                return

            # ── Trường hợp chọn giọng đọc Podcast: setvoice:<voice> ──
            elif action_code == "setvoice":
                new_voice = token
                self.settings["voice"] = new_voice
                if new_voice in ZEROTTS_POPULAR_VOICES:
                    self.settings["engine"] = "zerotts"
                elif new_voice in VIENEU_POPULAR_VOICES:
                    self.settings["engine"] = "vieneu"
                self._save_settings()
                curr_eng = self.settings.get("engine", "zerotts")
                r_name = get_reader_name(new_voice, engine=curr_eng)
                eng_name = "ZeroTTS 48kHz" if curr_eng == "zerotts" else ("VieNeu 48kHz" if curr_eng == "vieneu" else "Vbee Cloud")
                self.answer_callback_query(query_id, text=f"✅ Đã chọn giọng: {r_name} ({eng_name})")
                self.edit_message_text(
                    chat_id,
                    msg_id,
                    f"🗣️ <b>Đã chuyển Host Podcast sang:</b> <b>{r_name}</b> (<code>{new_voice}</code>)\n"
                    f"⚙️ <b>Công nghệ:</b> <code>{eng_name}</code>\n\n"
                    f"💡 <i>Từ bây giờ, các tập Audio Podcast sẽ do Host {r_name} thể hiện!</i>",
                    reply_markup={"inline_keyboard": []},
                )
                return

            # ── Trường hợp chọn tốc độ đọc Podcast: setspeed:<speed> ──
            elif action_code == "setspeed":
                try:
                    new_speed = round(float(token), 2)
                except ValueError:
                    new_speed = 1.15
                if new_speed not in (1.1, 1.15, 1.2):
                    new_speed = 1.15
                self.settings["podcast_speed"] = new_speed
                self._save_settings()
                self.answer_callback_query(query_id, text=f"⚡ Đã đổi tốc độ: {new_speed}x")
                self.edit_message_text(
                    chat_id,
                    msg_id,
                    f"⚡ <b>Đã cài đặt tốc độ đọc Podcast:</b> <b>{new_speed}x</b>\n\n"
                    f"💡 <i>Từ bây giờ, các tập Audio Podcast sẽ được đọc ở tốc độ {new_speed}x!</i>",
                    reply_markup={"inline_keyboard": []},
                )
                return

            # ── Trường hợp làm mới Private RSS: refresh_rss_feed ──
            elif action_code in ("refresh_rss_feed", "refresh_feed") or data == "refresh_rss_feed":
                try:
                    from ebook_translator.core.podcast_rss import update_podcast_feed, scan_podcast_episodes
                    update_podcast_feed()
                    eps = scan_podcast_episodes()
                    self.answer_callback_query(query_id, text=f"✅ Đã làm mới Feed ({len(eps)} tập)!", show_alert=True)
                except Exception as e:
                    self.answer_callback_query(query_id, text=f"Lỗi: {e}", show_alert=True)
                return

            # ── Trường hợp xem file log lỗi: errlog:<token> ──
            elif action_code == "errlog":
                log_file = LOGS_DIR / f"error_{token}.log"
                if log_file.exists():
                    self.answer_callback_query(query_id, text="Đang tải file log...")
                    self.send_document(chat_id, log_file, caption="📋 <b>Chi tiết lỗi hệ thống</b>", thread_id=thread_id)
                else:
                    self.answer_callback_query(query_id, text="⚠️ File log không tồn tại hoặc đã bị xóa.", show_alert=True)
                return

            # ── Trường hợp tóm tắt nhanh 1 trang: quick:<token> ──
            elif action_code == "quick":
                info = self.pending_files.get(token)
                fname = info.get("file_name", "") if info else ""
                stem = (
                    Path(fname).stem
                    .replace("_shortform", "")
                    .replace("_short", "")
                    .replace(".vi", "")
                    .replace("_vi", "")
                    .replace("_vn", "")
                    .replace("_VN", "")
                    .replace(".vn", "")
                )
                self.answer_callback_query(query_id, text="⚡ Đang tải bản tóm tắt 1 trang...")
                analyses = list(OUTPUT_DIR.glob(f"*{stem}*.analysis.json"))
                if analyses:
                    brief = self.format_executive_brief(analyses[0], stem)
                    if brief:
                        self.send_message(chat_id, brief, thread_id=thread_id)
                        return
                self.send_message(chat_id, f"💡 Gõ <code>/quick {stem}</code> để xem bản tóm tắt điều hành!", thread_id=thread_id)
                return

            # ── Trường hợp hỏi đáp sách: ask:<token> ──
            elif action_code == "ask":
                self.answer_callback_query(query_id, text="🧠 Gõ /ask <câu hỏi>")
                self.send_message(
                    chat_id,
                    "💡 <b>Hỏi đáp phản biện với sách:</b>\n"
                    "Reply vào file sách và gõ <code>/ask &lt;câu hỏi của bạn&gt;</code> để được đúc kết câu trả lời chuyên sâu!",
                    thread_id=thread_id,
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

            # ── Trường hợp tương tác Radar Sách Tuần: rec:... ──
            elif action_code == "rec":
                parts = token.split(":", 1)
                rec_action = parts[0]
                rec_arg = parts[1] if len(parts) > 1 else ""

                if rec_action == "brief":
                    self.answer_callback_query(query_id, text="⚡ Đang trích xuất tóm tắt 1 phút...")
                    self.send_message(
                        chat_id,
                        "⏳ <i>Đang phân tích và đúc kết bản tóm tắt điều hành 1 trang theo chuẩn Shortform...</i>",
                        reply_to_message_id=msg_id,
                        thread_id=thread_id,
                    )
                    # Tìm thông tin sách
                    target_book = None
                    for b in SEED_RECOMMENDATIONS:
                        if rec_arg in b.get("id", ""):
                            target_book = b
                            break
                    if not target_book:
                        for issue in book_recommender.history.get("weekly_issues", {}).values():
                            for b in issue.get("books", []):
                                if rec_arg in b.get("id", ""):
                                    target_book = b
                                    break
                            if target_book:
                                break

                    if target_book:
                        brief_text = book_recommender.generate_book_brief(target_book)
                        header = (
                            f"⚡ <b>BẢN TÓM TẮT ĐIỀU HÀNH 1 PHÚT: {target_book.get('title')}</b>\n"
                            f"🇻🇳 <i>{target_book.get('title_vi', '')}</i>\n"
                            f"✍️ Tác giả: <b>{target_book.get('author')}</b> ({target_book.get('year')})\n"
                            "━━━━━━━━━━━━━━━━━━━━\n\n"
                        )
                        self.send_message(chat_id, header + brief_text, thread_id=thread_id)
                    else:
                        self.send_message(chat_id, "🔍 Không tìm thấy dữ liệu chi tiết của cuốn sách này.", thread_id=thread_id)
                    return

                elif rec_action == "wish":
                    target_book = None
                    for b in SEED_RECOMMENDATIONS:
                        if rec_arg in b.get("id", ""):
                            target_book = b
                            break
                    if not target_book:
                        for issue in book_recommender.history.get("weekly_issues", {}).values():
                            for b in issue.get("books", []):
                                if rec_arg in b.get("id", ""):
                                    target_book = b
                                    break
                            if target_book:
                                break

                    if target_book:
                        user_id = from_user.get("id", chat_id)
                        added = book_recommender.add_to_wishlist(user_id, target_book)
                        if added:
                            self.answer_callback_query(query_id, text=f"📌 Đã lưu '{target_book.get('title')[:30]}...' vào Wishlist!")
                        else:
                            self.answer_callback_query(query_id, text="ℹ️ Cuốn sách này đã có sẵn trong Wishlist của bạn!")
                    else:
                        self.answer_callback_query(query_id, text="⚠️ Không tìm thấy sách.", show_alert=True)
                    return

                elif rec_action == "cat":
                    cat = rec_arg if rec_arg in CATEGORIES else "all"
                    cat_title = CATEGORIES.get(cat, cat)
                    self.answer_callback_query(query_id, text=f"Đang tải chuyên mục: {cat_title}...")
                    issue = book_recommender.get_weekly_recommendations(category=cat, force_refresh=False)
                    msg_text = book_recommender.format_telegram_digest(issue)
                    new_kb = build_recommendations_keyboard(issue)
                    self.edit_message_text(chat_id, msg_id, msg_text, reply_markup=new_kb)
                    return

                elif rec_action == "refresh":
                    self.answer_callback_query(query_id, text="🔄 Đang tuyển chọn bộ sách mới từ AI...")
                    issue = book_recommender.get_weekly_recommendations(category="all", force_refresh=True)
                    msg_text = book_recommender.format_telegram_digest(issue)
                    new_kb = build_recommendations_keyboard(issue)
                    self.edit_message_text(chat_id, msg_id, msg_text, reply_markup=new_kb)
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

        # ── Xử lý khi người dùng gửi ảnh làm Cover Podcast ──
        photos = message.get("photo")
        caption = (message.get("caption") or "").strip()
        if photos and any(k in caption.lower() for k in ("/cover", "/set_cover", "ảnh bìa", "anh bia")):
            if not self.is_authorized(from_user, chat):
                self.send_message(chat_id, "🔒 Bạn chưa có quyền dùng bot.", thread_id=thread_id)
                return
            file_id = photos[-1]["file_id"]
            tmp_path = LOGS_DIR / f"temp_cover_{int(time.time())}.jpg"
            if self.download_file(file_id, tmp_path):
                try:
                    from generate_podcast_cover import save_custom_cover_image
                    save_custom_cover_image(tmp_path)
                    tmp_path.unlink(missing_ok=True)
                    self.send_message(
                        chat_id,
                        "🎨 <b>ĐÃ CẬP NHẬT ẢNH BÌA PODCAST THÀNH CÔNG!</b>\n\n"
                        "✨ Ảnh đã được tự động cắt vuông tâm & chuẩn hóa 1400x1400 chuẩn Apple Podcasts.\n"
                        "🚗 Apple Podcasts và CarPlay sẽ tự động hiển thị ảnh bìa mới này!",
                        reply_to_message_id=msg_id,
                        thread_id=thread_id,
                    )
                except Exception as e:
                    self.send_message(chat_id, f"❌ Lỗi xử lý ảnh: {e}", reply_to_message_id=msg_id, thread_id=thread_id)
            return

        text = (message.get("text") or "").strip()
        if not text:
            return

        # ── Xử lý khi tin nhắn chứa link bài viết (URL) ──
        urls = re.findall(r"https?://[^\s<>\"']+", text)
        if urls:
            self.handle_incoming_url(message, urls[0])
            return

        # ── Xử lý khi người dùng Reply bằng chữ (ví dụ: "tóm tắt", "dịch", "1", "2") ──
        reply_msg = message.get("reply_to_message", {})
        if reply_msg and not text.startswith("/"):
            doc = reply_msg.get("document")
            if not doc and reply_msg.get("reply_to_message"):
                doc = reply_msg.get("reply_to_message", {}).get("document")

            if doc and doc.get("file_name", "").lower().endswith((".epub", ".pdf", ".md", ".markdown", ".txt")):
                if not self.is_authorized(from_user, chat):
                    self.send_message(chat_id, "🔒 Bạn chưa có quyền dùng bot.", thread_id=thread_id)
                    return

                txt_lower = text.lower()
                chosen = None
                if any(w in txt_lower for w in ["toàn văn", "đọc toàn văn", "doc toan van", "đọc hết", "doc het"]):
                    chosen = "pod_direct"
                elif any(w in txt_lower for w in ["podcast sâu", "chuyên sâu", "deep", "deepdive", "podcast dài"]):
                    chosen = "pod_deep"
                elif any(w in txt_lower for w in ["podcast", "audio", "nghe", "đọc", "vbee", "vieneu", "tinh gọn"]):
                    chosen = "pod_direct" if doc.get("file_name", "").lower().endswith((".md", ".markdown", ".txt")) else "pod_quick"
                elif any(w in txt_lower for w in ["tóm tắt", "tom tat", "shortform", "summary", "brief", "1"]):
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
                        "pod_direct": "🎙️ Đọc Audio Toàn Văn (MP3 48kHz)",
                        "pod_quick": "🎙️ Tạo Podcast Tinh Gọn",
                        "pod_deep": "🎙️ Tạo Podcast Chuyên Sâu",
                        "podcast": "🎙️ Tạo Podcast Tinh Gọn",
                        "summarize": "📝 Tóm tắt Shortform",
                        "translate": "📖 Dịch toàn bộ sách",
                        "both": "🚀 Cả Dịch & Tóm tắt",
                        "preview": "👁️ Đọc thử 1 chương",
                    }
                    job_types = {
                        "pod_direct": "podcast_book_direct",
                        "pod_quick": "podcast_book_quick",
                        "pod_deep": "podcast_book_deep",
                        "podcast": "podcast_book_quick",
                        "summarize": "summarize_book",
                        "translate": "translate_book",
                        "both": "both",
                        "preview": "preview_book",
                    }
                    self.send_message(
                        chat_id,
                        f"🎯 Đã nhận yêu cầu <b>{action_descs[chosen]}</b> cho sách: <code>{file_name}</code>!\n⏳ Đang đưa vào hàng đợi...",
                        reply_to_message_id=msg_id,
                        thread_id=thread_id,
                    )
                    self.job_queue.put({
                        "type": job_types.get(chosen, f"{chosen}_book"),
                        "action": chosen,
                        "file_id": doc["file_id"],
                        "file_name": file_name,
                        "chat_id": chat_id,
                        "thread_id": thread_id,
                        "msg_id": msg_id,
                        "sender_name": sender_name,
                    })
                    return

            # 2. Reply vào tin nhắn chứa liên kết YouTube hoặc bài viết
            reply_url = self.extract_url_from_message(reply_msg)
            if reply_url:
                if not self.is_authorized(from_user, chat):
                    self.send_message(chat_id, "🔒 Bạn chưa có quyền dùng bot.", thread_id=thread_id)
                    return
                self.handle_incoming_url(message, reply_url)
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
            curr_engine = self.settings.get("engine", "zerotts")
            voice_code = self.settings.get("voice")
            reader_name = get_reader_name(voice_code, engine=curr_engine)
            podcast_speed = self.settings.get("podcast_speed", 1.15)
            mode_name = {
                "ask": "Hỏi qua nút bấm (Interactive)",
                "summarize": "Tự động Tóm tắt Shortform",
                "translate": "Tự động Dịch toàn văn",
                "both": "Tự động Cả hai",
            }.get(active_mode, active_mode)

            menu_text = (
                "📚 <b>TRỢ LÝ DỊCH THUẬT, TÓM TẮT & PODCAST SÁCH AI</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "Chào bạn! Bot chuyên biệt 100% cho việc tiếp nhận, dịch thuật, tóm tắt chuyên sâu và tạo Audio Podcast bằng giọng đọc AI VieNeu v3 Turbo (48kHz).\n\n"
                "🌐 <b>1. DỊCH BÀI VIẾT & TẠO AUDIO TỪ LINK:</b>\n"
                "• Chỉ cần <b>dán link bài báo / blog</b> bất kỳ vào chat (Substack, Medium, TechCrunch, Paul Graham...).\n"
                "• Bot sẽ cung cấp menu 1-chạm để: <b>🎙️ Dịch toàn văn & Tạo Audio MP3 (48kHz)</b>, <b>📖 Dịch bài viết</b>, hoặc <b>⚡🎙️ Tóm tắt & Podcast</b>!\n"
                "• Hoặc gõ lệnh: <code>/article &lt;link bài viết&gt;</code>\n\n"
                "📖 <b>2. CÁCH GỬI SÁCH ĐỂ XỬ LÝ:</b>\n"
                "• Gửi hoặc forward file <code>.epub</code> / <code>.pdf</code> vào chat này.\n"
                "• Bot sẽ hiển thị <b>menu nút bấm 1-chạm</b> để bạn chọn ngay:\n"
                "  ⚡ <b>Tóm tắt Shortform:</b> Luận đề cốt lõi, 3 trụ cột, Shortform Notes & Heuristics.\n"
                "  ⚡🎙️ <b>Podcast Tinh Gọn (8–12p):</b> Kịch bản cô đọng 2-3 ý tưởng lớn, nghe nhanh tiện lợi.\n"
                "  🧠🎙️ <b>Podcast Chuyên Sâu (20–30p):</b> Masterclass bẻ khóa cơ chế, phản biện đa chiều.\n"
                "  📖 <b>Dịch toàn bộ sách:</b> Dịch trung thực toàn văn kèm thuật ngữ glossary chuẩn.\n"
                "  🚀 <b>Cả Dịch & Tóm tắt:</b> Xuất bản cả 2 file EPUB hoàn chỉnh.\n"
                "  👁️ <b>Đọc thử 1 chương:</b> Dịch mẫu chương đầu để kiểm tra chất lượng văn phong.\n\n"
                "🛠️ <b>3. CÁC LỆNH TÍNH NĂNG NỔI BẬT:</b>\n"
                "• <code>/article &lt;link&gt;</code> — 🌐 Dịch bài viết từ link web & tạo audio đọc toàn văn.\n"
                "• <code>/podcast &lt;tên sách&gt;</code> — ⚡🎙️ Tạo <b>Podcast Tinh Gọn</b> (8–12p).\n"
                "• <code>/podcast_deep &lt;tên sách&gt;</code> — 🧠🎙️ Tạo <b>Podcast Chuyên Sâu</b> (20–30p).\n"
                "• <code>/voice</code> — 🗣️ Chọn giọng đọc AI cho Podcast (Minh Quân, Thái Sơn, Anh Khôi, Quỳnh Anh...).\n"
                "• <code>/speed</code> — ⚡ Cài đặt tốc độ đọc Podcast (1.1x, 1.15x, 1.2x).\n"
                "• <code>/rss</code> — 🚗 Link Private RSS & Hướng dẫn nghe trên Apple CarPlay.\n"
                "• <code>/recommend</code> — 🌟 Radar sách mới & High-rating tuần này (Amazon, Goodreads).\n"
                "• <code>/wishlist</code> — 📌 Danh sách sách muốn đọc đã lưu lại.\n"
                "• <code>/library</code> — Mở Thư viện sách đã xử lý (xem & tải lại ngay).\n"
                "• <code>/quick &lt;tên sách&gt;</code> — Đọc ngay bản tóm tắt 1 trang trong tin nhắn.\n"
                "• <code>/glossary &lt;tên sách&gt;</code> — Tra cứu bảng thuật ngữ song ngữ Anh - Việt.\n"
                "• <code>/ask &lt;câu hỏi&gt;</code> — Reply file sách và hỏi đáp phản biện với nội dung.\n"
                "• <code>/mode</code> — Cài đặt chế độ xử lý mặc định khi nhận file.\n"
                "• <code>/model</code> — Chọn mô hình AI (Gemini / DeepSeek).\n"
                "• <code>/status</code> — Kiểm tra hàng đợi và trạng thái máy Mac.\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"⚙️ Chế độ: <b>{mode_name}</b> | Host: <b>{reader_name}</b> (⚡ {podcast_speed}x) | Model: <code>{active_model}</code>\n"
                f"📢 Nhóm đồng bộ: <code>{self.default_chat_id}</code> (topic {self.default_topic_id})"
            )
            self.send_message(chat_id, menu_text, reply_to_message_id=msg_id, thread_id=thread_id)
            return

        # ── /rss, /feed, /podcast_feed, /carplay ──
        elif cmd in ("/rss", "/feed", "/podcast_feed", "/carplay"):
            if not self.is_authorized(from_user, chat):
                self.send_message(chat_id, "🔒 Bạn chưa có quyền dùng bot.", thread_id=thread_id)
                return

            try:
                from ebook_translator.core.podcast_rss import (
                    get_apple_podcasts_url,
                    get_base_url,
                    get_feed_url,
                    get_local_ip,
                    scan_podcast_episodes,
                    update_podcast_feed,
                )
                update_podcast_feed()
                episodes = scan_podcast_episodes()
                feed_url = get_feed_url()
                apple_url = get_apple_podcasts_url()
                base_url = get_base_url()
                local_ip = get_local_ip()

                rss_msg = (
                    "🚗 <b>KÊNH PODCAST APPLE CARPLAY (PRIVATE RSS)</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"🎧 Kênh: <b>Sách Nói &amp; Podcast Tóm Tắt (AI)</b>\n"
                    f"📚 Tổng số tập hiện có: <b>{len(episodes)} tập</b>\n"
                    f"🌐 Server LAN: <code>{local_ip}:8000</code>\n\n"
                    f"📡 <b>Đường Link RSS Feed:</b>\n"
                    f"<code>{feed_url}</code>\n\n"
                    "📱 <b>CÁCH THÊM VÀO APPLE PODCASTS (1 LẦN DUY NHẤT):</b>\n"
                    "1️⃣ Kết nối iPhone vào cùng <b>Wi-Fi ở nhà</b> với máy Mac.\n"
                    "2️⃣ Mở app <b>Apple Podcasts (Podcast)</b> trên iPhone.\n"
                    "3️⃣ Vào tab <b>Thư viện (Library)</b> ➔ Bấm nút <b>...</b> góc trên bên phải.\n"
                    "4️⃣ Chọn <b>'Theo dõi chương trình bằng URL...'</b> (Follow a Show by URL).\n"
                    "5️⃣ Dán link RSS ở trên vào ➔ Bấm <b>Theo dõi</b>.\n\n"
                    "🚘 <i>Khi lên xe, mở Apple CarPlay: Tất cả các tập sách nói/podcast sẽ hiển thị đầy đủ trên màn hình xe, tua bài 15s/30s bằng phím vô-lăng và tự nhớ đoạn nghe dở!</i>"
                )

                keyboard = {
                    "inline_keyboard": [
                        [
                            {"text": "🎧 Mở Apple Podcasts", "url": apple_url},
                            {"text": "🌐 Web Dashboard", "url": f"{base_url}/"},
                        ],
                        [
                            {"text": "🔄 Làm mới Feed", "callback_data": "refresh_rss_feed"},
                        ],
                    ]
                }

                self.send_message(
                    chat_id,
                    rss_msg,
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                    reply_markup=keyboard,
                )
            except Exception as e:
                self.send_message(
                    chat_id,
                    f"❌ Lỗi khi tạo Private RSS Feed: {e}",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                )
            return

        # ── /cover hoặc /set_cover (khi reply ảnh hoặc hướng dẫn) ──
        elif cmd in ("/cover", "/set_cover", "/podcast_cover"):
            if not self.is_authorized(from_user, chat):
                self.send_message(chat_id, "🔒 Bạn chưa có quyền dùng bot.", thread_id=thread_id)
                return

            reply_msg = message.get("reply_to_message", {})
            reply_photos = reply_msg.get("photo") if reply_msg else None
            if reply_photos:
                file_id = reply_photos[-1]["file_id"]
                tmp_path = LOGS_DIR / f"temp_cover_{int(time.time())}.jpg"
                if self.download_file(file_id, tmp_path):
                    try:
                        from generate_podcast_cover import save_custom_cover_image
                        save_custom_cover_image(tmp_path)
                        tmp_path.unlink(missing_ok=True)
                        self.send_message(
                            chat_id,
                            "🎨 <b>ĐÃ CẬP NHẬT ẢNH BÌA PODCAST THÀNH CÔNG!</b>\n\n"
                            "✨ Ảnh đã được tự động cắt vuông tâm & chuẩn hóa 1400x1400 chuẩn Apple Podcasts.\n"
                            "🚗 Apple Podcasts và CarPlay sẽ tự động hiển thị ảnh bìa mới này!",
                            reply_to_message_id=msg_id,
                            thread_id=thread_id,
                        )
                    except Exception as e:
                        self.send_message(chat_id, f"❌ Lỗi xử lý ảnh: {e}", reply_to_message_id=msg_id, thread_id=thread_id)
                return

            # Nếu không reply ảnh, hiển thị hướng dẫn
            guide_cover = (
                "🎨 <b>HƯỚNG DẪN TÙY CHỈNH HÌNH ĐẠI DIỆN PODCAST</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "Có 3 cách cực kỳ dễ dàng để đổi hình đại diện:\n\n"
                "1️⃣ <b>Gửi ảnh trực tiếp qua Telegram:</b>\n"
                "• Gửi 1 tấm ảnh bất kỳ vào chat này kèm chú thích (caption) là: <code>/cover</code>\n"
                "• Hoặc reply vào tấm ảnh bất kỳ đã có trong chat và gõ: <code>/cover</code>\n"
                "<i>(Bot sẽ tự động cắt vuông tâm và chuẩn hóa 1400x1400 sắc nét cho Apple Podcasts)</i>\n\n"
                "2️⃣ <b>Chép đè file ảnh trên máy Mac:</b>\n"
                "• Chép ảnh bạn muốn dùng vào đường dẫn: <code>output/podcasts/cover.jpg</code>\n\n"
                "3️⃣ <b>Dùng dòng lệnh tự động sinh:</b>\n"
                "• <code>python scripts/generate_podcast_cover.py --image /duong/dan/anh.jpg</code>\n"
                "• Hoặc sinh ảnh đồ họa: <code>python scripts/generate_podcast_cover.py --title 'Tên Kênh' --author 'Tên Bạn'</code>"
            )
            self.send_message(chat_id, guide_cover, reply_to_message_id=msg_id, thread_id=thread_id)
            return

        # ── /podcast_title hoặc /channel_title ──
        elif cmd in ("/podcast_title", "/channel_title", "/title_podcast"):
            if not self.is_authorized(from_user, chat):
                self.send_message(chat_id, "🔒 Bạn chưa có quyền dùng bot.", thread_id=thread_id)
                return

            from ebook_translator.core.podcast_rss import get_channel_metadata, set_channel_metadata, update_podcast_feed
            if not arg:
                meta = get_channel_metadata()
                self.send_message(
                    chat_id,
                    f"🏷️ <b>Tên kênh Podcast hiện tại:</b>\n<b>{meta['title']}</b>\n\n"
                    f"💡 Để đổi tên, gõ kèm tên mới, ví dụ:\n"
                    f"<code>/podcast_title Sách Nói Tinh Hoa</code>",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                )
                return

            meta = set_channel_metadata(title=arg)
            try:
                from generate_podcast_cover import generate_branded_cover
                generate_branded_cover(title=arg)
            except Exception:
                pass
            update_podcast_feed()
            self.send_message(
                chat_id,
                f"✅ <b>ĐÃ ĐỔI TÊN KÊNH PODCAST THÀNH:</b>\n<b>{meta['title']}</b>\n\n"
                f"📡 Feed XML và ảnh bìa đã được cập nhật tự động!",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
            return

        # ── /podcast_author hoặc /podcast_host ──
        elif cmd in ("/podcast_author", "/podcast_host"):
            if not self.is_authorized(from_user, chat):
                self.send_message(chat_id, "🔒 Bạn chưa có quyền dùng bot.", thread_id=thread_id)
                return

            from ebook_translator.core.podcast_rss import get_channel_metadata, set_channel_metadata, update_podcast_feed
            if not arg:
                meta = get_channel_metadata()
                self.send_message(
                    chat_id,
                    f"👤 <b>Tác giả / Host Podcast hiện tại:</b>\n<b>{meta['author']}</b>\n\n"
                    f"💡 Để đổi tác giả, gõ kèm tên mới, ví dụ:\n"
                    f"<code>/podcast_author Bùi Tấn Việt</code>",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                )
                return

            meta = set_channel_metadata(author=arg)
            try:
                from generate_podcast_cover import generate_branded_cover
                generate_branded_cover(author=arg)
            except Exception:
                pass
            update_podcast_feed()
            self.send_message(
                chat_id,
                f"✅ <b>ĐÃ ĐỔI TÁC GIẢ / HOST PODCAST THÀNH:</b>\n<b>{meta['author']}</b>\n\n"
                f"📡 Feed XML và ảnh bìa đã được cập nhật tự động!",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
            return

        # ── /article, /url, /dich ──
        elif cmd in ("/article", "/url", "/dich"):
            if not arg:
                reply_url = self.extract_url_from_message(reply_msg) if reply_msg else None
                if reply_url:
                    self.handle_incoming_url(message, reply_url)
                    return
                self.send_message(
                    chat_id,
                    "💡 <b>Cách dùng:</b> <code>/article &lt;link bài viết&gt;</code>\n"
                    "Ví dụ: <code>/article https://paulgraham.com/foundermode.html</code>\n\n"
                    "Hoặc bạn chỉ cần <b>dán thẳng link</b> vào chat, bot sẽ tự động nhận diện và cung cấp nút bấm Dịch & Tạo Audio!",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                )
                return
            urls = re.findall(r"https?://[^\s<>\"']+", arg)
            if urls:
                self.handle_incoming_url(message, urls[0])
            else:
                self.send_message(
                    chat_id,
                    "❌ Không tìm thấy đường dẫn hợp lệ. Vui lòng gửi link bắt đầu bằng <code>http://</code> hoặc <code>https://</code>!",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                )
            return

        # ── /status ──
        elif cmd == "/status":
            q_size = self.job_queue.qsize()
            active_model = self.settings.get("model", "gemini-3.7-flash")
            active_mode = self.settings.get("default_mode", "ask")
            curr_engine = self.settings.get("engine", "zerotts")
            voice_code = self.settings.get("voice")
            reader_name = get_reader_name(voice_code, engine=curr_engine)
            podcast_speed = self.settings.get("podcast_speed", 1.15)

            rec_cfg = self.settings.get("weekly_recommendation", {})
            rec_enabled = rec_cfg.get("enabled", True)
            last_week = rec_cfg.get("last_sent_week") or "Chưa gửi kỳ nào"
            rec_status_str = f"Bật (Thứ Hai 09:00, Kỳ gần nhất: {last_week})" if rec_enabled else "Đang tắt"

            short_count = len([f for f in OUTPUT_DIR.glob("*_short.epub") if not f.name.startswith(".")])
            trans_count = len([f for f in OUTPUT_DIR.glob("*.vi.epub") if not f.name.startswith(".")])
            pod_count = len([f for f in (PROJECT_DIR / "output" / "podcasts").glob("*.mp3")]) if (PROJECT_DIR / "output" / "podcasts").exists() else 0
            art_count = len([f for f in (OUTPUT_DIR / "articles").glob("*.md")]) if (OUTPUT_DIR / "articles").exists() else 0
            art_pod_count = len([f for f in (OUTPUT_DIR / "articles").glob("*.mp3")]) if (OUTPUT_DIR / "articles").exists() else 0

            status_text = (
                f"🟢 <b>HỆ THỐNG ĐANG HOẠT ĐỘNG BÌNH THƯỜNG</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"• Hàng đợi đang chờ: <b>{q_size}</b> tác vụ\n"
                f"• Mô hình LLM đang dùng: <code>{active_model}</code>\n"
                f"• Chế độ nhận sách: <b>{active_mode}</b>\n"
                f"• Host Podcast: <b>{reader_name}</b> (VieNeu AI 48kHz, <b>{podcast_speed}x</b>)\n"
                f"• Radar Sách Tuần: <b>{rec_status_str}</b>\n"
                f"• Thư mục output: <code>{OUTPUT_DIR}</code>\n"
                f"• Nhóm đồng bộ: <code>{self.default_chat_id}</code> (topic {self.default_topic_id})\n\n"
                f"📊 <b>Thống kê Thư viện hiện tại:</b>\n"
                f"⚡ Sách Tóm tắt Shortform: <b>{short_count}</b> cuốn\n"
                f"📖 Sách Dịch toàn văn: <b>{trans_count}</b> cuốn\n"
                f"🎙️ Tập Audio Podcast sách: <b>{pod_count}</b> tập\n"
                f"🌐 Bài viết dịch từ Link: <b>{art_count}</b> bài (🎙️ <b>{art_pod_count}</b> audio)"
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

        elif cmd == "/model":
            if not self.is_authorized(from_user, chat):
                self.send_message(chat_id, "🔒 Bạn chưa có quyền dùng bot.", thread_id=thread_id)
                return
                
            if arg:
                self.settings["model"] = arg.strip()
                self._save_settings()
                self.send_message(
                    chat_id,
                    f"✅ Đã đổi mô hình AI sang: <b>{arg.strip()}</b>",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                )
                return

            keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "🧠 Gemini 3.7 Flash", "callback_data": "setmodel:gemini-3.7-flash"},
                    ],
                    [
                        {"text": "💎 Gemini 1.5 Pro", "callback_data": "setmodel:gemini-1.5-pro"},
                    ],
                    [
                        {"text": "🚀 DeepSeek V4 Pro", "callback_data": "setmodel:deepseek-v4-pro"},
                    ],
                    [
                        {"text": "🐳 DeepSeek V3", "callback_data": "setmodel:deepseek-chat"},
                        {"text": "🧠 DeepSeek R1", "callback_data": "setmodel:deepseek-reasoner"},
                    ],
                ]
            }
            curr = self.settings.get("model", "gemini-3.7-flash")
            self.send_message(
                chat_id,
                f"🧠 <b>Cấu hình Mô hình AI (LLM)</b>\n"
                f"Mô hình hiện tại: <code>{curr}</code>\n\n"
                f"Chọn mô hình bạn muốn dùng cho Dịch & Tóm tắt:",
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

            short_books = [f for f in epubs if "_short" in f.stem.lower() or "_shortform" in f.stem.lower()]
            trans_books = [
                f for f in epubs
                if any(t in f.stem.lower() for t in (".vi", "_vi", "_vn", ".vn"))
                or ("_short" not in f.stem.lower() and "_shortform" not in f.stem.lower())
            ]

            lines = ["📚 <b>THƯ VIỆN SÁCH ĐÃ XỬ LÝ</b>\n━━━━━━━━━━━━━━━━━━━━\n"]
            if short_books:
                lines.append(f"⚡ <b>Bản Tóm Tắt Shortform ({len(short_books)} cuốn):</b>")
                for i, f in enumerate(short_books[:10], 1):
                    clean = (
                        f.stem.replace("_shortform", "")
                        .replace("_short", "")
                        .replace("_", " ")
                    )
                    lines.append(f"{i}. <b>{clean}</b> ➔ <code>/get {f.stem}</code> (hoặc <code>/quick {f.stem}</code>)")
                lines.append("")

            if trans_books:
                lines.append(f"📖 <b>Bản Dịch Tiếng Việt ({len(trans_books)} cuốn):</b>")
                for i, f in enumerate(trans_books[:10], 1):
                    clean = (
                        f.stem.replace(".vi", "")
                        .replace("_vi", "")
                        .replace("_vn", "")
                        .replace("_VN", "")
                        .replace(".vn", "")
                        .replace("_", " ")
                    )
                    lines.append(f"{i}. <b>{clean}</b> ➔ <code>/get {f.stem}</code>")
                lines.append("")

            lines.append("━━━━━━━━━━━━━━━━━━━━")
            lines.append("💡 <i>Gõ lệnh <code>/get &lt;tên sách&gt;</code> để nhận file EPUB ngay tức thì!</i>")
            self.send_message(chat_id, "\n".join(lines), reply_to_message_id=msg_id, thread_id=thread_id)
            return

        # ── /podcast hoặc /audio hoặc /nghe hoặc /podcast_deep hoặc /deepdive ──
        elif cmd in ("/podcast", "/audio", "/nghe", "/podcast_quick", "/podcast_deep", "/deepdive"):
            if not self.is_authorized(from_user, chat):
                self.send_message(chat_id, "🔒 Bạn chưa có quyền dùng bot.", thread_id=thread_id)
                return

            is_deep = cmd in ("/podcast_deep", "/deepdive")
            job_type = "podcast_book_deep" if is_deep else "podcast_book_quick"
            action_code = "pod_deep" if is_deep else "pod_quick"
            type_title = "🎙️" if is_deep else "🎙️ Tóm tắt"
            icon = "🧠🎙️" if is_deep else "⚡🎙️"

            reply_msg = message.get("reply_to_message", {})
            doc = reply_msg.get("document") if reply_msg else None
            if not doc and reply_msg and reply_msg.get("reply_to_message"):
                doc = reply_msg.get("reply_to_message", {}).get("document")

            # 1. Trường hợp Reply vào tin nhắn có file sách
            if doc and doc.get("file_name", "").lower().endswith((".epub", ".pdf")):
                raw_file_name = doc["file_name"]
                file_name = clean_book_filename(raw_file_name)
                curr_engine = self.settings.get("engine", "zerotts")
                voice_code = self.settings.get("voice")
                reader_name = get_reader_name(voice_code, engine=curr_engine)
                self.send_message(
                    chat_id,
                    f"{icon} Đã nhận yêu cầu tạo <b>{type_title} (Host: {reader_name})</b> cho sách: <code>{file_name}</code>!\n⏳ Đang đưa vào hàng đợi...",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                )
                self.job_queue.put({
                    "type": job_type,
                    "action": action_code,
                    "file_id": doc["file_id"],
                    "file_name": file_name,
                    "chat_id": chat_id,
                    "thread_id": thread_id,
                    "msg_id": msg_id,
                    "sender_name": sender_name,
                })
                return
            else:
                # 2. Trường hợp Reply vào tin nhắn có liên kết YouTube hoặc bài viết
                reply_url = self.extract_url_from_message(reply_msg) if reply_msg else None
                if reply_url:
                    fake_msg = dict(message)
                    action_word = "podcast sâu" if is_deep else "podcast"
                    fake_msg["text"] = f"{arg} {action_word}".strip()
                    self.handle_incoming_url(fake_msg, reply_url)
                    return

            # 2. Trường hợp gõ lệnh kèm tên sách (ví dụ: /podcast Barking_Up_the_Wrong_Tree)
            if arg:
                matches = [
                    f for f in OUTPUT_DIR.glob("*.epub")
                    if arg.lower() in f.stem.lower() and not f.name.startswith(".")
                ]
                short_matches = [f for f in matches if "_short" in f.stem or "_shortform" in f.stem]
                target_file = short_matches[0] if short_matches else (matches[0] if matches else None)

                if target_file:
                    curr_engine = self.settings.get("engine", "zerotts")
                    voice_code = self.settings.get("voice")
                    reader_name = get_reader_name(voice_code, engine=curr_engine)
                    self.send_message(
                        chat_id,
                        f"{icon} Đã tìm thấy sách <b>{target_file.name}</b> trong Thư viện.\n"
                        f"⏳ Đang đưa vào hàng đợi tạo <b>{type_title} (Host: {reader_name})</b>...",
                        reply_to_message_id=msg_id,
                        thread_id=thread_id,
                    )
                    self.job_queue.put({
                        "type": job_type,
                        "action": action_code,
                        "file_id": None,
                        "file_name": target_file.name,
                        "chat_id": chat_id,
                        "thread_id": thread_id,
                        "msg_id": msg_id,
                        "sender_name": sender_name,
                    })
                    return
                else:
                    self.send_message(
                        chat_id,
                        f"🔍 Không tìm thấy cuốn sách nào khớp với từ khóa <i>'{arg}'</i> trong thư viện để tạo Podcast.\n"
                        f"💡 Hãy gõ <code>/library</code> để xem danh sách sách có sẵn nhé!",
                        reply_to_message_id=msg_id,
                        thread_id=thread_id,
                    )
                    return

            # 3. Trường hợp gõ /podcast trống -> Hướng dẫn chi tiết
            curr_engine = self.settings.get("engine", "zerotts")
            voice_code = self.settings.get("voice")
            reader_name = get_reader_name(voice_code, engine=curr_engine)
            podcast_speed = self.settings.get("podcast_speed", 1.15)
            help_podcast = (
                "🎙️ <b>TẠO AUDIO PODCAST TÓM TẮT SÁCH (VIENEU AI 48kHz)</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Bot hỗ trợ <b>2 định dạng Audio chuyên biệt</b> phục vụ các nhu cầu tiếp nhận khác nhau:\n\n"
                "⚡🎙️ <b>1. Podcast Tinh Gọn (Quick Listen - 8–12 phút):</b>\n"
                "• Cô đọng luận đề cốt lõi, 2-3 bài học đắt giá nhất và 1 hành vi hành động.\n"
                "• Dùng lệnh: <code>/podcast &lt;tên sách&gt;</code> (hoặc Reply file và gõ <code>/podcast</code>)\n\n"
                "🧠🎙️ <b>2. Podcast Chuyên Sâu (Deep Dive - 20–30 phút):</b>\n"
                "• Masterclass đi sâu vào cơ chế hoạt động, phản biện điểm mù tác giả và ma trận Heuristics.\n"
                "• Dùng lệnh: <code>/podcast_deep &lt;tên sách&gt;</code> (hoặc Reply file và gõ <code>/podcast_deep</code>)\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🗣️ <b>Host hiện tại:</b> <b>{reader_name}</b> (<code>{resolve_voice_code(voice_code, engine=curr_engine)}</code>)\n"
                f"⚡ <b>Tốc độ đọc:</b> <b>{podcast_speed}x</b> (Gõ <code>/speed</code> để chọn 1.1x / 1.15x / 1.2x)\n"
                f"👉 Gõ <code>/voice</code> để chọn Host đọc khác (Minh Quân, Thái Sơn, Anh Khôi, Quỳnh Anh...)!"
            )
            self.send_message(chat_id, help_podcast, reply_to_message_id=msg_id, thread_id=thread_id)
            return

        # ── /voice hoặc /giongdoc ──
        elif cmd in ("/voice", "/giongdoc"):
            if not self.is_authorized(from_user, chat):
                self.send_message(chat_id, "🔒 Bạn chưa có quyền dùng bot.", thread_id=thread_id)
                return

            curr_engine = self.settings.get("engine", "zerotts")
            if arg:
                code = resolve_voice_code(arg, engine=curr_engine)
                if code in POPULAR_VOICES:
                    self.settings["voice"] = code
                    if code in ZEROTTS_POPULAR_VOICES:
                        self.settings["engine"] = "zerotts"
                    elif code in VIENEU_POPULAR_VOICES:
                        self.settings["engine"] = "vieneu"
                    self._save_settings()
                    r_name = get_reader_name(code, engine=self.settings.get("engine"))
                    self.send_message(
                        chat_id,
                        f"✅ Đã chọn giọng đọc Podcast: <b>{r_name}</b> (<code>{code}</code>)",
                        reply_to_message_id=msg_id,
                        thread_id=thread_id,
                    )
                    return

            current_voice = resolve_voice_code(self.settings.get("voice"), engine=curr_engine)
            current_name = get_reader_name(current_voice, engine=curr_engine)
            curr_speed = self.settings.get("podcast_speed", 1.15)

            keyboard = {
                "inline_keyboard": [
                    [
                        {"text": f"{'✅ ' if current_voice == 'maichi' else ''}🎙️ Mai Chi (Nữ nhẹ nhàng)", "callback_data": "setvoice:maichi"},
                        {"text": f"{'✅ ' if current_voice == 'giahuy' else ''}☕ Gia Huy (Nam trầm ấm)", "callback_data": "setvoice:giahuy"},
                    ],
                    [
                        {"text": f"{'✅ ' if current_voice == 'baotrang' else ''}📰 Bảo Trang (Nữ tin tức)", "callback_data": "setvoice:baotrang"},
                        {"text": f"{'✅ ' if current_voice == 'kimoanh' else ''}🌸 Kim Oanh (Nữ ấm áp)", "callback_data": "setvoice:kimoanh"},
                    ],
                    [
                        {"text": f"{'✅ ' if current_voice == 'quangminh' else ''}⚡ Quang Minh (Nam dứt khoát)", "callback_data": "setvoice:quangminh"},
                        {"text": f"{'✅ ' if current_voice == 'huuduc' else ''}📖 Hữu Đức (Nam điềm đạm)", "callback_data": "setvoice:huuduc"},
                    ],
                    [
                        {"text": f"{'✅ ' if current_voice == 'Minh Quân' else ''}🦜 VieNeu - Minh Quân", "callback_data": "setvoice:Minh Quân"},
                        {"text": f"{'✅ ' if current_voice == 'Thái Sơn' else ''}🦜 VieNeu - Thái Sơn", "callback_data": "setvoice:Thái Sơn"},
                    ],
                    [
                        {"text": "⚙️ Chọn TTS Engine (/engine)", "callback_data": "cmd_engine"},
                    ],
                ]
            }

            eng_title = "ZeroTTS 48kHz (Real-Time CPU)" if curr_engine == "zerotts" else ("VieNeu-TTS 48kHz" if curr_engine == "vieneu" else "Vbee Cloud")
            voice_msg = (
                "🗣️ <b>CÀI ĐẶT GIỌNG ĐỌC PODCAST (ZEROTTS & VIENEU)</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Giọng đọc hiện tại: <b>{current_name}</b> (<code>{current_voice}</code>)\n"
                f"Công nghệ TTS: <b>{eng_title}</b>\n"
                f"Tốc độ đọc: <b>{curr_speed}x</b> (Gõ <code>/speed</code> để đổi tốc độ)\n\n"
                "Bấm chọn giọng Host bạn muốn người dẫn chuyện cho các tập Audio Podcast:"
            )
            self.send_message(chat_id, voice_msg, reply_to_message_id=msg_id, thread_id=thread_id, reply_markup=keyboard)
            return

        # ── /engine hoặc /tts ──
        elif cmd in ("/engine", "/tts"):
            if not self.is_authorized(from_user, chat):
                self.send_message(chat_id, "🔒 Bạn chưa có quyền dùng bot.", thread_id=thread_id)
                return

            if arg:
                clean_eng = arg.lower().strip()
                if clean_eng in ("zerotts", "vieneu", "vbee"):
                    self.settings["engine"] = clean_eng
                    if clean_eng == "zerotts":
                        self.settings["voice"] = "maichi"
                    elif clean_eng == "vieneu":
                        self.settings["voice"] = "Minh Quân"
                    else:
                        self.settings["voice"] = "hn_female_maiphuong_vdts_48k-fhg"
                    self._save_settings()
                    r_name = get_reader_name(self.settings["voice"], engine=clean_eng)
                    self.send_message(
                        chat_id,
                        f"✅ Đã đổi TTS Engine sang: <b>{clean_eng.upper()}</b> (Host: {r_name})",
                        reply_to_message_id=msg_id,
                        thread_id=thread_id,
                    )
                    return

            curr_engine = self.settings.get("engine", "zerotts")
            keyboard = {
                "inline_keyboard": [
                    [
                        {"text": f"{'✅ ' if curr_engine == 'zerotts' else ''}⚡ ZeroTTS (48kHz CPU, Real-Time)", "callback_data": "setengine:zerotts"},
                    ],
                    [
                        {"text": f"{'✅ ' if curr_engine == 'vieneu' else ''}🦜 VieNeu-TTS (48kHz On-device)", "callback_data": "setengine:vieneu"},
                    ],
                    [
                        {"text": f"{'✅ ' if curr_engine == 'vbee' else ''}☁️ Vbee AIVoice (Cloud API)", "callback_data": "setengine:vbee"},
                    ],
                ]
            }
            curr_title = "ZeroTTS 48kHz (Khuyên dùng)" if curr_engine == "zerotts" else ("VieNeu-TTS 48kHz" if curr_engine == "vieneu" else "Vbee Cloud")
            engine_msg = (
                "⚙️ <b>CÀI ĐẶT CÔNG NGHỆ GIỌNG ĐỌC AI (TTS ENGINE)</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Engine hiện tại: <b>{curr_title}</b>\n\n"
                "• <b>ZeroTTS</b>: Chạy real-time trên CPU laptop, WER chỉ 1.03%, âm thanh chuẩn 48kHz, đọc song ngữ Anh-Việt liền mạch.\n"
                "• <b>VieNeu-TTS</b>: Chạy on-device 48kHz, hỗ trợ Voice Cloning tức thì từ file mẫu.\n"
                "• <b>Vbee AIVoice</b>: Cloud API thương mại.\n\n"
                "<i>Bấm chọn công nghệ bên dưới hoặc gõ ví dụ: <code>/engine zerotts</code></i>"
            )
            self.send_message(chat_id, engine_msg, reply_to_message_id=msg_id, thread_id=thread_id, reply_markup=keyboard)
            return

        # ── /speed hoặc /tocdo ──
        elif cmd in ("/speed", "/tocdo"):
            if not self.is_authorized(from_user, chat):
                self.send_message(chat_id, "🔒 Bạn chưa có quyền dùng bot.", thread_id=thread_id)
                return

            if arg:
                clean_arg = arg.lower().replace("x", "").strip()
                try:
                    val = round(float(clean_arg), 2)
                    if val in (1.1, 1.15, 1.2):
                        self.settings["podcast_speed"] = val
                        self._save_settings()
                        self.send_message(
                            chat_id,
                            f"✅ Đã đổi tốc độ đọc Podcast sang: <b>{val}x</b>\n"
                            f"💡 Các tập Audio Podcast tiếp theo sẽ được tạo ở tốc độ này!",
                            reply_to_message_id=msg_id,
                            thread_id=thread_id,
                        )
                        return
                    else:
                        self.send_message(
                            chat_id,
                            f"⚠️ Tốc độ không hợp lệ. Vui lòng chọn một trong các mức: <b>1.1x</b>, <b>1.15x</b>, <b>1.2x</b> (ví dụ: <code>/speed 1.15</code>).",
                            reply_to_message_id=msg_id,
                            thread_id=thread_id,
                        )
                        return
                except ValueError:
                    pass

            curr_speed = float(self.settings.get("podcast_speed", 1.15))
            keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": f"{'✅ ' if curr_speed == 1.1 else ''}⚡ 1.1x (Thong thả)",
                            "callback_data": "setspeed:1.1",
                        },
                        {
                            "text": f"{'✅ ' if curr_speed == 1.15 else ''}✨ 1.15x (Mặc định)",
                            "callback_data": "setspeed:1.15",
                        },
                        {
                            "text": f"{'✅ ' if curr_speed == 1.2 else ''}🚀 1.2x (Hơi nhanh)",
                            "callback_data": "setspeed:1.2",
                        },
                    ]
                ]
            }

            speed_msg = (
                "⚡ <b>CÀI ĐẶT TỐC ĐỘ ĐỌC PODCAST (VIENEU AI)</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Tốc độ hiện tại: <b>{curr_speed}x</b>\n\n"
                "Chọn tốc độ đọc phù hợp với phong cách nghe của bạn:\n"
                "• <b>1.1x</b>: Thong thả, tự nhiên, rõ ràng từng chi tiết.\n"
                "• <b>1.15x</b> (Mặc định): Tối ưu, liền mạch và giữ trọn ngữ điệu tự nhiên nhất.\n"
                "• <b>1.2x</b>: Hơi nhanh, tiết kiệm thời gian, dễ tập trung nắm bắt ý chính.\n\n"
                "<i>Bấm nút bên dưới để chọn ngay hoặc gõ ví dụ: <code>/speed 1.15</code></i>"
            )
            self.send_message(
                chat_id,
                speed_msg,
                reply_to_message_id=msg_id,
                thread_id=thread_id,
                reply_markup=keyboard,
            )
            return

        # ── /recommend, /weekly, /goy, /radar ──
        elif cmd in ("/recommend", "/weekly", "/goy", "/radar"):
            category = "all"
            force_refresh = False
            if arg:
                arg_lower = arg.lower()
                if any(w in arg_lower for w in ("force", "moi", "new", "refresh")):
                    force_refresh = True
                for cat_key in CATEGORIES:
                    if cat_key in arg_lower:
                        category = cat_key
                        break

            self.send_message(
                chat_id,
                f"🌟 <i>Đang mở Radar Sách Hay ({CATEGORIES.get(category, category)})...</i>",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )

            issue = book_recommender.get_weekly_recommendations(category=category, force_refresh=force_refresh)
            text_digest = book_recommender.format_telegram_digest(issue)
            keyboard = build_recommendations_keyboard(issue)

            self.send_message(
                chat_id,
                text_digest,
                reply_to_message_id=msg_id,
                thread_id=thread_id,
                reply_markup=keyboard,
            )
            return

        # ── /wishlist, /sachmuondoc ──
        elif cmd in ("/wishlist", "/sachmuondoc"):
            user_id = from_user.get("id", chat_id)
            items = book_recommender.get_wishlist(user_id)
            if not items:
                self.send_message(
                    chat_id,
                    "📌 <b>DANH SÁCH SÁCH MUỐN ĐỌC (WISHLIST)</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                    "Bạn chưa lưu cuốn sách nào vào Wishlist.\n\n"
                    "💡 <i>Hãy gõ <code>/recommend</code> để khám phá sách mới & high-rating rồi bấm nút [📌 Lưu] nhé!</i>",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                )
                return

            lines = [
                f"📌 <b>DANH SÁCH SÁCH MUỐN ĐỌC CỦA BẠN ({len(items)} cuốn)</b>",
                "━━━━━━━━━━━━━━━━━━━━\n",
            ]
            for i, it in enumerate(items, 1):
                title = it.get("title", "")
                title_vi = it.get("title_vi", "")
                author = it.get("author", "")
                added_at = it.get("added_at", "")
                lines.append(f"<b>{i}. {title}</b>")
                if title_vi and title_vi != title:
                    lines.append(f"🇻🇳 <i>{title_vi}</i>")
                lines.append(f"✍️ Tác giả: <b>{author}</b> (Lưu lúc: {added_at})")
                gr, amz = book_recommender.make_urls(title, author)
                lines.append(f"🔗 <a href=\"{gr}\">Goodreads</a> | <a href=\"{amz}\">Amazon</a>\n")

            lines.append("━━━━━━━━━━━━━━━━━━━━")
            lines.append("💡 <i>Khi bạn có file .epub/.pdf của cuốn sách, hãy gửi thẳng vào bot để dịch và tóm tắt tự động nhé!</i>")
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

    def run_command_with_progress(
        self,
        cmd: list[str],
        env: dict[str, str],
        task_label: str,
        file_name: str,
        active_model: str,
        chat_id: int | str,
        status_msg_id: int | None,
        start_time: float,
    ) -> ProcessResult:
        """Thực thi tiến trình CLI và cập nhật thanh trạng thái Telegram [██████░░░░] realtime."""
        sub_env = dict(env)
        sub_env["PYTHONUNBUFFERED"] = "1"

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
                cwd=str(PROJECT_DIR),
                env=sub_env,
            )
        except Exception as e:
            print(f"[Worker] Không thể khởi chạy tiến trình: {e}", file=sys.stderr)
            return ProcessResult(returncode=-1, stdout="", stderr=str(e))

        stdout_lines: list[str] = []
        last_edit_time = 0.0
        last_edit_text = ""
        current_step = 0
        total_steps = 0
        current_detail = "Đang đọc và phân tích cấu trúc tài liệu..."
        is_summarize = "tóm tắt" in task_label.lower()
        icon = "⚙️" if is_summarize else "📖"

        re_plan_total = re.compile(r"Kế hoạch:\s*(\d+)\s*bài học", re.IGNORECASE)
        re_lesson_prog = re.compile(r"Bài\s+(\d+)/(\d+)(?:\s*\((.*?)\))?", re.IGNORECASE)
        re_chap_prog = re.compile(r"Chương\s+(\d+)/(\d+)(?:\s*—\s*chunk\s+(\d+)/(\d+))?", re.IGNORECASE)
        re_ocr_prog = re.compile(r"OCR trang\s+(\d+)\s*\((\d+)/(\d+)\)", re.IGNORECASE)

        def update_telegram_status(force: bool = False) -> None:
            nonlocal last_edit_time, last_edit_text
            if not status_msg_id:
                return
            now = time.time()
            if not force and (now - last_edit_time) < 3.5:
                return

            elapsed_sec = int(now - start_time)
            elapsed_m, elapsed_s = elapsed_sec // 60, elapsed_sec % 60
            time_str = f"{elapsed_m}m{elapsed_s}s" if elapsed_m < 60 else f"{elapsed_m // 60}h{elapsed_m % 60:02d}m"

            if total_steps > 0:
                bar_str = render_progress_bar(current_step, total_steps)
                if is_summarize:
                    step_info = f" (Bài {current_step}/{total_steps})" if current_step > 0 else f" (Tổng {total_steps} bài)"
                else:
                    step_info = f" (Chương {current_step}/{total_steps})" if current_step > 0 else f" (Tổng {total_steps} chương)"
                prog_label = f"<code>{bar_str}</code>{step_info}"

                if current_step > 0 and current_step < total_steps:
                    sec_per_step = elapsed_sec / current_step
                    rem_sec = int(sec_per_step * (total_steps - current_step))
                    rem_m = rem_sec // 60
                    rem_str = f"~{rem_m}m" if rem_m < 60 else f"~{rem_m // 60}h{rem_m % 60:02d}m"
                    time_info = f"Đã chạy {time_str} | Dự kiến còn {rem_str}"
                else:
                    time_info = f"Đã chạy {time_str}"
            else:
                bar_str = render_progress_bar(0, 10)
                prog_label = f"<code>{bar_str}</code>"
                time_info = f"Đã chạy {time_str}"

            text = (
                f"{icon} <b>Đang {task_label}:</b> <code>{file_name}</code>\n"
                f"🧠 <b>Model:</b> <code>{active_model}</code>\n"
                f"📊 <b>Tiến độ:</b> {prog_label}\n"
                f"📍 <b>Chi tiết:</b> <i>{current_detail}</i>\n"
                f"⏱️ <b>Thời gian:</b> {time_info}"
            )

            if text == last_edit_text:
                return

            res = self.edit_message_text(chat_id, status_msg_id, text)
            if res and res.get("ok"):
                last_edit_time = now
                last_edit_text = text

        try:
            if proc.stdout:
                for line in iter(proc.stdout.readline, ""):
                    stdout_lines.append(line)
                    line_clean = line.strip()
                    if not line_clean:
                        continue

                    # Bắt kế hoạch tổng số bài (Summarize)
                    if m_plan := re_plan_total.search(line_clean):
                        total_steps = int(m_plan.group(1))
                        current_detail = f"Kế hoạch gồm {total_steps} bài học Shortform."
                        update_telegram_status()
                        continue

                    # Bắt tiến trình từng bài (Summarize)
                    if m_lesson := re_lesson_prog.search(line_clean):
                        current_step = int(m_lesson.group(1))
                        total_steps = int(m_lesson.group(2))
                        scope = m_lesson.group(3) or ""
                        current_detail = f"Đang phân tích và sinh Bài {current_step}/{total_steps}"
                        if scope:
                            current_detail += f" ({scope})"
                        current_detail += "..."
                        update_telegram_status()
                        continue

                    # Bắt tiến trình từng chương (Translate)
                    if m_chap := re_chap_prog.search(line_clean):
                        current_step = int(m_chap.group(1))
                        total_steps = int(m_chap.group(2))
                        chunk_cur = m_chap.group(3)
                        chunk_tot = m_chap.group(4)
                        if chunk_cur and chunk_tot:
                            current_detail = f"Đang dịch Chương {current_step}/{total_steps} (phần {chunk_cur}/{chunk_tot})..."
                        else:
                            current_detail = f"Đang dịch Chương {current_step}/{total_steps}..."
                        update_telegram_status()
                        continue

                    # Bắt OCR
                    if m_ocr := re_ocr_prog.search(line_clean):
                        current_detail = f"Đang OCR trang {m_ocr.group(1)} ({m_ocr.group(2)}/{m_ocr.group(3)})..."
                        update_telegram_status()
                        continue

                    # Các mốc sự kiện quan trọng
                    if "Đang phân tích sách" in line_clean:
                        current_detail = "Đang phân tích bối cảnh & phân loại chương..."
                        update_telegram_status()
                    elif "Đang viết bài mở đầu" in line_clean:
                        current_detail = "Đang tổng hợp bài mở đầu (Overview)..."
                        update_telegram_status()
                    elif "Đang viết bài tổng kết" in line_clean:
                        current_detail = "Đang tổng hợp bài kết luận (Recap)..."
                        update_telegram_status()
                    elif "Hoàn tất:" in line_clean or "Ghi EPUB" in line_clean:
                        current_detail = "Đang hoàn tất đóng gói file EPUB..."
                        if total_steps > 0:
                            current_step = total_steps
                        update_telegram_status()

            proc.wait()
        except Exception as e:
            print(f"[Worker] Ngoại lệ khi theo dõi tiến độ: {e}", file=sys.stderr)
        finally:
            if proc.poll() is None:
                proc.kill()

        if proc.returncode == 0 and status_msg_id:
            if total_steps > 0:
                current_step = total_steps
            current_detail = "Đã xử lý hoàn tất! Đang đóng gói và gửi file..."
            update_telegram_status(force=True)

        full_stdout = "".join(stdout_lines)
        return ProcessResult(returncode=proc.returncode or 0, stdout=full_stdout, stderr="")

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

        # ── TRƯỜNG HỢP D: XỬ LÝ BÀI VIẾT TỪ LIÊN KẾT (URL) ──
        if job_type.startswith("article_"):
            self.process_article_job(job)
            return

        # ── TRƯỜNG HỢP E: TẠO VIDEO REEL 9:16 TỪ TẬP PODCAST ──
        if job_type == "make_reel_video":
            self.process_reel_job(job)
            return

        file_id = job.get("file_id")
        file_name = clean_book_filename(job.get("file_name", "book.epub"))

        target_group_chat = self.default_chat_id or -1003879100454
        target_group_topic = self.default_topic_id or "365"
        is_already_in_group = (
            chat_id == target_group_chat
            and str(thread_id or "") == str(target_group_topic)
        )

        # 1. Xác định file đầu vào (ưu tiên kiểm tra file đã có trên máy để tránh lỗi getFile hết hạn)
        target_path = INBOX_DIR / file_name
        if not target_path.exists():
            if (ORIGINALS_DIR / file_name).exists():
                target_path = ORIGINALS_DIR / file_name
            elif (PROCESSING_DIR / file_name).exists():
                target_path = PROCESSING_DIR / file_name
            elif file_id:
                if not self.download_file(file_id, target_path):
                    self.send_message(chat_id, f"❌ Tải file <b>{file_name}</b> thất bại. Vui lòng thử lại!", reply_to_message_id=msg_id, thread_id=thread_id)
                    return
            else:
                self.send_message(
                    chat_id,
                    f"❌ Không tìm thấy file <b>{file_name}</b> trên máy tính để xử lý. Vui lòng gửi lại file!",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                )
                return

        stem = Path(file_name).stem
        active_model = self.settings.get("model", "gemini-3.7-flash")

        # ── TRƯỜNG HỢP A: TÓM TẮT SHORTFORM (HOẶC CẢ TÓM TẮT & LÀM PODCAST) ──
        if job_type in ("summarize_book", "sum_and_pod", "both"):
            status_msg = self.send_message(
                chat_id,
                f"⚙️ <b>Bắt đầu tóm tắt Shortform:</b> <code>{file_name}</code>\n"
                f"🧠 <b>Model:</b> <code>{active_model}</code>\n"
                f"📊 <b>Tiến độ:</b> <code>[░░░░░░░░░░] 0%</code>\n"
                f"📍 <i>Đang đọc và phân tích cấu trúc tài liệu...</i>",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
            status_msg_id = status_msg.get("result", {}).get("message_id") if status_msg else None

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

            sub_env = dict(os.environ)
            sub_env.update(load_env(ENV_PATH))
            res = self.run_command_with_progress(
                cmd=cmd,
                env=sub_env,
                task_label="tóm tắt Shortform",
                file_name=file_name,
                active_model=active_model,
                chat_id=chat_id,
                status_msg_id=status_msg_id,
                start_time=start_time,
            )
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
                book_tags_str = format_tags(generate_topic_tags(stem, getattr(analysis, "book_context", ""), output_type="short"))
                caption = (
                    f"⚡ <b>{stem}</b>\n"
                    f"✨ <i>Bản tóm tắt chuyên sâu phong cách Shortform</i>\n\n"
                    f"⏱️ <b>Thời gian xử lý:</b> {dur_m}m{dur_s}s\n"
                    f"📱 <i>Đã đóng gói chuẩn EPUB3, tương thích Apple Books, Kindle, Kobo!</i>\n"
                    f"💡 <i>Gõ /podcast để nghe tập tóm tắt này dưới dạng Audio!</i>"
                )
                if book_tags_str:
                    caption += f"\n\n{book_tags_str}"
                self.send_document(chat_id, output_epub, caption=caption, reply_to_message_id=msg_id, thread_id=thread_id)

                if not is_already_in_group:
                    group_caption = (
                        f"⚡ <b>{stem}</b>\n"
                        f"✨ <i>Bản tóm tắt chuyên sâu phong cách Shortform</i>\n\n"
                        f"⏱️ <b>Thời gian xử lý:</b> {dur_m}m{dur_s}s\n"
                        f"📱 <i>Đã đóng gói chuẩn EPUB3!</i>"
                    )
                    if book_tags_str:
                        group_caption += f"\n\n{book_tags_str}"
                    self.send_document(target_group_chat, output_epub, caption=group_caption, thread_id=target_group_topic)

                # Nếu người dùng chọn Tóm tắt & Làm Podcast (sum_and_pod, sum_and_pod_quick, sum_and_pod_deep)
                if job_type in ("sum_and_pod", "sum_and_pod_quick", "sum_and_pod_deep"):
                    is_deep = (job_type == "sum_and_pod_deep")
                    mode = "deep" if is_deep else "quick"
                    type_title = "Podcast Chuyên Sâu" if is_deep else "Podcast Tinh Gọn"
                    icon = "🧠🎙️" if is_deep else "⚡🎙️"
                    est_desc = "20–30 phút • Masterclass" if is_deep else "8–12 phút • Tinh cất"

                    engine_code = self.settings.get("engine", "zerotts")
                    voice_code = self.settings.get("voice")
                    target_voice = resolve_voice_code(voice_code, engine=engine_code)
                    reader_name = get_reader_name(target_voice, engine=engine_code)
                    podcast_speed = float(self.settings.get("podcast_speed", 1.15))

                    pod_msg = self.send_message(
                        chat_id,
                        f"{icon} <b>Bắt đầu tạo {type_title}:</b> <code>{stem}</code>\n"
                        f"🗣️ <b>Giọng đọc:</b> {reader_name}\n"
                        f"📊 <b>Tiến độ:</b> <code>{render_progress_bar(1, 3)}</code>\n"
                        f"📍 <i>Đang biên soạn kịch bản {type_title} ({est_desc})...</i>",
                        thread_id=thread_id,
                    )
                    pod_msg_id = pod_msg.get("result", {}).get("message_id") if pod_msg else None

                    mp3_path = create_podcast_for_book(
                        output_epub,
                        voice=target_voice,
                        engine=engine_code,
                        speed=podcast_speed,
                        send_telegram=False,
                        mode=mode,
                    )

                    if pod_msg_id:
                        self.edit_message_text(
                            chat_id,
                            pod_msg_id,
                            f"{icon} <b>Đã tạo {type_title}:</b> <code>{stem}</code>\n"
                            f"🗣️ <b>Giọng đọc:</b> {reader_name}\n"
                            f"📊 <b>Tiến độ:</b> <code>{render_progress_bar(3, 3)}</code>\n"
                            f"📍 <i>Đã tạo thành công! Đang gửi file âm thanh...</i>",
                        )

                    if mp3_path and mp3_path.exists():
                        sub_desc = (
                            "Bản Nghe Masterclass (20–30 phút) — Phân tích chi tiết cơ chế, phản biện đa chiều và ma trận hành động"
                            if is_deep
                            else "Bản Nghe Tinh Cất (8–12 phút) — Nắm bắt nhanh luận đề và 2–3 bài học cốt lõi"
                        )
                        clean_stem = (
                            stem.replace("_shortform", "")
                            .replace("_short", "")
                            .replace("_vn", "")
                            .replace("_VN", "")
                            .replace(".vi", "")
                            .replace("_vi", "")
                        )
                        clean_title = clean_stem.replace("_", " ").strip()
                        engine_tag = "ZeroTTS" if engine_code == "zerotts" else ("VieNeu" if engine_code == "vieneu" else "Vbee")
                        is_long = (mp3_path.stat().st_size > 3 * 1024 * 1024) or is_deep
                        type_tag = "#podcast" if is_long else "#audio"
                        raw_tags = generate_topic_tags(clean_stem, output_type="")
                        kw_cands = [t for t in raw_tags if t.lower() not in ("#podcast", "#audio", "#short", "#dich", "#article", "#shortform", "#kienthuc")]
                        chosen_kw = kw_cands[0] if kw_cands else "#kienthuc"
                        if not chosen_kw.startswith("#"):
                            chosen_kw = f"#{chosen_kw}"
                        desc_line = f"📝 {type_tag} {chosen_kw}"

                        p_cap = (
                            f"{icon} <b>{clean_title}</b>\n"
                            f"🎧 {reader_name} ({engine_tag}, {podcast_speed}x)\n"
                            f"{desc_line}\n"
                            f"🚗 <i>Đã đồng bộ vào Apple Podcasts (CarPlay)</i>"
                        )
                        self.send_audio(
                            chat_id,
                            mp3_path,
                            caption=p_cap,
                            title=clean_title,
                            performer=f"{reader_name} • {'Chuyên Sâu' if is_deep else 'Tinh Gọn'} ({podcast_speed}x)",
                            thread_id=thread_id,
                        )
                        if not is_already_in_group:
                            self.send_audio(
                                target_group_chat,
                                mp3_path,
                                caption=p_cap,
                                title=clean_title,
                                performer=f"{reader_name} • {'Chuyên Sâu' if is_deep else 'Tinh Gọn'} ({podcast_speed}x)",
                                thread_id=target_group_topic,
                            )

                shutil.rmtree(workdir, ignore_errors=True)
            else:
                print(f"[Worker] Lỗi tóm tắt {file_name}:\n{res.stderr}\n{res.stdout}", file=sys.stderr)
                err_token = uuid.uuid4().hex[:8]
                job_to_save = job.copy()
                job_to_save["timestamp"] = time.time()
                self.pending_files[err_token] = job_to_save
                self._save_pending_files()
                
                log_file = LOGS_DIR / f"error_{err_token}.log"
                log_file.write_text(f"--- STDOUT & STDERR cho {file_name} ---\n{res.stdout}\n{res.stderr}", encoding="utf-8")
                
                action_map_rev = {
                    "summarize_book": "sum",
                    "sum_and_pod": "sum_pod",
                    "sum_and_pod_quick": "sum_pod",
                    "sum_and_pod_deep": "sum_deep_pod",
                    "both": "both",
                }
                action_code = job.get("action") or action_map_rev.get(job_type, "sum")
                
                keyboard = {"inline_keyboard": [
                    [
                        {"text": "🔄 Thử lại", "callback_data": f"{action_code}:{err_token}"},
                        {"text": "📋 Xem log", "callback_data": f"errlog:{err_token}"}
                    ]
                ]}

                self.send_message(
                    chat_id,
                    f"❌ Có lỗi xảy ra khi tóm tắt <b>{file_name}</b>.\n"
                    f"Vui lòng kiểm tra log hoặc thử lại.",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                    reply_markup=keyboard,
                )
                return

        # ── TRƯỜNG HỢP B: DỊCH TOÀN BỘ SÁCH HOẶC ĐỌC THỬ CHƯƠNG ĐẦU ──
        if job_type in ("translate_book", "preview_book", "both"):
            is_preview = (job_type == "preview_book")
            out_suffix = "_preview.vi.epub" if is_preview else ".vi.epub"
            output_epub = OUTPUT_DIR / f"{stem}{out_suffix}"
            workdir = PROCESSING_DIR / f"{stem}_trans.workdir"

            status_desc = "dịch thử 1 chương đầu" if is_preview else "dịch toàn bộ cuốn sách"
            est_time = "~1 – 2 phút" if is_preview else "~15 – 25 phút"

            status_msg = self.send_message(
                chat_id,
                f"📖 <b>Bắt đầu {status_desc}:</b> <code>{file_name}</code>\n"
                f"🧠 <b>Model:</b> <code>{active_model}</code>\n"
                f"📊 <b>Tiến độ:</b> <code>[░░░░░░░░░░] 0%</code>\n"
                f"📍 <i>Đang trích xuất thuật ngữ Glossary và chuẩn bị dịch...</i>",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
            status_msg_id = status_msg.get("result", {}).get("message_id") if status_msg else None

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

            sub_env = dict(os.environ)
            sub_env.update(load_env(ENV_PATH))
            res = self.run_command_with_progress(
                cmd=cmd,
                env=sub_env,
                task_label=status_desc,
                file_name=file_name,
                active_model=active_model,
                chat_id=chat_id,
                status_msg_id=status_msg_id,
                start_time=start_time,
            )
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

                trans_tags_str = format_tags(generate_topic_tags(stem, output_type="dich"))
                caption = (
                    f"📖 <b>{stem}</b>\n"
                    f"✨ <i>{'Bản dịch thử chương 1' if is_preview else 'Bản dịch tiếng Việt toàn văn chất lượng cao'}</i>\n"
                    f"{term_note}\n"
                    f"⏱️ <b>Thời gian xử lý:</b> {dur_m}m{dur_s}s\n"
                    f"📱 <i>Đã đóng gói chuẩn EPUB3!</i>{preview_note}"
                )
                if trans_tags_str:
                    caption += f"\n\n{trans_tags_str}"

                self.send_document(chat_id, output_epub, caption=caption, reply_to_message_id=msg_id, thread_id=thread_id)

                if not is_already_in_group and not is_preview:
                    group_caption = (
                        f"📖 <b>{stem}</b>\n"
                        f"✨ <i>Bản dịch tiếng Việt toàn văn chất lượng cao</i>\n"
                        f"⏱️ <b>Thời gian xử lý:</b> {dur_m}m{dur_s}s\n"
                        f"📱 <i>Đã đóng gói chuẩn EPUB3!</i>"
                    )
                    if trans_tags_str:
                        group_caption += f"\n\n{trans_tags_str}"
                    self.send_document(target_group_chat, output_epub, caption=group_caption, thread_id=target_group_topic)

                shutil.rmtree(workdir, ignore_errors=True)
            else:
                print(f"[Worker] Lỗi dịch {file_name}:\n{res.stderr}\n{res.stdout}", file=sys.stderr)
                err_token = uuid.uuid4().hex[:8]
                job_to_save = job.copy()
                job_to_save["timestamp"] = time.time()
                self.pending_files[err_token] = job_to_save
                self._save_pending_files()
                
                log_file = LOGS_DIR / f"error_{err_token}.log"
                log_file.write_text(f"--- STDOUT & STDERR cho {file_name} ---\n{res.stdout}\n{res.stderr}", encoding="utf-8")
                
                action_map_rev = {"translate_book": "trans", "preview_book": "prev", "both": "both"}
                action_code = job.get("action") or action_map_rev.get(job_type, "trans")
                
                keyboard = {"inline_keyboard": [
                    [
                        {"text": "🔄 Thử lại", "callback_data": f"{action_code}:{err_token}"},
                        {"text": "📋 Xem log", "callback_data": f"errlog:{err_token}"}
                    ]
                ]}

                self.send_message(
                    chat_id,
                    f"❌ Quá trình dịch <b>{file_name}</b> gặp lỗi.\n"
                    f"Vui lòng kiểm tra log hoặc thử lại.",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                    reply_markup=keyboard,
                )
                return

        # ── TRƯỜNG HỢP C: TẠO AUDIO PODCAST / ĐỌC TOÀN VĂN (VIENEU TTS) ──
        if job_type in ("podcast_book", "podcast_book_quick", "podcast_book_deep", "podcast_book_direct"):
            start_time = time.time()
            is_direct = (job_type == "podcast_book_direct")
            is_deep = (job_type == "podcast_book_deep")
            mode = "direct" if is_direct else ("deep" if is_deep else "quick")
            if is_direct:
                type_title = "Bản Đọc Nguyên Văn"
                icon = "🎙️"
                est_desc = "Đọc trọn vẹn 100% từng câu chữ • Chuẩn phòng thu 48kHz"
            elif is_deep:
                type_title = "Podcast Chuyên Sâu"
                icon = "🧠🎙️"
                est_desc = "20–30 phút • Mổ xẻ luận điểm & phản biện"
            else:
                type_title = "Podcast Tinh Gọn"
                icon = "⚡🎙️"
                est_desc = "8–12 phút • Đúc kết ý tưởng cốt lõi"

            engine_code = self.settings.get("engine", "zerotts")
            voice_code = self.settings.get("voice")
            target_voice = resolve_voice_code(voice_code, engine=engine_code)
            reader_name = get_reader_name(target_voice, engine=engine_code)

            eng_desc = "ZeroTTS AI 48kHz" if engine_code == "zerotts" else ("Giọng AI VieNeu v3 Turbo - 48kHz" if engine_code == "vieneu" else "Vbee Cloud API")
            step_desc = "Đang tổng hợp giọng nói đọc nguyên văn..." if is_direct else f"Đang biên soạn kịch bản {type_title} ({est_desc})..."
            pod_msg = self.send_message(
                chat_id,
                f"{icon} <b>Bắt đầu tạo {type_title}:</b> <code>{file_name}</code>\n"
                f"🗣️ <b>Người đọc:</b> Host {reader_name} ({eng_desc})\n"
                f"📊 <b>Tiến độ:</b> <code>{render_progress_bar(1, 3)}</code>\n"
                f"📍 <i>{step_desc}</i>",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
            pod_msg_id = pod_msg.get("result", {}).get("message_id") if pod_msg else None

            podcast_input = target_path
            clean_stem = (
                stem.replace("_shortform", "")
                .replace("_short", "")
                .replace("_vn", "")
                .replace("_VN", "")
                .replace(".vi", "")
                .replace("_vi", "")
            )
            clean_title = clean_stem.replace("_", " ").strip()
            short_candidate = OUTPUT_DIR / f"{clean_stem}_short.epub"
            if short_candidate.exists() and not stem.lower().endswith(("_short", "_shortform")) and not is_direct:
                podcast_input = short_candidate

            podcast_speed = float(self.settings.get("podcast_speed", 1.15))
            mp3_path = create_podcast_for_book(
                input_file=podcast_input,
                voice=target_voice,
                engine=engine_code,
                speed=podcast_speed,
                send_telegram=False,
                mode=mode,
            )
            dur = int(time.time() - start_time)
            dur_m, dur_s = dur // 60, dur % 60

            if pod_msg_id:
                self.edit_message_text(
                    chat_id,
                    pod_msg_id,
                    f"{icon} <b>Đã tạo {type_title}:</b> <code>{clean_title}</code>\n"
                    f"🗣️ <b>Giọng đọc:</b> {reader_name}\n"
                    f"📊 <b>Tiến độ:</b> <code>{render_progress_bar(3, 3)}</code>\n"
                    f"📍 <i>Đã tạo thành công! Đang gửi file âm thanh...</i>",
                )

            if mp3_path and mp3_path.exists():
                pod_tags_str = format_tags(generate_topic_tags(clean_stem, output_type="podcast"))
                if is_direct:
                    sub_desc = "Bản đọc âm thanh trọn vẹn toàn bộ nội dung bài viết"
                elif is_deep:
                    sub_desc = (
                        "Bản Nghe Masterclass (20–30 phút) — Phân tích chi tiết cơ chế, phản biện đa chiều và ma trận hành động"
                    )
                else:
                    sub_desc = "Bản Nghe Tinh Cất (8–12 phút) — Nắm bắt nhanh luận đề và 2–3 bài học cốt lõi"

                header_title = f"{icon} <b>{clean_title}</b>"
                audio_title = clean_title
                engine_tag = "ZeroTTS" if engine_code == "zerotts" else ("VieNeu" if engine_code == "vieneu" else "Vbee")

                is_long = (mp3_path.stat().st_size > 3 * 1024 * 1024) or is_deep
                type_tag = "#podcast" if is_long else "#audio"
                raw_tags = generate_topic_tags(clean_stem, output_type="")
                kw_cands = [t for t in raw_tags if t.lower() not in ("#podcast", "#audio", "#short", "#dich", "#article", "#shortform", "#kienthuc")]
                chosen_kw = kw_cands[0] if kw_cands else "#kienthuc"
                if not chosen_kw.startswith("#"):
                    chosen_kw = f"#{chosen_kw}"
                desc_line = f"📝 {type_tag} {chosen_kw}"

                caption = (
                    f"{header_title}\n"
                    f"🎧 {reader_name} ({engine_tag}, {podcast_speed}x)\n"
                    f"{desc_line}\n"
                    f"⏱️ <b>Thời gian xử lý:</b> {dur_m}m{dur_s}s\n"
                    f"🚗 <i>Đã đồng bộ vào Apple Podcasts (CarPlay)</i>"
                )
                self.send_audio(
                    chat_id,
                    mp3_path,
                    caption=caption,
                    title=audio_title,
                    performer=f"{reader_name} • {'Nguyên Văn' if is_direct else ('Chuyên Sâu' if is_deep else 'Tinh Gọn')} ({podcast_speed}x)",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                )

                # ── Gửi nút "🎬 Tạo Video Reel" cho phép xuất Short/Reel 9:16 từ chính tập Podcast này ──
                if not is_direct:
                    script_candidate = mp3_path.parent / f"{mp3_path.stem}_script.txt"
                    reel_token = uuid.uuid4().hex[:8]
                    self.pending_reels[reel_token] = {
                        "mp3_path": str(mp3_path),
                        "script_path": str(script_candidate) if script_candidate.exists() else "",
                        "title": clean_title,
                        "chat_id": chat_id,
                        "thread_id": thread_id,
                        "timestamp": time.time(),
                    }
                    self._save_pending_reels()
                    self.send_message(
                        chat_id,
                        f"🎬 <b>{clean_title}</b>\nMuốn xuất thêm bản Video Short/Reel 9:16 (có phụ đề động) từ tập Podcast này không?",
                        reply_to_message_id=msg_id,
                        thread_id=thread_id,
                        reply_markup={"inline_keyboard": [[
                            {"text": "🎬 Tạo Video Reel", "callback_data": f"make_reel:{reel_token}"}
                        ]]},
                    )

                if not is_already_in_group:
                    group_caption = (
                        f"{header_title}\n"
                        f"🎧 {reader_name} ({engine_tag}, {podcast_speed}x)\n"
                        f"{desc_line}\n"
                        f"⏱️ <b>Thời gian xử lý:</b> {dur_m}m{dur_s}s\n"
                        f"🚗 <i>Đã đồng bộ vào Apple Podcasts (CarPlay)</i>"
                    )
                    self.send_audio(
                        target_group_chat,
                        mp3_path,
                        caption=group_caption,
                        title=audio_title,
                        performer=f"{reader_name} • {'Nguyên Văn' if is_direct else ('Chuyên Sâu' if is_deep else 'Tinh Gọn')} ({podcast_speed}x)",
                        thread_id=target_group_topic,
                    )
            else:
                err_token = uuid.uuid4().hex[:8]
                job_to_save = job.copy()
                job_to_save["timestamp"] = time.time()
                self.pending_files[err_token] = job_to_save
                self._save_pending_files()

                action_map_rev = {
                    "podcast_book": "pod_quick",
                    "podcast_book_quick": "pod_quick",
                    "podcast_book_deep": "pod_deep",
                    "podcast_book_direct": "pod_direct",
                    "sum_and_pod": "sum_pod",
                    "sum_and_pod_quick": "sum_pod",
                    "sum_and_pod_deep": "sum_deep_pod",
                    "both": "both",
                }
                action_code = job.get("action") or action_map_rev.get(job_type, "pod_quick")
                
                keyboard = {"inline_keyboard": [
                    [
                        {"text": "🔄 Thử lại", "callback_data": f"{action_code}:{err_token}"}
                    ]
                ]}
                
                self.send_message(
                    chat_id,
                    f"❌ Quá trình tạo {type_title} cho <b>{file_name}</b> gặp lỗi.\n"
                    f"Vui lòng thử lại hoặc kiểm tra log hệ thống.",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                    reply_markup=keyboard,
                )
                return

        # Lưu bản gốc vào originals/ (chỉ khi file nằm trong INBOX_DIR)
        if target_path.exists() and target_path.parent == INBOX_DIR:
            dest_orig = ORIGINALS_DIR / target_path.name
            try:
                shutil.move(str(target_path), str(dest_orig))
            except Exception:
                pass

    # ── 7b. Worker xử lý Bài viết từ URL (Dịch & Tạo Audio) ──
    # ── 7c. Worker xử lý tạo Video Reel 9:16 (Remotion) từ 1 tập Podcast có sẵn ──
    def process_reel_job(self, job: dict[str, Any]) -> None:
        chat_id = job["chat_id"]
        thread_id = job.get("thread_id")
        msg_id = job.get("msg_id")
        title = job.get("title") or "Podcast Digest"
        mp3_path = Path(job["mp3_path"])
        script_path = job.get("script_path") or ""

        # KHÔNG render nếu thiếu script gốc (*_script.txt) — nếu để Whisper tự đoán chữ
        # mà không có ground-truth, phụ đề tiếng Việt sẽ sai chính tả (đã xác nhận qua test thực tế).
        if not script_path or not Path(script_path).exists():
            print(f"[Reel] Từ chối render: thiếu script gốc cho {mp3_path.name}", file=sys.stderr)
            self.send_message(
                chat_id,
                f"⚠️ <b>{title}</b>\nTập này chưa có file kịch bản gốc (script.txt) nên chưa thể tạo Video Reel với phụ đề chính xác. Vui lòng thử với tập Podcast khác.",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
            return

        EBOOK_SHORTFORM_ROOT = Path("/Users/mktmda/Projects/ebook-shortform")
        render_script = EBOOK_SHORTFORM_ROOT / "scripts" / "render_short_reel.py"
        venv_python = EBOOK_SHORTFORM_ROOT / ".venv" / "bin" / "python"

        job_uid = uuid.uuid4().hex[:8]
        out_file = EBOOK_SHORTFORM_ROOT / "output" / "reels" / f"{mp3_path.stem}_{job_uid}.mp4"

        cmd = [
            str(venv_python), str(render_script),
            "--audio", str(mp3_path),
            "--title", title,
            "--duration", "45",
            "--output", str(out_file),
        ]
        if script_path:
            cmd += ["--script", script_path]

        start_time = time.time()
        try:
            result = subprocess.run(
                cmd, cwd=str(EBOOK_SHORTFORM_ROOT),
                capture_output=True, text=True, timeout=600,
            )
        except Exception as e:
            print(f"[Reel] Ngoại lệ khi render video: {e}", file=sys.stderr)
            result = None

        dur = int(time.time() - start_time)

        if result is not None and result.returncode == 0 and out_file.exists():
            self.send_video(
                chat_id,
                out_file,
                caption=f"🎬 <b>{title}</b>\n⏱️ Đã tạo trong {dur}s\n📱 Video Reel 9:16 — sẵn sàng đăng TikTok/Reels/Shorts!",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
            try:
                out_file.unlink()
            except Exception:
                pass
        else:
            err_tail = (result.stderr[-500:] if result and result.stderr else "Không rõ nguyên nhân")
            print(f"[Reel] Lỗi render video cho {title}: {err_tail}", file=sys.stderr)
            self.send_message(
                chat_id,
                f"❌ Không tạo được Video Reel cho <b>{title}</b>. Vui lòng thử lại sau.",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )

    def process_article_job(self, job: dict[str, Any]) -> None:
        job_type = job.get("type", "article_translate_audio")
        url = job["url"]
        chat_id = job["chat_id"]
        thread_id = job.get("thread_id")
        msg_id = job.get("msg_id")
        sender_name = job.get("sender_name", "Bạn đọc")
        active_model = self.settings.get("model", "gemini-3.7-flash")

        needs_audio = "audio" in job_type or "podcast_deep" in job_type
        is_summary = "summarize" in job_type
        is_yt_podcast_deep = "yt_podcast_deep" in job_type

        # 1. Gửi tin nhắn khởi tạo tiến độ
        source_label = "video YouTube" if is_youtube_url(url) else "bài viết"
        seg_notice = ""
        if is_youtube_url(url):
            if job.get("chapter"):
                ch_title_desc = f": {job.get('chapter_title')}" if job.get("chapter_title") else ""
                seg_notice = f"\n🎯 <b>Phân đoạn:</b> Chương {job['chapter']}{ch_title_desc}"
            elif job.get("start_time") and job.get("end_time"):
                seg_notice = f"\n🎯 <b>Phân đoạn:</b> {job['start_time']} - {job['end_time']}"
            elif job.get("start_time"):
                seg_notice = f"\n🎯 <b>Phân đoạn:</b> Từ mốc {job['start_time']}"

        status_msg = self.send_message(
            chat_id,
            f"{'📺' if is_youtube_url(url) else '🌐'} <b>Bắt đầu xử lý {source_label}:</b> <code>{url}</code>{seg_notice}\n"
            f"📊 <b>Tiến độ:</b> <code>[░░░░░░░░░░] 10%</code>\n"
            f"📍 <i>Đang {'trích xuất phụ đề từ video' if is_youtube_url(url) else 'cào và trích xuất nội dung bài viết'}...</i>",
            reply_to_message_id=msg_id,
            thread_id=thread_id,
        )
        status_msg_id = status_msg.get("result", {}).get("message_id") if status_msg else None

        # 2. Cào bài viết hoặc trích xuất phụ đề YouTube
        is_yt = is_youtube_url(url)
        try:
            if is_yt:
                article = youtube_to_article(
                    url,
                    start_time=job.get("start_time"),
                    end_time=job.get("end_time"),
                    chapter=job.get("chapter"),
                )
            else:
                article = fetch_and_parse_article(url)
        except Exception as e:
            source_label = "phụ đề YouTube" if is_yt else "nội dung bài viết"
            err_text = f"❌ Không thể trích xuất {source_label} từ liên kết: {e}"
            if status_msg_id:
                self.edit_message_text(chat_id, status_msg_id, err_text)
            else:
                self.send_message(chat_id, err_text, reply_to_message_id=msg_id, thread_id=thread_id)
            return

        # Cập nhật trạng thái sang dịch thuật
        total_steps = 4 if is_yt_podcast_deep else (3 if needs_audio else 2)
        if status_msg_id:
            self.edit_message_text(
                chat_id,
                status_msg_id,
                f"{'📺' if is_yt else '📰'} <b>{article.title}</b> ({article.domain})\n"
                f"🧠 <b>Model:</b> <code>{active_model}</code>\n"
                f"📊 <b>Tiến độ:</b> <code>{render_progress_bar(1, total_steps)}</code>\n"
                f"📍 <i>Đang dịch {'transcript' if is_yt else 'bài viết'} sang tiếng Việt chuẩn xác...</i>",
            )

        # 3. Dịch bài viết
        llm = LLMClient(model=active_model)

        def on_translate_progress(detail: str, current_model: str) -> None:
            if status_msg_id:
                self.edit_message_text(
                    chat_id,
                    status_msg_id,
                    f"{'📺' if is_yt else '📰'} <b>{article.title}</b> ({article.domain})\n"
                    f"🧠 <b>Model:</b> <code>{current_model}</code>\n"
                    f"📊 <b>Tiến độ:</b> <code>{render_progress_bar(1, total_steps)}</code>\n"
                    f"📍 <i>{detail}</i>",
                )

        try:
            translated = translate_article(article, llm=llm, on_progress=on_translate_progress)
        except Exception as e:
            err_text = f"❌ Lỗi khi dịch '{article.title}': {e}"
            if status_msg_id:
                self.edit_message_text(chat_id, status_msg_id, err_text)
            else:
                self.send_message(chat_id, err_text, reply_to_message_id=msg_id, thread_id=thread_id)
            return

        # 4. Tạo Audio nếu được yêu cầu
        audio_path = None
        if needs_audio:
            engine_code = self.settings.get("engine", "zerotts")
            voice_code = self.settings.get("voice")
            target_voice = resolve_voice_code(voice_code, engine=engine_code)
            reader_name = get_reader_name(target_voice, engine=engine_code)
            podcast_speed = float(self.settings.get("podcast_speed", 1.15))

            if is_yt_podcast_deep:
                # Mode Podcast Chuyên Sâu: AI biên kịch lại nội dung thành kịch bản podcast
                step_label = f"{render_progress_bar(2, total_steps)}"
                if status_msg_id:
                    self.edit_message_text(
                        chat_id,
                        status_msg_id,
                        f"📺 <b>{translated.title_vi}</b>\n"
                        f"🧠 <b>AI đang biên soạn kịch bản Podcast Chuyên Sâu...</b>\n"
                        f"📊 <b>Tiến độ:</b> <code>{step_label}</code>\n"
                        f"📍 <i>Chuyển đổi nội dung thành kịch bản podcast deep-dive...</i>",
                    )

                from generate_podcast import generate_podcast_script, call_vieneu_tts, PODCASTS_DIR
                try:
                    podcast_script = generate_podcast_script(
                        text=translated.content_vi,
                        book_title=translated.title_vi,
                        voice_code=target_voice,
                        mode="deep",
                    )
                except Exception as e:
                    err_text = f"❌ Lỗi khi biên soạn kịch bản Podcast: {e}"
                    if status_msg_id:
                        self.edit_message_text(chat_id, status_msg_id, err_text)
                    else:
                        self.send_message(chat_id, err_text, reply_to_message_id=msg_id, thread_id=thread_id)
                    return

                # Lưu kịch bản podcast theo safe_stem của chapter
                PODCASTS_DIR.mkdir(parents=True, exist_ok=True)
                script_stem = article.safe_stem
                script_path = PODCASTS_DIR / f"{script_stem}_script.txt"
                script_path.write_text(podcast_script, encoding="utf-8")
                print(f"📝 Đã lưu kịch bản Podcast YT tại: {script_path}")

                # TTS kịch bản podcast
                step_label = f"{render_progress_bar(3, total_steps)}"
                if status_msg_id:
                    self.edit_message_text(
                        chat_id,
                        status_msg_id,
                        f"📺 <b>{translated.title_vi}</b>\n"
                        f"🗣️ <b>Giọng đọc:</b> {reader_name}\n"
                        f"📊 <b>Tiến độ:</b> <code>{step_label}</code>\n"
                        f"📍 <i>Đang tổng hợp Audio Podcast Chuyên Sâu...</i>",
                    )

                audio_path = PODCASTS_DIR / f"{script_stem}.mp3"
                engine_code = self.settings.get("engine", "zerotts")
                success = call_tts(
                    text=podcast_script,
                    output_path=audio_path,
                    voice=target_voice,
                    engine=engine_code,
                    speed=podcast_speed,
                    fallback=True,
                )
                if not (success and audio_path.exists()):
                    audio_path = None
                    print(f"❌ Không thể tạo Audio Podcast cho {translated.title_vi}", file=sys.stderr)
            else:
                # Mode đọc toàn văn (Direct Narration)
                if status_msg_id:
                    self.edit_message_text(
                        chat_id,
                        status_msg_id,
                        f"{'📺' if is_yt else '📰'} <b>{translated.title_vi}</b>\n"
                        f"🗣️ <b>Giọng đọc:</b> {reader_name}\n"
                        f"📊 <b>Tiến độ:</b> <code>{render_progress_bar(2, 3)}</code>\n"
                        f"📍 <i>Đang tổng hợp Audio bản dịch...</i>",
                    )

                engine_code = self.settings.get("engine", "zerotts")
                audio_path = generate_article_audio(
                    translated=translated,
                    voice=target_voice,
                    speed=podcast_speed,
                    engine=engine_code,
                )

        # 4.1 Tự động đồng bộ vào kho Podcast, lấy thumbnail YouTube làm Episode Cover và cập nhật Feed
        yt_cover_path = None
        if audio_path and audio_path.exists():
            try:
                import shutil
                from ebook_translator.core.podcast_rss import update_podcast_feed

                PODCASTS_DIR.mkdir(parents=True, exist_ok=True)
                # Nếu file đang nằm ở output/articles, sao chép sang output/podcasts để phục vụ Apple Podcasts
                if audio_path.parent.resolve() != PODCASTS_DIR.resolve():
                    pod_dest = PODCASTS_DIR / audio_path.name
                    try:
                        shutil.copy2(audio_path, pod_dest)
                        audio_path = pod_dest
                    except Exception:
                        pass

                if is_yt:
                    from ebook_translator.core.youtube import generate_youtube_podcast_cover
                    from scripts.update_podcast_episode_covers import embed_cover_to_mp3

                    ep_covers_dir = PODCASTS_DIR / "episode_covers"
                    ep_covers_dir.mkdir(parents=True, exist_ok=True)
                    clean_stem = (
                        audio_path.stem
                        .replace("_podcast_tinh_gon", "")
                        .replace("_podcast_chuyen_sau", "")
                        .replace("_podcast", "")
                        .replace("_audio", "")
                        .replace("_short_short", "")
                        .replace("_short", "")
                    )
                    yt_cover_path = ep_covers_dir / f"{clean_stem}.jpg"
                    if not yt_cover_path.exists():
                        generate_youtube_podcast_cover(url, yt_cover_path)

                    if yt_cover_path.exists():
                        embed_cover_to_mp3(audio_path, yt_cover_path)

                # Luôn cập nhật Private RSS Feed cho Apple Podcasts & CarPlay
                update_podcast_feed()
            except Exception as e:
                print(f"⚠️ Lỗi xử lý đồng bộ podcast / cover: {e}", file=sys.stderr)

        # 5. Cập nhật thông báo hoàn tất
        if status_msg_id:
            self.edit_message_text(
                chat_id,
                status_msg_id,
                f"🎉 <b>Đã xử lý hoàn tất bài viết!</b>\n"
                f"📰 <b>{translated.title_vi}</b>\n"
                f"📍 <i>Đang gửi file âm thanh...</i>",
            )

        # 6. Gửi kết quả về Telegram:
        article_tags_str = format_tags(getattr(translated, "tags", [])) or format_tags(
            generate_topic_tags(translated.title_vi, translated.summary_vi, article.domain, output_type="article")
        )

        # A. Bản dịch văn bản — đã tắt, chỉ gửi audio
        # (giữ article_tags_str để dùng cho audio_tags_str bên dưới)

        # B. File Audio MP3
        if audio_path and audio_path.exists():
            engine_code = self.settings.get("engine", "zerotts")
            target_voice = resolve_voice_code(self.settings.get("voice"), engine=engine_code)
            reader_name = get_reader_name(target_voice, engine=engine_code)
            podcast_speed = float(self.settings.get("podcast_speed", 1.15))
            engine_label = "ZeroTTS" if engine_code == "zerotts" else ("VieNeu" if engine_code == "vieneu" else "Vbee")
            source_icon = "🧠🎙️" if is_yt_podcast_deep else ("📺" if is_yt else "🌐")
            audio_type = "Podcast Phân Tích (AI đúc kết)" if is_yt_podcast_deep else ("Bản Đọc" if is_yt else "Bản Đọc Nguyên Văn")
            # Đánh giá file audio có dài không (>3MB tương đương ~3-5 phút, hoặc là podcast deep)
            is_long = is_yt_podcast_deep or (audio_path.stat().st_size > 3 * 1024 * 1024)
            type_tag = "#podcast" if is_long else "#audio"
            
            # 1 hashtag keyword theo nội dung/tiêu đề
            raw_tags = generate_topic_tags(translated.title_vi, translated.summary_vi, article.domain, output_type="")
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

            if article.is_chapter or article.chapter_title:
                display_title = (article.chapter_title or translated.title_vi).strip()
            else:
                display_title = translated.title_vi.strip()

            if getattr(article, "video_title", None):
                caption_lines = [
                    f"{source_icon} <b>{display_title}</b>",
                    f"🎬 <b>Từ video:</b> <i>{article.video_title}</i>",
                    f"👤: {article.author} • <b>Nguồn:</b> {article.domain}",
                    f"🎧 {reader_name} ({engine_label}, {podcast_speed}x)",
                    desc_line,
                ]
            else:
                caption_lines = [
                    f"{source_icon} <b>{display_title}</b>",
                    f"🔗: {article.author} • <b>Nguồn:</b> {article.domain}",
                    f"🎧 {reader_name} ({engine_label}, {podcast_speed}x)",
                    desc_line,
                ]
            caption = "\n".join(caption_lines)
            audio_title = display_title
            self.send_audio(
                chat_id,
                audio_path,
                caption=caption,
                title=audio_title,
                performer=f"{article.author} • {reader_name}",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )

        # C. Đồng bộ sang nhóm/topic nếu người dùng chat riêng
        target_group_chat = self.default_chat_id or -1003879100454
        target_group_topic = self.default_topic_id or "365"
        is_already_in_group = (
            chat_id == target_group_chat
            and str(thread_id or "") == str(target_group_topic)
        )
        if not is_already_in_group:
            # Chỉ đồng bộ audio sang nhóm (không gửi markdown)
            if audio_path and audio_path.exists():
                if getattr(article, "video_title", None):
                    sync_lines = [
                        f"{source_icon} <b>{display_title}</b>",
                        f"🎬 <b>Từ video:</b> <i>{article.video_title}</i>",
                        f"👤: {article.author} • <b>Nguồn:</b> {article.domain}",
                        f"🎧 {reader_name} ({engine_label}, {podcast_speed}x)",
                        desc_line,
                    ]
                else:
                    sync_lines = [
                        f"{source_icon} <b>{display_title}</b>",
                        f"🔗: {article.author} • <b>Nguồn:</b> {article.domain}",
                        f"🎧 {reader_name} ({engine_label}, {podcast_speed}x)",
                        desc_line,
                    ]
                sync_audio_cap = "\n".join(sync_lines)
                self.send_audio(
                    target_group_chat,
                    audio_path,
                    caption=sync_audio_cap,
                    title=audio_title,
                    performer=f"{article.author} • {reader_name}",
                    thread_id=target_group_topic,
                )

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
                        is_short = "_short" in fname.lower() or "_shortform" in fname.lower()
                        type_tag = "⚡ [Tóm Tắt Shortform]" if is_short else "📖 [Bản Dịch Toàn Văn]"
                        clean_stem = (
                            file_path.stem.replace("_shortform", "")
                            .replace("_short", "")
                            .replace(".vi", "")
                            .replace("_vi", "")
                            .replace("_vn", "")
                            .replace("_VN", "")
                            .replace(".vn", "")
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

    # ── 8. Vòng lặp phát Bản tin Gợi ý Sách Hàng Tuần ──
    def weekly_recommendation_loop(self) -> None:
        print("📅 Khởi động Scheduler kiểm tra bản tin Gợi ý Sách Tuần...")
        while self.running:
            try:
                rec_cfg = self.settings.get("weekly_recommendation", {})
                enabled = rec_cfg.get("enabled", True)
                target_day = rec_cfg.get("day", 0)  # 0: Thứ Hai
                target_hour = rec_cfg.get("hour", 9)  # 09:00 AM
                last_sent = rec_cfg.get("last_sent_week", "")

                now = datetime.datetime.now()
                current_week = f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"

                # Kiểm tra đúng Thứ Hai, từ 09:00 trở đi và tuần này chưa gửi
                if enabled and now.weekday() == target_day and now.hour >= target_hour:
                    if current_week != last_sent:
                        print(f"[WeeklyRec] 🚀 Bắt đầu phát Bản tin Sách Hay cho tuần {current_week}...")
                        issue = book_recommender.get_weekly_recommendations(category="all", force_refresh=False)
                        msg_text = book_recommender.format_telegram_digest(issue)
                        kb = build_recommendations_keyboard(issue)

                        resp = self.send_message(
                            self.default_chat_id,
                            msg_text,
                            thread_id=self.default_topic_id,
                            reply_markup=kb,
                        )
                        if resp and resp.get("ok"):
                            rec_cfg["last_sent_week"] = current_week
                            self.settings["weekly_recommendation"] = rec_cfg
                            self._save_settings()
                            print(f"[WeeklyRec] ✅ Đã gửi thành công Bản tin Sách Hay tuần {current_week}!")

            except Exception as e:
                print(f"[WeeklyRec] Lỗi vòng lặp Scheduler: {e}", file=sys.stderr)

            # Nghỉ 300 giây (5 phút) trước lần kiểm tra kế tiếp
            time.sleep(300)

    # ── 9. Đăng ký menu lệnh với Telegram API ──
    def register_bot_commands(self) -> None:
        commands = [
            {"command": "menu", "description": "📖 Menu & Hướng dẫn sử dụng bot"},
            {"command": "article", "description": "🌐 Dịch bài viết từ link & Tạo Audio"},
            {"command": "podcast", "description": "🎙️ Tạo Audio Podcast tóm tắt sách (ZeroTTS 48kHz)"},
            {"command": "rss", "description": "🚗 Link Private RSS cho Apple Podcasts & CarPlay"},
            {"command": "voice", "description": "🗣️ Chọn giọng đọc AI cho Podcast"},
            {"command": "engine", "description": "⚙️ Chọn công nghệ TTS (ZeroTTS, VieNeu, Vbee)"},
            {"command": "speed", "description": "⚡ Cài đặt tốc độ đọc Podcast (1.1x, 1.15x, 1.2x)"},
            {"command": "recommend", "description": "🌟 Radar sách mới & High-rating tuần này"},
            {"command": "wishlist", "description": "📌 Danh sách sách muốn đọc đã lưu"},
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

        rec_scheduler = threading.Thread(target=self.weekly_recommendation_loop, daemon=True)
        rec_scheduler.start()

        # Khởi động máy chủ Private Podcast RSS Server cho Apple Podcasts & CarPlay
        try:
            from podcast_server import start_server_in_background
            start_server_in_background(port=8000)
            print("📡 Private Podcast RSS Server đã khởi động ngầm trên cổng 8000")
        except Exception as e:
            print(f"⚠️ Không thể khởi động Podcast RSS Server: {e}")

        print("👂 Đang lắng nghe tài liệu, lệnh Dịch, Tóm tắt và Gợi ý Sách từ Telegram...")

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
