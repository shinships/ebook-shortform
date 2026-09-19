"""tts.py — Module trung tâm quản lý Text-to-Speech đa nền tảng (ZeroTTS, VieNeu-TTS, Vbee AIVoice).

Cung cấp:
- ZeroTTS (Mặc định): Chạy real-time trên CPU, 48kHz, WER 1.03%, chuẩn hóa tiếng Việt và code-switching tiếng Anh.
- VieNeu-TTS v3 Turbo: Chạy on-device 48kHz, hỗ trợ Voice Cloning tức thì từ audio mẫu.
- Vbee AIVoice API: Cloud TTS dự phòng.
- Hàm gọi thống nhất call_tts() với cơ chế tự động fallback thông minh giữa các engine.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

import requests

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = PROJECT_DIR / ".env"
PODCASTS_DIR = PROJECT_DIR / "output" / "podcasts"

# ── 1. Danh mục giọng đọc ZeroTTS (Mặc định - Real-time CPU 48kHz) ──
ZEROTTS_POPULAR_VOICES = {
    "maichi": "HN - Mai Chi (Nữ Bắc, trẻ trung, kể chuyện nhẹ nhàng, thân thiện - Mặc định ZeroTTS)",
    "baotrang": "HN - Bảo Trang (Nữ Bắc, trưởng thành, tin tức, rõ ràng, trung tính)",
    "kimoanh": "HN - Kim Oanh (Nữ Bắc, trung niên, kể chuyện, ấm áp, truyền cảm)",
    "hamy": "HN - Hà My (Nữ Bắc, trẻ trung, hoạt hình, cao, biểu cảm)",
    "giahuy": "HN - Gia Huy (Nam Bắc, trẻ trung, kể chuyện, trầm ấm, tâm tình)",
    "huuduc": "HN - Hữu Đức (Nam Bắc, lớn tuổi, kể chuyện, trầm, điềm đạm)",
    "quangminh": "HN - Quang Minh (Nam Bắc, trẻ trung, tin tức, rõ ràng, dứt khoát)",
    "tiendat": "HN - Tiến Đạt (Nam Bắc, trẻ trung, bình luận, sôi nổi, năng lượng cao)",
}

# ── 2. Danh mục giọng đọc VieNeu-TTS v3 Turbo (On-device 48kHz) ──
VIENEU_POPULAR_VOICES = {
    "Minh Quân": "HN - Minh Quân (Nam Bắc, tự nhiên, sinh động - Mặc định VieNeu)",
    "Thái Sơn": "SG - Thái Sơn (Nam Nam, kể chuyện / audiobook truyền cảm)",
    "Anh Khôi": "HN - Anh Khôi (Nam Bắc, kể chuyện / sách nói trầm ấm)",
    "Quỳnh Anh": "HN - Quỳnh Anh (Nữ Bắc, đọc truyện / diễn cảm sâu lắng)",
    "Ngọc Huyền": "HN - Ngọc Huyền (Nữ Bắc, tự nhiên, phong cách podcast)",
    "Thanh Bình": "HN - Thanh Bình (Nam Bắc, kể chuyện, mạch lạc)",
    "Ngọc Linh": "HN - Ngọc Linh (Nữ Bắc, kể chuyện nhẹ nhàng)",
    "Thục Đoan": "SG - Thục Đoan (Nữ Nam, kể chuyện dịu dàng)",
    "Trúc Ly": "HN - Trúc Ly (Nữ Bắc, tự nhiên, trong trẻo)",
    "Phạm Tuyên": "HN - Phạm Tuyên (Nam Bắc, tự nhiên, gần gũi)",
    "Mỹ Duyên": "SG - Mỹ Duyên (Nữ Nam, đọc truyện ấm áp)",
    "Quang Sơn": "Huế - Quang Sơn (Nam Trung, tự nhiên)",
    "Ngọc Trân": "Huế - Ngọc Trân (Nữ Trung, tự nhiên ngọt ngào)",
    "Đoan Trang": "HN - Đoan Trang (Nữ Bắc, tự nhiên)",
    "Mai Anh": "HN - Mai Anh (Nữ Bắc, phong cách tin tức)",
    "Minh Đức": "HN - Minh Đức (Nam Bắc, phong cách tin tức)",
    "Adam": "SG - Adam (Nam Nam, hiện đại, tự nhiên)",
    "Đức Trí": "SG - Đức Trí (Nam Nam, đọc truyện)",
    "Kim Thanh": "SG - Kim Thanh (Nữ Nam, đọc truyện)",
    "Minh Triết": "SG - Minh Triết (Nam Nam, tin tức)",
    "Thùy Dung": "SG - Thùy Dung (Nữ Nam, tin tức)",
    "Xuân Vĩnh": "HN - Xuân Vĩnh (Nam Bắc, tự nhiên)",
}

# ── 3. Danh mục giọng đọc Vbee AIVoice (Cloud API) ──
VBEE_POPULAR_VOICES = {
    "hn_female_maiphuong_vdts_48k-fhg": "HN - Mai Phương (Nữ Bắc, nhẹ nhàng)",
    "hn_male_manhdung_news_48k-fhg": "HN - Mạnh Dũng (Nam Bắc, thời sự)",
    "hn_female_ngochuyen_full_48k-fhg": "HN - Ngọc Huyền (Nữ Bắc, podcast)",
    "hn_male_thanhlong_talk_48k-fhg": "HN - Thanh Long (Nam Bắc, đàm thoại)",
    "hn_male_phuthang_stor80dt_48k-fhg": "HN - Anh Khôi (Nam Bắc, đọc truyện)",
    "hn_male_minhquan_yt-stable": "HN - Minh Quân (Nam Bắc, trẻ trung)",
    "sg_female_lantrinh_vdts_48k-fhg": "SG - Lan Trinh (Nữ Nam, dịu dàng)",
    "sg_female_thaotrinh_full_48k-fhg": "SG - Thảo Trinh (Nữ Nam, truyền cảm)",
    "sg_male_trungkien_vdts_48k-fhg": "SG - Trung Kiên (Nam Nam, ấm)",
    "sg_male_minhhoang_full_48k-fhg": "SG - Minh Hoàng (Nam Nam, hiện đại)",
    "hue_female_huonggiang_full_48k-fhg": "Huế - Hương Giang (Nữ Huế, ngọt ngào)",
    "hue_male_duyphuong_full_48k-fhg": "Huế - Duy Phương (Nam Huế, sâu lắng)",
}

# Tổng hợp tra cứu
POPULAR_VOICES = {**ZEROTTS_POPULAR_VOICES, **VIENEU_POPULAR_VOICES, **VBEE_POPULAR_VOICES}

# ── 3b. Hồ sơ giọng đọc: giới tính + vùng miền (dùng cho casting đa giọng) ──
# Giới tính/vùng miền hiện chỉ nằm trong chuỗi mô tả tiếng Việt ("Nữ Bắc") hoặc
# tiền tố ID Vbee (hn_female_...). Cả hai đều mong manh, nên khai báo tường minh.
#   gender: "female" | "male"      region: "north" | "south" | "central"
VOICE_PROFILES: dict[str, dict[str, str]] = {
    # ZeroTTS (8 giọng, toàn bộ giọng Bắc)
    "maichi":    {"engine": "zerotts", "gender": "female", "region": "north"},
    "baotrang":  {"engine": "zerotts", "gender": "female", "region": "north"},
    "kimoanh":   {"engine": "zerotts", "gender": "female", "region": "north"},
    "hamy":      {"engine": "zerotts", "gender": "female", "region": "north"},
    "giahuy":    {"engine": "zerotts", "gender": "male",   "region": "north"},
    "huuduc":    {"engine": "zerotts", "gender": "male",   "region": "north"},
    "quangminh": {"engine": "zerotts", "gender": "male",   "region": "north"},
    "tiendat":   {"engine": "zerotts", "gender": "male",   "region": "north"},

    # VieNeu (22 giọng: 11 nam / 11 nữ)
    "Minh Quân":  {"engine": "vieneu", "gender": "male",   "region": "north"},
    "Anh Khôi":   {"engine": "vieneu", "gender": "male",   "region": "north"},
    "Thanh Bình": {"engine": "vieneu", "gender": "male",   "region": "north"},
    "Phạm Tuyên": {"engine": "vieneu", "gender": "male",   "region": "north"},
    "Minh Đức":   {"engine": "vieneu", "gender": "male",   "region": "north"},
    "Xuân Vĩnh":  {"engine": "vieneu", "gender": "male",   "region": "north"},
    "Thái Sơn":   {"engine": "vieneu", "gender": "male",   "region": "south"},
    "Adam":       {"engine": "vieneu", "gender": "male",   "region": "south"},
    "Đức Trí":    {"engine": "vieneu", "gender": "male",   "region": "south"},
    "Minh Triết": {"engine": "vieneu", "gender": "male",   "region": "south"},
    "Quang Sơn":  {"engine": "vieneu", "gender": "male",   "region": "central"},
    "Quỳnh Anh":  {"engine": "vieneu", "gender": "female", "region": "north"},
    "Ngọc Huyền": {"engine": "vieneu", "gender": "female", "region": "north"},
    "Ngọc Linh":  {"engine": "vieneu", "gender": "female", "region": "north"},
    "Trúc Ly":    {"engine": "vieneu", "gender": "female", "region": "north"},
    "Đoan Trang": {"engine": "vieneu", "gender": "female", "region": "north"},
    "Mai Anh":    {"engine": "vieneu", "gender": "female", "region": "north"},
    "Thục Đoan":  {"engine": "vieneu", "gender": "female", "region": "south"},
    "Mỹ Duyên":   {"engine": "vieneu", "gender": "female", "region": "south"},
    "Kim Thanh":  {"engine": "vieneu", "gender": "female", "region": "south"},
    "Thùy Dung":  {"engine": "vieneu", "gender": "female", "region": "south"},
    "Ngọc Trân":  {"engine": "vieneu", "gender": "female", "region": "central"},

    # Vbee (12 giọng: 7 nam / 5 nữ)
    "hn_female_maiphuong_vdts_48k-fhg":   {"engine": "vbee", "gender": "female", "region": "north"},
    "hn_female_ngochuyen_full_48k-fhg":   {"engine": "vbee", "gender": "female", "region": "north"},
    "sg_female_lantrinh_vdts_48k-fhg":    {"engine": "vbee", "gender": "female", "region": "south"},
    "sg_female_thaotrinh_full_48k-fhg":   {"engine": "vbee", "gender": "female", "region": "south"},
    "hue_female_huonggiang_full_48k-fhg": {"engine": "vbee", "gender": "female", "region": "central"},
    "hn_male_manhdung_news_48k-fhg":      {"engine": "vbee", "gender": "male",   "region": "north"},
    "hn_male_thanhlong_talk_48k-fhg":     {"engine": "vbee", "gender": "male",   "region": "north"},
    "hn_male_phuthang_stor80dt_48k-fhg":  {"engine": "vbee", "gender": "male",   "region": "north"},
    "hn_male_minhquan_yt-stable":         {"engine": "vbee", "gender": "male",   "region": "north"},
    "sg_male_trungkien_vdts_48k-fhg":     {"engine": "vbee", "gender": "male",   "region": "south"},
    "sg_male_minhhoang_full_48k-fhg":     {"engine": "vbee", "gender": "male",   "region": "south"},
    "hue_male_duyphuong_full_48k-fhg":    {"engine": "vbee", "gender": "male",   "region": "central"},
}

# Giọng có trong catalogue nhưng chưa khai báo hồ sơ (cảnh báo mềm, không raise
# lúc import vì module này được import ở khắp nơi kể cả CLI).
_UNPROFILED_VOICES = sorted(set(POPULAR_VOICES) - set(VOICE_PROFILES))

# ── 3c. Thứ tự ưu tiên khi phân vai giọng đọc ──
# Xếp theo ĐỘ KHÁC BIỆT ÂM SẮC, không theo bảng chữ cái: hai phần tử đầu mỗi danh
# sách là cặp nghe khác nhau rõ nhất, nên cast 2 người là tối ưu mà không cần
# xử lý đặc biệt. Cặp nam vieneu (Minh Quân + Anh Khôi) trùng đúng cặp đã chọn
# tay trong scripts/build_dual_host_podcast.py.
CASTING_ORDER: dict[str, dict[str, list[str]]] = {
    "vieneu": {
        "male":   ["Minh Quân", "Anh Khôi", "Thái Sơn", "Thanh Bình", "Minh Đức",
                   "Phạm Tuyên", "Xuân Vĩnh", "Adam", "Đức Trí", "Minh Triết", "Quang Sơn"],
        "female": ["Ngọc Huyền", "Quỳnh Anh", "Thục Đoan", "Trúc Ly", "Ngọc Linh",
                   "Mai Anh", "Đoan Trang", "Mỹ Duyên", "Kim Thanh", "Thùy Dung", "Ngọc Trân"],
    },
    "zerotts": {
        "male":   ["giahuy", "quangminh", "huuduc", "tiendat"],
        "female": ["maichi", "baotrang", "kimoanh", "hamy"],
    },
    "vbee": {
        "male":   ["hn_male_minhquan_yt-stable", "hn_male_phuthang_stor80dt_48k-fhg",
                   "hn_male_manhdung_news_48k-fhg", "hn_male_thanhlong_talk_48k-fhg",
                   "sg_male_trungkien_vdts_48k-fhg", "sg_male_minhhoang_full_48k-fhg",
                   "hue_male_duyphuong_full_48k-fhg"],
        "female": ["hn_female_ngochuyen_full_48k-fhg", "hn_female_maiphuong_vdts_48k-fhg",
                   "sg_female_thaotrinh_full_48k-fhg", "sg_female_lantrinh_vdts_48k-fhg",
                   "hue_female_huonggiang_full_48k-fhg"],
    },
}

# ⚠️ "nam" vừa nghĩa là male (giới tính) vừa là miền Nam (vùng). Đây là lý do
# giới tính và vùng miền PHẢI có hai bảng alias tách biệt và không bao giờ được
# suy ra từ cùng một trường free-text.
_GENDER_ALIASES = {
    "female": "female", "f": "female", "nu": "female", "nữ": "female",
    "nu gioi": "female", "nữ giới": "female", "woman": "female", "w": "female",
    "male": "male", "m": "male", "nam": "male",
    "nam gioi": "male", "nam giới": "male", "man": "male",
}

_REGION_ALIASES = {
    "north": "north", "bac": "north", "bắc": "north", "hn": "north", "hanoi": "north",
    "south": "south", "sg": "south", "saigon": "south", "hcm": "south",
    "central": "central", "trung": "central", "hue": "central", "huế": "central",
}

# ── 4. Bảng bí danh (Aliases) nhận diện giọng đọc linh hoạt ──
VOICE_ALIASES = {
    # ZeroTTS Aliases
    "maichi": "maichi",
    "mai chi": "maichi",
    "baotrang": "baotrang",
    "bảo trang": "baotrang",
    "bao trang": "baotrang",
    "kimoanh": "kimoanh",
    "kim oanh": "kimoanh",
    "hamy": "hamy",
    "hà my": "hamy",
    "ha my": "hamy",
    "giahuy": "giahuy",
    "gia huy": "giahuy",
    "huuduc": "huuduc",
    "hữu đức": "huuduc",
    "huu duc": "huuduc",
    "quangminh": "quangminh",
    "quang minh": "quangminh",
    "tiendat": "tiendat",
    "tiến đạt": "tiendat",
    "tien dat": "tiendat",

    # VieNeu Aliases
    "minhquan": "Minh Quân",
    "minh quân": "Minh Quân",
    "thaison": "Thái Sơn",
    "thái sơn": "Thái Sơn",
    "anhkhoi": "Anh Khôi",
    "anh khôi": "Anh Khôi",
    "quynhanh": "Quỳnh Anh",
    "quỳnh anh": "Quỳnh Anh",
    "ngochuyen": "Ngọc Huyền",
    "ngọc huyền": "Ngọc Huyền",
    "thanhbinh": "Thanh Bình",
    "thanh bình": "Thanh Bình",
    "ngoclinh": "Ngọc Linh",
    "ngọc linh": "Ngọc Linh",
    "thucdoan": "Thục Đoan",
    "thục đoan": "Thục Đoan",
    "trucly": "Trúc Ly",
    "trúc ly": "Trúc Ly",
    "phamtuyen": "Phạm Tuyên",
    "phạm tuyên": "Phạm Tuyên",
    "myduyen": "Mỹ Duyên",
    "mỹ duyên": "Mỹ Duyên",
    "quangson": "Quang Sơn",
    "quang sơn": "Quang Sơn",
    "ngoctran": "Ngọc Trân",
    "ngọc trân": "Ngọc Trân",
    "doantrang": "Đoan Trang",
    "đoan trang": "Đoan Trang",
    "maianh": "Mai Anh",
    "mai anh": "Mai Anh",
    "minhduc": "Minh Đức",
    "minh đức": "Minh Đức",
    "adam": "Adam",
    "xuanvinh": "Xuân Vĩnh",
    "xuân vĩnh": "Xuân Vĩnh",

    # Vbee Aliases
    "maiphuong": "hn_female_maiphuong_vdts_48k-fhg",
    "mai phương": "hn_female_maiphuong_vdts_48k-fhg",
    "manhdung": "hn_male_manhdung_news_48k-fhg",
    "mạnh dũng": "hn_male_manhdung_news_48k-fhg",
    "thanhlong": "hn_male_thanhlong_talk_48k-fhg",
    "thanh long": "hn_male_thanhlong_talk_48k-fhg",
    "lantrinh": "sg_female_lantrinh_vdts_48k-fhg",
    "lan trinh": "sg_female_lantrinh_vdts_48k-fhg",
    "thaotrinh": "sg_female_thaotrinh_full_48k-fhg",
    "thảo trinh": "sg_female_thaotrinh_full_48k-fhg",
    "trungkien": "sg_male_trungkien_vdts_48k-fhg",
    "trung kiên": "sg_male_trungkien_vdts_48k-fhg",
    "minhhoang": "sg_male_minhhoang_full_48k-fhg",
    "minh hoàng": "sg_male_minhhoang_full_48k-fhg",
    "huonggiang": "hue_female_huonggiang_full_48k-fhg",
    "hương giang": "hue_female_huonggiang_full_48k-fhg",
    "duyphuong": "hue_male_duyphuong_full_48k-fhg",
    "duy phương": "hue_male_duyphuong_full_48k-fhg",
}

# ── 5. Mapping tên người đọc thân thiện (Reader Name) ──
VOICE_TO_READER_NAME = {
    # ZeroTTS
    "maichi": "Mai Chi",
    "baotrang": "Bảo Trang",
    "kimoanh": "Kim Oanh",
    "hamy": "Hà My",
    "giahuy": "Gia Huy",
    "huuduc": "Hữu Đức",
    "quangminh": "Quang Minh",
    "tiendat": "Tiến Đạt",

    # VieNeu
    "Minh Quân": "Minh Quân",
    "Thái Sơn": "Thái Sơn",
    "Anh Khôi": "Anh Khôi",
    "Quỳnh Anh": "Quỳnh Anh",
    "Ngọc Huyền": "Ngọc Huyền",
    "Thanh Bình": "Thanh Bình",
    "Ngọc Linh": "Ngọc Linh",
    "Thục Đoan": "Thục Đoan",
    "Trúc Ly": "Trúc Ly",
    "Phạm Tuyên": "Phạm Tuyên",
    "Mỹ Duyên": "Mỹ Duyên",
    "Quang Sơn": "Quang Sơn",
    "Ngọc Trân": "Ngọc Trân",
    "Đoan Trang": "Đoan Trang",
    "Mai Anh": "Mai Anh",
    "Minh Đức": "Minh Đức",
    "Adam": "Adam",
    "Đức Trí": "Đức Trí",
    "Kim Thanh": "Kim Thanh",
    "Minh Triết": "Minh Triết",
    "Thùy Dung": "Thùy Dung",
    "Xuân Vĩnh": "Xuân Vĩnh",

    # Vbee
    "hn_female_maiphuong_vdts_48k-fhg": "Mai Phương",
    "hn_male_manhdung_news_48k-fhg": "Mạnh Dũng",
    "hn_female_ngochuyen_full_48k-fhg": "Ngọc Huyền",
    "hn_male_thanhlong_talk_48k-fhg": "Thanh Long",
    "hn_male_phuthang_stor80dt_48k-fhg": "Anh Khôi",
    "hn_male_minhquan_yt-stable": "Minh Quân",
    "sg_female_lantrinh_vdts_48k-fhg": "Lan Trinh",
    "sg_female_thaotrinh_full_48k-fhg": "Thảo Trinh",
    "sg_male_trungkien_vdts_48k-fhg": "Trung Kiên",
    "sg_male_minhhoang_full_48k-fhg": "Minh Hoàng",
    "hue_female_huonggiang_full_48k-fhg": "Hương Giang",
    "hue_male_duyphuong_full_48k-fhg": "Duy Phương",
}

DEFAULT_FALLBACK_VOICE_ZEROTTS = "maichi"
DEFAULT_FALLBACK_VOICE_VIENEU = "Minh Quân"
DEFAULT_FALLBACK_VOICE_VBEE = "hn_female_maiphuong_vdts_48k-fhg"

VBEE_TTS_URL = "https://vbee.vn/api/v1/tts"

# ── Hằng số ước lượng thời lượng đọc (ĐO từ 40 tập thật trong output/podcasts/) ──
# Phép đo: median 21.39 ký tự/giây ở atempo 1.15 (p10 19.64 / p90 21.96, mono 192kbps)
#   => chuẩn hóa về 1.0x: 21.39 / 1.15 ≈ 18.6 ký tự/giây
# Chạy lại scripts/calibrate_tts_speed.py khi đổi engine hoặc đổi speed mặc định.
VI_CHARS_PER_SEC_1X: dict[str, float] = {
    "vieneu": 18.6,
    "zerotts": 18.6,
    "vbee": 15.5,   # đo từ 3 tập Vbee 128kbps: 17.57 / 17.71 / 18.71 ch/s @1.15x
}
DEFAULT_VI_CHARS_PER_SEC_1X = 18.6

# Ảnh bìa nhúng ID3: median 240KB, max 377KB trên 62 file -> chừa 512KB cho chắc.
MP3_ID3_OVERHEAD_BYTES = 512 * 1024

# Telegram Bot API giới hạn 50MB khi gửi file; chừa biên cho multipart overhead.
TELEGRAM_MAX_UPLOAD_BYTES = 49 * 1024 * 1024

# Singleton cache cho model ZeroTTS / VieNeu
_zerotts_model = None
_vieneu_model = None


def load_env(path: Path = ENV_PATH) -> dict[str, str]:
    """Đọc file .env an toàn."""
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def get_default_engine() -> str:
    """Xác định TTS engine mặc định: zerotts (ưu tiên), vieneu hoặc vbee."""
    env = load_env(ENV_PATH)
    engine = os.environ.get("TTS_ENGINE") or env.get("TTS_ENGINE") or "zerotts"
    return engine.lower().strip()


def resolve_voice_code(voice_input: str | None = None, engine: str | None = None) -> str:
    """Xác định mã giọng đọc chuẩn theo từng engine từ tên trực tiếp, alias hoặc .env."""
    # Nếu không truyền engine, cố gắng suy luận engine từ voice_input nếu có
    if not engine and voice_input:
        raw_check = str(voice_input).strip()
        if raw_check in ZEROTTS_POPULAR_VOICES:
            engine = "zerotts"
        elif raw_check in VIENEU_POPULAR_VOICES:
            engine = "vieneu"
        elif raw_check in VBEE_POPULAR_VOICES:
            engine = "vbee"
        else:
            norm_check = raw_check.lower().replace(" ", "").replace("_", "").replace("-", "")
            for alias, code in VOICE_ALIASES.items():
                alias_norm = alias.replace(" ", "").replace("_", "").replace("-", "")
                if norm_check == alias_norm or norm_check in alias_norm or alias_norm in norm_check:
                    if code in ZEROTTS_POPULAR_VOICES:
                        engine = "zerotts"
                        break
                    elif code in VIENEU_POPULAR_VOICES:
                        engine = "vieneu"
                        break
                    elif code in VBEE_POPULAR_VOICES:
                        engine = "vbee"
                        break

    if not engine:
        engine = get_default_engine()
    engine = engine.lower().strip()

    env = load_env(ENV_PATH)
    if not voice_input:
        if engine == "zerotts":
            voice_input = os.environ.get("ZEROTTS_VOICE") or env.get("ZEROTTS_VOICE") or DEFAULT_FALLBACK_VOICE_ZEROTTS
        elif engine == "vbee":
            voice_input = os.environ.get("VBEE_VOICE") or env.get("VBEE_VOICE") or DEFAULT_FALLBACK_VOICE_VBEE
        else:
            voice_input = os.environ.get("VIENEU_VOICE") or env.get("VIENEU_VOICE") or DEFAULT_FALLBACK_VOICE_VIENEU

    raw = str(voice_input).strip()
    if engine == "zerotts" and raw in ZEROTTS_POPULAR_VOICES:
        return raw
    if engine == "vieneu" and raw in VIENEU_POPULAR_VOICES:
        return raw
    if engine == "vbee" and raw in VBEE_POPULAR_VOICES:
        return raw

    norm = raw.lower().replace(" ", "").replace("_", "").replace("-", "")
    for alias, code in VOICE_ALIASES.items():
        alias_norm = alias.replace(" ", "").replace("_", "").replace("-", "")
        if norm == alias_norm:
            # Kiểm tra xem code có tương thích engine không
            if engine == "zerotts" and code in ZEROTTS_POPULAR_VOICES:
                return code
            elif engine == "vieneu" and code in VIENEU_POPULAR_VOICES:
                return code
            elif engine == "vbee" and code in VBEE_POPULAR_VOICES:
                return code

    # Tìm kiếm gần đúng trong alias
    for alias, code in VOICE_ALIASES.items():
        alias_norm = alias.replace(" ", "").replace("_", "").replace("-", "")
        if norm in alias_norm or alias_norm in norm:
            if engine == "zerotts" and code in ZEROTTS_POPULAR_VOICES:
                return code
            elif engine == "vieneu" and code in VIENEU_POPULAR_VOICES:
                return code
            elif engine == "vbee" and code in VBEE_POPULAR_VOICES:
                return code

    # Fallback mặc định theo engine
    if engine == "zerotts":
        return DEFAULT_FALLBACK_VOICE_ZEROTTS
    elif engine == "vbee":
        return DEFAULT_FALLBACK_VOICE_VBEE
    else:
        return DEFAULT_FALLBACK_VOICE_VIENEU


def get_reader_name(voice_code: str | None = None, engine: str | None = None) -> str:
    """Lấy tên người đọc thân thiện (ví dụ: Mai Chi, Minh Quân, Thái Sơn)."""
    if not voice_code:
        target_code = resolve_voice_code(None, engine=engine)
        return VOICE_TO_READER_NAME.get(target_code, target_code)

    raw = str(voice_code).strip()
    if raw in VOICE_TO_READER_NAME:
        if not engine:
            return VOICE_TO_READER_NAME[raw]
        eng = engine.lower().strip()
        if (eng == "zerotts" and raw in ZEROTTS_POPULAR_VOICES) or \
           (eng == "vieneu" and raw in VIENEU_POPULAR_VOICES) or \
           (eng == "vbee" and raw in VBEE_POPULAR_VOICES):
            return VOICE_TO_READER_NAME[raw]

    target_code = resolve_voice_code(raw, engine=engine)
    return VOICE_TO_READER_NAME.get(target_code, target_code)


# ── 5b. Phân vai giọng đọc theo giới tính (casting đa giọng) ──

def normalize_gender(value: str | None) -> str | None:
    """Chuẩn hóa nhãn giới tính về 'female' | 'male' (None nếu không nhận ra)."""
    if not value:
        return None
    return _GENDER_ALIASES.get(str(value).strip().lower())


def normalize_region(value: str | None) -> str | None:
    """Chuẩn hóa nhãn vùng miền về 'north' | 'south' | 'central'."""
    if not value:
        return None
    return _REGION_ALIASES.get(str(value).strip().lower())


def _profile(voice_code: str | None, engine: str | None = None) -> dict[str, str] | None:
    """Tra hồ sơ giọng. Chỉ fuzzy-resolve khi tra trực tiếp không ra."""
    if not voice_code:
        return None
    raw = str(voice_code).strip()
    if raw in VOICE_PROFILES:
        return VOICE_PROFILES[raw]
    resolved = resolve_voice_code(raw, engine=engine)
    return VOICE_PROFILES.get(resolved)


def voice_gender(voice_code: str | None, engine: str | None = None) -> str | None:
    """Trả về 'female' | 'male' cho một mã giọng (None nếu chưa khai báo hồ sơ)."""
    prof = _profile(voice_code, engine)
    return prof["gender"] if prof else None


def voice_region(voice_code: str | None, engine: str | None = None) -> str | None:
    """Trả về 'north' | 'south' | 'central' cho một mã giọng."""
    prof = _profile(voice_code, engine)
    return prof["region"] if prof else None


def list_voices_by_gender(
    engine: str,
    gender: str,
    region: str | None = None,
) -> list[str]:
    """Danh sách mã giọng theo engine + giới tính, ưu tiên giọng cùng vùng miền lên đầu.

    Hoàn toàn tất định: giữ nguyên thứ tự CASTING_ORDER, chỉ ổn định-sắp lại để
    các giọng đúng vùng miền đứng trước. Không dùng set() hay random.
    """
    eng = (engine or "").lower().strip()
    gen = normalize_gender(gender) or "female"
    pool = list(CASTING_ORDER.get(eng, {}).get(gen, []))
    reg = normalize_region(region)
    if not reg:
        return pool
    same = [v for v in pool if VOICE_PROFILES.get(v, {}).get("region") == reg]
    other = [v for v in pool if VOICE_PROFILES.get(v, {}).get("region") != reg]
    return same + other


def _normalize_roster(roster) -> list[tuple[str, str, str | None]]:
    """Chuẩn hóa roster về [(speaker_id, gender, region|None)] giữ nguyên thứ tự khai báo."""
    items: list[tuple[str, str, str | None]] = []
    if isinstance(roster, dict):
        pairs = list(roster.items())
    else:
        pairs = list(roster or [])

    for entry in pairs:
        if isinstance(entry, dict):
            sid = str(entry.get("id") or entry.get("speaker") or entry.get("name") or "")
            gender = normalize_gender(entry.get("gender"))
            region = normalize_region(entry.get("region"))
        else:
            sid, raw_gender = entry[0], entry[1]
            sid = str(sid)
            gender = normalize_gender(raw_gender)
            region = None
        if not sid:
            continue
        items.append((sid, gender or "male", region))
    return items


def pick_voices_for_cast(
    roster,
    engine: str | None = None,
    host_voice: str | None = None,
    prefer_region: str | None = None,
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    """Phân vai giọng đọc cho từng người nói.

    roster chấp nhận 3 dạng:
      - {"MC": "nam", "GUEST": "nữ"}
      - [("MC", "male"), ("GUEST", "female")]
      - [{"id": "MC", "gender": "male", "region": "north"}, ...]

    Bảo đảm: mỗi người một giọng khác nhau, đúng giới tính, không trùng host_voice,
    ưu tiên cùng vùng miền với host, và TẤT ĐỊNH (cùng input → cùng output).
    """
    eng = (engine or get_default_engine()).lower().strip()
    entries = _normalize_roster(roster)

    reserved: set[str] = set()
    host_code: str | None = None
    if host_voice:
        host_code = resolve_voice_code(host_voice, engine=eng)
        reserved.add(host_code)
        prefer_region = prefer_region or voice_region(host_code, eng)
    prefer_region = normalize_region(prefer_region)

    assigned: dict[str, str] = {}

    # 1. Chỉ định thủ công của người dùng luôn thắng, kể cả khi trái giới tính suy đoán.
    for sid, code in (overrides or {}).items():
        picked = resolve_voice_code(code, engine=eng)
        assigned[str(sid)] = picked
        reserved.add(picked)
        declared = next((g for s, g, _ in entries if s == str(sid)), None)
        actual = voice_gender(picked, eng)
        if declared and actual and declared != actual:
            print(
                f"⚠️ [casting] '{sid}' được chỉ định giọng {get_reader_name(picked, eng)} "
                f"({actual}) trái với giới tính suy đoán ({declared}). Tôn trọng lựa chọn thủ công.",
                file=sys.stderr,
            )

    # 2. Phân vai phần còn lại theo đúng thứ tự khai báo (đảm bảo tất định).
    gender_seen: dict[str, int] = {}
    for sid, gender, region in entries:
        idx = gender_seen.get(gender, 0)
        gender_seen[gender] = idx + 1
        if sid in assigned:
            continue

        ordered = list_voices_by_gender(eng, gender, region or prefer_region)
        free = [v for v in ordered if v not in reserved]

        if free:
            picked = free[0]
        elif [v for v in ordered if v != host_code]:
            # Tầng 1 — xoay vòng trong cùng giới tính (chỉ chạm tới khi số người nói
            # cùng giới vượt số giọng có sẵn: >11 với vieneu, >4 với zerotts).
            # Loại host_code khỏi vòng xoay để giọng dẫn luôn còn phân biệt được.
            recycle = [v for v in ordered if v != host_code]
            picked = recycle[idx % len(recycle)]
            print(
                f"⚠️ [casting] Hết giọng {gender} riêng biệt cho engine '{eng}'; "
                f"'{sid}' dùng lại giọng {get_reader_name(picked, eng)}.",
                file=sys.stderr,
            )
        else:
            # Tầng 2 — engine không có giọng nào thuộc giới tính này.
            opposite = "male" if gender == "female" else "female"
            alt = [v for v in list_voices_by_gender(eng, opposite, prefer_region) if v not in reserved]
            if alt:
                picked = alt[0]
                print(
                    f"⚠️ [casting] Engine '{eng}' không có giọng {gender}; "
                    f"'{sid}' phải dùng giọng {opposite} ({get_reader_name(picked, eng)}).",
                    file=sys.stderr,
                )
            else:
                # Tầng 3 — quay về giọng mặc định của engine.
                picked = resolve_voice_code(None, engine=eng)
                print(f"⚠️ [casting] '{sid}' quay về giọng mặc định của '{eng}'.", file=sys.stderr)

        assigned[sid] = picked
        reserved.add(picked)

    return assigned


def describe_cast(voice_map: dict[str, str], engine: str | None = None) -> str:
    """Mô tả dàn giọng để in log / show notes: 'MC → Anh Khôi (Nam Bắc)'."""
    eng = (engine or get_default_engine()).lower().strip()
    _GENDER_VI = {"female": "Nữ", "male": "Nam"}
    _REGION_VI = {"north": "Bắc", "south": "Nam", "central": "Trung"}
    parts = []
    for sid, code in voice_map.items():
        name = get_reader_name(code, eng)
        prof = _profile(code, eng)
        if prof:
            tag = f" ({_GENDER_VI.get(prof['gender'], '?')} {_REGION_VI.get(prof['region'], '?')})"
        else:
            tag = ""
        parts.append(f"{sid} → {name}{tag}")
    return " · ".join(parts)


def sanitize_repetitions(text: str) -> str:
    """Loại bỏ hiện tượng lặp từ/cụm từ kéo dài (repetition loop/hallucination) do LLM sinh ra."""
    if not text:
        return ""
    # 1. Khử lặp từ đơn lẻ liên tiếp quá 2 lần (ví dụ: "dằn dằn dằn dằn..." -> "dằn")
    pattern_word = re.compile(r"(\b\w+\b)(?:\s+\1\b){2,}", re.IGNORECASE | re.UNICODE)
    text = pattern_word.sub(r"\1", text)

    # 2. Khử lặp cụm từ (2-8 từ) liên tiếp quá 2 lần
    pattern_phrase = re.compile(r"(\b(?:\w+\s+){1,7}\w+\b)(?:\s*[,.;:!?]?\s*\1\b){2,}", re.IGNORECASE | re.UNICODE)
    text = pattern_phrase.sub(r"\1", text)

    return text


def split_text_into_sentences(text: str, max_chars: int = 220) -> list[str]:
    """Cắt văn bản thành các câu / phân đoạn ngắn vừa vặn cho bộ suy luận TTS.

    max_chars=220 là cố ý: `V3TurboVieNeuTTS.infer()` có `max_chars=256` riêng của
    nó, nên cắt trước ở 220 bảo đảm một câu = một lần infer và giữ quyền kiểm soát
    khoảng lặng ở phía chúng ta.
    """
    raw_sentences = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    chunks: list[str] = []
    for s in raw_sentences:
        s = s.strip()
        if not s:
            continue
        if len(s) <= max_chars:
            chunks.append(s)
            continue
        # Câu quá dài: cắt tiếp theo dấu phẩy / chấm phẩy / gạch ngang
        sub_parts = re.split(r"(?<=[,;:—])\s+", s)
        cur = ""
        for part in sub_parts:
            if len(cur) + len(part) + 1 <= max_chars:
                cur = f"{cur} {part}".strip()
            else:
                if cur:
                    chunks.append(cur)
                cur = part.strip()
        if cur:
            chunks.append(cur)
    return chunks


def _segment_for_engine(text: str, engine: str, max_chars: int = 220) -> list[str]:
    """Phân đoạn văn bản theo bộ chunker phù hợp với từng engine.

    ZeroTTS dùng bộ chunker riêng (normalize_vi_text + chunk_text 15s) để giữ
    nguyên hành vi của call_zerotts_tts; các engine khác dùng split_text_into_sentences.
    """
    if engine != "zerotts":
        return split_text_into_sentences(text, max_chars=max_chars)

    try:
        norm_text = normalize_vi_text(text)
    except Exception:
        norm_text = text
    try:
        segments = [
            clean_segment_punctuation(s)
            for s in chunk_text(normalize_punctuation(norm_text), max_chunk_sec=15.0)
        ]
        segments = [s for s in segments if s.strip()]
    except Exception:
        segments = []
    return segments or [norm_text]


def _atempo_filter(speed: float) -> str | None:
    """Chuỗi filter atempo cho ffmpeg. atempo chỉ nhận 0.5–2.0 nên phải xâu chuỗi."""
    if abs(speed - 1.0) <= 0.01:
        return None
    factors: list[float] = []
    remaining = float(speed)
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(remaining)
    return ",".join(f"atempo={f:g}" for f in factors)


def get_vieneu_model():
    """Lấy singleton instance của VieNeu-TTS v3 Turbo.

    Bắt buộc cho render đa giọng: khởi tạo model rất tốn thời gian, một tập 60
    lượt nói mà tạo model mỗi lượt thì trả giá init 60 lần.
    Lưu ý `vieneu.Vieneu` là factory function, không phải class.
    """
    global _vieneu_model
    if _vieneu_model is None:
        from vieneu import Vieneu
        print("🧠 [VieNeu-TTS v3 Turbo] Đang khởi tạo mô hình on-device (48kHz)...")
        _vieneu_model = Vieneu()
        print("✅ [VieNeu] Khởi tạo mô hình thành công!")
    return _vieneu_model


def get_zerotts_model():
    """Lấy singleton instance của mô hình ZeroTTS."""
    global _zerotts_model
    if _zerotts_model is None:
        from zerotts import ZeroTTS
        print("🧠 [ZeroTTS] Đang tải mô hình zeroweight-ai/ZeroTTS...")
        _zerotts_model = ZeroTTS.from_pretrained("zeroweight-ai/ZeroTTS")
        print("✅ [ZeroTTS] Khởi tạo mô hình thành công!")
    return _zerotts_model


def call_zerotts_tts(
    text: str,
    output_path: Path,
    voice: str = "maichi",
    speed: float = 1.0,
    cfg_scale: float = 1.0,
    audio_temperature: float = 0.8,
) -> bool:
    """Tổng hợp âm thanh bằng ZeroTTS (CPU Real-Time, 48kHz, WER 1.03%) và xuất MP3 192k."""
    text = sanitize_repetitions(text)
    try:
        from zerotts import normalize_vi_text
        from zerotts.audio import concat_with_silence, save_wav
        from zerotts.chunking import chunk_text, clean_segment_punctuation, normalize_punctuation
    except ImportError:
        print("❌ Chưa cài đặt zerotts. Vui lòng chạy: pip install zerotts", file=sys.stderr)
        return False

    target_voice = resolve_voice_code(voice, engine="zerotts")
    reader_name = get_reader_name(target_voice, engine="zerotts")

    print(f"🎙️ [ZeroTTS 48kHz] Khởi chạy tổng hợp ({len(text)} ký tự) với Host [{reader_name}] (Mã: {target_voice})...")
    start_t = time.perf_counter()

    try:
        tts = get_zerotts_model()
    except Exception as e:
        print(f"❌ Lỗi khởi tạo ZeroTTS model: {e}", file=sys.stderr)
        return False

    # 1. Chuẩn hóa tiếng Việt (ngày tháng, số tiền, giờ giấc, viết tắt)
    try:
        norm_text = normalize_vi_text(text)
    except Exception as e:
        print(f"⚠️ Cảnh báo chuẩn hóa văn bản ZeroTTS: {e}, dùng văn bản gốc...", file=sys.stderr)
        norm_text = text

    # 2. Phân đoạn câu (chunking) thông minh
    try:
        segments = [
            clean_segment_punctuation(s)
            for s in chunk_text(normalize_punctuation(norm_text), max_chunk_sec=15.0)
        ]
        segments = [s for s in segments if s.strip()]
    except Exception:
        segments = [norm_text]

    if not segments:
        segments = [norm_text]

    # 3. Tiến hành tổng hợp từng phân đoạn
    chunks = []
    sampling_kwargs = {
        "cfg_scale": cfg_scale,
        "audio_temperature": audio_temperature,
        "audio_topk": 25,
        "audio_topp": 0.95,
        "audio_repetition_penalty": 1.2,
    }

    try:
        for i, seg in enumerate(segments, 1):
            if len(segments) > 1:
                display_seg = seg[:65] + "…" if len(seg) > 65 else seg
                print(f"   [{i}/{len(segments)}] {display_seg}")
            audio_chunk = tts.synthesize(seg, voice=target_voice, **sampling_kwargs)
            chunks.append(audio_chunk)

        # 4. Ghép các mẩu âm thanh kèm khoảng lặng tự nhiên 0.15s
        audio = concat_with_silence(chunks, silence_sec=0.15, sample_rate=tts.sample_rate)
    except Exception as e:
        print(f"❌ Lỗi trong quá trình tổng hợp ZeroTTS: {e}", file=sys.stderr)
        return False

    elapsed = time.perf_counter() - start_t
    audio_dur = audio.shape[-1] / tts.sample_rate
    rtf = elapsed / audio_dur if audio_dur > 0 else 0
    print(f"⚡ [ZeroTTS] Hoàn tất {audio_dur:.1f}s âm thanh trong {elapsed:.2f}s (RTF: {rtf:.3f}x)")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
        tmp_wav_path = Path(tmp_wav.name)

    try:
        save_wav(audio, str(tmp_wav_path), sample_rate=tts.sample_rate)

        # 5. Dùng FFmpeg để tối ưu, chuẩn hóa và áp dụng tốc độ (atempo) sang MP3 192k 48kHz
        cmd = ["ffmpeg", "-y", "-i", str(tmp_wav_path)]
        if abs(speed - 1.0) > 0.01:
            cmd.extend(["-filter:a", f"atempo={speed}"])
        cmd.extend(["-b:a", "192k", "-ar", "48000", str(output_path)])

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"⚠️ Cảnh báo FFmpeg: {proc.stderr}. Lưu trực tiếp file WAV...", file=sys.stderr)
            save_wav(audio, str(output_path.with_suffix(".wav")), sample_rate=tts.sample_rate)
            return True

        print(f"🎉 Đã tạo thành công file Podcast MP3 (ZeroTTS 48kHz, {speed}x): {output_path}")
        return True
    finally:
        tmp_wav_path.unlink(missing_ok=True)


def call_vieneu_tts(
    text: str,
    output_path: Path,
    voice: str = "Minh Quân",
    ref_audio: str | Path | None = None,
    speed: float = 1.0,
) -> bool:
    """Tổng hợp giọng nói bằng VieNeu-TTS v3 Turbo on-device (48kHz) và xuất file MP3 studio."""
    text = sanitize_repetitions(text)
    try:
        vieneu_model = get_vieneu_model()
    except ImportError:
        print("❌ Chưa cài đặt thư viện vieneu. Vui lòng chạy: pip install vieneu", file=sys.stderr)
        return False
    except Exception as e:
        print(f"❌ Lỗi khởi tạo VieNeu model: {e}", file=sys.stderr)
        return False

    target_voice = resolve_voice_code(voice, engine="vieneu")
    reader_name = get_reader_name(target_voice, engine="vieneu")
    print(f"🔊 [VieNeu] Đang tổng hợp giọng nói ({len(text)} ký tự) với Host [{reader_name}] ({target_voice})...")
    start_t = time.time()

    try:
        if ref_audio and Path(ref_audio).exists():
            print(f"🧬 [VieNeu] Đang thực hiện Voice Cloning tức thì từ audio mẫu: {ref_audio}")
            audio = vieneu_model.infer(text, ref_audio=str(ref_audio), denoise=True)
        else:
            audio = vieneu_model.infer(text, voice=target_voice)
    except Exception as e:
        print(f"❌ Lỗi khi thực hiện infer trên VieNeu: {e}", file=sys.stderr)
        return False

    elapsed = time.time() - start_t
    audio_dur = len(audio) / 48000
    rtf = elapsed / audio_dur if audio_dur > 0 else 0
    print(f"⚡ [VieNeu] Xử lý xong {audio_dur:.1f}s audio trong {elapsed:.2f}s (RTF: {rtf:.3f})")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
        tmp_wav_path = Path(tmp_wav.name)

    try:
        vieneu_model.save(audio, str(tmp_wav_path))

        cmd = ["ffmpeg", "-y", "-i", str(tmp_wav_path)]
        if abs(speed - 1.0) > 0.01:
            cmd.extend(["-filter:a", f"atempo={speed}"])
        cmd.extend(["-b:a", "192k", "-ar", "48000", str(output_path)])

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"⚠️ Cảnh báo FFmpeg: {proc.stderr}. Lưu file WAV...", file=sys.stderr)
            vieneu_model.save(audio, str(output_path.with_suffix(".wav")))
            return True

        print(f"🎉 Đã tạo thành công file Podcast MP3 (VieNeu 48kHz, {speed}x): {output_path}")
        return True
    finally:
        tmp_wav_path.unlink(missing_ok=True)


def call_vbee_tts(
    text: str,
    output_path: Path,
    app_id: str,
    token: str,
    voice_code: str = "hn_female_maiphuong_vdts_48k-fhg",
    speed: float = 1.15,
) -> bool:
    """Gọi Vbee TTS API để sinh file âm thanh MP3 (Cloud dự phòng)."""
    text = sanitize_repetitions(text)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    payload = {
        "app_id": app_id,
        "response_type": "indirect",
        "callback_url": "https://httpbin.org/post",
        "input_text": text,
        "voice_code": voice_code,
        "audio_type": "mp3",
        "bitrate": 128,
        "speed_rate": speed,
    }

    print(f"🎙️ Đang gửi kịch bản ({len(text)} ký tự) tới Vbee API với giọng [{voice_code}]...")
    try:
        res = requests.post(VBEE_TTS_URL, headers=headers, json=payload, timeout=30)
        data = res.json()
    except Exception as e:
        print(f"❌ Lỗi kết nối tới Vbee API: {e}", file=sys.stderr)
        return False

    if data.get("status") != 1 or "result" not in data or "request_id" not in data["result"]:
        print(f"❌ Vbee API trả về lỗi: {data}", file=sys.stderr)
        return False

    request_id = data["result"]["request_id"]
    print(f"⏳ Đang render audio trên Vbee (Request ID: {request_id})...")

    status_url = f"{VBEE_TTS_URL}/{request_id}"
    start_time = time.time()

    while time.time() - start_time < 360:
        time.sleep(6)
        try:
            status_res = requests.get(status_url, headers=headers, timeout=15)
            status_data = status_res.json()
        except Exception:
            continue

        res_info = status_data.get("result", {})
        status = res_info.get("status")
        progress = res_info.get("progress", 0)

        if status == "SUCCESS":
            audio_url = res_info.get("audio_link")
            print(f"✅ Render Vbee thành công! Đang tải file MP3...")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with requests.get(audio_url, stream=True) as stream_res:
                stream_res.raise_for_status()
                with open(output_path, "wb") as f:
                    for chunk in stream_res.iter_content(chunk_size=16384):
                        f.write(chunk)
            print(f"🎉 Đã lưu file Podcast MP3: {output_path}")
            return True
        elif status == "FAILED":
            print(f"❌ Render thất bại từ Vbee: {status_data}", file=sys.stderr)
            return False
        else:
            print(f"   ⏳ Đang xử lý âm thanh ({progress}%)...")

    print("❌ Hết thời gian chờ kết quả từ Vbee.", file=sys.stderr)
    return False


def call_tts(
    text: str,
    output_path: Path,
    voice: str | None = None,
    engine: str | None = None,
    speed: float = 1.15,
    ref_audio: str | Path | None = None,
    fallback: bool = True,
) -> bool:
    """Hàm thống nhất gọi TTS với cơ chế tự động fallback thông minh giữa các engine.
    
    Thứ tự ưu tiên fallback:
    - Nếu engine='zerotts': ZeroTTS -> VieNeu -> Vbee (nếu có key)
    - Nếu engine='vieneu': VieNeu -> ZeroTTS -> Vbee (nếu có key)
    - Nếu engine='vbee': Vbee -> ZeroTTS -> VieNeu
    """
    text = sanitize_repetitions(text)
    env = load_env(ENV_PATH)
    active_engine = (engine or os.environ.get("TTS_ENGINE") or env.get("TTS_ENGINE") or "zerotts").lower().strip()

    def try_zerotts() -> bool:
        target_v = resolve_voice_code(voice, engine="zerotts")
        return call_zerotts_tts(text=text, output_path=output_path, voice=target_v, speed=speed)

    def try_vieneu() -> bool:
        target_v = resolve_voice_code(voice, engine="vieneu")
        clone_ref = ref_audio or os.environ.get("VIENEU_REF_AUDIO") or env.get("VIENEU_REF_AUDIO")
        return call_vieneu_tts(text=text, output_path=output_path, voice=target_v, ref_audio=clone_ref, speed=speed)

    def try_vbee() -> bool:
        app_id = os.environ.get("VBEE_APP_ID") or env.get("VBEE_APP_ID")
        token = os.environ.get("VBEE_TOKEN") or env.get("VBEE_TOKEN")
        if not app_id or not token:
            return False
        target_v = resolve_voice_code(voice, engine="vbee")
        return call_vbee_tts(text=text, output_path=output_path, app_id=app_id, token=token, voice_code=target_v, speed=speed)

    # Lập danh sách engine cần thử theo thứ tự ưu tiên
    if active_engine == "zerotts":
        engines_order = [("zerotts", try_zerotts), ("vieneu", try_vieneu), ("vbee", try_vbee)]
    elif active_engine == "vieneu":
        engines_order = [("vieneu", try_vieneu), ("zerotts", try_zerotts), ("vbee", try_vbee)]
    elif active_engine == "vbee":
        engines_order = [("vbee", try_vbee), ("zerotts", try_zerotts), ("vieneu", try_vieneu)]
    else:
        engines_order = [("zerotts", try_zerotts), ("vieneu", try_vieneu)]

    if not fallback:
        # Chỉ gọi duy nhất engine được chỉ định
        engines_order = [engines_order[0]]

    for eng_name, eng_func in engines_order:
        try:
            success = eng_func()
            if success and output_path.exists() and output_path.stat().st_size > 1024:
                return True
        except Exception as e:
            print(f"⚠️ Engine [{eng_name}] gặp lỗi: {e}", file=sys.stderr)

        if fallback and eng_name != engines_order[-1][0]:
            print(f"🔄 Đang chuyển tiếp fallback sang engine tiếp theo...", file=sys.stderr)

    return False


# ── 8. Render đa giọng (multi-voice) ──

def _resolve_cast(voice_map: dict[str, str], engine: str) -> dict[str, str]:
    """Chuyển voice_map sang không gian mã giọng của engine đang hoạt động.

    Khi fallback đổi engine, resolve_voice_code("Anh Khôi", engine="zerotts") không
    tìm thấy nên trả giọng mặc định — CẢ HAI host sẽ sập về cùng một giọng. Bước
    sửa trùng ở đây là bắt buộc, không phải tùy chọn: nó giữ đúng giới tính của
    từng người nói khi đổi engine, thứ mà người nghe nhận ra ngay.
    """
    resolved = {sid: resolve_voice_code(code, engine=engine) for sid, code in voice_map.items()}
    if len(set(resolved.values())) == len(resolved):
        return resolved

    print(
        f"🔄 [multivoice] Phát hiện giọng bị trùng sau khi chuyển sang engine '{engine}'. "
        f"Đang phân vai lại theo giới tính gốc...",
        file=sys.stderr,
    )
    roster = [(sid, voice_gender(code) or "male") for sid, code in voice_map.items()]
    return pick_voices_for_cast(roster, engine=engine)


def _render_multivoice_numpy(
    turns: list[tuple[str, str]],
    output_path: Path,
    voice_map: dict[str, str],
    engine: str,
    speed: float,
    silence_sentence: float,
    silence_turn: float,
    bitrate: str,
    progress: Callable[[int, int, str], None] | None = None,
) -> bool:
    """Render đa giọng cho các engine on-device trả về numpy array (vieneu, zerotts)."""
    import numpy as np

    if engine == "zerotts":
        model = get_zerotts_model()
        sample_rate = model.sample_rate
        sampling_kwargs = {
            "cfg_scale": 1.0,
            "audio_temperature": 0.8,
            "audio_topk": 25,
            "audio_topp": 0.95,
            "audio_repetition_penalty": 1.2,
        }

        def synth(seg: str, voice: str):
            return model.synthesize(seg, voice=voice, **sampling_kwargs)

        from zerotts.audio import save_wav as _save_wav

        def write_wav(audio, path: Path) -> None:
            _save_wav(audio, str(path), sample_rate=sample_rate)
    else:
        model = get_vieneu_model()
        sample_rate = 48000

        def synth(seg: str, voice: str):
            return model.infer(seg, voice=voice)

        def write_wav(audio, path: Path) -> None:
            model.save(audio, str(path))

    gap_sentence = np.zeros(int(silence_sentence * sample_rate), dtype=np.float32)
    gap_turn = np.zeros(int(silence_turn * sample_rate), dtype=np.float32)

    default_voice = resolve_voice_code(None, engine=engine)
    pieces: list = []
    prev_speaker: str | None = None
    total = len(turns)
    failed = 0

    for idx, (speaker_id, text) in enumerate(turns):
        # Khoảng lặng đặt GIỮA các mảnh (không có khoảng lặng thừa ở đuôi file),
        # và chỉ dùng khoảng lặng dài khi thực sự đổi người nói.
        if prev_speaker is not None:
            pieces.append(gap_turn if speaker_id != prev_speaker else gap_sentence)

        voice = voice_map.get(speaker_id) or default_voice
        segments = _segment_for_engine(text, engine)
        if progress:
            progress(idx + 1, total, speaker_id)
        else:
            reader = get_reader_name(voice, engine)
            print(f"   [{idx + 1}/{total}] 🔊 {speaker_id} · {reader} ({len(text)} ký tự, {len(segments)} đoạn)")

        for seg_idx, seg in enumerate(segments):
            if seg_idx:
                pieces.append(gap_sentence)
            try:
                pieces.append(np.asarray(synth(seg, voice), dtype=np.float32))
            except Exception as e:
                failed += 1
                print(f"   ⚠️ Bỏ qua đoạn lỗi (lượt {idx + 1}, đoạn {seg_idx + 1}): {e}", file=sys.stderr)

        prev_speaker = speaker_id

    if not pieces:
        print("❌ [multivoice] Không tổng hợp được đoạn âm thanh nào.", file=sys.stderr)
        return False
    if failed:
        print(f"⚠️ [multivoice] Có {failed} đoạn bị bỏ qua do lỗi tổng hợp.", file=sys.stderr)

    master = np.concatenate(pieces)

    # Chống clipping khi ghép nhiều giọng có mức âm lượng khác nhau.
    peak = float(np.max(np.abs(master))) if master.size else 0.0
    if peak > 1.0:
        print(f"🔧 [multivoice] Chuẩn hóa đỉnh {peak:.2f} → 0.99 để tránh méo tiếng.")
        master = (master / peak) * 0.99

    duration_sec = len(master) / sample_rate
    print(f"⏱️ [multivoice] Tổng thời lượng thô: {duration_sec / 60:.2f} phút ({duration_sec:.1f}s)")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
        tmp_wav_path = Path(tmp_wav.name)

    try:
        write_wav(master, tmp_wav_path)
        cmd = ["ffmpeg", "-y", "-i", str(tmp_wav_path)]
        tempo = _atempo_filter(speed)
        if tempo:
            cmd.extend(["-filter:a", tempo])
        cmd.extend(["-c:a", "libmp3lame", "-b:a", bitrate, "-ar", "48000", str(output_path)])

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"⚠️ Cảnh báo FFmpeg: {proc.stderr}. Lưu trực tiếp file WAV...", file=sys.stderr)
            write_wav(master, output_path.with_suffix(".wav"))
            return True

        size_mb = output_path.stat().st_size / (1024 * 1024)
        print(f"🎉 Đã tạo file Podcast đa giọng ({engine} 48kHz, {speed}x, {bitrate}): {output_path} ({size_mb:.2f} MB)")
        return True
    finally:
        tmp_wav_path.unlink(missing_ok=True)


def _render_multivoice_vbee(
    turns: list[tuple[str, str]],
    output_path: Path,
    voice_map: dict[str, str],
    speed: float,
    silence_sentence: float,
    silence_turn: float,
    bitrate: str,
    app_id: str,
    token: str,
    progress: Callable[[int, int, str], None] | None = None,
) -> bool:
    """Render đa giọng qua Vbee Cloud: mỗi lượt một file MP3 rồi ghép bằng ffmpeg."""
    default_voice = resolve_voice_code(None, engine="vbee")
    total = len(turns)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        rendered: list[tuple[Path, float]] = []   # (file, khoảng lặng nối sau file này)

        for idx, (speaker_id, text) in enumerate(turns):
            voice = voice_map.get(speaker_id) or default_voice
            part_path = tmp / f"turn_{idx:04d}.mp3"
            if progress:
                progress(idx + 1, total, speaker_id)
            else:
                print(f"   [{idx + 1}/{total}] ☁️ {speaker_id} · {get_reader_name(voice, 'vbee')} ({len(text)} ký tự)")

            # speed=1.0 là cố ý: Vbee áp speed_rate phía server còn nhánh numpy áp
            # atempo trong ffmpeg. Áp tốc độ ĐÚNG MỘT LẦN lúc ghép để hai nhánh
            # dùng chung một định nghĩa `speed` (nếu không sẽ nhân đôi và phá bộ
            # ước lượng thời lượng của split_into_episodes).
            try:
                ok = call_vbee_tts(
                    text=text, output_path=part_path, app_id=app_id, token=token,
                    voice_code=voice, speed=1.0,
                )
            except Exception as e:
                print(f"   ⚠️ Lượt {idx + 1} lỗi: {e}", file=sys.stderr)
                ok = False

            if ok and part_path.exists() and part_path.stat().st_size > 1024:
                next_speaker = turns[idx + 1][0] if idx + 1 < total else None
                if next_speaker is None:
                    gap = 0.0          # không để im lặng thừa ở đuôi file
                elif next_speaker != speaker_id:
                    gap = silence_turn
                else:
                    gap = silence_sentence
                rendered.append((part_path, gap))

        if not rendered:
            print("❌ [multivoice/vbee] Không render được lượt nào.", file=sys.stderr)
            return False

        return _concat_mp3_with_gaps(rendered, output_path, speed, bitrate, tmp)


def _concat_mp3_with_gaps(
    rendered: list[tuple[Path, float]],
    output_path: Path,
    speed: float,
    bitrate: str,
    workdir: Path,
    group_size: int = 40,
) -> bool:
    """Ghép các MP3 kèm khoảng lặng bằng concat FILTER (không dùng concat demuxer).

    Concat demuxer sai ở đây vì: `-c copy` để lại khoảng trống encoder-delay và
    tiếng click ở mỗi mối nối; nó đòi tham số stream giống hệt nhau, trong khi
    Vbee trả 128kbps không khớp file sinh tại chỗ. Filter `apad=pad_dur=` tự tổng
    hợp khoảng lặng inline nên không cần file silence rời và mỗi mối nối có thể
    có độ dài khác nhau.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def run_group(items: list[tuple[Path, float]], dest: Path, final: bool) -> bool:
        cmd = ["ffmpeg", "-y"]
        for path, _ in items:
            cmd.extend(["-i", str(path)])

        filters = []
        labels = []
        for i, (_, gap) in enumerate(items):
            label = f"a{i}"
            if gap > 0:
                filters.append(f"[{i}:a]apad=pad_dur={gap:g}[{label}]")
            else:
                filters.append(f"[{i}:a]anull[{label}]")
            labels.append(f"[{label}]")

        chain = "".join(labels) + f"concat=n={len(items)}:v=0:a=1[c]"
        tempo = _atempo_filter(speed) if final else None
        tail = f"[c]aresample=48000{',' + tempo if tempo else ''}[out]"
        filters.extend([chain, tail])

        cmd.extend(["-filter_complex", ";".join(filters), "-map", "[out]"])
        if final:
            cmd.extend(["-c:a", "libmp3lame", "-b:a", bitrate, "-ar", "48000"])
        else:
            cmd.extend(["-c:a", "pcm_s16le", "-ar", "48000"])
        cmd.append(str(dest))

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"❌ Lỗi ffmpeg khi ghép: {proc.stderr[-800:]}", file=sys.stderr)
            return False
        return True

    # Gom theo nhóm để filtergraph và dòng lệnh không vượt giới hạn OS/ffmpeg.
    if len(rendered) <= group_size:
        return run_group(rendered, output_path, final=True)

    groups: list[tuple[Path, float]] = []
    for gi in range(0, len(rendered), group_size):
        batch = rendered[gi:gi + group_size]
        # Khoảng lặng nối giữa hai nhóm thuộc về phần tử cuối của nhóm trước
        trailing_gap = batch[-1][1]
        batch = batch[:-1] + [(batch[-1][0], 0.0)]
        inter = workdir / f"group_{gi // group_size:03d}.wav"
        if not run_group(batch, inter, final=False):
            return False
        groups.append((inter, trailing_gap))

    groups = groups[:-1] + [(groups[-1][0], 0.0)]
    return run_group(groups, output_path, final=True)


