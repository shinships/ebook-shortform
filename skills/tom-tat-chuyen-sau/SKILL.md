---
name: tom-tat-chuyen-sau
description: >-
  Viết TÓM TẮT CHUYÊN SÂU tiếng Việt cho sách phi hư cấu (self-help, kinh
  doanh, tâm lý, khoa học thường thức...) theo phong cách Shortform — mỗi bài
  15–25 phút trả lời "vì sao ý tưởng quan trọng → cơ chế hoạt động → cách áp
  dụng", kèm box Góc nhìn thêm, Điểm chính và bài Thực hành. KHÔNG phải tóm tắt
  ngắn gọn kiểu takeaway. Dùng skill này bất cứ khi nào người dùng muốn "tóm tắt
  chuyên sâu", "tóm tắt chi tiết", "sách tóm tắt", "guide/hướng dẫn từ cuốn
  sách", "đọc hộ và giảng lại", "biến cuốn sách thành bài học", "shortform",
  "blinkist nhưng sâu hơn", hoặc dán một chương/nội dung sách và nhờ giảng giải
  sâu để học — kể cả khi họ không nói đúng chữ "chuyên sâu". Nếu người dùng chỉ
  muốn tóm tắt vài dòng, tóm tắt bài báo ngắn, hay tóm tắt cuộc họp/tài liệu
  công việc thì KHÔNG dùng skill này.
---

# Tóm tắt chuyên sâu (phong cách Shortform)

## Skill này làm gì

Biến một cuốn sách (hoặc chương/đoạn dài) thành **sách hướng dẫn chuyên sâu tiếng
Việt** — không phải bản tóm tắt gạch đầu dòng, mà là loạt **bài học** giúp người
đọc *hiểu và làm được*, không chỉ *biết kết luận*.

Nguyên tắc gốc, quán xuyến mọi thứ bên dưới: với mỗi ý tưởng lớn, luôn trả lời ba
câu hỏi — **VÌ SAO** nó quan trọng, **CƠ CHẾ** nó hoạt động thế nào (tại sao
đúng), và **ÁP DỤNG** ra sao vào đời sống/công việc của chính người đọc.

Skill này lo phần **nội dung và văn phong**. Nếu người dùng có sẵn file sách
(EPUB/PDF) và muốn ra một **file EPUB hoàn chỉnh** — có mục lục, giữ nguyên bìa
gốc của sách — thì cách nhanh và nhất quán nhất là
chạy công cụ dòng lệnh `ebook-summarize` (dự án `ebook-shortform`), vốn tự động
hoá đúng quy trình bên dưới. Xem mục **Đóng gói thành EPUB** ở cuối. Chỉ tự tay
viết theo skill khi người dùng dán nội dung trực tiếp, hoặc muốn tinh chỉnh từng
bài, hoặc không tiện chạy công cụ.

## Quy trình 4 bước

Làm tuần tự. Đừng nhảy vào viết bài ngay khi chưa phân tích và lập dàn — bài viết
tốt đến đâu cũng vô nghĩa nếu chia sai hoặc bỏ sót mạch lập luận của sách.

### Bước 1 — Phân tích cuốn sách

Trước khi viết, tự trả lời (ghi ngắn gọn để bám suốt quá trình):

- **Bối cảnh sách** (2–3 câu): thể loại, luận điểm trung tâm, giá trị cho người
  đọc.
- **Đối tượng** (1 câu): cuốn này viết cho ai.
- **Chủ đề cốt lõi** (3–6 ý): những trụ ý xuyên suốt.
- **Cần bỏ qua**: bìa, mục lục, lời cảm ơn, index, thư mục, phụ lục quảng cáo —
  những phần không phải nội dung thật. Chỉ tóm tắt phần có chất.

Nếu người dùng chỉ đưa **một chương/một đoạn**, vẫn làm bước này ở quy mô nhỏ:
xác định chương này giải quyết vấn đề gì, nằm ở đâu trong mạch lớn.

### Bước 2 — Chia bài học

Mục tiêu: mỗi bài là **một ý tưởng lớn trọn vẹn**, đọc trong ~15–25 phút
(≈ 2.400–4.000 từ tiếng Việt).

- Chương quá ngắn/liên quan chặt → **gộp** vào một bài.
- Chương quá dài, chứa nhiều ý độc lập → **tách** thành nhiều bài.
- Nguồn rất dài (>~9.000 từ): trước khi viết, **trích notes từng phần** (map) —
  liệt kê luận điểm, ví dụ, câu chuyện, số liệu, trích dẫn đáng nhớ thành các
  gạch đầu dòng — rồi mới viết bài từ notes đó (reduce). Cách này tránh bỏ sót
  chi tiết khi văn bản dài vượt tầm bao quát trong một lượt.

