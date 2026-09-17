#!/usr/bin/env bash
# =============================================================================
# auto-pipeline.sh — Dây chuyền tóm tắt ebook tự động (Task 1)
#
# Quét inbox/, xử lý từng file .epub/.pdf bằng ebook-summarize,
# chuyển kết quả vào output/.  Dùng làm lệnh chạy tay hoặc gắn vào
# Scheduled Task của Antigravity Desktop.
#
# Cách dùng:
#   ./auto-pipeline.sh                  # mặc định dùng gemini-2.5-flash
#   ./auto-pipeline.sh --model gemini-2.5-pro
#   ./auto-pipeline.sh --dry-run        # chỉ liệt kê, không xử lý
# =============================================================================
set -euo pipefail

# ── Cấu hình ────────────────────────────────────────────────────────────────
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_BIN="$PROJECT_DIR/.venv-mac/bin"
SUMMARIZE="$VENV_BIN/ebook-summarize"

INBOX="$PROJECT_DIR/inbox"
OUTPUT="$PROJECT_DIR/output"
ORIGINALS="$OUTPUT/originals"
PROCESSING="$PROJECT_DIR/processing"
LOGS="$PROJECT_DIR/logs"
COVERS="$PROJECT_DIR/covers"

LOG_FILE="$LOGS/$(date +%Y-%m-%d).md"

# Nạp biến môi trường từ .env nếu có
if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$PROJECT_DIR/.env"
    set +a
fi

# ── Parse arguments ─────────────────────────────────────────────────────────
DRY_RUN=false
NO_TELEGRAM=false
EXTRA_ARGS=()
SPECIFIC_FILES=()

for arg in "$@"; do
    if [[ "$arg" == "--dry-run" ]]; then
        DRY_RUN=true
    elif [[ "$arg" == "--no-telegram" ]]; then
        NO_TELEGRAM=true
    elif [[ -f "$arg" ]]; then
        SPECIFIC_FILES+=("$arg")
    else
        EXTRA_ARGS+=("$arg")
    fi
done

# ── Hàm tiện ích ────────────────────────────────────────────────────────────
timestamp() { date "+%Y-%m-%d %H:%M:%S"; }

log() {
    local msg="[$(timestamp)] $1"
    echo "$msg"
    echo "$msg" >> "$LOG_FILE"
}

# Ghi một mục vào file log Markdown
log_section() {
    echo "" >> "$LOG_FILE"
    echo "### $1" >> "$LOG_FILE"
    echo "" >> "$LOG_FILE"
}

# ── Pre-flight checks ───────────────────────────────────────────────────────
if [[ ! -x "$SUMMARIZE" ]]; then
    echo "❌ Không tìm thấy $SUMMARIZE"
    echo "   Chạy: cd $PROJECT_DIR && .venv-mac/bin/pip install -e ."
    exit 1
fi

mkdir -p "$INBOX" "$OUTPUT" "$ORIGINALS" "$PROCESSING" "$LOGS"