def render_multivoice(
    turns: list[tuple[str, str]],
    output_path: Path,
    voice_map: dict[str, str],
    engine: str | None = None,
    speed: float = 1.15,
    silence_sentence: float = 0.20,
    silence_turn: float = 0.65,
    bitrate: str = "192k",
    fallback: bool = True,
    progress: Callable[[int, int, str], None] | None = None,
) -> bool:
    """Render một kịch bản đa giọng [(speaker_id, text)] thành một file MP3 duy nhất.

    Args:
        turns: Danh sách lượt nói (mã người nói, nội dung).
        voice_map: Ánh xạ mã người nói → mã giọng (xem pick_voices_for_cast).
        silence_sentence: Khoảng lặng giữa các câu trong cùng một lượt.
        silence_turn: Khoảng lặng khi đổi người nói.
        progress: Callback (chỉ_số, tổng, mã_người_nói) để báo tiến độ.
    """
    clean_turns = [
        (str(sid), sanitize_repetitions(text).strip())
        for sid, text in (turns or [])
        if text and str(text).strip()
    ]
    clean_turns = [(sid, text) for sid, text in clean_turns if text]
    if not clean_turns:
        print("❌ [multivoice] Kịch bản rỗng.", file=sys.stderr)
        return False

    env = load_env(ENV_PATH)
    active_engine = (engine or os.environ.get("TTS_ENGINE") or env.get("TTS_ENGINE") or "zerotts").lower().strip()

    def try_engine(eng: str) -> bool:
        cast = _resolve_cast(voice_map, eng)
        print(f"🎭 [multivoice/{eng}] Dàn giọng: {describe_cast(cast, eng)}")
        if eng == "vbee":
            app_id = os.environ.get("VBEE_APP_ID") or env.get("VBEE_APP_ID")
            token = os.environ.get("VBEE_TOKEN") or env.get("VBEE_TOKEN")
            if not app_id or not token:
                return False
            return _render_multivoice_vbee(
                clean_turns, output_path, cast, speed, silence_sentence,
                silence_turn, bitrate, app_id, token, progress,
            )
        return _render_multivoice_numpy(
            clean_turns, output_path, cast, eng, speed, silence_sentence,
            silence_turn, bitrate, progress,
        )

    if active_engine == "vieneu":
        order = ["vieneu", "zerotts", "vbee"]
    elif active_engine == "vbee":
        order = ["vbee", "zerotts", "vieneu"]
    else:
        order = ["zerotts", "vieneu", "vbee"]

    if not fallback:
        order = order[:1]

    for eng in order:
        try:
            if try_engine(eng) and output_path.exists() and output_path.stat().st_size > 1024:
                return True
        except Exception as e:
            print(f"⚠️ [multivoice] Engine [{eng}] gặp lỗi: {e}", file=sys.stderr)
        if fallback and eng != order[-1]:
            print("🔄 [multivoice] Chuyển tiếp fallback sang engine tiếp theo...", file=sys.stderr)

    return False