Lập một **dàn bài học** (danh sách tiêu đề + nguồn tương ứng) và, nếu là dự án
lớn hoặc người dùng có thể muốn điều chỉnh, đưa dàn cho họ duyệt trước khi viết.

### Bước 3 — Viết từng bài học

Mỗi bài theo đúng cấu trúc ở mục **Cấu trúc một bài học** bên dưới. Giữ **giọng
và bố cục nhất quán** giữa các bài của cùng cuốn sách. Khi viết bài sau, nhắc lại
một câu bài trước vừa bàn gì để mạch sách liền lạc, không lặp.

### Bước 4 — Khung mở đầu và tổng kết

Sau khi có đủ các bài:

- **"Về cuốn sách này"** (mở đầu): giới thiệu sách + tác giả + vì sao đáng đào
  sâu; kèm **Tóm tắt 1 trang** — xâu chuỗi toàn bộ lập luận chính thành một mạch
  liền để người đọc nắm bức tranh lớn *trước khi* vào từng bài.
- **"Tổng kết"** (cuối): xâu chuỗi các ý lớn thành bức tranh tổng thể (đừng lặp
  máy móc từng ý), chốt bằng một lời nhắn hành động cuối cùng.

## Cấu trúc một bài học

Dùng đúng khung này cho mỗi bài. Định dạng đầu ra mặc định là **Markdown**; nếu
người dùng cần HTML/EPUB thì chỉ dùng thẻ `p, ul, ol, li, strong, em,
blockquote`.

```
# [Tiêu đề bài — nói rõ bài này giải quyết vấn đề gì]
*≈ N phút đọc*

[Mở bài: 1–2 đoạn — phần này của sách nói về gì và VÌ SAO nó quan trọng với bạn.]

## [Tiêu đề mục 1]
[3–6 đoạn giải thích CƠ CHẾ của ý tưởng: vì sao nó đúng, nó vận hành thế nào,
minh hoạ bằng chính ví dụ/câu chuyện/số liệu của cuốn sách.]

> 💡 **Góc nhìn thêm** *(tuỳ chọn)*
> [1–2 đoạn đối chiếu với nghiên cứu/cuốn sách khác, phản biện, hoặc bối cảnh bổ
> sung từ kiến thức NGOÀI cuốn sách. Nêu rõ nguồn. Bỏ hẳn box này nếu không có gì
> thật sự đáng nói.]

## [Tiêu đề mục 2]
...

---
### 📌 Điểm chính
- [Ý cô đọng 1–2 câu]
- ... (3–6 gạch đầu dòng)

### ✍️ Thực hành
- [Câu hỏi tự vấn giúp bạn soi ý tưởng vào đời sống/công việc của chính mình]
- ... (2–3 câu hỏi)

**Hành động hôm nay:** [MỘT việc cụ thể người đọc có thể làm ngay từ bài này.]
```

Ràng buộc: **2–5 mục**, **3–6 Điểm chính**, **2–3 câu Thực hành**, tổng độ dài
**~2.400–4.000 từ** mỗi bài.

## Nguyên tắc văn phong (quan trọng nhất)

Đây là phần quyết định chất lượng — đọc kỹ.

- **Ưu tiên sự thấu hiểu, không phải sự ngắn gọn.** Giải thích lý lẽ và cơ chế
  đằng sau mỗi ý, đừng chỉ nêu kết luận. Người đọc phải hiểu *tại sao*, để tự vận
  dụng được vào tình huống mới chứ không thuộc lòng vài câu chốt.

- **Dùng chính chất liệu của sách.** Ví dụ, câu chuyện, số liệu, thí nghiệm,
  trích dẫn trong bài phải lấy từ cuốn sách. Đây là thứ làm cho bản tóm tắt sống
  động và đáng tin — người đọc cảm được "vị" của cuốn sách gốc.

- **Trung thực tuyệt đối với nguồn.** Ở mọi phần nội dung chính (mở bài, các mục,
  Điểm chính, Thực hành, hành động), **không bịa** sự kiện, ví dụ, con số hay lời
  khuyên không có trong sách. Nếu không chắc sách nói gì, đừng phịa cho đủ ý.

- **Ngoại lệ duy nhất — box "Góc nhìn thêm".** Chỉ ở đây mới được dùng kiến thức
  ngoài sách (sách khác, nghiên cứu nổi tiếng, phản biện thường gặp) để so sánh,
  bổ sung, phê bình — và phải **nêu rõ nguồn** (tên sách/nhà nghiên cứu). Không
  bao giờ trộn kiến thức ngoài vào các phần khác; ranh giới "trong sách / ngoài
  sách" phải luôn rạch ròi để người đọc biết đâu là tác giả, đâu là bình luận.

