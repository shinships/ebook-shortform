#!/usr/bin/env bash
# =============================================================================
# setup_schedule.sh — Quản lý tự động hóa macOS LaunchAgent (Lịch 12:10 & Telegram Bot)
#
# Cách dùng:
#   ./scripts/setup_schedule.sh status            # Xem trạng thái cả 2 dịch vụ
#   ./scripts/setup_schedule.sh install-all       # Cài đặt cả 2 dịch vụ ngầm
#
#   -- Dịch vụ Lịch chạy 12:10 hàng ngày:
#   ./scripts/setup_schedule.sh schedule-install
#   ./scripts/setup_schedule.sh schedule-uninstall
#   ./scripts/setup_schedule.sh schedule-run
#
#   -- Dịch vụ Telegram Inbound Bot (24/7):
#   ./scripts/setup_schedule.sh bot-install
#   ./scripts/setup_schedule.sh bot-status
#   ./scripts/setup_schedule.sh bot-uninstall
#   ./scripts/setup_schedule.sh bot-logs
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# 1. Daily Schedule Service
DAILY_PLIST="com.mktmda.ebook-shortform.plist"
DAILY_SRC="$SCRIPT_DIR/$DAILY_PLIST"
DAILY_DEST="$HOME/Library/LaunchAgents/$DAILY_PLIST"

# 2. Telegram Bot Service
BOT_PLIST="com.mktmda.ebook-telegram-bot.plist"
BOT_SRC="$SCRIPT_DIR/$BOT_PLIST"
BOT_DEST="$HOME/Library/LaunchAgents/$BOT_PLIST"

mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT_DIR/logs"

ACTION="${1:-status}"

install_service() {
    local src="$1"
    local dest="$2"
    local label="$3"

    if launchctl list | grep -q "$label"; then
        launchctl unload "$dest" 2>/dev/null || true
    fi
    cp "$src" "$dest"
    launchctl load "$dest"
}

uninstall_service() {
    local dest="$1"
    local label="$2"
    if [[ -f "$dest" ]]; then
        launchctl unload "$dest" 2>/dev/null || true
        rm -f "$dest"
    fi
}

case "$ACTION" in
    # ── Status chung ──
    status)
        echo "═══════════════════════════════════════════════════════════"
        echo "  📊 TRẠNG THÁI CÁC DỊCH VỤ TỰ ĐỘNG HÓA MACOS"
        echo "═══════════════════════════════════════════════════════════"
        echo ""
        echo "1️⃣  Lịch quét tự động 12:10 hàng ngày:"
        if launchctl list | grep -q "com.mktmda.ebook-shortform"; then
            echo "   🟢 Đang HOẠT ĐỘNG (12:10 mỗi ngày)"
        else
            echo "   ⚪ Chưa kích hoạt (Bật bằng: ./scripts/setup_schedule.sh schedule-install)"
        fi
        echo ""
        echo "2️⃣  Telegram Inbound Bot (Tiếp nhận sách từ điện thoại 24/7):"
        if launchctl list | grep -q "com.mktmda.ebook-telegram-bot"; then
            echo "   🟢 Đang HOẠT ĐỘNG (Lắng nghe tin nhắn Telegram ngầm)"
        else
            echo "   ⚪ Chưa kích hoạt (Bật bằng: ./scripts/setup_schedule.sh bot-install)"
        fi
        echo ""
        echo "═══════════════════════════════════════════════════════════"
        ;;

    install-all)
        echo "📦 Đang cài đặt cả 2 dịch vụ tự động hóa..."
        install_service "$DAILY_SRC" "$DAILY_DEST" "com.mktmda.ebook-shortform"
        install_service "$BOT_SRC" "$BOT_DEST" "com.mktmda.ebook-telegram-bot"
        echo "✅ Đã kích hoạt cả Lịch 12:10 và Telegram Inbound Bot thành công!"
        ;;

    # ── Quản lý Lịch 12:10 ──
    install|schedule-install)
        echo "📦 Đang cài đặt Lịch chạy 12:10 mỗi ngày..."
        install_service "$DAILY_SRC" "$DAILY_DEST" "com.mktmda.ebook-shortform"
        echo "✅ Đã kích hoạt lịch chạy 12:10 hàng ngày!"
        ;;

    schedule-run|run-now)
        echo "🚀 Kích hoạt chạy pipeline ngay bây giờ qua LaunchAgent..."
        if launchctl list | grep -q "com.mktmda.ebook-shortform"; then
            launchctl start com.mktmda.ebook-shortform
            echo "✅ Đã gửi tín hiệu! Xem log: tail -f $PROJECT_DIR/logs/launchd.stdout.log"
        else
            "$PROJECT_DIR/auto-pipeline.sh"
        fi
        ;;

    uninstall|schedule-uninstall)
        echo "🗑️  Đang hủy lịch chạy 12:10..."
        uninstall_service "$DAILY_DEST" "com.mktmda.ebook-shortform"
        echo "✅ Đã gỡ bỏ lịch chạy 12:10."
        ;;

    # ── Quản lý Telegram Inbound Bot ──
    bot-install)
        echo "🤖 Đang cài đặt dịch vụ Telegram Inbound Bot 24/7..."
        install_service "$BOT_SRC" "$BOT_DEST" "com.mktmda.ebook-telegram-bot"
        echo "✅ Telegram Bot đã được nạp vào macOS LaunchAgent!"
        echo "📝 Log bot: $PROJECT_DIR/logs/telegram_bot.stdout.log"
        ;;

    bot-status)
        echo "🤖 Trạng thái Telegram Bot Daemon:"
        if launchctl list | grep -q "com.mktmda.ebook-telegram-bot"; then
            echo "   🟢 Đang HOẠT ĐỘNG"
            launchctl list | grep "com.mktmda.ebook-telegram-bot"
        else
            echo "   ⚪ Chưa chạy. Bật bằng: ./scripts/setup_schedule.sh bot-install"
        fi
        ;;

    bot-logs)
        tail -f "$PROJECT_DIR/logs/telegram_bot.stdout.log" "$PROJECT_DIR/logs/telegram_bot.stderr.log"
        ;;

    bot-uninstall)
        echo "🗑️  Đang dừng và gỡ bỏ Telegram Bot..."
        uninstall_service "$BOT_DEST" "com.mktmda.ebook-telegram-bot"
        echo "✅ Đã gỡ bỏ Telegram Bot."
        ;;

    *)
        echo "Lựa chọn không hợp lệ: $ACTION"
        echo "Cách dùng: $0 {status|install-all|schedule-install|schedule-uninstall|bot-install|bot-status|bot-logs|bot-uninstall}"
        exit 1
        ;;
esac
