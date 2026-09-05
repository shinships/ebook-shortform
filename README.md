# ebook-shortform

Bộ công cụ CLI và pipeline tự động hóa xử lý ebook toàn diện bằng LLM: hỗ trợ dịch thuật chuẩn xác, biên soạn **tóm tắt chuyên sâu kiểu Shortform** (microlearning), đóng gói chuẩn EPUB3, tự động sản xuất **Audio Podcast MP3** bằng Vbee AIVoice TTS, và tích hợp **Telegram Inbound Bot 2 chiều chạy ngầm 24/7**.

Dự án gồm các thành phần cốt lõi:

- **`ebook-translate`** — Dịch toàn bộ ebook tiếng Anh sang tiếng Việt, bảo toàn định dạng HTML/CSS, cấu trúc chương mục và hệ thống thuật ngữ nhất quán.
- **`ebook-summarize`** — Biên soạn tóm tắt ebook thành "sách hướng dẫn chuyên sâu" kiểu Shortform: mỗi bài 15–25 phút trả lời *vì sao ý tưởng quan trọng → cơ chế hoạt động & case study → góc nhìn mở rộng/đối chiếu → tóm lược điểm cốt lõi → bài tập tự vấn & hành động thực tiễn*.
- **`auto-pipeline.sh`** — Dây chuyền tự động hóa: theo dõi thư mục `inbox/`, tự động tóm tắt sách, ghi log chi tiết và đẩy thẳng file `.epub` vào Topic Telegram.
- **`scripts/generate_podcast.py`** — Tự động chuyển đổi tài liệu tóm tắt thành kịch bản solo Podcast đàm thoại hấp dẫn và gọi Vbee TTS API render thành file MP3 chất lượng cao.
- **`scripts/telegram_inbound_bot.py`** — Dịch vụ Telegram Bot 2 chiều chạy ngầm 24/7 trên macOS (`@ebookshort_bot`), cho phép người dùng gửi sách từ điện thoại, nhận lại bản EPUB và Podcast MP3 ngay tại khung chat.
- **`scripts/setup_schedule.sh`** — Bộ công cụ quản trị daemon launchd cho hệ thống bot và lịch xử lý tự động hàng ngày.

---

### Điểm nổi bật

- **Đa định dạng đầu vào**: Hỗ trợ `.epub`, `.pdf` (bao gồm cả PDF văn bản và PDF trang scan — tự động OCR bằng LLM Vision không cần cài Tesseract).
- **Đầu ra chuẩn EPUB3**: Giữ nguyên hình ảnh, trang bìa tiêu chuẩn (EPUB2 + EPUB3), mục lục điều hướng phân cấp (nav + ncx) và font chữ tiếng Việt hiển thị đẹp mắt trên Apple Books, Kindle, Kobo.
- **Cấu trúc tóm tắt đa chiều**: Mỗi bài học đi qua chuỗi phân tích nhân quả (*reasoning*), bóc tách bối cảnh, giới hạn áp dụng & giả định ngầm (*assumptions & limits*), kèm box *Góc nhìn thêm* đối chiếu triết lý tác giả với các học giả/chuyên gia khác.
- **Audio Podcast AI tiếng Việt chân thực**: Biên soạn kịch bản đàm thoại tự nhiên với Gemini, tích hợp hơn 30+ giọng đọc AI Vbee chất lượng cao đa vùng miền (Bắc/Trung/Nam), hỗ trợ đặt giọng qua lệnh hoặc cấu hình.
- **Tương tác 2 chiều qua Telegram 24/7**: Gửi sách trực tiếp qua Telegram trên điện thoại, bot tự động đưa vào queue xử lý, tạo EPUB và Podcast, đồng bộ thẳng về nhóm và topic thảo luận.
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

## Cấu hình Backend LLM, Telegram & Vbee TTS

Bạn có thể cấu hình các biến môi trường trực tiếp trong file `.env` hoặc export ra terminal. Tool tự động nhận diện theo thứ tự ưu tiên:

### 1. Cấu hình file `.env` (Khuyến nghị)

Mở file `.env` (tạo từ `.env.example`) và điền các thông tin của bạn:

