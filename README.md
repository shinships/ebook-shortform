# ebook-shortform

Bộ công cụ CLI và pipeline tự động hóa xử lý ebook bằng LLM, hỗ trợ dịch thuật chuẩn xác và biên soạn **tóm tắt chuyên sâu kiểu Shortform** (microlearning), tích hợp đóng gói chuẩn EPUB3 và tự động gửi file tới nhóm/topic Telegram.

Dự án gồm hai lệnh chính cùng hệ thống kịch bản tự động hóa:

- **`ebook-translate`** — Dịch toàn bộ ebook tiếng Anh sang tiếng Việt, bảo toàn định dạng HTML/CSS, cấu trúc chương mục và hệ thống thuật ngữ nhất quán.
- **`ebook-summarize`** — Biên soạn tóm tắt ebook thành "sách hướng dẫn chuyên sâu" kiểu Shortform: mỗi bài 15–25 phút trả lời *vì sao ý tưởng quan trọng → cơ chế hoạt động & case study → góc nhìn mở rộng/đối chiếu → tóm lược điểm cốt lõi → bài tập tự vấn & hành động thực tiễn*.
- **`auto-pipeline.sh`** — Dây chuyền tự động hóa: theo dõi thư mục `inbox/`, tự động tóm tắt sách, ghi log chi tiết và đẩy thẳng file `.epub` vào Topic Telegram.
- **`scripts/`** — Tiện ích gửi Telegram độc lập (`send_to_telegram.py`) và pipeline tóm tắt chuyên sâu tác phẩm kinh điển của Philip Fisher (`generate_fisher_summaries.py`).

---

### Điểm nổi bật

- **Đa định dạng đầu vào**: Hỗ trợ `.epub`, `.pdf` (bao gồm cả PDF văn bản và PDF trang scan — tự động OCR bằng LLM Vision không cần cài Tesseract).
- **Đầu ra chuẩn EPUB3**: Giữ nguyên hình ảnh, trang bìa tiêu chuẩn (EPUB2 + EPUB3), mục lục điều hướng phân cấp (nav + ncx) và font chữ tiếng Việt hiển thị đẹp mắt trên Apple Books, Kindle, Kobo.
- **Cấu trúc tóm tắt đa chiều**: Mỗi bài học đi qua chuỗi phân tích nhân quả (*reasoning*), bóc tách bối cảnh, giới hạn áp dụng & giả định ngầm (*assumptions & limits*), kèm box *Góc nhìn thêm* đối chiếu triết lý tác giả với các học giả/chuyên gia khác.
- **Ảnh bìa thông minh**: Kiểm duyệt ảnh bìa bằng LLM Vision để tránh tình trạng lấy nhầm trang scan mục lục/chữ li ti làm bìa sách (lỗi phổ biến khi convert qua Calibre).
- **Cơ chế Cache thông minh**: Khi bị ngắt giữa chừng (Ctrl+C, mạng chập chờn), chỉ cần chạy lại là hệ thống tiếp tục từ điểm dừng, không tốn thêm chi phí LLM.

---

## Cài đặt

Yêu cầu Python 3.10 trở lên.

### Trên macOS / Linux

```bash
git clone https://github.com/shinships/ebook-shortform.git
cd ebook-shortform

# Tạo virtual environment và kích hoạt
python3 -m venv .venv
source .venv/bin/activate

# Cài đặt gói ở chế độ editable
pip install -e .

# Tạo file cấu hình môi trường
cp .env.example .env
```

### Trên Windows (PowerShell)

```powershell
git clone https://github.com/shinships/ebook-shortform.git
cd ebook-shortform

python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -e .
Copy-Item .env.example .env
```

---

## Cấu hình Backend LLM & Telegram

Bạn có thể cấu hình các biến môi trường trực tiếp trong file `.env` hoặc export ra terminal. Tool tự động nhận diện theo thứ tự ưu tiên:

### 1. Cấu hình file `.env` (Khuyến nghị)