# ── 9. Ước lượng thời lượng & cắt tập ──

def estimate_duration_sec(text: str, engine: str | None = None, speed: float = 1.15) -> float:
    """Ước lượng thời lượng đọc (giây) từ số ký tự tiếng Việt."""
    eng = (engine or get_default_engine()).lower().strip()
    cps = VI_CHARS_PER_SEC_1X.get(eng, DEFAULT_VI_CHARS_PER_SEC_1X)
    return len(text or "") / (cps * max(speed, 0.01))


def estimate_turns_seconds(
    turns: list[tuple[str, str]],
    engine: str | None = None,
    speed: float = 1.15,
    silence_sentence: float = 0.20,
    silence_turn: float = 0.65,
) -> list[float]:
    """Thời lượng ước lượng của TỪNG lượt nói, đã gồm khoảng lặng dẫn vào lượt đó.

    Khoảng lặng được CHIA cho speed vì atempo áp lên toàn bộ waveform đã ghép nên
    nén cả phần im lặng. Làm ngược lại sai ~4% trên kịch bản nhiều đối thoại.
    """
    eng = (engine or get_default_engine()).lower().strip()
    cps = VI_CHARS_PER_SEC_1X.get(eng, DEFAULT_VI_CHARS_PER_SEC_1X)
    spd = max(speed, 0.01)

    out: list[float] = []
    prev_speaker: str | None = None
    for speaker_id, text in turns:
        n_sentences = max(1, len(split_text_into_sentences(text)))
        cost = len(text) / (cps * spd)
        cost += (n_sentences - 1) * silence_sentence / spd
        if prev_speaker is not None:
            cost += (silence_turn if speaker_id != prev_speaker else silence_sentence) / spd
        out.append(cost)
        prev_speaker = speaker_id
    return out