# ── Thu thập file đầu vào ───────────────────────────────────────────────────
if [[ ${#SPECIFIC_FILES[@]} -gt 0 ]]; then
    FILES=("${SPECIFIC_FILES[@]}")
else
    shopt -s nullglob
    FILES=("$INBOX"/*.epub "$INBOX"/*.pdf "$INBOX"/*.EPUB "$INBOX"/*.PDF)
    shopt -u nullglob
fi

if [[ ${#FILES[@]} -eq 0 ]]; then
    echo "📭 Không có file mới trong inbox/. Không làm gì."
    exit 0
fi

# ── Bắt đầu ─────────────────────────────────────────────────────────────────
{
    echo "# Pipeline Log — $(date +%Y-%m-%d)"
    echo ""
    echo "- **Thời điểm bắt đầu:** $(timestamp)"
    echo "- **Số file trong inbox:** ${#FILES[@]}"
    echo "- **Extra args:** ${EXTRA_ARGS[*]:-_(không)_}"
    echo ""
} >> "$LOG_FILE"

echo "═══════════════════════════════════════════════════════════"
echo "  🏭 ebook-shortform Auto-Pipeline"
echo "  $(timestamp) — ${#FILES[@]} file(s) trong inbox/"
echo "═══════════════════════════════════════════════════════════"
echo ""

TOTAL=0
SUCCESS=0
FAILED=0
SKIPPED=0
TOTAL_TOKENS_IN=0
TOTAL_TOKENS_OUT=0

for filepath in "${FILES[@]}"; do
    TOTAL=$((TOTAL + 1))
    filename="$(basename "$filepath")"
    stem="${filename%.*}"

    # -- Chuẩn hóa tên file: loại bỏ noise tag thư viện sách (z_library, 1lib, libgen, v.v.) --
    clean_stem=$(python3 -c "
import sys, re
stem = sys.argv[1]
pattern = r'([_,\s\.\-]+|\b)[\(\[]?(?:z[-_]?library|1lib|z-lib|zlib|libgen)[\s\S]*$'
cleaned = re.sub(pattern, '', stem, flags=re.IGNORECASE)
cleaned = re.sub(r'[\s_,\.\-\(\)\[\]]+$', '', cleaned).strip()
print(cleaned if cleaned else stem)
" "$stem" 2>/dev/null || echo "$stem")

    if [[ "$clean_stem" != "$stem" ]]; then
        ext="${filename##*.}"
        clean_filename="${clean_stem}.${ext}"
        clean_path="$(dirname "$filepath")/$clean_filename"
        if [[ "$filepath" != "$clean_path" && ! -f "$clean_path" ]]; then
            mv "$filepath" "$clean_path"
            filepath="$clean_path"
            filename="$clean_filename"
            stem="$clean_stem"
            echo "   🏷️  Đã chuẩn hóa tên file: $filename"
        fi
    fi

    echo "────────────────────────────────────────────────────────"
    echo "📖 [$TOTAL/${#FILES[@]}] $filename"
    echo "────────────────────────────────────────────────────────"

    # -- Dry run: chỉ liệt kê --
    if $DRY_RUN; then
        size=$(du -h "$filepath" | cut -f1)
        echo "   📐 Kích thước: $size (dry-run, bỏ qua)"
        log_section "$filename"
        echo "- Kích thước: $size" >> "$LOG_FILE"
        echo "- Trạng thái: ⏭️ dry-run (bỏ qua)" >> "$LOG_FILE"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    # -- Bỏ qua nếu file vốn đã là file đã xử lý (_short, _shortform, _VN, .vi) --
    filename_lower=$(echo "$filename" | tr '[:upper:]' '[:lower:]')
    if [[ "$filename_lower" == *"_short."* || "$filename_lower" == *"_shortform."* || "$filename_lower" == *"_vn."* || "$filename_lower" == *".vi."* || "$filename_lower" == *"_vi."* ]]; then
        echo "   ⏭️  File đã qua xử lý ($filename), bỏ qua."
        log_section "$filename"
        echo "- Trạng thái: ⏭️ file đã xử lý, bỏ qua" >> "$LOG_FILE"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    # -- Kiểm tra trùng lặp: đã có output chưa? --
    if [[ -f "$OUTPUT/${stem}_short.epub" ]]; then
        echo "   ⏭️  Đã có ${stem}_short.epub trong output/, bỏ qua."
        log_section "$filename"
        echo "- Trạng thái: ⏭️ đã tồn tại, bỏ qua" >> "$LOG_FILE"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    # -- Di chuyển vào processing/ (nếu chưa ở đó) --
    if [[ "$filepath" != "$PROCESSING/$filename" ]]; then
        mv "$filepath" "$PROCESSING/$filename"
    fi
    processing_file="$PROCESSING/$filename"

    # -- Tìm ảnh bìa tùy chỉnh trong covers/ --
    COVER_ARG=""
    for ext in jpg jpeg png webp gif; do
        if [[ -f "$COVERS/$stem.$ext" ]]; then
            COVER_ARG="--cover $COVERS/$stem.$ext"
            echo "   🖼️  Tìm thấy bìa: $stem.$ext"
            break
        fi
    done

    # -- Chạy ebook-summarize --
    output_epub="$PROCESSING/${stem}_short.epub"
    analysis_json="$PROCESSING/${stem}_short.analysis.json"

    log_section "$filename"

    echo "   ⏳ Đang chạy ebook-summarize..."
    start_time=$(date +%s)

    # shellcheck disable=SC2086
    if "$SUMMARIZE" "$processing_file" \
        -o "$output_epub" \
        --keep-workdir \
        $COVER_ARG \
        ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} \
        2>&1 | tee -a "$LOG_FILE"; then

        end_time=$(date +%s)
        duration=$(( end_time - start_time ))
        duration_min=$(( duration / 60 ))

        # Trích token usage từ dòng cuối stdout (format: "Token đã dùng: X vào / Y ra")
        token_line=$(grep -o "Token đã dùng:.*" "$LOG_FILE" | tail -1 || true)
        tokens_in=$(echo "$token_line" | grep -o '[0-9,]*' | head -1 | tr -d ',' || echo "0")
        tokens_out=$(echo "$token_line" | grep -o '[0-9,]*' | sed -n '2p' | tr -d ',' || echo "0")
        TOTAL_TOKENS_IN=$(( TOTAL_TOKENS_IN + ${tokens_in:-0} ))
        TOTAL_TOKENS_OUT=$(( TOTAL_TOKENS_OUT + ${tokens_out:-0} ))

        # Chuyển kết quả vào output/
        [[ -f "$output_epub" ]] && mv "$output_epub" "$OUTPUT/"
        [[ -f "$analysis_json" ]] && mv "$analysis_json" "$OUTPUT/"

        # Chuyển file gốc vào output/originals/
        mv "$processing_file" "$ORIGINALS/$filename"

        # Xóa workdir (đã thành công, không cần cache nữa)
        workdir="$PROCESSING/${stem}_short.workdir"
        [[ -d "$workdir" ]] && rm -rf "$workdir"

        echo ""
        echo "   ✅ Thành công! (${duration_min}m$(( duration % 60 ))s)"
        echo "   → $OUTPUT/${stem}_short.epub"
        echo ""

        # Gửi tới Telegram nếu có cấu hình trong .env hoặc env vars (và không tắt bằng --no-telegram)
        if ! $NO_TELEGRAM && [[ -f "$PROJECT_DIR/scripts/send_to_telegram.py" ]]; then
            echo "   📤 Đang gửi file tới Telegram..."
            "$VENV_BIN/python" "$PROJECT_DIR/scripts/send_to_telegram.py" "$OUTPUT/${stem}_short.epub" \
                --caption "📚 <b>${stem}</b> (Tóm tắt chuyên sâu kiểu Shortform)" || true
        fi
        echo ""
        {
            echo "- Trạng thái: ✅ thành công"
            echo "- Thời gian: ${duration_min} phút $(( duration % 60 )) giây"
            echo "- Token: $token_line"
        } >> "$LOG_FILE"

        SUCCESS=$((SUCCESS + 1))
    else
        end_time=$(date +%s)
        duration=$(( end_time - start_time ))

        echo ""
        echo "   ❌ Lỗi! File giữ tại processing/$filename"
        echo "   Xem chi tiết: $LOG_FILE"
        echo ""
        {
            echo "- Trạng thái: ❌ LỖI"
            echo "- Thời gian chạy trước khi lỗi: $(( duration / 60 )) phút"
            echo "- File giữ tại: processing/$filename"
            echo "- Workdir cache: processing/${stem}_short.workdir (chạy lại sẽ tiếp tục)"
        } >> "$LOG_FILE"

        FAILED=$((FAILED + 1))
    fi
done

# ── Tổng kết ─────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  📊 Tổng kết Pipeline — $(date +%Y-%m-%d)"
echo "═══════════════════════════════════════════════════════════"
echo "  Tổng cộng:    $TOTAL file(s)"
echo "  ✅ Thành công: $SUCCESS"
echo "  ❌ Lỗi:        $FAILED"
echo "  ⏭️  Bỏ qua:    $SKIPPED"
if [[ $TOTAL_TOKENS_IN -gt 0 ]]; then
    echo "  🔤 Token tổng: $(printf "%'d" $TOTAL_TOKENS_IN) vào / $(printf "%'d" $TOTAL_TOKENS_OUT) ra"
fi
echo "  📝 Log:        $LOG_FILE"
echo "═══════════════════════════════════════════════════════════"

{
    echo ""
    echo "---"
    echo ""
    echo "## Tổng kết"
    echo ""
    echo "| Chỉ số | Giá trị |"
    echo "|:---|:---|"
    echo "| Tổng file | $TOTAL |"
    echo "| ✅ Thành công | $SUCCESS |"
    echo "| ❌ Lỗi | $FAILED |"
    echo "| ⏭️ Bỏ qua | $SKIPPED |"
    echo "| Token vào | $(printf "%'d" $TOTAL_TOKENS_IN) |"
    echo "| Token ra | $(printf "%'d" $TOTAL_TOKENS_OUT) |"
    echo "| Kết thúc lúc | $(timestamp) |"
} >> "$LOG_FILE"

# Exit code khác 0 nếu có file lỗi — để Antigravity biết cần thông báo
[[ $FAILED -gt 0 ]] && exit 1
exit 0
