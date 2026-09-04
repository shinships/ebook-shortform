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
#   ./auto-pipeline.sh --anthropic      # dùng Claude
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

# ── Parse arguments ─────────────────────────────────────────────────────────
DRY_RUN=false
EXTRA_ARGS=()

for arg in "$@"; do
    if [[ "$arg" == "--dry-run" ]]; then
        DRY_RUN=true
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
shopt -s nullglob
FILES=("$INBOX"/*.epub "$INBOX"/*.pdf "$INBOX"/*.EPUB "$INBOX"/*.PDF)
shopt -u nullglob

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

    # -- Kiểm tra trùng lặp: đã có output chưa? --
    if [[ -f "$OUTPUT/${stem}_short.epub" ]]; then
        echo "   ⏭️  Đã có ${stem}_short.epub trong output/, bỏ qua."
        log_section "$filename"
        echo "- Trạng thái: ⏭️ đã tồn tại, bỏ qua" >> "$LOG_FILE"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    # -- Di chuyển vào processing/ --
    mv "$filepath" "$PROCESSING/$filename"
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
        "${EXTRA_ARGS[@]}" \
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
        echo "   ✅ Thành công! (${duration_min}m${(( duration % 60 ))}s)"
        echo "   → $OUTPUT/${stem}_short.epub"
        echo ""

        # Gửi tới Telegram nếu có cấu hình trong .env hoặc env vars
        if [[ -f "$PROJECT_DIR/scripts/send_to_telegram.py" ]]; then
            echo "   📤 Đang gửi file tới Telegram..."
            python3 "$PROJECT_DIR/scripts/send_to_telegram.py" "$OUTPUT/${stem}_short.epub" \
                --caption "📚 <b>${stem}</b> (Tóm tắt chuyên sâu kiểu Shortform)" || true
        fi
        echo ""
        {
            echo "- Trạng thái: ✅ thành công"
            echo "- Thời gian: ${duration_min} phút ${(( duration % 60 ))} giây"
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
