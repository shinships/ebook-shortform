"""recommender.py — Module tuyển chọn và gợi ý sách mới, high-rating (Amazon & Goodreads).

Tính năng:
1. Tuyển chọn sách Non-fiction, Kinh doanh, Công nghệ/AI, Tâm lý học, Năng suất...
   mới nhất hoặc được đánh giá cao nhất (Goodreads 4.2+ ⭐, Amazon 4.5+ ⭐).
2. Chống lặp (Deduplication) qua logs/.recommended_books_history.json.
3. Quản lý danh sách muốn đọc (Wishlist) qua logs/.book_wishlist.json.
4. Sinh bản tóm tắt nhanh 1-trang (Instant Brief) theo phong cách Shortform.
5. Định dạng tin nhắn Telegram HTML trực quan, chuyên nghiệp kèm Inline Keyboard.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parents[2]
LOGS_DIR = PROJECT_DIR / "logs"
HISTORY_FILE = LOGS_DIR / ".recommended_books_history.json"
WISHLIST_FILE = LOGS_DIR / ".book_wishlist.json"

CATEGORIES = {
    "all": "🌟 Toàn Diện (Tổng Hợp)",
    "business": "💼 Kinh Doanh & Chiến Lược",
    "psychology": "🧠 Tâm Lý & Ra Quyết Định",
    "tech": "🤖 Công Nghệ & AI",
    "productivity": "⚡ Năng Suất & Tư Duy Sâu",
}

# Hạt giống chất lượng cao dự phòng (fallback curation) phòng trường hợp mất mạng / hết quota LLM
SEED_RECOMMENDATIONS: list[dict[str, Any]] = [
    {
        "id": "nexus-harari-2024",
        "title": "Nexus: A Brief History of Information Networks from the Stone Age to AI",
        "title_vi": "Nexus: Lược Sử Các Mạng Lưới Thông Tin Từ Thời Đồ Đá Đến Kỷ Nguyên AI",
        "author": "Yuval Noah Harari",
        "year": 2024,
        "category": "tech",
        "ratings": {
            "goodreads": "4.2/5 (32,000+ đánh giá)",
            "amazon": "4.5/5 (6,200+ đánh giá)",
        },
        "one_sentence_hook": "Thông tin không phải là chân lý; thông tin là mạng lưới kết nối quyền lực, và AI là tác nhân đầu tiên có thể tự tạo ra ý tưởng và lũng đoạn mạng lưới đó.",
        "why_read": "Harari giải mã cách thức AI đe dọa nền dân chủ không phải bằng sức mạnh hủy diệt vật lý mà bằng việc kiểm soát hệ thống tạo lập ý nghĩa và câu chuyện của nhân loại.",
        "key_takeaways": [
            "Mạng thông tin luôn đánh đổi giữa chân lý (truth) và trật tự (order).",
            "Khác với máy in hay bom nguyên tử, AI có khả năng tự sáng tạo nội dung và thiết lập quan hệ tâm lý với con người.",
            "Cần cơ chế tự hiệu chỉnh độc lập (self-correcting mechanisms) như báo chí tự do để kiềm chế sai lầm của thuật toán.",
        ],
    },
    {
        "id": "slow-productivity-newport-2024",
        "title": "Slow Productivity: The Lost Art of Accomplishment Without Burnout",
        "title_vi": "Năng Suất Chậm Rãi: Nghệ Thuật Đạt Thành Tựu Lớn Mà Không Kiệt Sức",
        "author": "Cal Newport",
        "year": 2024,
        "category": "productivity",
        "ratings": {
            "goodreads": "4.1/5 (24,000+ đánh giá)",
            "amazon": "4.6/5 (3,100+ đánh giá)",
        },
        "one_sentence_hook": "Giải thoát lao động tri thức khỏi cái bẫy 'năng suất giả tạo' (pseudo-productivity) để tập trung tạo ra tác phẩm mang giá trị lâu dài.",
        "why_read": "Cal Newport tiếp nối tư tưởng Deep Work với giải pháp thiết thực cho sự quá tải của giới trí thức thời đại số bằng 3 nguyên lý tối giản đột phá.",
        "key_takeaways": [
            "Làm ít việc hơn cùng một lúc (Do fewer things) để tạo không gian nhận thức.",
            "Làm việc theo nhịp điệu tự nhiên (Work at a natural pace) thay vì duy trì cường độ cao liên tục.",
            "Bị ám ảnh bởi chất lượng (Obsess over quality) để thành quả tự bảo vệ cho bạn.",
        ],
    },
    {
        "id": "supercommunicators-duhigg-2024",
        "title": "Supercommunicators: How to Unlock the Secret Language of Connection",
        "title_vi": "Bậc Thầy Giao Tiếp: Khơi Mở Ngôn Ngữ Bí Mật Của Sự Kết Nối",
        "author": "Charles Duhigg",
        "year": 2024,
        "category": "psychology",
        "ratings": {
            "goodreads": "4.2/5 (19,000+ đánh giá)",
            "amazon": "4.6/5 (2,800+ đánh giá)",
        },
        "one_sentence_hook": "Mọi cuộc giao tiếp đều thuộc 1 trong 3 dạng (thực tế, cảm xúc, hoặc xã hội), và mâu thuẫn chỉ xảy ra khi hai người đang nói ở hai tầng hội thoại khác nhau.",
        "why_read": "Tác giả của 'Sức Mạnh Của Thói Quen' giải phẫu khoa học thần kinh đằng sau những cuộc đối thoại có sức lay động sâu sắc nhất.",
        "key_takeaways": [
            "Nhận diện 3 loại hội thoại: Thực tế (Cần giải pháp), Cảm xúc (Cần thấu cảm), Xã hội (Cần danh tính/vị thế).",
            "Quy tắc phù hợp (The Matching Principle): Chỉ giao tiếp hiệu quả khi cả hai cùng bước vào một kiểu hội thoại.",
            "Kỹ thuật lắng nghe sâu qua việc lặp lại và xác thực lại hiểu biết (Looping for understanding).",
        ],
    },
    {
        "id": "right-kind-of-wrong-edmondson-2023",
        "title": "Right Kind of Wrong: The Science of Failing Well",
        "title_vi": "Sai Lầm Đáng Giá: Khoa Học Về Thất Bại Thông Minh",
        "author": "Amy C. Edmondson",
        "year": 2023,
        "category": "business",
        "ratings": {
            "goodreads": "4.2/5 (8,500+ đánh giá)",
            "amazon": "4.6/5 (1,400+ đánh giá)",
        },
        "one_sentence_hook": "Không phải thất bại nào cũng tốt: Hãy phân biệt rạch ròi giữa sai lầm cẩu thả và 'thất bại thông minh' (intelligent failure) mang lại phát kiến mới.",
        "why_read": "Giáo sư Amy Edmondson (người tiên phong về Khái niệm An toàn Tâm lý tại Harvard) cung cấp khung tư duy thực chiến để biến thất bại thành lợi thế cạnh tranh.",
        "key_takeaways": [
            "3 kiểu thất bại: Cơ bản (do bất cẩn), Phức tạp (do hệ thống nhiều yếu tố) và Thông minh (thử nghiệm ở địa hạt mới).",
            "Thất bại thông minh phải diễn ra trong bối cảnh chưa có tiền lệ, hướng đến mục tiêu rõ ràng và có chi phí tổn thất nhỏ nhất có thể.",
            "Xây dựng văn hóa an toàn tâm lý để mọi người dám báo cáo sự cố sớm thay vì che giấu.",
        ],
    },
    {
        "id": "co-intelligence-mollick-2024",
        "title": "Co-Intelligence: Living and Working with AI",
        "title_vi": "Đồng Trí Tuệ: Sống Và Làm Việc Cùng Trí Tuệ Nhân Tạo",
        "author": "Ethan Mollick",
        "year": 2024,
        "category": "tech",
        "ratings": {
            "goodreads": "4.3/5 (15,000+ đánh giá)",
            "amazon": "4.6/5 (2,200+ đánh giá)",
        },
        "one_sentence_hook": "Đối xử với AI như một đồng nghiệp thông minh nhưng lập dị (alien intern), thay vì chỉ coi nó là một cỗ máy tìm kiếm hay công cụ thụ động.",
        "why_read": "Cuốn cẩm nang thực chiến hàng đầu về ứng dụng Generative AI trong công việc quản trị, sáng tạo và giải quyết vấn đề từ Giáo sư Wharton.",
        "key_takeaways": [
            "4 nguyên tắc vàng: Luôn mời AI vào cuộc trò chuyện, Hãy là người chịu trách nhiệm cuối, Coi AI như một người và Giả định đây là AI tệ nhất bạn từng dùng.",
            "Biên giới răng cưa (Jagged Frontier): AI làm xuất sắc việc rất khó nhưng có thể vấp ngã ở việc tưởng chừng cực kỳ đơn giản.",
            "Tái định hình năng lực cạnh tranh: Chuyển từ kỹ năng thực thi chuyên môn sang kỹ năng thẩm định và điều phối.",
        ],
    },
    {
        "id": "same-as-ever-housel-2023",
        "title": "Same as Ever: A Guide to What Never Changes",
        "title_vi": "Muôn Đời Như Một: Kim Chỉ Nam Về Những Điều Bất Biến Giữa Thế Giới Biến Động",
        "author": "Morgan Housel",
        "year": 2023,
        "category": "business",
        "ratings": {
            "goodreads": "4.3/5 (45,000+ đánh giá)",
            "amazon": "4.7/5 (5,800+ đánh giá)",
        },
        "one_sentence_hook": "Thay vì đoán xem tương lai sẽ thay đổi thế nào trong 10 năm tới, hãy đầu tư vào những bản tính con người không bao giờ thay đổi.",
        "why_read": "Morgan Housel (tác giả Tâm Lý Học Về Tiền) tổng kết 23 câu chuyện ngắn giàu chiều sâu triết học về rủi ro, lòng tham, nỗi sợ và bản chất của thành công.",
        "key_takeaways": [
            "Lịch sử đầy rẫy những bất ngờ bất khả tri, nhưng phản ứng tâm lý của con người trước biến cố thì luôn lặp lại y hệt.",
            "Hiệu quả quá mức sẽ làm giảm tính bền vững: Mọi hệ thống khỏe mạnh đều cần có một khoảng đệm hao phí (slack).",
            "Cái giá của sự kiên định thường vô hình, và thành công bền vững đòi hỏi khả năng chịu đựng những giai đoạn nhàm chán.",
        ],
    },
]


class BookRecommender:
    """Quản lý tuyển chọn sách, lưu lịch sử và sinh nội dung tóm tắt."""

    def __init__(self):
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self.history = self._load_history()
        self.wishlist = self._load_wishlist()

    def _load_history(self) -> dict[str, Any]:
        if HISTORY_FILE.exists():
            try:
                return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"recommended_ids": [], "weekly_issues": {}}

    def _save_history(self) -> None:
        try:
            HISTORY_FILE.write_text(json.dumps(self.history, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[Recommender] Lỗi lưu history: {e}", file=sys.stderr)

    def _load_wishlist(self) -> dict[str, list[dict[str, Any]]]:
        if WISHLIST_FILE.exists():
            try:
                return json.loads(WISHLIST_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_wishlist(self) -> None:
        try:
            WISHLIST_FILE.write_text(json.dumps(self.wishlist, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[Recommender] Lỗi lưu wishlist: {e}", file=sys.stderr)

    def add_to_wishlist(self, user_id: str | int, book: dict[str, Any]) -> bool:
        """Lưu một cuốn sách vào Wishlist của người dùng."""
        u_key = str(user_id)
        if u_key not in self.wishlist:
            self.wishlist[u_key] = []

        # Kiểm tra trùng lặp
        book_id = book.get("id") or book.get("title", "")
        for item in self.wishlist[u_key]:
            if (item.get("id") and item.get("id") == book_id) or item.get("title") == book.get("title"):
                return False  # Đã có trong danh sách

        saved_item = {
            "id": book_id,
            "title": book.get("title"),
            "title_vi": book.get("title_vi", book.get("title")),
            "author": book.get("author", "N/A"),
            "ratings": book.get("ratings", {}),
            "added_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        self.wishlist[u_key].append(saved_item)
        self._save_wishlist()
        return True

    def get_wishlist(self, user_id: str | int) -> list[dict[str, Any]]:
        """Lấy danh sách muốn đọc của người dùng."""
        return self.wishlist.get(str(user_id), [])

    def make_urls(self, title: str, author: str) -> tuple[str, str]:
        """Tạo search URL dẫn thẳng đến Goodreads và Amazon."""
        query = f"{title} {author}".strip()
        encoded = urllib.parse.quote_plus(query)
        goodreads_url = f"https://www.goodreads.com/search?q={encoded}"
        amazon_url = f"https://www.amazon.com/s?k={encoded}&i=stripbooks"
        return goodreads_url, amazon_url

    def get_weekly_recommendations(
        self,
        category: str = "all",
        force_refresh: bool = False,
        count: int = 3,
    ) -> dict[str, Any]:
        """
        Lấy bộ sách gợi ý cho tuần hiện tại (hoặc theo danh mục yêu cầu).
        Sử dụng ISO week string (ví dụ: '2026-W36') để định danh tuần.
        """
        now = datetime.datetime.now()
        current_week_key = f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"
        issue_key = f"{current_week_key}_{category}"

        # Nếu đã có lưu trong tuần và không force refresh -> tái sử dụng
        if not force_refresh and issue_key in self.history.get("weekly_issues", {}):
            issue = self.history["weekly_issues"][issue_key]
            return issue

        # Nếu cần tạo mới: Thử gọi LLM với Gemini, nếu lỗi/hết quota -> dùng Seed Fallback
        books = self._curate_books_with_llm(category=category, count=count)
        if not books:
            books = self._get_fallback_books(category=category, count=count)

        # Bổ sung link tra cứu và chuẩn hóa ID
        for b in books:
            title = b.get("title", "")
            author = b.get("author", "")
            b["goodreads_url"], b["amazon_url"] = self.make_urls(title, author)
            if not b.get("id"):
                b["id"] = re.sub(r"[^\w]+", "-", f"{title}-{author}").lower().strip("-")[:40]

        # Ghi nhớ vào history
        recommended_ids = set(self.history.get("recommended_ids", []))
        for b in books:
            recommended_ids.add(b["id"])
        self.history["recommended_ids"] = list(recommended_ids)

        issue_data = {
            "week_key": current_week_key,
            "category": category,
            "category_name": CATEGORIES.get(category, category),
            "generated_at": now.strftime("%d/%m/%Y %H:%M"),
            "books": books,
        }

        if "weekly_issues" not in self.history:
            self.history["weekly_issues"] = {}
        self.history["weekly_issues"][issue_key] = issue_data
        self._save_history()

        return issue_data

    def _curate_books_with_llm(self, category: str, count: int) -> list[dict[str, Any]] | None:
        """Gọi LLMClient tuyển chọn sách chất lượng cao từ Amazon & Goodreads."""
        try:
            from ebook_translator.core.llm import LLMClient

            # Ưu tiên gemini-3.5-flash hoặc gemini-3.7-flash
            llm = LLMClient(model="gemini-3.5-flash")
        except Exception as e:
            print(f"[Recommender] Không thể khởi tạo LLMClient: {e}", file=sys.stderr)
            return None

        recent_ids = self.history.get("recommended_ids", [])[-20:]
        category_desc = CATEGORIES.get(category, "Non-fiction tổng hợp")

        system_prompt = (
            "Bạn là Chuyên gia Tuyển chọn Sách Hàng đầu & Biên tập viên Trưởng theo phương pháp Shortform.\n"
            "Nhiệm vụ của bạn là tuyển chọn những cuốn sách phi hư cấu (Non-fiction) xuất sắc nhất, "
            "đang có xếp hạng cao vượt trội (Goodreads từ 4.1⭐, Amazon từ 4.5⭐) và được giới học giả, lãnh đạo đánh giá cao.\n"
            "QUY TẮC BẮT BUỘC:\n"
            "1. Bắt buộc trả về đúng định dạng JSON chuẩn (JSON Array of Objects), không kèm lời dẫn hay markdown thừa.\n"
            "2. Trong các trường văn bản, KHÔNG dùng dấu ngoặc kép (\") bên trong câu, hãy dùng dấu ngoặc đơn (') để tránh lỗi cú pháp JSON."
        )

        user_prompt = f"""Hãy chọn lọc {count} cuốn sách TIẾNG ANH nổi bật nhất thuộc chủ đề: "{category_desc}".
