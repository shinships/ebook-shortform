#!/usr/bin/env python3
"""
Pipeline trọn gói: Tự động trích xuất cover từ PDF/EPUB tiếng Anh gốc
+ Chạy Whisper kết hợp Forced Alignment với ground-truth script.txt
+ Tách biệt Job ID chống ghi đè file tạm
+ Render Short Reel 9:16 chất lượng cao với Remotion.

Usage:
    python scripts/render_short_reel.py --audio "output/podcasts/Slow Productivity - Cal Newport_podcast_tinh_gon.mp3" \
                                        --duration 40 \
                                        --output "output/reels/slow_productivity_reel.mp4"
"""

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REMOTION_DIR = PROJECT_ROOT / "remotion-renderer"

# Thêm path để import scripts nội bộ
sys.path.insert(0, str(PROJECT_ROOT))
from scripts.cover_extractor import find_and_extract_book_cover
from scripts.whisper_aligner import transcribe_and_align

def main():
    parser = argparse.ArgumentParser(description="End-to-End Podcast Short Reel Generator with Remotion & Whisper Alignment")
    parser.add_argument("--audio", required=True, help="Path to podcast mp3 file")
    parser.add_argument("--script", default="", help="Path to ground truth script.txt (optional, auto-detected)")
    parser.add_argument("--title", default="", help="Video Title / Book Name")
    parser.add_argument("--subtitle", default="", help="Author / Speaker")
    parser.add_argument("--cover", default="", help="Custom cover image path (optional)")
    parser.add_argument("--duration", type=int, default=30, help="Duration in seconds (default: 30)")
    parser.add_argument("--output", default="", help="Output MP4 path")
    parser.add_argument("--accent", default="#10b981", help="Accent color (default: #10b981)")
    args = parser.parse_args()

    audio_path = Path(args.audio)
    if not audio_path.is_absolute():
        audio_path = PROJECT_ROOT / audio_path

    if not audio_path.exists():
        print(f"Error: Audio file not found at {audio_path}")
        sys.exit(1)

    job_id = uuid.uuid4().hex[:8]
    print(f"\n=======================================================")
    print(f"🚀 BẮT ĐẦU TẠO SHORT REEL [Job: {job_id}]: {audio_path.name}")
    print(f"=======================================================\n")

    # 1. Thư mục public & output
    public_dir = REMOTION_DIR / "public"
    public_dir.mkdir(exist_ok=True)
    
    out_file_str = args.output or f"output/reels/{audio_path.stem}_{job_id}.mp4"
    out_file = Path(out_file_str)
    if not out_file.is_absolute():
        out_file = PROJECT_ROOT / out_file
    out_file.parent.mkdir(parents=True, exist_ok=True)

    # 2. Trim audio ra file tạm độc lập theo job_id
    target_audio_name = f"job_{job_id}_audio.mp3"
    target_audio = public_dir / target_audio_name
    print(f"[1/4] ✂️ Cắt audio {args.duration}s cho Reel...")
    subprocess.run([
        "ffmpeg", "-y", "-i", str(audio_path), "-t", str(args.duration),
        str(target_audio)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    # 3. Tự động tìm & trích xuất Cover từ PDF/EPUB tiếng Anh gốc theo job_id
    target_cover_name = f"job_{job_id}_cover.jpg"
    target_cover = public_dir / target_cover_name
    cover_found = False

    if args.cover and Path(args.cover).exists():
        subprocess.run(["cp", args.cover, str(target_cover)], check=True)
        cover_found = True
    else:
        book_query = args.title or audio_path.stem
        print(f"[2/4] 🔍 Tự động tìm bìa sách tiếng Anh gốc cho '{book_query}'...")
        extracted = find_and_extract_book_cover(book_query, PROJECT_ROOT, target_cover)
        if extracted:
            print(f"      ✅ Đã trích xuất thành công bìa sách từ file gốc!")
            cover_found = True
        else:
            print(f"      ⚠️ Không tìm thấy file gốc khớp, fallback sang default cover...")
            fallback = PROJECT_ROOT / "output/podcasts/cover.jpg"
            if fallback.exists():
                subprocess.run(["cp", str(fallback), str(target_cover)], check=True)
                cover_found = True

    # 4. Tự động phát hiện script.txt tương ứng nếu không truyền
    script_path = None
    if args.script and Path(args.script).exists():
        script_path = Path(args.script)
    else:
        potential_scripts = [
            audio_path.parent / f"{audio_path.stem}_script.txt",
            audio_path.parent / f"{audio_path.stem.replace('_short_podcast', '').replace('_podcast_tinh_gon', '')}_script.txt",
            audio_path.parent / f"{audio_path.stem.split('_podcast')[0]}_script.txt",
        ]
        for p_script in potential_scripts:
            if p_script.exists():
                script_path = p_script
                break

    # 5. Chạy Whisper kết hợp Forced Alignment với script gốc
    print(f"[3/4] 🎙️ Chạy Faster-Whisper + Forced Alignment với Script gốc...")
    subtitles = transcribe_and_align(target_audio, script_path=script_path)

    # 6. Tạo JSON Props cho Remotion
    title = args.title
    subtitle = args.subtitle
    if not title:
        parts = audio_path.stem.replace("_", " ").split("-")
        title = parts[0].strip()
        if len(parts) > 1 and not subtitle:
            subtitle = parts[1].replace("podcast", "").replace("short", "").strip()

    props = {
        "title": title,
        "subtitle": subtitle or "Podcast Digest",
        "audioSrc": target_audio_name,
        "coverSrc": target_cover_name if cover_found else "",
        "accentColor": args.accent,
        "subtitles": subtitles
    }

    props_file = REMOTION_DIR / f"props_{job_id}.json"
    with open(props_file, "w", encoding="utf-8") as f:
        json.dump(props, f, ensure_ascii=False, indent=2)

    # 7. Render bằng Remotion CLI
    frames = args.duration * 30
    print(f"[4/4] 🎬 Remotion Render MP4 (1080x1920, {frames} frames @ 30fps)...")
    cmd = [
        "npx", "remotion", "render",
        "src/index.ts", "PodcastShort",
        str(out_file),
        f"--props={props_file}",
        f"--frames=0-{frames-1}"
    ]

    try:
        result = subprocess.run(cmd, cwd=str(REMOTION_DIR))
        if result.returncode == 0:
            print(f"\n✨ XUẤT BẢN THÀNH CÔNG: {out_file}")
            print(f"Dung lượng: {out_file.stat().st_size / (1024*1024):.1f} MB\n")
        else:
            print(f"\n❌ Lỗi khi render Remotion (code {result.returncode})")
            sys.exit(result.returncode)
    finally:
        # Dọn dẹp toàn bộ file tạm để tránh phình to thư mục public/
        if props_file.exists():
            props_file.unlink()
        if target_audio.exists():
            target_audio.unlink()
        if target_cover.exists():
            target_cover.unlink()

if __name__ == "__main__":
    main()
