# ebook-shortform

Tool CLI xử lý ebook bằng LLM, gồm hai lệnh:

- **`ebook-translate`** — dịch ebook tiếng Anh sang tiếng Việt.
- **`ebook-summarize`** — tóm tắt ebook thành "sách hướng dẫn chuyên sâu" kiểu Shortform: mỗi bài 15–25 phút trả lời *vì sao ý tưởng quan trọng → cơ chế hoạt động → cách áp dụng*, kèm box "Góc nhìn thêm" (đối chiếu nghiên cứu/sách khác) và bài tập thực hành cuối bài.

Điểm chung:

- **Input**: `.epub`, `.pdf` (cả PDF text lẫn PDF scan — trang scan được OCR bằng LLM vision, không cần cài Tesseract)
- **Output**: `.epub` tiếng Việt, giữ nguyên hình ảnh và cấu trúc mục lục (mục lục cũng được dịch)
- Dịch dễ hiểu nhưng bám sát nội dung gốc; thuật ngữ/tên riêng nhất quán xuyên suốt nhờ glossary tự xây
- Có cache: ngắt giữa chừng (Ctrl+C, mất mạng) → chạy lại là tiếp tục từ chỗ dừng, không tốn tiền dịch lại

## Cài đặt

Yêu cầu Python 3.10+.

```powershell
cd D:\Projects\ebook-shortform
uv venv .venv                                          # hoặc: python -m venv .venv
uv pip install -e . --python .venv\Scripts\python.exe  # hoặc: .venv\Scripts\pip install -e .
```

## Chọn backend LLM

Tool tự động chọn backend theo thứ tự ưu tiên dựa trên biến môi trường và flags. Chỉ cần set **1 biến** là đủ:

### Cách 1 — Gemini API key (đơn giản nhất)

