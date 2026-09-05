#!/usr/bin/env python3
"""telegram_inbound_bot.py — Dịch vụ lắng nghe và tiếp nhận ebook từ Telegram.

Chức năng:
1. Long-polling lắng nghe tin nhắn từ bot Telegram.
2. Gửi file .epub hoặc .pdf từ điện thoại để tự động tóm tắt Shortform.
3. HỖ TRỢ TẠO AUDIO PODCAST QUA TELEGRAM:
   - Gửi sách kèm caption 'audio' hoặc 'podcast' để tự động tạo cả EPUB lẫn Podcast MP3.
   - Dùng lệnh /podcast (hoặc /audio) để yêu cầu tạo podcast cho sách bất kỳ.
   - Reply vào file sách bất kỳ trong chat và gõ /podcast để render audio ngay.
4. Tự động gửi file hoàn thành tới cả nhóm chính: https://t.me/c/3879100454/365.
5. Theo dõi thư mục output/: Tự động thông báo khi có file mới được tạo từ máy tính.
6. Chạy ngầm 24/7 dưới dạng macOS LaunchAgent daemon.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import requests

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))
sys.path.insert(0, str(PROJECT_DIR / "src"))

ENV_PATH = PROJECT_DIR / ".env"
INBOX_DIR = PROJECT_DIR / "inbox"
OUTPUT_DIR = PROJECT_DIR / "output"
LOGS_DIR = PROJECT_DIR / "logs"
AUTO_PIPELINE = PROJECT_DIR / "auto-pipeline.sh"

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


class TelegramInboundBot:
    def __init__(self):
        self.env = load_env(ENV_PATH)
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN") or self.env.get("TELEGRAM_BOT_TOKEN")
        if not self.token:
            raise ValueError("Không tìm thấy TELEGRAM_BOT_TOKEN trong .env hoặc biến môi trường.")

        # Cấu hình chat ID mặc định & whitelist (Nhóm chính: https://t.me/c/3879100454/365)
        raw_chat = os.environ.get("TELEGRAM_CHAT_ID") or self.env.get("TELEGRAM_CHAT_ID", "-1003879100454")
        self.default_chat_id = int(raw_chat) if raw_chat.lstrip("-").isdigit() else -1003879100454
        self.default_topic_id = self.env.get("TELEGRAM_TOPIC_ID", "365")

        # Whitelist người dùng (phòng ngừa người lạ spam bot)
        allowed_str = self.env.get("TELEGRAM_ALLOWED_USERS", "1563046373")
        self.allowed_users = {
            int(u.strip()) for u in allowed_str.split(",") if u.strip().isdigit()
        }

        self.api_url = f"{TELEGRAM_API_BASE}/bot{self.token}"
        self.file_api_url = f"{TELEGRAM_API_BASE}/file/bot{self.token}"

        self.job_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.running = True
        self.offset = 0

        # Quản lý theo dõi file output được tạo từ máy tính
        self.sent_files_path = LOGS_DIR / ".sent_files.json"
        self.bot_processed_files: dict[str, float] = {}
        self.sent_files: dict[str, float] = self._load_sent_files()

    def _load_sent_files(self) -> dict[str, float]:
        """Tải danh sách các file đã gửi để tránh gửi trùng khi khởi động lại."""
        if self.sent_files_path.exists():
            try:
                data = json.loads(self.sent_files_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass

        initial_map: dict[str, float] = {}
        if OUTPUT_DIR.exists():
            for p in OUTPUT_DIR.glob("*.epub"):
                if not p.name.startswith("."):
                    initial_map[p.name] = p.stat().st_mtime
            podcasts_dir = OUTPUT_DIR / "podcasts"
            if podcasts_dir.exists():
                for p in podcasts_dir.glob("*.mp3"):
                    if not p.name.startswith("."):
                        initial_map[p.name] = p.stat().st_mtime

        self._save_sent_files(initial_map)
        return initial_map

    def _save_sent_files(self, data: dict[str, float]) -> None:
        try:
            self.sent_files_path.parent.mkdir(parents=True, exist_ok=True)
            self.sent_files_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[Watcher] Không thể lưu sent_files: {e}", file=sys.stderr)

    def is_authorized(self, from_user: dict[str, Any], chat: dict[str, Any]) -> bool:
        """Kiểm tra người dùng có quyền gửi sách tới bot không."""
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
    ) -> dict[str, Any] | None:
        """Gửi tin nhắn phản hồi qua Telegram."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        if thread_id:
            payload["message_thread_id"] = thread_id

        try:
            r = requests.post(f"{self.api_url}/sendMessage", json=payload, timeout=15)
            return r.json()
        except Exception as e:
            print(f"[Telegram] Lỗi sendMessage: {e}", file=sys.stderr)
            return None

    def send_document(
        self,
        chat_id: int | str,
        file_path: Path,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
        thread_id: int | str | None = None,
    ) -> bool:
        """Gửi file tài liệu về Telegram."""
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
                    timeout=120,
                )
            res = r.json()
            return bool(res.get("ok"))
        except Exception as e:
            print(f"[Telegram] Lỗi sendDocument: {e}", file=sys.stderr)
            return False

    def download_file(self, file_id: str, dest_path: Path) -> bool:
        """Tải file tài liệu từ Telegram về đĩa."""
        try:
            r = requests.get(f"{self.api_url}/getFile", params={"file_id": file_id}, timeout=20)
            res = r.json()
            if not res.get("ok"):
                print(f"[Telegram] getFile thất bại: {res}", file=sys.stderr)
                return False

            rel_path = res["result"]["file_path"]
            download_url = f"{self.file_api_url}/{rel_path}"

            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with requests.get(download_url, stream=True, timeout=180) as stream_res:
                stream_res.raise_for_status()
                with open(dest_path, "wb") as f:
                    for chunk in stream_res.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
            return True
        except Exception as e:
            print(f"[Telegram] Lỗi download file: {e}", file=sys.stderr)
            return False

    def handle_message(self, message: dict[str, Any]) -> None:
        """Xử lý tin nhắn và lệnh từ người dùng."""
        chat = message.get("chat", {})
        from_user = message.get("from", {})
        chat_id = chat.get("id")
        msg_id = message.get("message_id")
        thread_id = message.get("message_thread_id")

        if not chat_id:
            return

        # Lấy tên người gửi
        first_name = from_user.get("first_name", "")
        last_name = from_user.get("last_name", "")
        sender_name = f"{first_name} {last_name}".strip()
        if not sender_name:
            sender_name = from_user.get("username") or "Thành viên"

        text = (message.get("text") or "").strip()

        # ── 1. Xử lý các lệnh văn bản ──
        if text.startswith("/"):
            parts = text.split(maxsplit=1)
            cmd = parts[0].split("@")[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""

            if cmd in ("/start", "/help"):
                help_text = (
                    "📚 <b>Chào mừng đến với Ebook Shortform Bot!</b>\n\n"
                    "<b>Cách sử dụng:</b>\n"
                    "1. 📥 <b>Tóm tắt sách:</b> Gửi file <code>.epub</code> hoặc <code>.pdf</code> vào đây.\n"
                    "2. 🎙️ <b>Tạo Audio Podcast:</b>\n"
                    "   • Gửi sách kèm ghi chú: <i>'tạo audio'</i> hoặc <i>'podcast'</i> (ví dụ: <i>'podcast giọng nam'</i>)\n"
                    "   • Hoặc Reply vào file sách và gõ: <code>/podcast</code> hoặc <code>/podcast [giọng]</code>\n"
                    "   • Hoặc gõ: <code>/podcast &lt;tên sách&gt; [giọng]</code>\n"
                    "3. 📢 Mọi kết quả đều được đồng bộ tự động tới nhóm: https://t.me/c/3879100454/365\n\n"
                    "<i>Lệnh hỗ trợ:</i>\n"
                    "/voice — Xem danh sách giọng đọc Vbee và cách chọn giọng\n"
                    "/podcast — Danh sách sách có thể tạo audio\n"
                    "/status — Kiểm tra hàng đợi và trạng thái bot"
                )
                self.send_message(chat_id, help_text, reply_to_message_id=msg_id, thread_id=thread_id)
                return

            elif cmd in ("/voice", "/voices"):
                from generate_podcast import resolve_voice_code, POPULAR_VOICES
                current_voice = resolve_voice_code()
                curr_desc = POPULAR_VOICES.get(current_voice, current_voice)
                voice_lines = []
                for code, name in POPULAR_VOICES.items():
                    active = " <b>[Đang chọn]</b>" if code == current_voice else ""
                    # Lấy tên ngắn gọn
                    short_alias = code.split("_")[2]
                    voice_lines.append(f"• <code>{short_alias}</code>: {name}{active}")

                v_text = (
                    f"🎙️ <b>Cấu hình Giọng đọc Audio Podcast (Vbee AIVoice):</b>\n\n"
                    f"🎯 <b>Giọng mặc định hiện tại:</b>\n👉 <b>{curr_desc}</b>\n\n"
                    f"🎧 <b>Danh sách giọng đọc tiêu biểu:</b>\n"
                    + "\n".join(voice_lines) +
                    f"\n\n💡 <b>Cách chọn giọng:</b>\n"
                    f"1. <b>Chọn cố định:</b> Thêm vào file <code>.env</code>:\n"
                    f"   <code>VBEE_VOICE=\"hn_male_thanhlong_talk_48k-fhg\"</code>\n"
                    f"2. <b>Chọn linh hoạt khi ra lệnh:</b>\n"
                    f"   • Reply file sách: <code>/podcast lantrinh</code> hoặc <code>/podcast thanhlong</code>\n"
                    f"   • Gõ theo tên: <code>/podcast remote lantrinh</code>"
                )
                self.send_message(chat_id, v_text, reply_to_message_id=msg_id, thread_id=thread_id)
                return

            elif cmd == "/status":
                from generate_podcast import resolve_voice_code, POPULAR_VOICES
                q_size = self.job_queue.qsize()
                curr_voice = resolve_voice_code()
                voice_name = POPULAR_VOICES.get(curr_voice, curr_voice)
                status_text = (
                    f"🟢 <b>Bot đang hoạt động bình thường</b>\n"
                    f"• Hàng đợi xử lý: <b>{q_size}</b> tác vụ\n"
                    f"• Thư mục output: <code>{OUTPUT_DIR}</code>\n"
                    f"• Nhóm đích đồng bộ: <code>{self.default_chat_id}</code> (topic {self.default_topic_id})\n"
                    f"• Giọng đọc Audio Podcast: <b>{voice_name}</b>\n"
                    f"  <i>(Gõ /voice để xem tất cả giọng đọc)</i>"
                )
                self.send_message(chat_id, status_text, reply_to_message_id=msg_id, thread_id=thread_id)
                return

            elif cmd in ("/podcast", "/audio"):
                if not self.is_authorized(from_user, chat):
                    self.send_message(chat_id, "🔒 Bạn chưa được cấp quyền dùng bot.", reply_to_message_id=msg_id, thread_id=thread_id)
                    return

                from generate_podcast import resolve_voice_code, POPULAR_VOICES, VOICE_ALIASES

                # TH 1: Người dùng REPLY vào một tin nhắn có file EPUB
                reply_msg = message.get("reply_to_message")
                if reply_msg and reply_msg.get("document"):
                    doc = reply_msg["document"]
                    doc_name = doc.get("file_name", "")
                    if doc_name.lower().endswith(".epub"):
                        chosen_voice = arg.strip() if arg else None
                        target_code = resolve_voice_code(chosen_voice)
                        voice_desc = POPULAR_VOICES.get(target_code, target_code)
                        self.send_message(
                            chat_id,
                            f"🎙️ Đã nhận yêu cầu tạo Podcast cho <b>{doc_name}</b>!\n"
                            f"🗣️ Giọng đọc: <b>{voice_desc}</b>\n⏳ Đang đưa vào hàng đợi xử lý...",
                            reply_to_message_id=msg_id,
                            thread_id=thread_id,
                        )
                        self.job_queue.put({
                            "type": "podcast_from_file_id",
                            "file_id": doc.get("file_id"),
                            "file_name": doc_name,
                            "chat_id": chat_id,
                            "thread_id": thread_id,
                            "msg_id": msg_id,
                            "sender_name": sender_name,
                            "voice": chosen_voice,
                        })
                        return

                # TH 2: Người dùng gõ /podcast <tên sách> [giọng]
                if arg:
                    words = arg.strip().split()
                    chosen_voice = None
                    if len(words) > 1 and words[-1].lower() in VOICE_ALIASES:
                        chosen_voice = words[-1].lower()
                        book_query = " ".join(words[:-1])
                    else:
                        book_query = arg.strip()

                    # Tìm file .epub trong output/
                    matches = [
                        f for f in OUTPUT_DIR.glob("*.epub")
                        if book_query.lower() in f.stem.lower() and not f.name.startswith(".")
                    ]
                    if matches:
                        target_file = matches[0]
                        target_code = resolve_voice_code(chosen_voice)
                        voice_desc = POPULAR_VOICES.get(target_code, target_code)
                        self.send_message(
                            chat_id,
                            f"🎙️ Đã tìm thấy: <b>{target_file.name}</b>!\n"
                            f"🗣️ Giọng đọc: <b>{voice_desc}</b>\n⏳ Bắt đầu biên soạn kịch bản và tạo Podcast...",
                            reply_to_message_id=msg_id,
                            thread_id=thread_id,
                        )
                        self.job_queue.put({
                            "type": "podcast_existing_epub",
                            "target_path": str(target_file),
                            "chat_id": chat_id,
                            "thread_id": thread_id,
                            "msg_id": msg_id,
                            "sender_name": sender_name,
                            "voice": chosen_voice,
                        })
                        return
                    else:
                        self.send_message(
                            chat_id,
                            f"🔍 Không tìm thấy cuốn sách nào khớp với từ khóa <i>'{book_query}'</i> trong thư viện.",
                            reply_to_message_id=msg_id,
                            thread_id=thread_id,
                        )
                        return

                # TH 3: Người dùng chỉ gõ /podcast (hiển thị danh sách sách khả dụng)
                epubs = [f for f in OUTPUT_DIR.glob("*.epub") if not f.name.startswith(".")]
                if epubs:
                    book_list = "\n".join([f"• <code>/podcast {f.stem.split('_')[0]}</code> — {f.stem}" for f in epubs[:8]])
                    msg = (
                        "🎙️ <b>Tạo Audio Podcast Tóm Tắt Sách</b>\n\n"
                        "Chọn một cuốn sách có sẵn trong thư viện để tạo Podcast:\n"
                        f"{book_list}\n\n"
                        "<i>Mẹo: Bạn có thể Reply file sách kèm tên giọng, ví dụ: <code>/podcast lantrinh</code>!</i>\n"
                        "<i>Gõ <code>/voice</code> để xem tất cả giọng đọc có sẵn.</i>"
                    )
                else:
                    msg = "📚 Thư viện hiện chưa có file sách tóm tắt nào. Hãy gửi một file .epub hoặc .pdf vào đây trước nhé!"
                self.send_message(chat_id, msg, reply_to_message_id=msg_id, thread_id=thread_id)
                return

        # ── 2. Xử lý file tài liệu đính kèm (Document) ──
        document = message.get("document")
        if not document:
            return

        file_name = document.get("file_name", "unknown")
        file_id = document.get("file_id")
        file_size = document.get("file_size", 0)
        ext = Path(file_name).suffix.lower()

        # Kiểm tra đuôi file
        if ext not in (".epub", ".pdf"):
            self.send_message(
                chat_id,
                f"⚠️ Định dạng <code>{ext}</code> chưa được hỗ trợ. Vui lòng gửi file <b>.epub</b> hoặc <b>.pdf</b>.",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
            return

        # Kiểm tra quyền người gửi
        if not self.is_authorized(from_user, chat):
            self.send_message(
                chat_id,
                "🔒 Rất tiếc, bạn chưa có quyền sử dụng bot này. Vui lòng liên hệ admin để được cấp quyền.",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
            return

        # Giới hạn kích thước file tải qua Bot API của Telegram (tối đa 20 MB)
        if file_size > 20 * 1024 * 1024:
            size_mb = file_size / (1024 * 1024)
            self.send_message(
                chat_id,
                f"❌ File <b>{file_name}</b> quá lớn ({size_mb:.1f} MB).\n"
                f"Telegram Bot API chỉ hỗ trợ bot tải trực tiếp file dưới 20 MB. "
                f"Với file lớn hơn, vui lòng copy trực tiếp vào thư mục <code>inbox/</code> trên máy Mac.",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
            return

        size_str = (
            f"{file_size / (1024 * 1024):.1f} MB"
            if file_size > 1024 * 1024
            else f"{file_size / 1024:.0f} KB"
        )

        # Kiểm tra người dùng có muốn tạo luôn Audio Podcast không
        caption_lower = (message.get("caption") or "").lower()
        with_podcast = any(w in caption_lower for w in ["podcast", "audio", "nghe", "voice", "vbee"])
        chosen_voice = None
        if with_podcast:
            from generate_podcast import VOICE_ALIASES, resolve_voice_code, POPULAR_VOICES
            for alias in VOICE_ALIASES:
                if alias in caption_lower:
                    chosen_voice = alias
                    break
            v_desc = POPULAR_VOICES.get(resolve_voice_code(chosen_voice), resolve_voice_code(chosen_voice))
            podcast_note = f"\n🎙️ <i>(Kèm yêu cầu tạo Audio Podcast - Giọng: <b>{v_desc}</b>)</i>"
        else:
            podcast_note = ""

        ack_msg = (
            f"📥 <b>Đã nhận file:</b> <code>{file_name}</code> ({size_str}){podcast_note}\n"
            f"⏳ Đang tải về và đưa vào hàng đợi tóm tắt Shortform..."
        )
        self.send_message(chat_id, ack_msg, reply_to_message_id=msg_id, thread_id=thread_id)

        # Đưa vào queue xử lý ngầm
        job = {
            "type": "summarize_book",
            "file_id": file_id,
            "file_name": file_name,
            "chat_id": chat_id,
            "thread_id": thread_id,
            "msg_id": msg_id,
            "sender_name": sender_name,
            "with_podcast": with_podcast,
            "voice": chosen_voice,
        }
        self.job_queue.put(job)

    def worker_loop(self) -> None:
        """Worker chạy ngầm xử lý từng cuốn sách trong hàng đợi."""
        while self.running:
            try:
                job = self.job_queue.get(timeout=2)
            except queue.Empty:
                continue

            try:
                self.process_job(job)
            except Exception as e:
                print(f"[Worker] Lỗi xử lý job: {e}", file=sys.stderr)
            finally:
                self.job_queue.task_done()

    def process_job(self, job: dict[str, Any]) -> None:
        job_type = job.get("type", "summarize_book")
        chat_id = job["chat_id"]
        thread_id = job["thread_id"]
        msg_id = job["msg_id"]
        sender_name = job.get("sender_name", "Thành viên")

        target_group_chat = self.default_chat_id or -1003879100454
        target_group_topic = self.default_topic_id or "365"
        is_already_in_group_topic = (
            chat_id == target_group_chat
            and str(thread_id or "") == str(target_group_topic)
        )

        # ── TRƯỜNG HỢP 1: TẠO PODCAST CHO SÁCH ĐÃ CÓ ──
        if job_type in ("podcast_existing_epub", "podcast_from_file_id"):
            if job_type == "podcast_from_file_id":
                INBOX_DIR.mkdir(parents=True, exist_ok=True)
                target_path = INBOX_DIR / job["file_name"]
                if not self.download_file(job["file_id"], target_path):
                    self.send_message(chat_id, "❌ Tải file sách thất bại.", reply_to_message_id=msg_id, thread_id=thread_id)
                    return
            else:
                target_path = Path(job["target_path"])

            chosen_voice = job.get("voice")
            from generate_podcast import create_podcast_for_book, resolve_voice_code, POPULAR_VOICES
            target_voice = resolve_voice_code(chosen_voice)
            voice_desc = POPULAR_VOICES.get(target_voice, target_voice)

            self.send_message(
                chat_id,
                f"🎙️ <b>Bắt đầu tạo Podcast cho:</b> <code>{target_path.stem}</code>\n"
                f"🧠 Gemini đang biên soạn kịch bản & Vbee TTS render giọng <b>{voice_desc}</b>...\n"
                f"<i>(Thời gian xử lý khoảng 1 – 2 phút)</i>",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )

            try:
                podcast_mp3 = create_podcast_for_book(target_path, voice=target_voice)
                if podcast_mp3 and podcast_mp3.exists():
                    self.bot_processed_files[podcast_mp3.name] = time.time()
                    self.sent_files[podcast_mp3.name] = podcast_mp3.stat().st_mtime
                    self._save_sent_files(self.sent_files)

                    caption = (
                        f"🎙️ <b>Podcast Tóm Tắt Sách: {target_path.stem}</b>\n"
                        f"✨ <i>Giọng đọc AI Vbee tự nhiên (128kbps)</i>\n\n"
                        f"👤 <b>Yêu cầu bởi:</b> {sender_name}\n"
                        f"🎧 <i>Bấm nghe trực tiếp trên Telegram!</i>"
                    )
                    self.send_document(chat_id, podcast_mp3, caption=caption, reply_to_message_id=msg_id, thread_id=thread_id)
                    if not is_already_in_group_topic:
                        self.send_document(target_group_chat, podcast_mp3, caption=caption, thread_id=target_group_topic)
                else:
                    self.send_message(chat_id, "❌ Quá trình tạo Podcast thất bại. Vui lòng kiểm tra log trên máy Mac.", reply_to_message_id=msg_id, thread_id=thread_id)
            except Exception as e:
                print(f"[Worker] Lỗi tạo podcast: {e}", file=sys.stderr)
                self.send_message(chat_id, f"❌ Lỗi tạo Podcast: {e}", reply_to_message_id=msg_id, thread_id=thread_id)
            return

        # ── TRƯỜNG HỢP 2: TÓM TẮT SÁCH MỚI (.EPUB / .PDF) ──
        file_id = job["file_id"]
        file_name = job["file_name"]
        with_podcast = job.get("with_podcast", False)

        INBOX_DIR.mkdir(parents=True, exist_ok=True)
        target_path = INBOX_DIR / file_name

        print(f"[Worker] Đang tải {file_name}...")
        if not self.download_file(file_id, target_path):
            self.send_message(
                chat_id,
                f"❌ Tải file <b>{file_name}</b> thất bại. Vui lòng thử gửi lại!",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
            return

        self.send_message(
            chat_id,
            f"⚙️ <b>Bắt đầu tóm tắt:</b> <code>{file_name}</code>\n"
            f"🧠 Đang phân tích cấu trúc & biên soạn các bài học chuyên sâu kiểu Shortform...\n"
            f"<i>(Thời gian xử lý thường từ 3 – 8 phút tùy độ dài sách)</i>",
            reply_to_message_id=msg_id,
            thread_id=thread_id,
        )

        stem = Path(file_name).stem
        start_time = time.time()

        # Chạy auto-pipeline.sh với file cụ thể
        cmd = ["/bin/bash", str(AUTO_PIPELINE), str(target_path), "--no-telegram"]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PROJECT_DIR))
        duration = int(time.time() - start_time)
        dur_min = duration // 60
        dur_sec = duration % 60

        output_epub = OUTPUT_DIR / f"{stem}_short.epub"

        if res.returncode == 0 and output_epub.exists():
            self.bot_processed_files[output_epub.name] = time.time()
            self.sent_files[output_epub.name] = output_epub.stat().st_mtime
            self._save_sent_files(self.sent_files)

            caption = (
                f"📚 <b>{stem}</b>\n"
                f"✨ <i>Bản tóm tắt chuyên sâu phong cách Shortform</i>\n\n"
                f"⏱️ <b>Thời gian xử lý:</b> {dur_min}m{dur_sec}s\n"
                f"📱 <i>Đã đóng gói chuẩn EPUB3, có thể mở ngay trên Apple Books, Kindle, Kobo!</i>"
            )

            # 1. Gửi file về chat yêu cầu
            print(f"[Worker] Đang gửi kết quả về chat yêu cầu {chat_id}...")
            self.send_document(
                chat_id,
                output_epub,
                caption=caption,
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )

            # 2. Gửi tới cả nhóm Telegram chính
            if not is_already_in_group_topic:
                group_caption = (
                    f"📚 <b>{stem}</b>\n"
                    f"✨ <i>Bản tóm tắt chuyên sâu phong cách Shortform</i>\n\n"
                    f"👤 <b>Yêu cầu bởi:</b> {sender_name}\n"
                    f"⏱️ <b>Thời gian xử lý:</b> {dur_min}m{dur_sec}s\n"
                    f"📱 <i>Đã đóng gói chuẩn EPUB3!</i>"
                )
                print(f"[Worker] Đang gửi bản sao tới nhóm chính {target_group_chat} (topic {target_group_topic})...")
                self.send_document(
                    target_group_chat,
                    output_epub,
                    caption=group_caption,
                    thread_id=target_group_topic,
                )

            if with_podcast:
                chosen_voice = job.get("voice")
                from generate_podcast import create_podcast_for_book, resolve_voice_code, POPULAR_VOICES
                target_voice = resolve_voice_code(chosen_voice)
                voice_desc = POPULAR_VOICES.get(target_voice, target_voice)

                self.send_message(
                    chat_id,
                    f"🎙️ <b>Đang tiếp tục tạo Audio Podcast cho:</b> <code>{stem}</code> qua Vbee TTS (giọng: <b>{voice_desc}</b>)...\n"
                    f"<i>(Khoảng 1 – 2 phút nữa sẽ có bản âm thanh gửi tới bạn)</i>",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                )
                try:
                    podcast_mp3 = create_podcast_for_book(output_epub, voice=target_voice)
                    if podcast_mp3 and podcast_mp3.exists():
                        self.bot_processed_files[podcast_mp3.name] = time.time()
                        self.sent_files[podcast_mp3.name] = podcast_mp3.stat().st_mtime
                        self._save_sent_files(self.sent_files)

                        podcast_caption = (
                            f"🎙️ <b>Podcast Tóm Tắt Sách: {stem}</b>\n"
                            f"✨ <i>Giọng đọc AI Vbee tự nhiên (128kbps)</i>\n\n"
                            f"👤 <b>Yêu cầu bởi:</b> {sender_name}\n"
                            f"🎧 <i>Bấm nghe ngay trên Telegram!</i>"
                        )
                        self.send_document(chat_id, podcast_mp3, caption=podcast_caption, reply_to_message_id=msg_id, thread_id=thread_id)
                        if not is_already_in_group_topic:
                            self.send_document(target_group_chat, podcast_mp3, caption=podcast_caption, thread_id=target_group_topic)
                except Exception as e:
                    print(f"[Worker] Lỗi tạo podcast kèm theo: {e}", file=sys.stderr)

            print(f"[Worker] Đã hoàn tất và gửi xong: {output_epub.name}")
        else:
            print(f"[Worker] Pipeline thất bại cho {file_name}:\n{res.stderr}\n{res.stdout}", file=sys.stderr)
            self.send_message(
                chat_id,
                f"❌ Có lỗi xảy ra khi tóm tắt <b>{file_name}</b>.\n"
                f"Vui lòng kiểm tra file log trên máy Mac để xem chi tiết.",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )

    def output_watcher_loop(self) -> None:
        """Theo dõi thư mục output/ và output/podcasts/ để tự động thông báo khi có file mới từ MÁY TÍNH."""
        print("👀 Đang theo dõi thư mục output/ để tự động thông báo khi có file mới từ máy tính...")
        while self.running:
            try:
                time.sleep(4)
                if not self.default_chat_id:
                    continue

                candidates: list[Path] = []
                # 1. Quét các file EPUB trong output/
                if OUTPUT_DIR.exists():
                    for f in OUTPUT_DIR.glob("*.epub"):
                        if not f.name.startswith(".") and f.is_file():
                            candidates.append(f)

                # 2. Quét các file Audio Podcast trong output/podcasts/
                podcasts_dir = OUTPUT_DIR / "podcasts"
                if podcasts_dir.exists():
                    for f in podcasts_dir.glob("*.mp3"):
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
                    # File mới hoặc file bị ghi đè cách đây ít nhất 3 giây
                    if last_sent_mtime is None or mtime > last_sent_mtime + 3.0:
                        time.sleep(2.0)
                        try:
                            new_size = file_path.stat().st_size
                        except OSError:
                            continue
                        if new_size != size:
                            continue

                        # Kiểm tra xem file có phải do chính Inbound Bot vừa tạo trong 90s qua không
                        bot_time = self.bot_processed_files.get(fname, 0)
                        if time.time() - bot_time < 90:
                            self.sent_files[fname] = mtime
                            self._save_sent_files(self.sent_files)
                            continue

                        # ĐÂY LÀ FILE ĐƯỢC TẠO TỪ MÁY TÍNH
                        stem = file_path.stem
                        clean_stem = (
                            stem.replace("_short", "")
                            .replace("_podcast", "")
                            .replace("_", " ")
                        )
                        size_str = (
                            f"{new_size / (1024 * 1024):.1f} MB"
                            if new_size > 1024 * 1024
                            else f"{new_size / 1024:.0f} KB"
                        )
                        curr_time = time.strftime("%H:%M:%S %d/%m/%Y")

                        if file_path.suffix.lower() == ".epub":
                            caption = (
                                f"🖥️ <b>[Tạo từ máy tính]</b> 📚 <b>{clean_stem}</b>\n"
                                f"✨ <i>Bản tóm tắt chuyên sâu phong cách Shortform vừa hoàn tất xuất bản trên máy Mac!</i>\n\n"
                                f"📁 <b>Tệp:</b> <code>{fname}</code> ({size_str})\n"
                                f"⏱️ <b>Hoàn tất lúc:</b> {curr_time}\n"
                                f"📱 <i>Đã đóng gói chuẩn EPUB3, có thể mở ngay trên Apple Books, Kindle, Kobo!</i>"
                            )
                        else:  # .mp3 podcast
                            caption = (
                                f"🖥️ <b>[Tạo từ máy tính]</b> 🎙️ <b>{clean_stem}</b>\n"
                                f"✨ <i>Tập Audio Podcast tóm tắt vừa render hoàn tất trên máy Mac!</i>\n\n"
                                f"📁 <b>Tệp:</b> <code>{fname}</code> ({size_str})\n"
                                f"⏱️ <b>Hoàn tất lúc:</b> {curr_time}\n"
                                f"🎧 <i>Bấm nghe ngay trên Telegram!</i>"
                            )

                        print(f"[Watcher] Phát hiện file mới từ máy tính: {fname}. Đang gửi tới nhóm Telegram...")
                        sent = self.send_document(
                            self.default_chat_id,
                            file_path,
                            caption=caption,
                            thread_id=self.default_topic_id,
                        )
                        if sent:
                            print(f"[Watcher] ✅ Đã gửi file từ máy tính thành công: {fname}")
                            self.sent_files[fname] = mtime
                            self._save_sent_files(self.sent_files)

            except Exception as e:
                print(f"[Watcher] Lỗi vòng lặp watcher: {e}", file=sys.stderr)

    def run(self) -> None:
        """Vòng lặp chính lấy updates qua Long Polling."""
        print("🤖 Telegram Inbound Bot đang khởi động...")
        try:
            r = requests.get(f"{self.api_url}/getMe", timeout=10)
            me = r.json()
            if not me.get("ok"):
                sys.exit(f"❌ Token bot không hợp lệ: {me}")
            bot_username = me["result"]["username"]
            print(f"✅ Đã kết nối thành công tới Bot: @{bot_username}")
        except Exception as e:
            sys.exit(f"❌ Không thể kết nối tới Telegram API: {e}")

        worker = threading.Thread(target=self.worker_loop, daemon=True)
        worker.start()

        watcher = threading.Thread(target=self.output_watcher_loop, daemon=True)
        watcher.start()

        print("👂 Đang lắng nghe tin nhắn, tài liệu và lệnh Podcast từ Telegram...")

        while self.running:
            try:
                params = {
                    "offset": self.offset,
                    "timeout": 25,
                    "allowed_updates": ["message"],
                }
                resp = requests.get(f"{self.api_url}/getUpdates", params=params, timeout=30)
                data = resp.json()

                if not data.get("ok"):
                    time.sleep(5)
                    continue

                for update in data.get("result", []):
                    self.offset = update["update_id"] + 1
                    message = update.get("message")
                    if message:
                        self.handle_message(message)

            except requests.RequestException as e:
                time.sleep(3)
            except Exception as e:
                print(f"[MainLoop] Ngoại lệ không mong muốn: {e}", file=sys.stderr)
                time.sleep(2)


def main():
    bot = TelegramInboundBot()
    bot.run()


if __name__ == "__main__":
    main()
