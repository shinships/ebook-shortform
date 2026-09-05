# Sổ Tay Điều Phối Thực Thi Dành Cho Agent (Execution Runbook)

Tài liệu này hướng dẫn chi tiết cách Agent tiếp nhận, giải quyết và tối ưu hóa các kịch bản đầu vào khi người dùng kích hoạt skill `shortform_reading`.

---

## 1. Xử Lý Các Kịch Bản Đầu Vào

### Kịch bản A: Người dùng cung cấp đầy đủ văn bản (`input_text`)
1. **Phân tích bóc tách (Deconstruction):**
   - Đọc kỹ toàn bộ văn bản đầu vào.
   - Trích xuất luận điểm chính, số liệu thực nghiệm, case study thực tế và các khái niệm mà tác giả trực tiếp định nghĩa.
2. **Khắc phục hiện tượng tóm tắt bề mặt:**
   - Không chỉ trích lọc câu chủ đề của các đoạn văn.
   - Đặt câu hỏi: *Logic ngầm đằng sau câu chuyện này là gì? Đâu là giả định tiền đề của tác giả?*

### Kịch bản B: Người dùng chỉ cung cấp tên sách (`book_title`) và tác giả (`author`)
1. **Khai thác kho tri thức chuyên sâu:**
   - Kích hoạt vốn hiểu biết về tác phẩm, tác giả và bối cảnh xuất bản.
   - Nếu cần xác minh chi tiết quan trọng, chủ động tìm kiếm các nguồn học thuật tin cậy.
2. **Nguyên tắc chống ảo giác (Zero-Hallucination Policy):**
   - Không bịa đặt số liệu nghiên cứu, trích dẫn giả mạo hoặc ví dụ không có trong sách.
   - Nếu một chi tiết là suy luận mở rộng hoặc liên hệ của Agent, phải đặt rõ ràng trong khối `[SHORTFORM NOTE: GÓC NHÌN ĐA CHIỀU]` hoặc ghi chú thích minh bạch.

### Kịch bản C: Văn bản gốc rất dài (Nhiều chương hoặc toàn bộ Ebook)
1. **Áp dụng mô hình Map-Reduce:**
   - **Giai đoạn 1 (Map):** Quét từng phần/chương, trích xuất danh sách luận điểm cốt lõi, ví dụ đắt giá và khái niệm chìa khóa.
   - **Giai đoạn 2 (Reduce):** Xâu chuỗi các ý tưởng thành 3 Trụ cột tư duy chính của tác phẩm để tránh tình trạng phân mảnh hoặc lặp lại ý.

---

## 2. Xử Lý Tham Số `reading_depth`

### 1. Khi `reading_depth: "executive_1_page"`
- Tập trung tối đa vào tốc độ tiếp thu và bức tranh tổng thể.
- Xuất bản:
  - Header (Tiêu đề, Tác giả, Thời gian đọc, Luận đề cốt lõi).
  - **Phần 1: Bản Tóm Tắt 1 Trang (1-Page Summary)** đầy đủ (Vấn đề thực tế, Giải pháp đột phá, 3 Trụ cột, Đánh giá giá trị & Rào cản).
  - Trích xuất ngay **Bảng Quy tắc Thực thi (Heuristics Matrix)** từ Phần 3 để người đọc có ngay hành vi thực tiễn.
  - Tối ưu cho việc đọc trong 3–5 phút.

### 2. Khi `reading_depth: "full_chapter_guide"` (Mặc định)
- Xuất bản đầy đủ cả 3 phần theo chuẩn `Output Template`.
- Mỗi trụ cột tư duy tại Phần 2 phải được phân tích sâu:
  - Giải thích cơ chế vận hành từ gốc rễ.
  - So sánh trực quan: *Sai lầm bản năng* vs. *Mô hình chuẩn xác*.
  - Khối callout `[SHORTFORM NOTE: GÓC NHÌN ĐA CHIỀU]` đầy đủ 3 thành tố: Liên kết chéo, Phản biện & Điểm mù, Bối cảnh đương đại.
- Phần 3 cung cấp cả Bảng Heuristics và Bài tập phản tư 3 bước.

---

## 3. Cá Nhân Hóa Theo `user_context`

Nếu người dùng truyền tham số `user_context` (hoặc đề cập trong lời nhắc):
- **Ví dụ đối sánh thực tiễn:** Thay vì dùng ví dụ tổng quát, hãy chuyển thể ví dụ đối sánh sang đúng ngành của người dùng (ví dụ: đối với Product Manager, so sánh giữa việc liên tục trả lời tin nhắn Slack vs. khóa 3 tiếng giải quyết spec tính năng).
- **Phản biện & Điểm mù:** Phân tích xem nguyên lý này khi áp dụng vào ngành nghề của người đọc sẽ gặp những cản trở đặc thù nào.
- **Bài tập phản tư & Áp dụng:** May đo Kịch bản kích hoạt và Kế hoạch 24 giờ cho công việc thực tế của người đọc.