- **Xưng hô "bạn".** Giọng tiếng Việt tự nhiên, chững chạc, như một người thầy
  giỏi đang giảng lại — không sáo rỗng, không dịch cứng.

- **Hình/biểu đồ**: nếu sách có hình minh hoạ hay biểu đồ, diễn đạt ý của nó bằng
  lời (người đọc bản tóm tắt không thấy hình gốc).

- **Nhất quán toàn cuốn.** Giữ cùng một giọng, cùng một kiểu bố cục qua tất cả
  các bài, để cả tập đọc liền mạch như một sản phẩm duy nhất.

## Ví dụ minh hoạ (một mục ngắn)

Đây là mức độ *chiều sâu và chất liệu* cần đạt, không phải mẫu để chép:

> ## Vì sao khung nhìn "có hoặc không" bào mòn quyết định của bạn
>
> Heath dẫn nghiên cứu của Paul Nutt trên 168 quyết định của các tổ chức: chỉ
> **29%** trong số đó từng cân nhắc quá một phương án. Phần lớn dừng ở dạng
> "có nên làm X hay không" — và những quyết định "có/không" ấy về sau thất bại
> tới **52%**, so với 32% ở nhóm cân nhắc từ hai phương án trở lên...
>
> [tiếp tục giải thích *cơ chế*: vì sao chỉ cần thêm một lựa chọn thứ hai đã kéo
> tâm trí ra khỏi bẫy thiên kiến xác nhận...]
>
> > 💡 **Góc nhìn thêm**
> > Cơ chế này ăn khớp với khái niệm "thu hẹp tầm nhìn" (bandwidth) mà
> > Sendhil Mullainathan mô tả trong *Scarcity*: khi bị dồn ép, ta tự bó hẹp
> > khung lựa chọn đúng vào lúc cần mở rộng nó nhất.

Lưu ý: số liệu và tên tác giả nằm trong nội dung chính vì chúng có trong sách;
phần liên hệ tới *Scarcity* được tách riêng vào box "Góc nhìn thêm" và ghi rõ
nguồn.

## Khi thiếu nguyên liệu

Nếu người dùng chỉ nói tên sách mà **không cung cấp nội dung**, và bạn không nắm
chắc chi tiết cuốn đó: nói thẳng rằng để tóm tắt *chuyên sâu và trung thực* cần
văn bản gốc (file EPUB/PDF, hoặc dán chương), thay vì viết dựa trên trí nhớ mơ hồ
— vì làm vậy dễ bịa chi tiết, đi ngược nguyên tắc cốt lõi của skill. Có thể đề
xuất bắt đầu bằng một chương để người dùng duyệt văn phong trước.

## Đóng gói thành EPUB (khi có sẵn file sách)

Khi người dùng muốn **một file EPUB hoàn chỉnh** từ cả cuốn sách (không phải chỉ
văn bản trong khung chat), dùng công cụ `ebook-summarize` — nó tự chạy đúng quy
trình 4 bước trên cho toàn bộ file và xuất EPUB.

```powershell
cd D:\Projects\ebook-shortform
$env:GOOGLE_CLOUD_PROJECT = "<gcp-project-id>"       # xác thực qua gcloud ADC
.\.venv\Scripts\ebook-summarize.exe "duong-dan\sach.epub"
# -> sinh sach.tomtat.epub  +  sach.tomtat.analysis.json
```

Điều cần biết khi tư vấn cho người dùng:

- **Bìa**: EPUB ra dùng lại **nguyên bản ảnh bìa gốc** của sách, đặt làm trang
  bìa chuẩn (hiện đúng trên mọi reader). Tự động, không cần thao tác gì.
- **Chạy thử trước**: thêm `--max-lessons 2 --keep-workdir` để duyệt văn phong 2
  bài đầu; bài đã sinh được cache nên chạy full sau đó không tốn lại.
- **Điều chỉnh phạm vi**: mở `*.tomtat.analysis.json` để đổi chương nào là
  `content`/`skip` rồi chạy lại (bìa/mục lục/bản quyền/index vốn tự bị bỏ qua).
- Đầu vào nhận cả `.epub` lẫn `.pdf` (kể cả PDF scan — tự OCR).

Xem `README.md` của dự án để biết đầy đủ tuỳ chọn. Nếu người dùng chỉ dán một
chương/đoạn vào chat thì **không cần công cụ** — cứ tự viết theo skill này.
