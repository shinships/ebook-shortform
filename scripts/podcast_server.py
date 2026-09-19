#!/usr/bin/env python3
"""podcast_server.py — Máy chủ HTTP phục vụ Private RSS Feed & Streaming Audio cho Apple Podcasts & CarPlay.

Hỗ trợ:
- /feed.xml: Podcast RSS Feed chuẩn iTunes 2.0
- /audio/{filename}: Streaming MP3 có hỗ trợ HTTP Range (tua bài/scrubbing mượt mà trên xe)
- /cover.jpg: Ảnh bìa Podcast chuẩn 1400x1400
- /: Giao diện web trực quan, liệt kê các tập và nút 1-chạm mở Apple Podcasts
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import threading
import unicodedata
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
import uvicorn

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from ebook_translator.core.podcast_rss import (
    DEFAULT_PORT,
    MIN_FUZZY_COVER_MATCH,
    PODCASTS_DIR,
    get_apple_podcasts_url,
    get_base_url,
    get_channel_metadata,
    get_feed_url,
    get_local_ip,
    scan_podcast_episodes,
    update_podcast_feed,
)

app = FastAPI(
    title="Private Podcast RSS Server",
    description="Máy chủ phát hành Podcast riêng tư cho Apple Podcasts & Apple CarPlay",
)

# Cho phép CORS để các client web hoặc widget có thể gọi được
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "-")
    method = request.method
    path = request.url.path
    qs = str(request.url.query)
    full_path = f"{path}?{qs}" if qs else path

    response = await call_next(request)
    print(f"📡 [{method}] {full_path} -> {response.status_code} (Client: {client_ip} | UA: {ua[:55]})", flush=True)
    return response


@app.api_route("/feed.xml", methods=["GET", "HEAD"])
@app.api_route("/podcast.rss", methods=["GET", "HEAD"])
@app.api_route("/rss", methods=["GET", "HEAD"])
async def get_rss_feed():
    """Trả về file RSS Feed XML cho Apple Podcasts."""
    feed_path = PODCASTS_DIR / "feed.xml"
    if not feed_path.exists():
        update_podcast_feed()

    if not feed_path.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy feed.xml")

    xml_content = feed_path.read_text(encoding="utf-8")
    return Response(
        content=xml_content,
        media_type="application/xml; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.api_route("/cover.jpg", methods=["GET", "HEAD"])
@app.api_route("/cover_v2.jpg", methods=["GET", "HEAD"])
@app.api_route("/cover_v3.jpg", methods=["GET", "HEAD"])
@app.api_route("/cover_v4.jpg", methods=["GET", "HEAD"])
@app.api_route("/cover.png", methods=["GET", "HEAD"])
async def get_cover():
    """Trả về ảnh bìa podcast (chuẩn Apple Podcasts 1400x1400)."""
    cover_jpg = PODCASTS_DIR / "cover.jpg"
    if cover_jpg.exists():
        return FileResponse(
            cover_jpg,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "public, max-age=3600, must-revalidate",
                "Accept-Ranges": "bytes",
            },
        )

    # Dự phòng sang thư mục covers/
    alt_cover = PROJECT_DIR / "covers" / "podcast_cover.jpg"
    if alt_cover.exists():
        return FileResponse(
            alt_cover,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "public, max-age=3600, must-revalidate",
                "Accept-Ranges": "bytes",
            },
        )

    raise HTTPException(status_code=404, detail="Chưa có ảnh bìa cover.jpg")


@app.api_route("/covers/{filename}", methods=["GET", "HEAD"])
@app.api_route("/cover_art/{filename}", methods=["GET", "HEAD"])
async def get_episode_cover(filename: str):
    """Phục vụ ảnh bìa tập sách (Episode Artwork) từ bìa sách gốc."""
    import urllib.parse
    import unicodedata

    def _slug(text: str) -> str:
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

    safe_name = urllib.parse.unquote(os.path.basename(filename)).split("?")[0].strip()
    slug_name = _slug(safe_name)
    unversioned = re.sub(r'_v\d+\.jpg$', '.jpg', slug_name)
    ep_covers_dir = PODCASTS_DIR / "episode_covers"

    candidates = [
        ep_covers_dir / safe_name,
        ep_covers_dir / slug_name,
        ep_covers_dir / unversioned,
        ep_covers_dir / safe_name.replace(" ", "_"),
        ep_covers_dir / safe_name.replace("_", " "),
        ep_covers_dir / f"{safe_name}.jpg",
        ep_covers_dir / f"{slug_name}.jpg",
        ep_covers_dir / f"{unversioned}.jpg",
    ]
    for cand in candidates:
        if cand.exists() and cand.is_file():
            return FileResponse(
                cand,
                media_type="image/jpeg",
                headers={
                    "Cache-Control": "public, max-age=3600, must-revalidate",
                    "Accept-Ranges": "bytes",
                },
            )

    # Fuzzy search nếu tên có sai khác nhỏ. Duyệt sorted() cho tất định và bỏ qua
    # chuỗi quá ngắn vì so khớp substring hai chiều rất dễ khớp nhầm sang tập khác.
    s_clean = slug_name.lower().replace(".jpg", "").replace("_", "").replace("-", "")
    for c_file in sorted(ep_covers_dir.glob("*.jpg")):
        c_clean = _slug(c_file.stem).lower().replace("_", "").replace("-", "")
        if min(len(c_clean), len(s_clean)) < MIN_FUZZY_COVER_MATCH:
            continue
        if c_clean in s_clean or s_clean in c_clean:
            print(f"⚠️ Ảnh bìa '{safe_name}' khớp gần đúng -> '{c_file.name}'", flush=True)
            return FileResponse(
                c_file,
                media_type="image/jpeg",
                headers={
                    "Cache-Control": "public, max-age=3600, must-revalidate",
                    "Accept-Ranges": "bytes",
                },
            )

    # Dự phòng sang ảnh bìa kênh chính. Trả ảnh sai vẫn hơn mất hẳn ảnh, nhưng phải
    # log rõ — nếu không thì lỗi lệch tên file bị che giấu hoàn toàn.
    print(f"❌ Không tìm thấy ảnh bìa tập cho '{safe_name}', tạm dùng ảnh bìa kênh", flush=True)
    main_cover = PODCASTS_DIR / "cover.jpg"
    if main_cover.exists():
        return FileResponse(
            main_cover,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "public, max-age=3600, must-revalidate",
                "Accept-Ranges": "bytes",
            },
        )

    raise HTTPException(status_code=404, detail="Không tìm thấy ảnh bìa tập")


@app.api_route("/audio/{filename}", methods=["GET", "HEAD"])
async def stream_audio(filename: str, request: Request):
    """Phục vụ file audio MP3 có hỗ trợ HTTP Range (RFC 7233) để tua bài trên CarPlay."""
    import urllib.parse
    safe_name = urllib.parse.unquote(os.path.basename(filename)).split("?")[0].strip()

    # Chống Path Traversal
    audio_path = PODCASTS_DIR / safe_name
    if not audio_path.exists() or not audio_path.is_file():
        # Dự phòng kiểm tra trong thư mục output/articles/
        art_path = PROJECT_DIR / "output" / "articles" / safe_name
        if art_path.exists() and art_path.is_file():
            audio_path = art_path
        else:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy file audio: {safe_name}")

    if not safe_name.lower().endswith(".mp3"):
        raise HTTPException(status_code=400, detail="Chỉ hỗ trợ file .mp3")

    return FileResponse(
        path=audio_path,
        media_type="audio/mpeg",
        filename=safe_name,
        headers={"Accept-Ranges": "bytes"},
    )


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Giao diện Dashboard trực quan cho kho Podcast cá nhân."""
    episodes = scan_podcast_episodes()
    meta = get_channel_metadata()
    ch_title = meta["title"]
    ch_author = meta["author"]
    ch_desc = meta["description"]
    base_url = get_base_url()
    feed_url = get_feed_url()
    apple_url = get_apple_podcasts_url()
    local_ip = get_local_ip()

    items_html = []
    for ep in episodes[:40]:  # 40 tập mới nhất
        audio_link = f"{base_url}/audio/{ep['filename']}"
        thumb_url = f"{base_url}/covers/{ep['cover_image']}" if ep.get("cover_image") else f"{base_url}/cover.jpg"
        items_html.append(f"""
        <div class="card">
            <div style="display: flex; gap: 18px; align-items: flex-start;">
                <img src="{thumb_url}" alt="Cover" class="ep-thumb">
                <div style="flex: 1; min-width: 0;">
                    <div class="card-header">
                        <div class="card-title">{ep['title']}</div>
                        <div class="card-meta">⏱️ {ep['duration_str']} • 📅 {ep['pub_date'][:16]} • 💾 {ep['file_size'] // (1024*1024)} MB</div>
                    </div>
                    <p class="card-desc">{ep['description']}</p>
                    <div class="card-player">
                        <audio controls preload="none" style="width: 100%;">
                            <source src="{audio_link}" type="audio/mpeg">
                            Trình duyệt không hỗ trợ audio player.
                        </audio>
                    </div>
                </div>
            </div>
        </div>
        """)
    items_str = "\n".join(items_html) if items_html else "<p>Chưa có tập podcast nào trong kho.</p>"

    html_content = f"""<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{ch_title} — Apple CarPlay Feed</title>
    <style>
        :root {{
            --bg-color: #0d0f12;
            --card-bg: #161b22;
            --accent: #e11d48;
            --accent-hover: #be123c;
            --text-main: #f3f4f6;
            --text-sub: #9ca3af;
            --border: #30363d;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
        body {{ background-color: var(--bg-color); color: var(--text-main); line-height: 1.6; padding: 24px 16px; }}
        .container {{ max-width: 860px; margin: 0 auto; }}
        
        .hero {{
            display: flex;
            align-items: center;
            gap: 24px;
            background: linear-gradient(135deg, #1f131a 0%, #161b22 100%);
            padding: 28px;
            border-radius: 20px;
            border: 1px solid var(--border);
            margin-bottom: 28px;
        }}
        .hero-cover {{
            width: 130px;
            height: 130px;
            border-radius: 16px;
            box-shadow: 0 12px 24px rgba(0,0,0,0.5);
            object-fit: cover;
            flex-shrink: 0;
        }}
        .hero-info h1 {{ font-size: 24px; margin-bottom: 6px; color: #fff; }}
        .hero-info p {{ font-size: 14px; color: var(--text-sub); margin-bottom: 16px; }}
        
        .btn-group {{ display: flex; flex-wrap: wrap; gap: 10px; }}
        .btn {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 10px 18px;
            border-radius: 10px;
            font-size: 14px;
            font-weight: 600;
            text-decoration: none;
            cursor: pointer;
            transition: all 0.2s;
            border: none;
        }}
        .btn-primary {{ background: var(--accent); color: white; }}
        .btn-primary:hover {{ background: var(--accent-hover); }}
        .btn-secondary {{ background: #21262d; color: #c9d1d9; border: 1px solid var(--border); }}
        .btn-secondary:hover {{ background: #30363d; }}
        
        .instructions {{
            background: #121820;
            border-left: 4px solid #38bdf8;
            padding: 18px 20px;
            border-radius: 8px;
            margin-bottom: 28px;
            font-size: 14px;
        }}
        .instructions h3 {{ color: #38bdf8; margin-bottom: 8px; font-size: 16px; }}
        .instructions ol {{ margin-left: 20px; color: #cbd5e1; }}
        .instructions li {{ margin-bottom: 6px; }}
        .code-box {{
            background: #000;
            padding: 6px 12px;
            border-radius: 6px;
            font-family: monospace;
            color: #4ade80;
            word-break: break-all;
            display: inline-block;
            margin: 4px 0;
        }}
        
        .section-title {{ font-size: 18px; font-weight: 700; margin-bottom: 16px; display: flex; align-items: center; justify-content: space-between; }}
        .card {{
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 18px 20px;
            margin-bottom: 16px;
            transition: border-color 0.2s;
        }}
        .card:hover {{ border-color: #58a6ff; }}
        .card-title {{ font-size: 16px; font-weight: 600; color: #ffffff; margin-bottom: 4px; }}
        .card-meta {{ font-size: 12px; color: var(--text-sub); margin-bottom: 10px; }}
        .card-desc {{ font-size: 13px; color: #94a3b8; margin-bottom: 14px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
        .card-player audio {{ height: 38px; outline: none; }}
        .ep-thumb {{
            width: 72px;
            height: 72px;
            border-radius: 10px;
            object-fit: cover;
            flex-shrink: 0;
            box-shadow: 0 4px 10px rgba(0,0,0,0.5);
            border: 1px solid rgba(255,255,255,0.08);
        }}
        
        @media (max-width: 600px) {{
            .hero {{ flex-direction: column; text-align: center; }}
            .btn-group {{ justify-content: center; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="hero">
            <img src="/cover.jpg" alt="Cover" class="hero-cover">
            <div class="hero-info">
                <h1>{ch_title}</h1>
                <p>{ch_desc} • Tác giả: {ch_author}</p>
                <div class="btn-group">
                    <a href="{apple_url}" class="btn btn-primary">🎧 Mở trong Apple Podcasts</a>
                    <button onclick="copyFeed()" class="btn btn-secondary">📋 Sao chép Link RSS</button>
                    <a href="/feed.xml" target="_blank" class="btn btn-secondary">📡 Xem XML Feed</a>
                </div>
            </div>
        </div>

        <div class="instructions">
            <h3>🚗 Cách Thêm Vào Apple Podcasts Để Nghe Trên Xe Hơi (CarPlay):</h3>
            <ol>
                <li>Đảm bảo iPhone đang kết nối chung <b>Wi-Fi ở nhà</b> với máy Mac (IP: <code>{local_ip}</code>).</li>
                <li>Trên iPhone: Mở ứng dụng <b>Apple Podcasts (Podcast)</b>.</li>
                <li>Chọn tab <b>Thư viện (Library)</b> ➔ Bấm vào biểu tượng dấu <b>3 chấm (...)</b> ở góc trên bên phải.</li>
                <li>Chọn <b>"Theo dõi chương trình bằng URL..." (Follow a Show by URL...)</b>.</li>
                <li>Dán link RSS sau vào: <br><span class="code-box" id="feed-url-text">{feed_url}</span></li>
                <li>Bấm <b>Theo dõi (Follow)</b>. Khi hoàn tất, lên xe cắm cáp CarPlay là kênh sẽ xuất hiện nguyên bản trên màn hình xe!</li>
            </ol>
        </div>

        <div class="section-title">
            <span>Danh Sách Tập ({len(episodes)} tập)</span>
            <span style="font-size: 13px; font-weight: normal; color: var(--text-sub);">Tự động cập nhật</span>
        </div>
        {items_str}
    </div>

    <script>
        function copyFeed() {{
            const url = "{feed_url}";
            navigator.clipboard.writeText(url).then(() => {{
                alert("Đã sao chép link RSS vào bộ nhớ tạm:\\n" + url + "\\n\\nHãy mở Apple Podcasts trên iPhone và dán vào phần 'Theo dõi bằng URL'.");
            }}).catch(() => {{
                prompt("Hãy copy đường link này:", url);
            }});
        }}
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html_content)


_server_thread: Optional[threading.Thread] = None


def start_server_in_background(host: str = "0.0.0.0", port: int = DEFAULT_PORT) -> threading.Thread:
    """Khởi động máy chủ FastAPI trong một Background Daemon Thread (tiện lợi khi nhúng vào Telegram Bot)."""
    global _server_thread

    def run():
        # uvicorn loglevel warning để không làm rối terminal log của bot
        config = uvicorn.Config(app=app, host=host, port=port, log_level="warning", access_log=False)
        server = uvicorn.Server(config)
        server.run()

    _server_thread = threading.Thread(target=run, daemon=True, name="PodcastServerThread")
    _server_thread.start()
    return _server_thread


def main():
    parser = argparse.ArgumentParser(description="Chạy máy chủ Private Podcast RSS Server")
    parser.add_argument("--host", default="0.0.0.0", help="Địa chỉ bind (Mặc định: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Cổng HTTP (Mặc định: {DEFAULT_PORT})")
    args = parser.parse_args()

    # Cập nhật feed một lần trước khi chạy
    update_podcast_feed()

    print("\n" + "═" * 65)
    print(f"🎙️  PRIVATE PODCAST RSS SERVER ĐANG CHẠY")
    print("═" * 65)
    print(f"📡 Feed URL         : {get_feed_url()}")
    print(f"🌐 Web Dashboard    : {get_base_url()}/")
    print(f"🎧 Apple Podcasts   : {get_apple_podcasts_url()}")
    print("═" * 65 + "\n")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
