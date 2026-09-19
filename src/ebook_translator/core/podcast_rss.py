"""podcast_rss.py — Quản lý và khởi tạo Private RSS Feed chuẩn Apple Podcasts (iTunes RSS 2.0).

Tự động quét kho podcast MP3 trong `output/podcasts/`, trích xuất metadata,
thời lượng, show notes và đóng gói thành feed.xml để Apple Podcasts / Apple CarPlay
có thể nhận diện và phát mượt mà trên xe hơi.
"""

from __future__ import annotations

import email.utils
import html
import os
import re
import shutil
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape

from ebook_translator.core.tags import generate_topic_tags

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent.parent
PODCASTS_DIR = PROJECT_DIR / "output" / "podcasts"
ARTICLES_DIR = PROJECT_DIR / "output" / "articles"
DEFAULT_PORT = 8000
SETTINGS_PATH = PROJECT_DIR / "logs" / ".bot_settings.json"
# Độ dài tối thiểu của chuỗi so khớp khi dò ảnh bìa gần đúng theo tên file
MIN_FUZZY_COVER_MATCH = 12


def _env_file_path() -> Path | None:
    """Trả về file .env đang dùng, ưu tiên thư mục làm việc rồi tới gốc dự án."""
    for candidate in (Path.cwd() / ".env", PROJECT_DIR / ".env"):
        if candidate.is_file():
            return candidate
    return None


def _read_env_value(key: str) -> str:
    """Đọc trực tiếp một khóa từ file .env, không phụ thuộc os.environ."""
    path = _env_file_path()
    if path is None:
        return ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip().strip("'\"")
    except Exception:
        pass
    return ""


def _load_env_file() -> None:
    """Nạp .env vào os.environ, không ghi đè biến môi trường đã có sẵn.

    Module này chỉ import `core.tags` nên không đi qua `core.llm` — nơi duy nhất
    gọi `_load_env_file()` lúc import. Nếu không tự nạp ở đây thì
    `PODCAST_SERVER_BASE_URL` trong .env sẽ không tới được `get_base_url()`
    khi chạy podcast_server.py độc lập.
    """
    candidate = _env_file_path()
    if candidate is not None:
        try:
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k and k not in os.environ:
                    os.environ[k] = v
        except Exception:
            pass


_load_env_file()


def get_channel_metadata() -> Dict[str, str]:
    """Lấy thông tin tiêu đề kênh, tác giả/host và mô tả từ cấu hình."""
    title = os.getenv("PODCAST_TITLE", "").strip()
    author = os.getenv("PODCAST_AUTHOR", "").strip()
    description = os.getenv("PODCAST_DESCRIPTION", "").strip()
    email_addr = (os.getenv("PODCAST_OWNER_EMAIL", "") or _read_env_value("PODCAST_OWNER_EMAIL")).strip()

    if SETTINGS_PATH.exists():
        try:
            import json
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            channel_cfg = data.get("podcast_channel", {})
            if not title:
                title = channel_cfg.get("title", "")
            if not author:
                author = channel_cfg.get("author", "")
            if not description:
                description = channel_cfg.get("description", "")
            if not email_addr:
                email_addr = channel_cfg.get("email", "")
        except Exception:
            pass

    return {
        "title": title or "Sách Nói & Podcast Tóm Tắt (AI)",
        "author": author or "Bùi Tấn Việt",
        "description": description or "Kênh Podcast sách nói tóm tắt chuyên sâu, bẻ khóa cơ chế tư duy và kiến thức tinh hoa bằng AI.",
        # Apple dùng địa chỉ này để xác minh quyền sở hữu kênh. Giữ ngoài git, đặt trong .env.
        "email": email_addr or "podcast@local.me",
    }


def set_channel_metadata(
    title: str | None = None,
    author: str | None = None,
    description: str | None = None,
) -> Dict[str, str]:
    """Lưu cập nhật thông tin kênh vào .bot_settings.json."""
    import json
    data = {}
    if SETTINGS_PATH.exists():
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    channel_cfg = data.setdefault("podcast_channel", {})
    if title is not None:
        channel_cfg["title"] = title.strip()
    if author is not None:
        channel_cfg["author"] = author.strip()
    if description is not None:
        channel_cfg["description"] = description.strip()

    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return get_channel_metadata()