def split_into_episodes(
    turns: list[tuple[str, str]],
    target_minutes: float = 40.0,
    max_mb: float = 45.0,
    engine: str | None = None,
    speed: float = 1.15,
    bitrate_kbps: int = 192,
    silence_sentence: float = 0.20,
    silence_turn: float = 0.65,
    prefer_speaker_break: bool = True,
) -> list[list[tuple[str, str]]]:
    """Cắt danh sách lượt nói dài thành N tập, luôn cắt tại ranh giới lượt nói.

    Trả về list chứa đúng một list khi không cần cắt, để người gọi không phải
    xử lý trường hợp đặc biệt.
    """
    if not turns:
        return []

    bytes_per_sec = bitrate_kbps * 1000 / 8
    size_cap_sec = max(60.0, (max_mb * 1024 * 1024 - MP3_ID3_OVERHEAD_BYTES) / bytes_per_sec)
    target_cap_sec = target_minutes * 60.0
    cap_sec = min(target_cap_sec, size_cap_sec)

    binding = "dung lượng" if size_cap_sec < target_cap_sec else "thời lượng"
    print(
        f"✂️ [split] Trần mỗi tập: {cap_sec / 60:.1f} phút "
        f"(khống chế bởi {binding}; thời lượng={target_cap_sec / 60:.0f}p, "
        f"dung lượng={size_cap_sec / 60:.1f}p @ {bitrate_kbps}kbps / {max_mb:g}MB)"
    )
    if binding == "dung lượng":
        print(
            f"   💡 Muốn tập dài {target_minutes:g} phút thì hạ bitrate xuống "
            f"{int(max_mb * 1024 * 1024 * 8 / 1000 / target_cap_sec / 8) * 8}kbps hoặc thấp hơn (gợi ý: 128k)."
        )

    # Lượt đơn dài hơn cả một tập: cắt theo câu thành các sub-turn CÙNG người nói.
    expanded: list[tuple[str, str]] = []
    for speaker_id, text in turns:
        if estimate_duration_sec(text, engine, speed) <= cap_sec:
            expanded.append((speaker_id, text))
            continue
        sentences = split_text_into_sentences(text)
        print(
            f"⚠️ [split] Một lượt nói của '{speaker_id}' dài hơn trần một tập "
            f"({len(text)} ký tự); buộc phải cắt giữa lượt thành {len(sentences)} câu.",
            file=sys.stderr,
        )
        buf: list[str] = []
        for sent in sentences:
            candidate = " ".join(buf + [sent])
            if buf and estimate_duration_sec(candidate, engine, speed) > cap_sec * 0.9:
                expanded.append((speaker_id, " ".join(buf)))
                buf = [sent]
            else:
                buf.append(sent)
        if buf:
            expanded.append((speaker_id, " ".join(buf)))

    costs = estimate_turns_seconds(expanded, engine, speed, silence_sentence, silence_turn)

    episodes: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    current_costs: list[float] = []
    running = 0.0
    lookback_sec = 0.12 * cap_sec

    for turn, cost in zip(expanded, costs):
        if current and running + cost > cap_sec:
            cut_at = len(current)
            if prefer_speaker_break:
                # Cắt giữa một cặp hỏi–đáp nghe như file hỏng: lùi lại tìm chỗ
                # đổi người nói gần nhất trong cửa sổ 12% độ dài tập.
                back = 0.0
                for k in range(len(current) - 1, 0, -1):
                    back += current_costs[k]
                    if back > lookback_sec:
                        break
                    if current[k][0] != current[k - 1][0]:
                        cut_at = k
                        break

            episodes.append(current[:cut_at])
            carried = current[cut_at:]
            carried_costs = current_costs[cut_at:]
            current, current_costs = carried, carried_costs
            running = sum(carried_costs)

        current.append(turn)
        current_costs.append(cost)
        running += cost

    if current:
        episodes.append(current)

    # Tập cuối quá ngắn thì gộp vào tập trước (nếu vẫn dưới giới hạn CỨNG).
    if len(episodes) > 1:
        last_cost = sum(estimate_turns_seconds(episodes[-1], engine, speed, silence_sentence, silence_turn))
        prev_cost = sum(estimate_turns_seconds(episodes[-2], engine, speed, silence_sentence, silence_turn))
        if last_cost < 0.25 * cap_sec and (last_cost + prev_cost) < size_cap_sec:
            print(f"🔗 [split] Gộp tập cuối ({last_cost / 60:.1f}p) vào tập trước để tránh tập quá ngắn.")
            episodes[-2].extend(episodes[-1])
            episodes.pop()

    for i, ep in enumerate(episodes, 1):
        dur = sum(estimate_turns_seconds(ep, engine, speed, silence_sentence, silence_turn))
        print(f"   📼 Tập {i}/{len(episodes)}: {len(ep)} lượt, ~{dur / 60:.1f} phút, ~{dur * bytes_per_sec / 1024 / 1024:.1f} MB")

    return episodes


