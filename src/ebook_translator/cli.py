"""CLI: ebook-translate input.pdf|input.epub [-o output.epub] [options]"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from ebook_translator.core.cache import TranslationCache
from ebook_translator.core.cover import resolve_cover
from ebook_translator.core.glossary import Glossary, build_glossary
from ebook_translator.core.llm import (
    ANTHROPIC_DEFAULT_MODEL,
    DEFAULT_REGION,
    GOOGLE_AI_DEFAULT_MODEL,
    PROXY_DEFAULT_BASE_URL,
    PROXY_DEFAULT_MODEL,
    VERTEX_DEFAULT_MODEL,
    LLMClient,
)
from ebook_translator.core.translator import translate_book, translate_titles
from ebook_translator.readers.loader import load_book
from ebook_translator.writers.epub_writer import write_epub


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="ebook-translate",
        description="Dịch ebook tiếng Anh (PDF/EPUB) sang tiếng Việt, xuất EPUB.",
    )
    parser.add_argument("input", help="File đầu vào (.pdf hoặc .epub)")
    parser.add_argument("-o", "--output", help="File EPUB đầu ra (mặc định: <input>.vi.epub)")
    parser.add_argument("--model", help=(
        f"Model LLM (mặc định tự chọn theo backend: "
        f"Gemini AI Studio={GOOGLE_AI_DEFAULT_MODEL}, "
        f"Anthropic={ANTHROPIC_DEFAULT_MODEL}, "
        f"Vertex AI={VERTEX_DEFAULT_MODEL}, "
        f"--proxy={PROXY_DEFAULT_MODEL})"
    ))
    parser.add_argument("--anthropic", action="store_true", help="Dùng Anthropic API trực tiếp (cần ANTHROPIC_API_KEY)")
    parser.add_argument("--project", help="GCP project ID cho Vertex AI (mặc định: env GOOGLE_CLOUD_PROJECT)")
    parser.add_argument("--region", help=f"Region Vertex AI (mặc định: {DEFAULT_REGION})")
    parser.add_argument("--proxy", action="store_true", help="Dùng proxy vertex-key (legacy, cần VERTEX_KEY_API_KEY)")
    parser.add_argument("--base-url", help=f"Base URL API proxy, chỉ dùng kèm --proxy (mặc định: {PROXY_DEFAULT_BASE_URL})")
    parser.add_argument("--cover", help=(
        "Ảnh bìa cho sách dịch (.jpg/.png/.gif/.webp). Mặc định dùng bìa của sách "
        "gốc; nếu bìa gốc không phải bìa thật (trang scan nội dung) thì bị bỏ."
    ))
    parser.add_argument("--glossary", help="File glossary.json có sẵn (bỏ qua bước tự xây)")
    parser.add_argument("--max-chapters", type=int, help="Chỉ dịch N chương đầu (dịch thử)")
    parser.add_argument("--keep-workdir", action="store_true", help="Giữ thư mục cache sau khi xong")
    parser.add_argument("--no-translate", action="store_true", help=argparse.SUPPRESS)  # debug: pass-through
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        sys.exit(f"Không tìm thấy file: {input_path}")
    suffix = input_path.suffix.lower()
    if suffix not in (".pdf", ".epub"):
        sys.exit(f"Định dạng không hỗ trợ: {suffix} (chỉ nhận .pdf, .epub)")

    output_path = Path(args.output) if args.output else input_path.with_suffix(".vi.epub")
    workdir = output_path.with_suffix(".workdir")
    workdir.mkdir(parents=True, exist_ok=True)

    llm = None
    if not args.no_translate:
        llm = LLMClient(
            model=args.model,
            project_id=args.project,
            region=args.region,
            proxy=args.proxy,
            anthropic=args.anthropic,
            base_url=args.base_url,
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

    if args.no_translate:
        write_epub(book, str(output_path))
        print(f"(pass-through) Đã ghi {output_path}")
        return

    # ---- [2] Glossary ----
    glossary_path = Path(args.glossary) if args.glossary else output_path.with_suffix(".glossary.json")
    if glossary_path.exists():
        glossary = Glossary.load(glossary_path)
        print(f"Dùng glossary có sẵn: {glossary_path} ({len(glossary.terms)} thuật ngữ)")
    else:
        print("Đang xây glossary (bảo đảm dịch nhất quán)...")
        glossary = build_glossary(book, llm)
        glossary.save(glossary_path)
        print(
            f"Đã tạo glossary {len(glossary.terms)} thuật ngữ -> {glossary_path}\n"
            "  (Có thể sửa file này rồi chạy lại để điều chỉnh cách dịch thuật ngữ.)"
        )

    # ---- [3] Dich tieu de + noi dung ----
    print("Đang dịch tiêu đề chương và mục lục...")
    translate_titles(book, llm, glossary)
    if book.title_translated:
        print(f"Tựa sách tiếng Việt: {book.title_translated}")

    cache = TranslationCache(workdir)
    try:
        translate_book(book, llm, glossary, cache, max_chapters=args.max_chapters)
    except KeyboardInterrupt:
        sys.exit(
            "\nĐã dừng. Các đoạn dịch xong đã được lưu cache — "
            "chạy lại cùng lệnh để tiếp tục từ chỗ dừng."
        )

    if args.max_chapters:
        book.chapters = book.chapters[: args.max_chapters]

    # ---- [4] Ghi EPUB ----
    write_epub(book, str(output_path))
    print(
        f"\nHoàn tất: {output_path}\n"
        f"Token đã dùng: {llm.input_tokens:,} vào / {llm.output_tokens:,} ra "
        f"(model {llm.model})"
    )
    if not args.keep_workdir:
        shutil.rmtree(workdir, ignore_errors=True)
    else:
        print(f"Cache giữ tại: {workdir}")


if __name__ == "__main__":
    main()