```bash
# LLM Backend API Keys (Gemini API key chính, Vertex AI backup)
GEMINI_API_KEY="your_gemini_api_key"
GOOGLE_CLOUD_PROJECT="your_gcp_project_id"

# Telegram Bot (để gửi và nhận file qua Telegram 24/7)
TELEGRAM_BOT_TOKEN="your_bot_token_here"
TELEGRAM_CHAT_ID="-100xxxxxxxxxx"
TELEGRAM_TOPIC_ID="365"   # ID của Topic trong Forum Group (nếu có)

# Vbee AIVoice TTS API (để tạo Audio Podcast tóm tắt sách tiếng Việt)
VBEE_APP_ID="your_vbee_app_id"
VBEE_APP_NAME="ebook-short"
VBEE_TOKEN="your_vbee_jwt_token"
# Mã giọng đọc mặc định (ví dụ: Ngọc Huyền, Lan Trinh, Thanh Long...)
VBEE_VOICE="hn_female_ngochuyen_full_48k-fhg"
```

### 2. Các tùy chọn Backend LLM

| Backend | Biến môi trường / Cấu hình | Model mặc định | Ghi chú |
|---|---|---|---|
| **Google AI Studio** *(chính)* | `GEMINI_API_KEY` | `gemini-3.7-flash` | Đơn giản, tốc độ cao tại [aistudio.google.com](https://aistudio.google.com/apikey). |
| **Google Cloud Vertex AI** *(backup)* | `GOOGLE_CLOUD_PROJECT` | `gemini-3.6-flash` | Cho enterprise/GCP. Cần `gcloud auth application-default login`. Region mặc định `global`. |

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
```

---

### 3. Telegram Inbound Bot 2 chiều (`scripts/telegram_inbound_bot.py`)

Dịch vụ chạy ngầm 24/7 trên macOS cho phép bạn **gửi sách trực tiếp từ điện thoại/iPad** qua Telegram (`@ebookshort_bot`) và nhận lại bản tóm tắt EPUB cùng Audio Podcast MP3 ngay tại khung chat:

- 📥 **Nhận sách tự động**: Gửi file `.epub` hoặc `.pdf` (dưới 20 MB) trực tiếp vào bot, bot tự tải về và xếp vào hàng đợi xử lý.
- 🎙️ **Tạo Audio Podcast kèm theo**: Thêm từ khóa vào caption khi gửi sách (ví dụ: *"tạo audio"*, *"podcast"*, *"podcast giọng nam"*, *"audio lan trinh"*), bot sẽ tự động xuất cả EPUB lẫn Podcast MP3.
- 💬 **Lệnh điều khiển tương tác**:
  - `/podcast` — Xem danh sách các sách trong thư viện có thể tạo Podcast ngay.
  - `/podcast <tên sách> [giọng]` — Tìm sách và render podcast (ví dụ: `/podcast remote lantrinh`).
  - **Reply file sách + gõ `/podcast [giọng]`** — Render audio trực tiếp cho cuốn sách được reply.
  - `/voice` — Liệt kê danh sách các giọng đọc Vbee được hỗ trợ và hướng dẫn đổi giọng.
  - `/status` — Kiểm tra trạng thái hàng đợi, nhóm đồng bộ và giọng đọc đang kích hoạt.
- 📢 **Tự động đồng bộ**: Mọi kết quả (EPUB và MP3) đều được tự động gửi về khung chat riêng của người yêu cầu và gửi bản sao tới Topic nhóm thảo luận chung.

```bash
# Quản lý dịch vụ bot ngầm bằng launchd (không cần mở Terminal hay IDE):
./scripts/setup_schedule.sh bot-install    # Cài đặt và bật bot chạy ngầm 24/7
./scripts/setup_schedule.sh bot-status     # Xem trạng thái daemon đang chạy
./scripts/setup_schedule.sh bot-logs       # Xem trực tiếp nhật ký hoạt động của bot
./scripts/setup_schedule.sh bot-uninstall  # Dừng và gỡ bỏ bot daemon

# Quản lý chung cả lịch chạy pipeline tự động 12:10 hàng ngày & Bot 24/7:
./scripts/setup_schedule.sh status         # Xem trạng thái tổng quan các dịch vụ
./scripts/setup_schedule.sh install-all    # Kích hoạt toàn bộ lịch tự động & Bot
```

---

### 4. Tạo Audio Podcast Tóm Tắt Sách (`scripts/generate_podcast.py`)

Quy trình tự động hóa sản xuất nội dung âm thanh từ sách tóm tắt bằng sự kết hợp giữa **Gemini AI** và **Vbee AIVoice TTS**:

1. **Biên kịch Podcast thông minh**: Gemini đóng vai trò Host/Producer chuyên nghiệp, phân tích tài liệu tóm tắt và chuyển thể thành **kịch bản nói đơn thoại (Solo Podcast Script)** kéo dài 8–12 phút. Văn phong đàm thoại gần gũi, mở đầu cuốn hút, xâu chuỗi bài học thành câu chuyện liền mạch và đúc kết hành động thực tiễn.
2. **Chuyển đổi âm thanh tự nhiên**: Gọi API Vbee AIVoice để render kịch bản thành file MP3 chuẩn 128kbps với ngữ điệu ngắt nghỉ chân thực.
3. **Phân phối tức thì**: Tự động lưu trữ vào `output/podcasts/` và tùy chọn đẩy thẳng lên kênh/topic Telegram.

#### Cách sử dụng từ dòng lệnh:

```bash
# Xem danh sách giọng đọc Vbee có sẵn
python scripts/generate_podcast.py --list-voices

# Tạo Podcast MP3 từ file tóm tắt và tự động gửi tới Telegram
python scripts/generate_podcast.py output/Remote_Office_Not_Required_short.epub --telegram

# Chọn giọng đọc bằng tên gợi nhớ (alias) hoặc mã Vbee:
python scripts/generate_podcast.py output/Remote_Office_Not_Required_short.epub --voice lantrinh
python scripts/generate_podcast.py output/Remote_Office_Not_Required_short.epub --voice thanhlong
python scripts/generate_podcast.py output/Remote_Office_Not_Required_short.epub --voice hn_male_phuthang_stor80dt_48k-fhg

# Tùy chỉnh tốc độ đọc (0.8 - 1.5, mặc định 1.0):
python scripts/generate_podcast.py output/Remote_Office_Not_Required_short.epub --speed 1.05

# Chỉ tạo kịch bản kịch bản văn bản (không gọi TTS API):
python scripts/generate_podcast.py output/Remote_Office_Not_Required_short.epub --script-only
```

#### Bảng tra cứu các giọng đọc tiêu biểu:

| Tên ngắn (Alias) | Mã giọng Vbee (`voice_code`) | Vùng miền / Giới tính | Phong cách phù hợp |
|:---|:---|:---|:---|
| `ngochuyen` | `hn_female_ngochuyen_full_48k-fhg` | Nữ - Miền Bắc *(Mặc định)* | Truyền cảm, chuẩn mực sách nói / podcast |
| `maiphuong` | `hn_female_maiphuong_vdts_48k-fhg` | Nữ - Miền Bắc | Tự nhiên, nhẹ nhàng, đàm thoại |
| `thanhlong` | `hn_male_thanhlong_talk_48k-fhg` | Nam - Miền Bắc | Talkshow, đàm thoại, podcast năng động |
| `anhkhoi` | `hn_male_phuthang_stor80dt_48k-fhg` | Nam - Miền Bắc | Trầm ấm, sâu lắng, tự sự / triết lý |
| `manhdung` | `hn_male_manhdung_news_48k-fhg` | Nam - Miền Bắc | Trang trọng, thời sự, sách kinh doanh |
| `minhquan` | `hn_male_minhquan_yt-stable` | Nam - Miền Bắc | Trẻ trung, phong cách review |
| `lantrinh` | `sg_female_lantrinh_vdts_48k-fhg` | Nữ - Miền Nam | Dịu dàng, đàm thoại tự nhiên, dễ nghe |
| `thaotrinh` | `sg_female_thaotrinh_full_48k-fhg` | Nữ - Miền Nam | Ấm áp, truyền cảm |
| `trungkien` | `sg_male_trungkien_vdts_48k-fhg` | Nam - Miền Nam | Nam tính, ấm áp, truyền cảm |
| `minhhoang` | `sg_male_minhhoang_full_48k-fhg` | Nam - Miền Nam | Hiện đại, năng động |
| `huonggiang` | `hue_female_huonggiang_full_48k-fhg` | Nữ - Miền Trung (Huế) | Ngọt ngào, nhẹ nhàng |
| `duyphuong` | `hue_male_duyphuong_full_48k-fhg` | Nam - Miền Trung (Huế) | Trầm ấm, sâu lắng |

---

### 5. Bộ tóm tắt chuyên sâu Philip Fisher (`scripts/generate_fisher_summaries.py`)

Kịch bản chuyên biệt tạo trọn bộ sách tóm tắt chuyên sâu về triết lý đầu tư tăng trưởng của **Philip A. Fisher**:

- **Cuốn 1**: *Common Stocks and Uncommon Profits and Other Writings* (Cổ phiếu thường, Lợi nhuận phi thường) — 8 bài học chuyên sâu (15 tiêu chí chọn cổ phiếu tăng trưởng, phương pháp Lời đồn đại - Scuttlebutt, 5 quy tắc khi mua, khi nào nên bán và khi nào không nên bán...).
- **Cuốn 2**: *Paths to Wealth Through Common Stocks* (Những con đường dẫn đến của cải qua cổ phiếu thường) — 7 bài học chuyên sâu (vai trò R&D, lạm phát và sức mua dài hạn, phân biệt đầu cơ và đầu tư, tìm kiếm công ty tăng trưởng vượt bậc...).
- **Tuyển tập Master Omnibus**: Kết hợp toàn diện cả 2 tác phẩm thành một cuốn bách khoa toàn thư duy nhất về Đầu tư Tăng trưởng, bổ sung ảnh bìa nghệ thuật chất lượng cao, đối chiếu sâu sắc với Warren Buffett ("85% Graham & 15% Fisher"), Charlie Munger, Peter Lynch, Benjamin Graham và thị trường chứng khoán Việt Nam.

**Khởi chạy**:
```bash
python scripts/generate_fisher_summaries.py
```

---

### 6. Tiện ích bổ trợ

- **Trích xuất EPUB sang Markdown (`scripts/export_epub_to_md.py`)**:
  ```bash
  python scripts/export_epub_to_md.py output/sach_short.epub
  # -> Tạo output/sach_short.md gom trọn vẹn nội dung sách
  ```
- **Gửi tài liệu sang Telegram độc lập (`scripts/send_to_telegram.py`)**:
  ```bash
  python scripts/send_to_telegram.py output/sach_short.epub --caption "📚 Sách tóm tắt mới"
  ```

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
├── .env.example                    # File mẫu cấu hình biến môi trường, Telegram & Vbee
├── inbox/                          # Thư mục chứa sách đầu vào chờ xử lý
├── processing/                     # Thư mục xử lý tạm thời kèm cache
├── output/                         # Thư mục chứa file EPUB tóm tắt hoàn chỉnh
│   ├── originals/                  # Lưu trữ file sách gốc đã xử lý
│   └── podcasts/                   # Lưu trữ kịch bản TXT và file âm thanh Podcast MP3
├── logs/                           # Nhật ký xử lý theo ngày (Markdown)
├── covers/                         # Thư mục lưu trữ ảnh bìa tùy chỉnh
├── .agents/
│   └── skills/
│       └── shortform_reading/      # Bộ kỹ năng phân tích và bẻ khóa sách kiểu Shortform
├── scripts/
│   ├── generate_podcast.py         # Biên soạn kịch bản & tạo Podcast MP3 bằng Vbee TTS
│   ├── telegram_inbound_bot.py     # Bot Telegram 2 chiều chạy ngầm 24/7
│   ├── setup_schedule.sh           # Bộ cài đặt và quản trị launchd daemon macOS
│   ├── com.mktmda.ebook-telegram-bot.plist # File cấu hình launchd cho Telegram Bot
│   ├── com.mktmda.ebook-shortform.plist    # File cấu hình launchd cho lịch tóm tắt tự động
│   ├── export_epub_to_md.py        # Tiện ích chuyển đổi EPUB sang Markdown
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
        │   └── llm.py              # Client tích hợp Gemini AI Studio & Vertex AI
        └── writers/
            └── epub_writer.py      # Đóng gói và xuất file chuẩn EPUB3
```

---

## License

Phát triển bởi đội ngũ và đóng góp cộng đồng. Mã nguồn phát hành dưới giấy phép MIT.