def episode_output_paths(base_path: Path, n_parts: int) -> list[Path]:
    """1 tập -> [base]; N tập -> base_phan_01.mp3, base_phan_02.mp3, ..."""
    base_path = Path(base_path)
    if n_parts <= 1:
        return [base_path]
    return [
        base_path.with_name(f"{base_path.stem}_phan_{i:02d}{base_path.suffix}")
        for i in range(1, n_parts + 1)
    ]


def ensure_under_telegram_limit(
    path: Path,
    max_bytes: int = TELEGRAM_MAX_UPLOAD_BYTES,
    ladder: tuple[str, ...] = ("128k", "96k", "64k"),
) -> Path:
    """Nếu file vượt hạn mức Telegram thì RE-ENCODE xuống bitrate thấp hơn.

    Không bao giờ render lại TTS: sai số ước lượng ±6% có thể đẩy một tập 45MB
    thành 48MB, và render lại thì vừa chậm vừa vô nghĩa.
    """
    path = Path(path)
    if not path.exists() or path.stat().st_size <= max_bytes:
        return path

    for bitrate in ladder:
        size_mb = path.stat().st_size / (1024 * 1024)
        print(
            f"📉 [telegram] {path.name} nặng {size_mb:.1f}MB > hạn mức "
            f"{max_bytes / 1024 / 1024:.0f}MB. Đang nén lại ở {bitrate}..."
        )
        tmp_out = path.with_name(f"{path.stem}.reenc{path.suffix}")
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", str(path), "-c:a", "libmp3lame",
             "-b:a", bitrate, "-ar", "48000", str(tmp_out)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0 or not tmp_out.exists():
            print(f"⚠️ Nén lại thất bại ở {bitrate}: {proc.stderr[-400:]}", file=sys.stderr)
            tmp_out.unlink(missing_ok=True)
            continue

        tmp_out.replace(path)
        if path.stat().st_size <= max_bytes:
            print(f"✅ [telegram] Đã nén còn {path.stat().st_size / 1024 / 1024:.1f}MB ({bitrate}).")
            return path

    print(
        f"⚠️ [telegram] {path.name} vẫn vượt hạn mức sau khi nén hết mức. "
        f"Cần cắt tập ngắn hơn.",
        file=sys.stderr,
    )
    return path