Lấy key miễn phí tại [aistudio.google.com](https://aistudio.google.com/apikey), model mặc định `gemini-2.5-flash`:

```powershell
$env:GEMINI_API_KEY = "<api-key>"

.\.venv\Scripts\ebook-translate.exe sach.epub
.\.venv\Scripts\ebook-summarize.exe sach.epub
```

Muốn model mạnh hơn:
```powershell
.\.venv\Scripts\ebook-summarize.exe sach.epub --model gemini-2.5-pro
```

### Cách 2 — Anthropic API key (Claude)

Lấy key tại [console.anthropic.com](https://console.anthropic.com), model mặc định `claude-sonnet-4-20250514`:

```powershell
$env:ANTHROPIC_API_KEY = "<api-key>"

.\.venv\Scripts\ebook-summarize.exe sach.epub
# hoặc ép dùng Claude khi có cả GEMINI_API_KEY:
.\.venv\Scripts\ebook-summarize.exe sach.epub --anthropic
```

Muốn dùng Opus:
```powershell
.\.venv\Scripts\ebook-summarize.exe sach.epub --anthropic --model claude-opus-4-20250514
```

### Cách 3 — Google Cloud Vertex AI

Cho team/enterprise, model mặc định `gemini-3.6-flash`:

```powershell
gcloud auth application-default login              # đăng nhập GCP (một lần)
$env:GOOGLE_CLOUD_PROJECT = "<gcp-project-id>"     # hoặc dùng --project

.\.venv\Scripts\ebook-translate.exe sach.epub
.\.venv\Scripts\ebook-summarize.exe sach.epub --model gemini-2.5-pro   # muốn model mạnh hơn
```

Yêu cầu: GCP project đã bật Vertex AI API và tài khoản có role `Vertex AI User`. Region mặc định `global` — **`gemini-3.6-flash` chỉ có ở `global`**, đổi `--region` chỉ khi dùng model khác (vd `gemini-2.5-pro`, `gemini-2.5-flash` có ở cả `us-central1`).

**Lưu ý về xác thực**:

- ADC (`gcloud auth application-default login`) là đăng nhập **riêng**, không tự đổi theo `gcloud auth login`.
- Biến môi trường `GOOGLE_APPLICATION_CREDENTIALS` (nếu công cụ khác đặt) vốn **đứng đầu** thứ tự ưu tiên của Google và sẽ vô hiệu hoá ADC. Tool tự bỏ qua biến này khi máy đã có gcloud login, và in một dòng thông báo — nên không cần gỡ biến đó.
- Nếu vẫn gặp 403: kiểm tra tài khoản trong ADC có role `Vertex AI User` trên project không.

### Cách 4 — Proxy vertex-key (legacy)

Flag `--proxy`, model `aws/claude-sonnet-5-medium` — đặt key vào biến `VERTEX_KEY_API_KEY`:

```powershell
$env:VERTEX_KEY_API_KEY = "<vertex-key api key>"
.\.venv\Scripts\ebook-translate.exe sach.epub --proxy
```

### Thứ tự ưu tiên tự động

| Ưu tiên | Điều kiện | Backend | Model mặc định |
|---|---|---|---|
| 1 | `--proxy` | Proxy vertex-key | `aws/claude-sonnet-5-medium` |
| 2 | `--anthropic` hoặc `ANTHROPIC_API_KEY` | Anthropic API | `claude-sonnet-4-20250514` |
| 3 | `GEMINI_API_KEY` hoặc `GOOGLE_API_KEY` | Google AI Studio | `gemini-2.5-flash` |
| 4 | `--project` hoặc `GOOGLE_CLOUD_PROJECT` | Vertex AI (GCP) | `gemini-3.6-flash` |

## Sử dụng

```powershell
.\.venv\Scripts\ebook-translate.exe sach.epub
# -> tạo sach.vi.epub và sach.vi.glossary.json
```

Nên **dịch thử 1-2 chương trước** để duyệt văn phong rồi mới dịch cả cuốn:

```powershell
.\.venv\Scripts\ebook-translate.exe sach.pdf --max-chapters 2
```

Các tuỳ chọn:

| Tuỳ chọn | Ý nghĩa |
|---|---|
| `-o output.epub` | Đường dẫn file ra (mặc định `<input>.vi.epub`) |
| `--model <id>` | Model LLM (mặc định tự chọn theo backend) |
| `--anthropic` | Dùng Anthropic API trực tiếp (cần `ANTHROPIC_API_KEY`) |
| `--project <id>` | GCP project ID cho Vertex AI |
| `--region <region>` | Region Vertex AI, mặc định `global` |
| `--proxy` | Dùng proxy vertex-key (legacy, cần `VERTEX_KEY_API_KEY`) |
| `--base-url <url>` | Base URL proxy, chỉ dùng kèm `--proxy` |
| `--glossary file.json` | Dùng glossary có sẵn thay vì tự xây |
| `--cover anh.jpg` | Ảnh bìa tự chọn (xem [Ảnh bìa](#ảnh-bìa)) |
| `--max-chapters N` | Chỉ dịch N chương đầu (dịch thử) |
| `--keep-workdir` | Giữ thư mục cache `<output>.workdir` sau khi xong |

## Tóm tắt chuyên sâu (`ebook-summarize`)

```powershell
.\.venv\Scripts\ebook-summarize.exe sach.epub
# -> tạo sach_short.epub và sach_short.analysis.json
```

Cấu trúc sách tóm tắt:

- **"Về cuốn sách này"** mở đầu: giới thiệu + **Tóm tắt 1 trang** (nắm bức tranh lớn trước khi vào từng bài).
- **Mỗi bài học** (2.400–4.000 từ, ≈ 15–25 phút): vì sao phần này quan trọng → từng mục giải thích cơ chế với ví dụ từ sách → box *Góc nhìn thêm* (bình luận từ kiến thức ngoài sách, tách bạch rõ) → box *Điểm chính* → box *Thực hành* (2–3 câu hỏi tự vấn + 1 hành động cụ thể).
- **"Tổng kết"** cuối sách: xâu chuỗi toàn bộ + nhìn lại mọi điểm chính.
- **Bìa**: dùng lại nguyên bản ảnh bìa gốc của sách, đặt làm trang bìa chuẩn (EPUB2 + EPUB3) để hiển thị đúng trên mọi reader. Xem mục [Ảnh bìa](#ảnh-bìa).

Chương gốc quá ngắn được gộp, quá dài được tách nhiều bài (chương rất dài đi qua bước trích notes từng đoạn trước khi viết bài). Trang bìa/mục lục/bản quyền/index tự bị bỏ qua — có thể sửa lại trong `*.analysis.json` rồi chạy lại.

Nên **chạy thử trước**: `--max-lessons 2 --keep-workdir` để duyệt văn phong (bài đã sinh được cache, chạy full không tốn lại).

Các tuỳ chọn riêng (các flag `--model`, `--anthropic`, `--project`, `--region`, `--proxy` dùng chung như `ebook-translate`):

| Tuỳ chọn | Ý nghĩa |
|---|---|
| `-o output.epub` | File ra (mặc định `<input>_short.epub`) |
| `--cover anh.jpg` | Ảnh bìa tự chọn (xem [Ảnh bìa](#ảnh-bìa)) |
| `--analysis file.json` | Dùng analysis có sẵn thay vì tự phân tích |
| `--max-lessons N` | Chỉ sinh N bài đầu (chạy thử; bỏ bài mở đầu/tổng kết) |

## Ảnh bìa

Mặc định tool dùng lại **ảnh bìa gốc** của sách. Nhưng nhiều EPUB do calibre
convert khai báo bìa sai: khi sách gốc không có bìa thật, calibre lấy đại ảnh
đầu tiên trong manifest — thường là một **trang scan nội dung** (index, mục lục,
hình minh hoạ). Sách xuất ra khi đó có "bìa" là một trang chữ li ti.

Nên trước khi nhúng, tool gửi ảnh bìa qua **LLM vision** hỏi xem nó có thật sự
là bìa sách không (một request nhỏ, kết quả được cache theo nội dung ảnh nên
chạy lại không tốn thêm). Nếu không đạt, bìa bị bỏ kèm cảnh báo.

Muốn có bìa đúng, tải ảnh bìa sách rồi truyền vào — khi có `--cover` thì bỏ qua
bước kiểm tra:

```powershell
.\.venv\Scripts\ebook-summarize.exe sach.epub --cover bia.jpg
```

Nhận `.jpg`, `.png`, `.gif`, `.webp`; định dạng ngoài chuẩn EPUB3 (vd webp) được
tự chuyển sang JPEG cho tương thích reader. Cả `ebook-translate` lẫn
`ebook-summarize` đều có flag này.

## Quy trình bên trong

1. **Đọc file gốc** → chương (HTML) + ảnh + mục lục. PDF: chia chương theo bookmark; không có bookmark thì dựa vào cỡ chữ heading. Trang scan được render ảnh và OCR bằng LLM vision.
2. **Xây glossary**: lấy mẫu ~15% nội dung, LLM liệt kê tên riêng/thuật ngữ lặp lại và chốt cách dịch → ghi `*.glossary.json`. **Có thể mở file này sửa cách dịch thuật ngữ rồi chạy lại** (bản dịch cũ tự mất hiệu lực vì cache gắn với phiên bản glossary).
3. **Dịch tiêu đề**: toàn bộ tiêu đề chương + mục lục dịch trong một request duy nhất → mục lục và tiêu đề trong bài luôn khớp nhau.
4. **Dịch nội dung**: cắt chương thành đoạn ~3000 token theo ranh giới đoạn văn; mỗi request kèm glossary + phần cuối bản dịch trước đó để mạch văn liền; giữ nguyên tag HTML, không dịch `<code>/<pre>`; kiểm tra cấu trúc HTML sau dịch, lệch thì tự dịch lại.
5. **Xuất EPUB3**: ảnh gốc nhúng nguyên trạng, mục lục nav + NCX, `dc:language = vi`, giữ tựa gốc làm metadata phụ.

Kết thúc, tool in tổng token vào/ra để ước lượng chi phí.

## Cấu trúc mã nguồn

```
src\ebook_translator\
├── cli.py                  # điều phối pipeline dịch (ebook-translate)
├── cli_summarize.py        # điều phối pipeline tóm tắt (ebook-summarize)
├── models.py               # Book / Chapter / ImageAsset / TocEntry
├── readers\
│   ├── loader.py           # dispatch epub/pdf + OCR (dùng chung 2 lệnh)
│   ├── epub_reader.py      # EPUB -> Book
│   ├── pdf_reader.py       # PDF text -> Book (PyMuPDF)
│   └── ocr.py              # phát hiện trang scan + OCR bằng LLM vision
├── core\
│   ├── cover.py            # xác thực ảnh bìa (LLM vision) + xử lý --cover
│   ├── segmenter.py        # cắt HTML thành chunk
│   ├── glossary.py         # xây/quản lý glossary EN->VI
│   ├── translator.py       # dịch nội dung + tiêu đề
│   ├── summarizer.py       # phân tích sách + chia bài + viết guide kiểu Shortform
│   ├── cache.py            # cache chunk/bài đã sinh (resume)
│   └── llm.py              # wrapper Gemini/Anthropic API (retry, đếm token)
└── writers\
    └── epub_writer.py      # Book -> EPUB3
```