Mở file `.env` và điền các thông tin bạn có:

```bash
# LLM Backend API Keys (chỉ cần ít nhất 1 backend)
GEMINI_API_KEY="your_gemini_api_key"
ANTHROPIC_API_KEY="your_claude_api_key"
GOOGLE_CLOUD_PROJECT="your_gcp_project_id"

# Telegram Bot (tùy chọn - để gửi file tự động sau khi tóm tắt)
TELEGRAM_BOT_TOKEN="1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"
TELEGRAM_CHAT_ID="-100xxxxxxxxxx"
TELEGRAM_TOPIC_ID="365"   # ID của Topic trong Forum Group (nếu có)
```

### 2. Các tùy chọn Backend LLM

| Backend | Biến môi trường / Cấu hình | Model mặc định | Ghi chú |
|---|---|---|---|
| **Google AI Studio** | `GEMINI_API_KEY` | `gemini-2.5-flash` | Đơn giản, miễn phí hạn mức cao tại [aistudio.google.com](https://aistudio.google.com/apikey). |
| **Anthropic (Claude)** | `ANTHROPIC_API_KEY` | `claude-sonnet-4-20250514` | Chất lượng văn phong xuất sắc, lấy key tại [console.anthropic.com](https://console.anthropic.com). |
| **Google Cloud Vertex AI** | `GOOGLE_CLOUD_PROJECT` | `gemini-3.6-flash` | Cho enterprise/GCP. Cần `gcloud auth application-default login`. Region mặc định `global`. |
| **Proxy vertex-key** | `VERTEX_KEY_API_KEY` (dùng cờ `--proxy`) | `aws/claude-sonnet-5-medium` | Chế độ proxy nội bộ legacy. |

---

## Sử dụng

### 1. Dịch Ebook (`ebook-translate`)

Dịch toàn bộ cuốn sách từ tiếng Anh sang tiếng Việt:

```bash
ebook-translate sach.epub
# -> Tạo sach.vi.epub và sach.vi.glossary.json
```

**Mẹo**: Nên dịch thử 1–2 chương đầu để duyệt văn phong và glossary trước khi chạy cả cuốn:

```bash
ebook-translate sach.pdf --max-chapters 2
```

Các cờ tùy chọn phổ biến:
- `-o output.epub`: Chỉ định đường dẫn file đầu ra.
- `--model <id>`: Chỉ định model LLM (ví dụ: `gemini-2.5-pro`, `claude-opus-4-20250514`).
- `--glossary file.json`: Sử dụng bảng thuật ngữ có sẵn thay vì để LLM tự trích xuất.
- `--cover bia.jpg`: Chỉ định ảnh bìa riêng.
- `--keep-workdir`: Giữ lại thư mục cache tạm `*.workdir` sau khi xuất bản.

---

### 2. Tóm tắt chuyên sâu (`ebook-summarize`)

Biến một cuốn sách dày cộp thành bộ microlearning chuyên sâu:

```bash
ebook-summarize sach.epub
# -> Tạo sach_short.epub và sach_short.analysis.json
```

#### Cấu trúc một cuốn sách tóm tắt Shortform:
1. **"Về cuốn sách này"**: Giới thiệu bối cảnh tác phẩm + **Tóm tắt 1 trang** (nắm toàn cảnh trước khi học từng bài).
2. **Các bài học chuyên sâu** (2.400–4.000 từ / bài, đọc trong 15–25 phút):
   - **Vì sao phần này quan trọng**: Đặt vấn đề và tính cấp thiết.
   - **Cơ chế hoạt động & Case studies**: Phân tích cặn kẽ logic vận hành, số liệu, bài học thực tế từ sách gốc.
   - **Góc nhìn thêm (Commentary)**: Hộp bình luận mở rộng, đối chiếu kiến thức của tác giả với các trường phái/tác phẩm kinh điển khác hoặc góc nhìn thị trường hiện đại.
   - **Giới hạn giả định & Phản biện (Assumptions & Limits)**: Những bối cảnh mà lời khuyên của tác giả có thể không còn phù hợp.
   - **Điểm cốt lõi**: Tóm tắt 3-5 ý đắt giá nhất.
   - **Bài tập thực hành**: 2–3 câu hỏi tự vấn phản tư + 1 hành động hành vi có thể làm ngay.
3. **"Tổng kết"**: Xâu chuỗi toàn bộ cuốn sách thành một hệ thống tư duy mạch lạc.

#### Chạy thử tóm tắt:
```bash
ebook-summarize sach.epub --max-lessons 2 --keep-workdir
```

---

## Tự động hóa Pipeline & Telegram

### 1. Dây chuyền tự động (`auto-pipeline.sh`)

Script Bash tự động hóa hoàn toàn quy trình xử lý hàng loạt sách:

```
[inbox/] ───► [processing/] ───► [output/] ───► [Telegram Group / Topic]
                     ▲                │
                     │                └───► [output/originals/] (Lưu trữ bản gốc)
                     └─── Cache workdir (tự khôi phục nếu lỗi)
```

**Cách sử dụng**:
1. Copy các file sách (`.epub`, `.pdf`) vào thư mục `inbox/`.
2. Khởi chạy pipeline:
   ```bash
   ./auto-pipeline.sh
   ```
3. Các cờ bổ sung:
   ```bash
   ./auto-pipeline.sh --model gemini-2.5-pro    # Dùng Gemini Pro
   ./auto-pipeline.sh --anthropic              # Dùng Claude
   ./auto-pipeline.sh --dry-run                # Chỉ xem danh sách file cần xử lý
   ```
4. Sau khi hoàn thành, file `.epub` tóm tắt sẽ nằm tại `output/`, bản gốc được chuyển sang `output/originals/`, nhật ký ghi lại ở `logs/YYYY-MM-DD.md`, và file tự động được gửi tới Telegram Topic nếu cấu hình `.env`.

---

### 2. Gửi file tới Telegram (`scripts/send_to_telegram.py`)

Công cụ dòng lệnh tiện ích dùng để gửi file EPUB hoặc tài liệu tới nhóm Telegram qua Bot API, hỗ trợ cả Telegram Forum Topics:

```bash
# Gửi 1 hoặc nhiều file (lấy token và chat id từ .env)
python scripts/send_to_telegram.py output/sach_short.epub

# Gửi kèm caption định dạng HTML
python scripts/send_to_telegram.py output/sach_short.epub \
    --caption "📚 <b>Tên Sách</b>\nTóm tắt chuyên sâu kiểu Shortform"

# Tùy biến trực tiếp tham số (không cần .env)
python scripts/send_to_telegram.py output/sach_short.epub \
    --token "YOUR_BOT_TOKEN" \
    --chat-id "-100xxxxxxxxxx" \
    --topic-id "365"
```

---

### 3. Bộ tóm tắt chuyên sâu Philip Fisher (`scripts/generate_fisher_summaries.py`)

Kịch bản chuyên biệt tạo trọn bộ sách tóm tắt chuyên sâu về triết lý đầu tư tăng trưởng của **Philip A. Fisher**:

- **Cuốn 1**: *Common Stocks and Uncommon Profits and Other Writings* (Cổ phiếu thường, Lợi nhuận phi thường) — 8 bài học chuyên sâu (15 tiêu chí chọn cổ phiếu tăng trưởng, phương pháp Lời đồn đại - Scuttlebutt, 5 quy tắc khi mua, khi nào nên bán và khi nào không nên bán...).
- **Cuốn 2**: *Paths to Wealth Through Common Stocks* (Những con đường dẫn đến của cải qua cổ phiếu thường) — 7 bài học chuyên sâu (vai trò R&D, lạm phát và sức mua dài hạn, phân biệt đầu cơ và đầu tư, tìm kiếm công ty tăng trưởng vượt bậc...).
- **Tuyển tập Master Omnibus**: Kết hợp toàn diện cả 2 tác phẩm thành một cuốn bách khoa toàn thư duy nhất về Đầu tư Tăng trưởng, bổ sung ảnh bìa nghệ thuật chất lượng cao, đối chiếu sâu sắc với Warren Buffett ("85% Graham & 15% Fisher"), Charlie Munger, Peter Lynch, Benjamin Graham và thị trường chứng khoán Việt Nam.

**Khởi chạy**:
```bash
python scripts/generate_fisher_summaries.py
```
*(Script tự động sinh các file EPUB tại `output/`, lưu cache tại `workdir_fisher/` và gửi thẳng lên Telegram Topic sau khi hoàn tất).*

---

## Ảnh bìa & Xử lý hình ảnh

- **Kiểm định bằng LLM Vision**: Nhiều file EPUB trích xuất từ Calibre thường lấy nhầm một trang scan mục lục hoặc hình ảnh nhỏ đầu trang làm ảnh bìa. Tool tự động gửi ảnh bìa qua LLM Vision để thẩm định tính xác thực. Nếu ảnh không phải là bìa sách thật, tool sẽ bỏ qua để tránh xuất ra ebook có trang bìa xấu.
- **Chỉ định bìa ngoài**: Sử dụng cờ `--cover anh_bia.jpg` (hỗ trợ `.jpg`, `.png`, `.webp`, `.gif` — tự động chuyển đổi sang JPEG chuẩn EPUB3).

---

## Cấu trúc mã nguồn

```
ebook-shortform/
├── auto-pipeline.sh                # Script Bash điều phối dây chuyền tự động
├── pyproject.toml                  # Khai báo dependency và CLI entrypoints
├── .env.example                    # File mẫu cấu hình biến môi trường & Telegram
├── inbox/                          # Thư mục chứa sách đầu vào chờ xử lý
├── processing/                     # Thư mục xử lý tạm thời kèm cache
├── output/                         # Thư mục chứa file EPUB tóm tắt hoàn chỉnh
│   └── originals/                  # Lưu trữ file sách gốc đã xử lý
├── logs/                           # Nhật ký xử lý theo ngày (Markdown)
├── covers/                         # Thư mục lưu trữ ảnh bìa tùy chỉnh
├── scripts/
│   ├── send_to_telegram.py         # Utility gửi tài liệu tới Telegram Topic qua Bot API
│   └── generate_fisher_summaries.py # Pipeline tóm tắt bộ tác phẩm Philip Fisher
└── src/
    └── ebook_translator/
        ├── cli.py                  # CLI dịch thuật (ebook-translate)
        ├── cli_summarize.py        # CLI tóm tắt Shortform (ebook-summarize)
        ├── models.py               # Data models: Book, Chapter, ImageAsset, TocEntry
        ├── readers/
        │   ├── loader.py           # Bộ nạp tập trung cho EPUB và PDF
        │   ├── epub_reader.py      # Trích xuất cấu trúc EPUB
        │   ├── pdf_reader.py       # Trích xuất PDF text & bookmark (PyMuPDF)
        │   └── ocr.py              # Nhận diện trang scan và OCR bằng LLM Vision
        ├── core/
        │   ├── cover.py            # Thẩm định & chuẩn hóa ảnh bìa sách
        │   ├── segmenter.py        # Chia cắt nội dung thành các chunk dịch logic
        │   ├── glossary.py         # Xây dựng và quản lý từ điển thuật ngữ EN -> VI
        │   ├── translator.py       # Dịch nội dung và tiêu đề mục lục
        │   ├── summarizer.py       # Phân tích, chia bài và biên soạn Shortform
        │   ├── cache.py            # Quản lý bộ nhớ đệm tiếp tục công việc
        │   └── llm.py              # Client tích hợp Gemini, Anthropic, Vertex AI
        └── writers/
            └── epub_writer.py      # Đóng gói và xuất file chuẩn EPUB3
```

---

## License

Phát triển bởi đội ngũ và đóng góp cộng đồng. Mã nguồn phát hành dưới giấy phép MIT.