def print_available_voices():
    """Hiển thị danh sách giọng đọc theo từng engine."""
    curr_engine = get_default_engine()
    curr_voice = resolve_voice_code(engine=curr_engine)

    print("\n🎧 DANH SÁCH GIỌNG ĐỌC HỖ TRỢ (AUDIOBOOK & PODCAST):")
    print("=" * 85)

    print(f"⚡ 1. ZEROTTS (CPU Real-Time 48kHz, WER 1.03%, Đọc song ngữ Anh-Việt chuẩn):")
    if curr_engine == "zerotts":
        print(f"   [ĐANG LÀ ENGINE MẶC ĐỊNH]")
    print("-" * 85)
    for code, desc in ZEROTTS_POPULAR_VOICES.items():
        is_cur = " [ĐANG CHỌN]" if curr_engine == "zerotts" and code == curr_voice else ""
        print(f"• {code:15} -> {desc}{is_cur}")

    print(f"\n🦜 2. VIENEU-TTS v3 Turbo (On-device 48kHz, Hỗ trợ Voice Cloning tức thì):")
    if curr_engine == "vieneu":
        print(f"   [ĐANG LÀ ENGINE MẶC ĐỊNH]")
    print("-" * 85)
    for code, desc in VIENEU_POPULAR_VOICES.items():
        is_cur = " [ĐANG CHỌN]" if curr_engine == "vieneu" and code == curr_voice else ""
        print(f"• {code:25} -> {desc}{is_cur}")

    print(f"\n☁️ 3. VBEE AIVoice (Cloud API):")
    if curr_engine == "vbee":
        print(f"   [ĐANG LÀ ENGINE MẶC ĐỊNH]")
    print("-" * 85)
    for code, desc in VBEE_POPULAR_VOICES.items():
        is_cur = " [ĐANG CHỌN]" if curr_engine == "vbee" and code == curr_voice else ""
        print(f"• {code:35} -> {desc}{is_cur}")

    print("=" * 85)
    print(f"💡 Cấu hình mặc định trong file .env:")
    print(f"   TTS_ENGINE=\"zerotts\"   # hoặc \"vieneu\", \"vbee\"")
    print(f"   ZEROTTS_VOICE=\"maichi\"\n")