def get_local_ip() -> str:
    """Tự động xác định địa chỉ IP trong mạng Wi-Fi/LAN của máy Mac."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Không thực sự gửi gói tin ra ngoài, chỉ mở socket để lấy routing IP nội bộ
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def get_base_url() -> str:
    """Lấy Base URL phục vụ podcast.

    Ưu tiên:
    1. Biến môi trường PODCAST_SERVER_BASE_URL (Cloudflare Tunnel: https://podcast.domain.com)
    2. Đọc thẳng cùng khóa đó từ file .env
    3. IP LAN nội bộ + cổng cấu hình: http://192.168.x.x:8000

    Bước 2 là cố ý dư thừa so với `_load_env_file()` lúc import: nếu chỉ dựa vào
    os.environ, một tiến trình nạp .env hụt sẽ âm thầm rơi về IP LAN và ghi đè
    feed.xml bằng URL nội bộ — khiến feed chập chờn giữa HTTPS và IP LAN tùy tiến
    trình nào ghi sau cùng. Ảnh bìa trên Apple Podcasts sẽ mất mỗi khi điều đó xảy ra.
    """
    env_url = (os.getenv("PODCAST_SERVER_BASE_URL", "") or _read_env_value("PODCAST_SERVER_BASE_URL")).strip()
    if env_url:
        return env_url.rstrip("/")

    port = os.getenv("PODCAST_PORT", str(DEFAULT_PORT)).strip()
    ip = get_local_ip()
    print(
        "⚠️ Chưa cấu hình PODCAST_SERVER_BASE_URL — feed sẽ dùng IP LAN "
        f"(http://{ip}:{port}). Apple Podcasts sẽ KHÔNG tải được ảnh bìa qua HTTP nội bộ.",
        flush=True,
    )
    return f"http://{ip}:{port}"


def get_feed_url() -> str:
    """Trả về link HTTP đầy đủ của file feed.xml."""
    return f"{get_base_url()}/feed.xml"


def get_apple_podcasts_url() -> str:
    """Trả về link dạng podcast:// để iPhone chạm 1 cái tự mở Apple Podcasts."""
    feed_url = get_feed_url()
    if feed_url.startswith("https://"):
        return "podcast://" + feed_url[len("https://"):]
    elif feed_url.startswith("http://"):
        return "podcast://" + feed_url[len("http://"):]
    return f"podcast://{feed_url}"


def get_audio_duration_seconds(file_path: Path) -> int:
    """Lấy thời lượng (giây) của file âm thanh MP3 bằng ffprobe hoặc ước lượng."""
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(file_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if res.returncode == 0 and res.stdout.strip():
            return int(float(res.stdout.strip()))
    except Exception:
        pass

    # Ước lượng dự phòng: với MP3 ~128kbps, 1MB ~ 65 giây
    size_bytes = file_path.stat().st_size
    return max(10, int(size_bytes / (16 * 1024)))


def format_duration(seconds: int) -> str:
    """Định dạng thời lượng theo chuẩn iTunes (HH:MM:SS hoặc MM:SS)."""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def clean_episode_title(stem: str, mp3_path: Path | None = None) -> str:
    """Làm sạch tên file thành tiêu đề tập podcast trực quan và dễ nhìn, loại bỏ các tiền tố Chương / Chapter ở đầu."""
    # 1. Thử trích xuất tiêu đề đẹp từ file markdown tương ứng nếu có
    if mp3_path:
        stem_raw = mp3_path.stem
        candidates = [
            mp3_path.parent / f"{stem_raw}_vi.md",
            mp3_path.parent / f"{stem_raw}.md",
            ARTICLES_DIR / f"{stem_raw}_vi.md",
            ARTICLES_DIR / f"{stem_raw}.md",
        ]
        # Thử tìm theo fuzzy nếu tên mp3 có gạch dưới kép hoặc hậu tố khác
        clean_cand_stem = re.sub(r"(_yt_podcast|_podcast|_audio|_vi)$", "", stem_raw)
        norm_cand = re.sub(r"_+", "_", clean_cand_stem)
        for c_file in list(mp3_path.parent.glob("*.md")) + list(ARTICLES_DIR.glob("*.md")):
            norm_c = re.sub(r"_+", "_", c_file.stem)
            if norm_cand.lower() in norm_c.lower() or norm_c.lower() in norm_cand.lower():
                if c_file not in candidates:
                    candidates.append(c_file)

        for c in candidates:
            if c.exists():
                try:
                    txt = c.read_text(encoding="utf-8")
                    # Tìm thông tin chương
                    ch_m = re.search(r"\*\*Chương:\*\*\s*(.+)", txt)
                    if ch_m:
                        ch_title = ch_m.group(1).strip()
                        # Loại bỏ tiền tố 'Chương X:' / 'Chapter X:' nếu có
                        ch_title = re.sub(r"^\[?(?:chuong|chương|chapter)\s*\d+[^\]]*\]?\s*[-–—:]?\s*", "", ch_title, flags=re.I).strip()
                        if ch_title:
                            return ch_title

                    # Tìm tiêu đề H1
                    h1_m = re.search(r"^#\s+(.+)$", txt, re.M)
                    if h1_m:
                        h1_title = h1_m.group(1).strip()
                        h1_clean = re.sub(r"^\[?(?:chuong|chương|chapter)\s*\d+[^\]]*\]?\s*[-–—:]?\s*", "", h1_title, flags=re.I).strip()
                        if len(h1_clean) >= 5:
                            return h1_clean
                except Exception:
                    pass

    # Xử lý quy tắc đặt tên file
    title = stem
    suffix = ""
    if "_podcast_chuyen_sau" in title:
        title = title.replace("_podcast_chuyen_sau", "")
        suffix = " (Chuyên Sâu)"
    elif "_podcast_tinh_gon" in title:
        title = title.replace("_podcast_tinh_gon", "")
        suffix = " (Tinh Gọn)"
    elif "_podcast" in title:
        title = title.replace("_podcast", "")
        suffix = " (Podcast)"
    elif "_audio" in title:
        title = title.replace("_audio", "")
        suffix = " (Audio Toàn Văn)"

    # Xử lý dạng tên file chương: Chuong_03_Happiness_... -> bỏ "Chương 03:"
    ch_match = re.match(r"^(?:chuong|chương)[-_]?0?(\d+)[-_](.+)$", title, re.I)
    if ch_match:
        body = ch_match.group(2).replace("_", " ").replace("-", " ")
        body = re.sub(r"\s+", " ", body).strip()
        title = body

    # Loại bỏ các hậu tố thừa
    title = (
        title.replace("_shortform", "")
        .replace("_short_short", "")
        .replace("_short", "")
        .replace("_VN", "")
        .replace(".vi", "")
        .replace("_vi", "")
        .replace("-vi", "")
        .replace("_yt", "")
    )

    # Thay gạch dưới bằng khoảng trắng
    title = title.replace("_", " ").replace("-", " ")
    title = re.sub(r"\s+", " ", title).strip()

    # Chuẩn hóa viết hoa
    parts = title.split()
    clean_parts = []
    for p in parts:
        if p.isupper() and len(p) <= 4:
            clean_parts.append(p)
        else:
            clean_parts.append(p.capitalize())
    clean_title = " ".join(clean_parts)

    # Loại bỏ đoạn "Chương..." / "Chapter..." ở đầu tiêu đề nếu còn sót lại
    clean_title = re.sub(r"^\[?(?:chuong|chương|chapter)\s*\d+[^\]]*\]?\s*[-–—:]?\s*", "", clean_title, flags=re.I).strip()

    return f"{clean_title}{suffix}"


def find_show_notes_for_audio(mp3_path: Path) -> str:
    """Tìm nội dung tóm tắt / kịch bản tương ứng để làm Show Notes cho tập podcast.
    
    Định dạng chuẩn theo yêu cầu:
    [🎬 Từ: ...] (nếu có)
    [👤 Kênh/Tác giả: ...] (nếu có)
    📝 #podcast (nếu audio dài >3MB) hoặc #audio (nếu audio ngắn) + 1 keyword hashtag
    """
    stem = mp3_path.stem
    clean_stem = (
        stem.replace("_podcast_tinh_gon", "")
        .replace("_podcast_chuyen_sau", "")
        .replace("_podcast", "")
        .replace("_audio", "")
        .replace("_vi", "")
    )
    candidates = [
        mp3_path.parent / f"{stem}_script.txt",
        mp3_path.parent / f"{clean_stem}_script.txt",
        mp3_path.parent / f"{clean_stem}_podcast_tinh_gon_script.txt",
        mp3_path.parent / f"{clean_stem}_podcast_chuyen_sau_script.txt",
        mp3_path.parent / f"{stem}_audio_script.txt",
        mp3_path.parent / f"{stem}.txt",
        # Hỗ trợ Markdown bài viết / YouTube
        mp3_path.parent / f"{stem}_vi.md",
        mp3_path.parent / f"{stem}.md",
        mp3_path.parent / f"{clean_stem}.md",
        ARTICLES_DIR / f"{stem}_vi.md",
        ARTICLES_DIR / f"{stem}.md",
        PROJECT_DIR / "output" / "originals" / f"{stem}.md",
        PROJECT_DIR / "output" / "originals" / f"{clean_stem}.md",
    ]

    prefix = ""
    tag_line = ""
    text_content = ""

    for c in candidates:
        if c.exists():
            try:
                text = c.read_text(encoding="utf-8").strip()
                if not text:
                    continue
                text_content = text
                author_m = re.search(r"\*\*Tác giả / Kênh:\*\*\s*(.+)", text)
                vid_m = re.search(r"\*\*Video gốc:\*\*\s*(.+)", text)
                if vid_m and not prefix:
                    prefix += f"🎬 Từ: {vid_m.group(1).strip()}\n"
                if author_m and "Kênh/Tác giả:" not in prefix:
                    prefix += f"👤 Kênh/Tác giả: {author_m.group(1).strip()}\n"

                desc_match = re.search(r">\s*📝\s*(.+?)(?=\n\n|\n[#\->]|$)", text)
                if desc_match:
                    raw_val = desc_match.group(1).strip()
                    if not raw_val.lower().startswith("let's use"):
                        tag_line = f"📝 {raw_val}"
                        break
            except Exception:
                continue

    if not tag_line:
        # Tính toán theo chuẩn: #podcast (nếu >3MB) hoặc #audio (nếu ngắn) + 1 keyword hashtag
        try:
            size = mp3_path.stat().st_size
        except Exception:
            size = 0
        is_long = size > 3 * 1024 * 1024 or "_chuyen_sau" in mp3_path.stem or "_deep" in mp3_path.stem
        type_tag = "#podcast" if is_long else "#audio"
        clean_title = clean_episode_title(stem, mp3_path)
        raw_tags = generate_topic_tags(clean_title, text_content[:500] if text_content else "", output_type="")
        kw_cands = [
            t for t in raw_tags
            if t.lower() not in ("#podcast", "#audio", "#short", "#dich", "#article", "#shortform", "#kienthuc")
        ]
        chosen_kw = kw_cands[0] if kw_cands else "#kienthuc"
        if not chosen_kw.startswith("#"):
            chosen_kw = f"#{chosen_kw}"
        tag_line = f"📝 {type_tag} {chosen_kw}"

    summary_extra = ""
    if text_content:
        parts = re.split(r">\s*📝\s*.*", text_content, maxsplit=1)
        if len(parts) > 1 and parts[1].strip():
            desc_body = parts[1].strip()
            desc_clean = re.sub(r"^#+\s+.*", "", desc_body, flags=re.MULTILINE).strip()
            if desc_clean:
                if len(desc_clean) > 800:
                    desc_clean = desc_clean[:797] + "..."
                summary_extra = f"\n\n{desc_clean}"

    return f"{prefix}{tag_line}{summary_extra}".strip()


def embed_cover_to_mp3(mp3_path: Path, cover_path: Path) -> bool:
    """Nhúng ảnh bìa vào metadata ID3 (APIC) của file MP3 qua ffmpeg để Apple CarPlay / Lock Screen hiển thị."""
    if not mp3_path.exists() or not cover_path.exists():
        return False
    temp_out = mp3_path.with_suffix(".tag_tmp.mp3")
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(mp3_path),
        "-i", str(cover_path),
        "-map", "0:a",
        "-map", "1",
        "-codec", "copy",
        "-id3v2_version", "3",
        "-metadata:s:v", "title=Album cover",
        "-metadata:s:v", "comment=Cover (front)",
        str(temp_out),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=25)
        if res.returncode == 0 and temp_out.exists() and temp_out.stat().st_size > 0:
            shutil.move(str(temp_out), str(mp3_path))
            return True
    except Exception as e:
        print(f"⚠️ Lỗi nhúng bìa ID3 vào {mp3_path.name}: {e}")
    finally:
        if temp_out.exists():
            temp_out.unlink(missing_ok=True)
    return False


def get_episode_creation_time(mp3_path: Path) -> float:
    """Xác định thời điểm khởi tạo thực tế của tập podcast từ kịch bản hoặc file gốc.
    
    Khi ffmpeg nhúng metadata ID3, st_mtime của MP3 bị cập nhật đồng loạt.
    Hàm này dò tìm file kịch bản (*_script.txt, *_vi.md, .md) hoặc file gốc để lấy
    mốc thời gian thực sự, đảm bảo thứ tự thời gian trên Apple Podcasts luôn chuẩn xác.
    """
    stem = mp3_path.stem
    clean_stem = (
        stem.replace("_podcast_chuyen_sau", "")
        .replace("_podcast_tinh_gon", "")
        .replace("_podcast", "")
        .replace("_audio", "")
        .replace("_short_short", "")
        .replace("_short", "")
        .replace(".vi", "")
        .replace("_vi", "")
        .replace("-vi", "")
    )
    candidates = [
        mp3_path.parent / f"{stem}_script.txt",
        mp3_path.parent / f"{clean_stem}_script.txt",
        mp3_path.parent / f"{clean_stem}_podcast_chuyen_sau_script.txt",
        mp3_path.parent / f"{clean_stem}_podcast_tinh_gon_script.txt",
        mp3_path.parent / f"{stem}_audio_script.txt",
        mp3_path.parent / f"{stem}_vi.md",
        mp3_path.parent / f"{clean_stem}_vi.md",
        mp3_path.parent / f"{stem}.md",
        mp3_path.parent / f"{clean_stem}.md",
        PODCASTS_DIR / f"{stem}_script.txt",
        PODCASTS_DIR / f"{clean_stem}_script.txt",
        ARTICLES_DIR / f"{stem}_vi.md",
        ARTICLES_DIR / f"{clean_stem}_vi.md",
        PROJECT_DIR / "output" / "originals" / f"{clean_stem}.epub",
        PROJECT_DIR / "output" / "originals" / f"{clean_stem}.md",
    ]
    timestamps = []
    for c in candidates:
        if c.exists():
            timestamps.append(c.stat().st_mtime)
    if timestamps:
        return max(timestamps)
    return mp3_path.stat().st_mtime


def scan_podcast_episodes(podcasts_dir: Path | None = None) -> List[Dict[str, Any]]:
    """Quét toàn bộ thư mục output/podcasts và output/articles để thu thập danh sách các tập audio."""
    target_dir = podcasts_dir or PODCASTS_DIR
    dirs_to_scan = [target_dir]
    if target_dir.resolve() == PODCASTS_DIR.resolve() and ARTICLES_DIR.exists():
        dirs_to_scan.append(ARTICLES_DIR)

    episodes = []
    ignored_patterns = ["test_sample", "test_markdown"]

    mp3_files = []
    seen_filenames = set()
    for d in dirs_to_scan:
        if d.exists():
            for p in d.glob("*.mp3"):
                if p.name not in seen_filenames and not any(ign in p.name for ign in ignored_patterns):
                    seen_filenames.add(p.name)
                    mp3_files.append(p)

    # Sắp xếp theo ngày tạo thực tế mới nhất lên đầu (để tập tạo đêm qua/sáng nay luôn đứng đầu kênh)
    mp3_files.sort(key=get_episode_creation_time, reverse=True)

    seen_titles = set()
    seen_canons = {}
    seen_final_names = set()

    for mp3 in mp3_files:
        stat = mp3.stat()
        file_size = stat.st_size
        if file_size < 100 * 1024:  # Bỏ qua file quá nhỏ < 100KB (thường là file lỗi)
            continue

        if mp3.name in seen_final_names:
            continue

        duration_sec = get_audio_duration_seconds(mp3)
        if duration_sec < 15:
            continue

        title = clean_episode_title(mp3.stem, mp3_path=mp3)

        # Lọc trùng lặp 1: Nếu tiêu đề hiển thị đã xuất hiện, bỏ qua bản sao cũ
        title_key = re.sub(r"[\W_]+", " ", title.lower()).strip()
        if title_key in seen_titles:
            continue

        # Lọc trùng lặp 2: Nhận diện canonical stem của tác phẩm
        canon_stem = re.sub(
            r"(_podcast_chuyen_sau|_podcast_tinh_gon|_podcast|_audio|_shortform|_short_short|_short|_vn|\.vi|_vi|-vi|_yt)$",
            "",
            mp3.stem.lower(),
        )
        canon_key = re.sub(r"[\W_]+", " ", canon_stem).strip()

        has_format_suffix = any(
            suf in mp3.stem.lower()
            for suf in ["_podcast_chuyen_sau", "_podcast_tinh_gon", "_podcast", "_audio"]
        )

        if canon_key in seen_canons:
            prev_info = seen_canons[canon_key]
            # Nếu cùng tác phẩm và thời lượng chênh lệch không quá 10s -> cùng một nội dung thu âm bị lặp
            if abs(duration_sec - prev_info["duration_sec"]) <= 10:
                if not has_format_suffix and prev_info["has_suffix"]:
                    continue
                elif has_format_suffix and not prev_info["has_suffix"]:
                    old_ep = prev_info["ep"]
                    if old_ep in episodes:
                        episodes.remove(old_ep)
                    old_tkey = re.sub(r"[\W_]+", " ", old_ep["title"].lower()).strip()
                    seen_titles.discard(old_tkey)
                else:
                    continue

        seen_titles.add(title_key)
        seen_final_names.add(mp3.name)

        real_time = get_episode_creation_time(mp3)
        pub_date = email.utils.format_datetime(datetime.fromtimestamp(real_time, tz=timezone.utc))
        description = find_show_notes_for_audio(mp3)

        # Tìm ảnh bìa tập sách (Episode Artwork) nếu có trong episode_covers/
        cover_image = None
        ep_covers_dir = PODCASTS_DIR / "episode_covers"
        if ep_covers_dir.exists():
            clean_stem = (
                mp3.stem
                .replace("_podcast_tinh_gon", "")
                .replace("_podcast_chuyen_sau", "")
                .replace("_podcast", "")
                .replace("_audio", "")
                .replace("_short_short", "")
                .replace("_short", "")
                .replace(".vi", "")
                .replace("_vi", "")
                .replace("-vi", "")
            )
            slug_stem = clean_stem.replace(" ", "_").replace("-", "_")
            candidates = [
                ep_covers_dir / f"{mp3.stem}.jpg",
                ep_covers_dir / f"{clean_stem}.jpg",
                ep_covers_dir / f"{slug_stem}.jpg",
            ]
            for c in candidates:
                if c.exists():
                    cover_image = c.name
                    break

            if not cover_image:
                # Dự phòng khi tên file ảnh lệch nhẹ so với tên MP3. Duyệt theo thứ tự
                # sorted() để kết quả tất định, và bỏ qua chuỗi quá ngắn vì so khớp
                # substring hai chiều rất dễ khớp rác (vd "ai" nằm trong mọi tên).
                s_clean = clean_stem.lower().replace("_", "").replace("-", "").replace(" ", "")
                for c_file in sorted(ep_covers_dir.glob("*.jpg")):
                    c_clean = c_file.stem.lower().replace("_", "").replace("-", "").replace(" ", "")
                    if min(len(c_clean), len(s_clean)) < MIN_FUZZY_COVER_MATCH:
                        continue
                    if c_clean in s_clean or s_clean in c_clean:
                        cover_image = c_file.name
                        print(f"⚠️ Ảnh bìa khớp gần đúng cho '{mp3.name}' -> '{cover_image}'")
                        break

        ep_data = {
            "filename": mp3.name,
            "filepath": str(mp3),
            "title": title,
            "description": description,
            "file_size": file_size,
            "duration_sec": duration_sec,
            "duration_str": format_duration(duration_sec),
            "pub_date": pub_date,
            "mtime": real_time,
            "cover_image": cover_image,
        }
        seen_canons[canon_key] = {
            "duration_sec": duration_sec,
            "has_suffix": has_format_suffix,
            "ep": ep_data,
        }
        episodes.append(ep_data)

    return episodes


def to_ascii_slug(text: str) -> str:
    """Chuyển đổi chuỗi có dấu/khoảng trắng thành slug ASCII chuẩn cho URL Apple Podcasts."""
    import unicodedata
    viet_map = {
        'à': 'a', 'á': 'a', 'ả': 'a', 'ã': 'a', 'ạ': 'a',
        'ă': 'a', 'ằ': 'a', 'ắ': 'a', 'ẳ': 'a', 'ẵ': 'a', 'ặ': 'a',
        'â': 'a', 'ầ': 'a', 'ấ': 'a', 'ẩ': 'a', 'ẫ': 'a', 'ậ': 'a',
        'đ': 'd',
        'è': 'e', 'é': 'e', 'ẻ': 'e', 'ẽ': 'e', 'ẹ': 'e',
        'ê': 'e', 'ề': 'e', 'ế': 'e', 'ể': 'e', 'ễ': 'e', 'ệ': 'e',
        'ì': 'i', 'í': 'i', 'ỉ': 'i', 'ĩ': 'i', 'ị': 'i',
        'ò': 'o', 'ó': 'o', 'ỏ': 'o', 'õ': 'o', 'ọ': 'o',
        'ô': 'o', 'ồ': 'o', 'ố': 'o', 'ổ': 'o', 'ỗ': 'o', 'ộ': 'o',
        'ơ': 'o', 'ờ': 'o', 'ớ': 'o', 'ở': 'o', 'ỡ': 'o', 'ợ': 'o',
        'ù': 'u', 'ú': 'u', 'ủ': 'u', 'ũ': 'u', 'ụ': 'u',
        'ư': 'u', 'ừ': 'u', 'ứ': 'u', 'ử': 'u', 'ữ': 'u', 'ự': 'u',
        'ỳ': 'y', 'ý': 'y', 'ỷ': 'y', 'ỹ': 'y', 'ỵ': 'y',
        'Đ': 'D'
    }
    s = text
    for k, v in viet_map.items():
        s = s.replace(k, v).replace(k.upper(), v.upper())
    s = unicodedata.normalize('NFKD', s).encode('ASCII', 'ignore').decode('ASCII')
    s = re.sub(r'[^a-zA-Z0-9._-]', '_', s)
    s = re.sub(r'_+', '_', s)
    return s.strip('_')


def generate_podcast_rss_xml(
    episodes: List[Dict[str, Any]],
    base_url: str | None = None,
    channel_title: str = "Sách Nói & Podcast Tóm Tắt (AI)",
    channel_author: str = "Bùi Tấn Việt",
    channel_description: str = "Kênh Podcast sách nói tóm tắt chuyên sâu, bẻ khóa cơ chế tư duy và kiến thức tinh hoa bằng AI.",
    channel_email: str | None = None,
) -> str:
    """Tạo chuỗi XML chuẩn Apple Podcasts (iTunes RSS 2.0)."""
    import urllib.parse
    base = (base_url or get_base_url()).rstrip("/")
    owner_email = channel_email or get_channel_metadata()["email"]
    cover_url = f"{base}/cover.jpg"
    feed_url = f"{base}/feed.xml"

    now_rfc = email.utils.format_datetime(datetime.now(timezone.utc))

    items_xml = []
    for ep in episodes:
        audio_url = f"{base}/audio/{urllib.parse.quote(ep['filename'])}"
        ep_title = escape(ep["title"])
        ep_desc = escape(ep["description"])
        ep_duration = ep["duration_str"]
        ep_len = ep["file_size"]
        ep_date = ep["pub_date"]
        # GUID vĩnh viễn, cố định, không có tham số version/mtime để chống trùng lặp tập trên Apple Podcasts
        safe_stem = to_ascii_slug(ep["filename"])
        ep_guid = f"{safe_stem}"

        if ep.get("cover_image"):
            # Trỏ thẳng tới tên file thật trên đĩa, percent-encode như cách audio đang làm.
            # Không slug hóa: 15/87 ảnh bìa có dấu tiếng Việt hoặc khoảng trắng, slug sẽ
            # tạo ra tên khác file thật và chỉ sống được nhờ fuzzy recovery của server.
            ep_img_url = f"{base}/covers/{urllib.parse.quote(ep['cover_image'])}"
        else:
            ep_img_url = cover_url

        item = f"""    <item>
      <title>{ep_title}</title>
      <description>{ep_desc}</description>
      <itunes:summary>{ep_desc}</itunes:summary>
      <link>{audio_url}</link>
      <guid isPermaLink="false">{ep_guid}</guid>
      <pubDate>{ep_date}</pubDate>
      <enclosure url="{audio_url}" length="{ep_len}" type="audio/mpeg"/>
      <itunes:duration>{ep_duration}</itunes:duration>
      <itunes:image href="{ep_img_url}"/>
      <itunes:author>{escape(channel_author)}</itunes:author>
      <itunes:explicit>false</itunes:explicit>
      <itunes:episodeType>full</itunes:episodeType>
    </item>"""
        items_xml.append(item)

    items_joined = "\n".join(items_xml)

    xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:atom="http://www.w3.org/2005/Atom"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>{escape(channel_title)}</title>
    <link>{base}/</link>
    <atom:link href="{feed_url}" rel="self" type="application/rss+xml"/>
    <language>vi</language>
    <copyright>© 2026 {escape(channel_author)}</copyright>
    <itunes:author>{escape(channel_author)}</itunes:author>
    <description>{escape(channel_description)}</description>
    <itunes:summary>{escape(channel_description)}</itunes:summary>
    <itunes:type>episodic</itunes:type>
    <itunes:owner>
      <itunes:name>{escape(channel_author)}</itunes:name>
      <itunes:email>{escape(owner_email)}</itunes:email>
    </itunes:owner>
    <itunes:image href="{cover_url}"/>
    <image>
      <url>{cover_url}</url>
      <title>{escape(channel_title)}</title>
      <link>{base}/</link>
    </image>
    <itunes:category text="Education"/>
    <itunes:category text="Business"/>
    <itunes:explicit>false</itunes:explicit>
    <lastBuildDate>{now_rfc}</lastBuildDate>
{items_joined}
  </channel>
</rss>
"""
    return xml_content.strip()


def update_podcast_feed(
    podcasts_dir: Path | None = None,
    output_xml: Path | None = None,
    base_url: str | None = None,
    channel_title: str | None = None,
    channel_author: str | None = None,
    channel_description: str | None = None,
    channel_email: str | None = None,
) -> Path:
    """Quét thư mục và ghi đè file feed.xml mới nhất."""
    target_dir = podcasts_dir or PODCASTS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_xml or (target_dir / "feed.xml")

    meta = get_channel_metadata()
    title = channel_title or meta["title"]
    author = channel_author or meta["author"]
    desc = channel_description or meta["description"]
    owner_email = channel_email or meta["email"]

    episodes = scan_podcast_episodes(target_dir)
    xml_str = generate_podcast_rss_xml(
        episodes,
        base_url=base_url,
        channel_title=title,
        channel_author=author,
        channel_description=desc,
        channel_email=owner_email,
    )
    out_file.write_text(xml_str, encoding="utf-8")
    print(f"📡 Đã cập nhật Private RSS Feed ({len(episodes)} tập) tại: {out_file}")
    return out_file


if __name__ == "__main__":
    out = update_podcast_feed()
    print(f"Feed URL: {get_feed_url()}")
    print(f"Apple Podcasts One-Click: {get_apple_podcasts_url()}")