Ưu tiên sách phát hành từ năm 2023 - 2026 (hoặc sách kinh điển có bản cập nhật mới), có ảnh hưởng lớn trên Amazon & Goodreads.
LƯU Ý: Tránh lặp lại các cuốn sách sau (đã giới thiệu gần đây): {', '.join(recent_ids) if recent_ids else 'Không có'}.

Trả về JSON Array với định dạng cấu trúc chính xác sau cho từng cuốn sách:
[
  {{
    "title": "Tên sách tiếng Anh chuẩn",
    "title_vi": "Tựa đề tiếng Việt gợi ý hay và cuốn hút",
    "author": "Tên tác giả",
    "year": 2024,
    "category": "{category if category != 'all' else 'business'}",
    "ratings": {{
      "goodreads": "4.3/5 (18,000+ đánh giá)",
      "amazon": "4.6/5 (3,200+ đánh giá)"
    }},
    "one_sentence_hook": "1 câu nêu bật luận đề đột phá nhất làm thay đổi góc nhìn",
    "why_read": "Lý do nên đọc dưới góc nhìn Shortform (2-3 câu phân tích sâu sắc)",
    "key_takeaways": [
      "Ý tưởng cốt lõi 1",
      "Ý tưởng cốt lõi 2",
      "Ý tưởng cốt lõi 3"
    ]
  }}
]"""

        try:
            raw_resp = llm.complete(
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                max_tokens=4000,
                json_mode=True,
            )
            # Làm sạch phản hồi JSON
            cleaned = raw_resp.strip()
            if "```json" in cleaned:
                cleaned = cleaned.split("```json", 1)[1]
            if "```" in cleaned:
                cleaned = cleaned.split("```", 1)[0]
            cleaned = cleaned.strip()

            parsed = json.loads(cleaned)
            if isinstance(parsed, list) and len(parsed) > 0:
                return parsed
        except Exception as e:
            print(f"[Recommender] Lỗi khi gọi LLM tuyển chọn sách: {e}", file=sys.stderr)

        return None

    def _get_fallback_books(self, category: str, count: int) -> list[dict[str, Any]]:
        """Lấy sách từ kho hạt giống chất lượng cao khi ngoại tuyến."""
        if category != "all":
            matched = [b for b in SEED_RECOMMENDATIONS if b.get("category") == category]
            if matched:
                return matched[:count]

        # Lấy xoay vòng theo ngày/tuần
        import random
        shuffled = list(SEED_RECOMMENDATIONS)
        random.shuffle(shuffled)
        return shuffled[:count]

    def generate_book_brief(self, book: dict[str, Any]) -> str:
        """Sinh bản tóm tắt nhanh 1 trang (Instant Brief) theo phong cách Shortform."""
        try:
            from ebook_translator.core.llm import LLMClient
            llm = LLMClient(model="gemini-3.5-flash")

            system_prompt = (
                "Bạn là Chuyên gia Phân tích Tri thức Shortform. Hãy tạo một bản tóm tắt điều hành 1 trang "
                "(Executive 1-Page Summary) cho cuốn sách sau. Văn phong sắc sảo, gãy gọn, giàu tính hành động. "
                "Độ dài khoảng 300 - 450 từ tiếng Việt."
            )
            user_prompt = f"""
