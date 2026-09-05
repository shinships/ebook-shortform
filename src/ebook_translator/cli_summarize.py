"""CLI: ebook-summarize input.pdf|input.epub [-o output.epub] [options]

Tao "sach huong dan chuyen sau" kieu Shortform: moi bai 15-25 phut tra loi
vi sao - the nao - ap dung, kem binh luan mo rong va bai tap thuc hanh.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from ebook_translator.core.cache import TranslationCache
from ebook_translator.core.cover import resolve_cover
from ebook_translator.core.llm import (
    DEFAULT_REGION,
    GOOGLE_AI_DEFAULT_MODEL,
    VERTEX_DEFAULT_MODEL,
    LLMClient,
)
from ebook_translator.core.summarizer import (
    BookAnalysis,
    analyze_book,
    format_plan_table,
    plan_lessons,
    summarize_book,
)
from ebook_translator.readers.loader import load_book
from ebook_translator.writers.epub_writer import write_epub


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="ebook-summarize",
        description=(
            "Tóm tắt ebook (PDF/EPUB) thành sách hướng dẫn chuyên sâu tiếng Việt "
            "kiểu Shortform: mỗi bài 15-25 phút giải thích vì sao - thế nào - áp dụng, "
            "kèm bình luận mở rộng và bài tập thực hành."
        ),
    )
    parser.add_argument("input", help="File đầu vào (.pdf hoặc .epub)")
    parser.add_argument("-o", "--output", help="File EPUB đầu ra (mặc định: <input>_short.epub)")
    parser.add_argument("--model", help=(
        f"Model LLM (mặc định tự chọn theo backend: "
        f"Gemini AI Studio={GOOGLE_AI_DEFAULT_MODEL}, "
        f"Vertex AI={VERTEX_DEFAULT_MODEL})"
    ))
    parser.add_argument("--project", help="GCP project ID cho Vertex AI (mặc định: env GOOGLE_CLOUD_PROJECT)")
    parser.add_argument("--region", help=f"Region Vertex AI (mặc định: {DEFAULT_REGION})")
    parser.add_argument("--cover", help=(
        "Ảnh bìa cho sách tóm tắt (.jpg/.png/.gif/.webp). Mặc định dùng bìa của "
        "sách gốc; nếu bìa gốc không phải bìa thật (trang scan nội dung) thì bị bỏ."
    ))
    parser.add_argument("--analysis", help="File analysis.json có sẵn (bỏ qua bước phân tích sách)")
    parser.add_argument("--max-lessons", type=int, help="Chỉ sinh N bài đầu (chạy thử; bỏ bài mở đầu/tổng kết)")
    parser.add_argument("--keep-workdir", action="store_true", help="Giữ thư mục cache sau khi xong")
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        sys.exit(f"Không tìm thấy file: {input_path}")
    suffix = input_path.suffix.lower()
    if suffix not in (".pdf", ".epub"):
        sys.exit(f"Định dạng không hỗ trợ: {suffix} (chỉ nhận .pdf, .epub)")

    output_path = Path(args.output) if args.output else input_path.with_name(f"{input_path.stem}_short.epub")
    workdir = output_path.with_suffix(".workdir")
    workdir.mkdir(parents=True, exist_ok=True)

    llm = LLMClient(
        model=args.model,
        project_id=args.project,
        region=args.region,
    )

    # ---- [1] Doc file goc ----
    print(f"Đang đọc {input_path.name}...")
    book = load_book(input_path, llm, workdir)
    n_words = sum(len(c.html.split()) for c in book.chapters)
    print(
        f"Đã đọc: {len(book.chapters)} chương, {len(book.images)} ảnh, "
        f"~{n_words:,} từ. Tựa sách: {book.title}"
    )
    if not book.chapters:
        sys.exit("Không trích được nội dung nào từ file đầu vào.")

    # ---- [1b] Chot anh bia ----
    resolve_cover(book, llm, args.cover, workdir)

    # ---- [2] Phan tich sach ----
    analysis_path = Path(args.analysis) if args.analysis else output_path.with_suffix(".analysis.json")
    if analysis_path.exists():
        analysis = BookAnalysis.load(analysis_path)
        print(f"Dùng analysis có sẵn: {analysis_path}")
    else:
        print("Đang phân tích sách (bối cảnh + phân loại chương)...")
        analysis = analyze_book(book, llm)
        analysis.save(analysis_path)
        print(
            f"Đã phân tích -> {analysis_path}\n"
            "  (Có thể sửa file này — vd đổi chương content/skip — rồi chạy lại.)"
        )
    if analysis.title_vi:
        print(f"Tựa tiếng Việt: {analysis.title_vi}")

    # ---- [3] Ke hoach bai hoc ----
    plans = plan_lessons(book, analysis)
    if not plans:
        sys.exit("Không có chương nội dung nào để tóm tắt (kiểm tra chapter_roles trong analysis.json).")
    n_skip = sum(1 for r in analysis.chapter_roles.values() if r == "skip")
    print(f"\nKế hoạch: {len(plans)} bài học ({n_skip} chương bị bỏ qua vì không phải nội dung chính)")
    print(format_plan_table(book, plans))
    print()

    # ---- [4] Sinh bai hoc ----
    cache = TranslationCache(workdir)
    try:
        summary = summarize_book(
            book, llm, analysis, plans, cache, max_lessons=args.max_lessons
        )
    except KeyboardInterrupt:
        sys.exit(
            "\nĐã dừng. Các bài đã sinh xong được lưu cache — "
            "chạy lại cùng lệnh để tiếp tục từ chỗ dừng."
        )

    # ---- [5] Ghi EPUB ----
    write_epub(summary, str(output_path))
    print(
        f"\nHoàn tất: {output_path} ({len(summary.chapters)} bài)\n"
        f"Token đã dùng: {llm.input_tokens:,} vào / {llm.output_tokens:,} ra "
        f"(model {llm.model})"
    )

    if not args.keep_workdir:
        shutil.rmtree(workdir, ignore_errors=True)
    else:
        print(f"Cache giữ tại: {workdir}")


if __name__ == "__main__":
    main()