Sách: {book.get('title')} ({book.get('title_vi')})
Tác giả: {book.get('author')} (Năm: {book.get('year')})
Luận đề cơ bản: {book.get('one_sentence_hook')}
Đánh giá: Goodreads {book.get('ratings', {}).get('goodreads')}, Amazon {book.get('ratings', {}).get('amazon')}

Hãy xuất bản bản tóm tắt theo bố cục chuẩn:
🎯 **LUẬN ĐỀ CỐT LÕI (THE CORE THESIS)** (1 câu duy nhất)
🏛️ **3 TRỤ CỘT TƯ DUY (KEY PILLARS)** (Phân tích mạch lạc 3 luận điểm đột phá)
🔍 **GÓC NHÌN PHẢN BIỆN & ĐIỂM MÙ (SHORTFORM NOTES)** (So sánh với các tác phẩm cùng đề tài hoặc hạn chế cần lưu ý)
⚡ **NGUYÊN TẮC THỰC HÀNH NGAY (HEURISTIC RULE)** (1 quy tắc ngón tay cái có thể áp dụng ngay hôm nay)
"""
            resp = llm.complete(
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                max_tokens=1500,
            )
            return resp
        except Exception as e:
            # Fallback nếu LLM bận
            takeaways = "\n".join([f"• {t}" for t in book.get("key_takeaways", [])])
            return (
                f"🎯 <b>LUẬN ĐỀ CỐT LÕI:</b>\n<i>\"{book.get('one_sentence_hook')}\"</i>\n\n"
                f"💡 <b>TẠI SAO NÊN ĐỌC (GÓC NHÌN SHORTFORM):</b>\n{book.get('why_read')}\n\n"
                f"🏛️ <b>CÁC ĐIỂM CHẠM QUAN TRỌNG:</b>\n{takeaways}\n\n"
                f"⭐ Đánh giá: Goodreads: <b>{book.get('ratings', {}).get('goodreads')}</b> | Amazon: <b>{book.get('ratings', {}).get('amazon')}</b>"
            )

    def format_telegram_digest(self, issue: dict[str, Any]) -> str:
        """Format bản tin gợi ý sách hàng tuần sang HTML Telegram chuẩn."""
        week_key = issue.get("week_key", "")
        cat_name = issue.get("category_name", "Toàn diện")
        books = issue.get("books", [])

        lines = [
            f"🌟 <b>RADAR SÁCH HAY TUẦN NÀY — {week_key}</b>",
            f"<i>Chuyên mục: {cat_name} | Amazon & Goodreads High-Rating</i>",
            "━━━━━━━━━━━━━━━━━━━━\n",
        ]

        for i, b in enumerate(books, 1):
            title = b.get("title", "")
            title_vi = b.get("title_vi", "")
            author = b.get("author", "")
            year = b.get("year", "")
            ratings = b.get("ratings", {})
            gr_rating = ratings.get("goodreads", "4.3★")
            amz_rating = ratings.get("amazon", "4.6★")
            hook = b.get("one_sentence_hook", "")
            why = b.get("why_read", "")

            lines.append(f"<b>{i}. {title}</b>")
            if title_vi:
                lines.append(f"🇻🇳 <i>{title_vi}</i>")
            lines.append(f"✍️ Tác giả: <b>{author}</b> ({year})")
            lines.append(f"⭐ <b>Đánh giá:</b> Goodreads <code>{gr_rating}</code> | Amazon <code>{amz_rating}</code>")
            lines.append(f"🎯 <b>Luận đề:</b> {hook}")
            lines.append(f"💡 <b>Shortform Insight:</b> {why}")

            # Takeaways
            takeaways = b.get("key_takeaways", [])
            if takeaways:
                lines.append("📌 <b>Điểm sáng cốt lõi:</b>")
                for t in takeaways[:2]:
                    lines.append(f"  • {t}")

            lines.append(f"🔗 <a href=\"{b.get('goodreads_url')}\">Goodreads</a> | <a href=\"{b.get('amazon_url')}\">Amazon</a>\n")

        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("💡 <i>Bấm nút bên dưới để đọc bản tóm tắt 1 phút hoặc lưu vào Wishlist của bạn!</i>")

        return "\n".join(lines)


# Singleton instance tiện dụng
book_recommender = BookRecommender()
