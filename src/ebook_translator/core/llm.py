"""Wrapper goi LLM API dung chung: retry, dem token.

Hai backend (tu dong chon theo env vars):
- Google AI Studio (GEMINI_API_KEY / GOOGLE_API_KEY): google-genai SDK, chi
  can API key, khong can GCP project.
- Gemini qua Google Cloud Vertex AI (--project / GOOGLE_CLOUD_PROJECT):
  google-genai SDK, xac thuc bang ADC cua gcloud.

Ca hai backend dung chung interface complete(system, messages, max_tokens).
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
import sys
import time

def _load_env_file() -> None:
    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parents[3] / ".env"):
        if candidate.is_file():
            try:
                for line in candidate.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k and k not in os.environ:
                        os.environ[k] = v
            except Exception:
                pass
            break

_load_env_file()

# --- Model mac dinh theo tung backend ---
VERTEX_DEFAULT_MODEL = "gemini-3.6-flash"
GOOGLE_AI_DEFAULT_MODEL = "gemini-3.7-flash"
GOOGLE_AI_FALLBACK_MODELS = [
    "gemini-flash-latest",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
]

# Aliases giu tuong thich nguoc (import tu ngoai)
DEFAULT_MODEL = VERTEX_DEFAULT_MODEL
DEFAULT_REGION = "global"  # gemini-3.x-flash chi co o region "global"
MAX_RETRIES = 5

SETUP_HINT = (
    "Không tìm thấy API key hoặc GCP project. Chọn một trong các cách:\n"
    "\n"
    "  Cách 1 — Gemini API key (đơn giản nhất):\n"
    '    $env:GEMINI_API_KEY = "<api-key-từ aistudio.google.com>"\n'
    "\n"
    "  Cách 2 — Google Cloud Vertex AI:\n"
    '    $env:GOOGLE_CLOUD_PROJECT = "<gcp-project-id>"\n'
    "    gcloud auth application-default login\n"
)

GCP_PROJECT_HINT = SETUP_HINT


class LLMClient:
    def __init__(
        self,
        model: str | None = None,
        project_id: str | None = None,
        region: str | None = None,
    ):
        self.provider = "gemini"

        if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
            # --- Backend 1: Google AI Studio (API key) ---
            self._init_google_ai(model)
        else:
            # --- Backend 2: Vertex AI (GCP) — backup ---
            self._init_vertex(model, project_id, region)

        self.input_tokens = 0
        self.output_tokens = 0

    # ---- init helpers ----


    def _init_google_ai(self, model: str | None) -> None:
        from google import genai

        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        try:
            self.client = genai.Client(api_key=key)
        except Exception as exc:
            raise SystemExit(
                f"Không khởi tạo được Google AI client: {exc}\n"
                "Kiểm tra API key có đúng không (lấy tại aistudio.google.com)"
            )
        self.model = model or GOOGLE_AI_DEFAULT_MODEL
        self._fallback_models = [m for m in GOOGLE_AI_FALLBACK_MODELS if m != self.model]
        print(f"  Backend: Google AI Studio — model {self.model}", file=sys.stderr)

    def _init_vertex(self, model: str | None, project_id: str | None, region: str | None) -> None:
        self.client = _make_vertex_client(project_id, region)
        self.model = model or VERTEX_DEFAULT_MODEL
        self._fallback_models = []
        print(f"  Backend: Vertex AI — model {self.model}", file=sys.stderr)

    # ---- API calls ----

    def complete(
        self,
        system: str,
        messages: list[dict],
        max_tokens: int = 8000,
        json_mode: bool = False,
    ) -> str:
        """Goi API voi retry/backoff; tra ve text cua response.

        json_mode=True: yeu cau model tra JSON hop le (Gemini dung
        response_mime_type). Cac model Gemini co "thinking" tinh ca token suy
        nghi vao max_output_tokens, nen goi JSON dai can max_tokens rong rai de
        khong bi cat giua chung -> "JSON did not parse".
        """
        return self._complete_gemini(system, messages, max_tokens, json_mode)

    def _complete_gemini(
        self, system: str, messages: list[dict], max_tokens: int, json_mode: bool = False
    ) -> str:
        from google.genai import errors, types

        contents = _to_gemini_contents(messages)
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            response_mime_type="application/json" if json_mode else None,
        )
        delay = 5.0
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.client.models.generate_content(
                    model=self.model, contents=contents, config=config
                )
                usage = resp.usage_metadata
                if usage is not None:
                    self.input_tokens += usage.prompt_token_count or 0
                    self.output_tokens += (usage.candidates_token_count or 0) + (
                        usage.thoughts_token_count or 0
                    )
                return resp.text or ""
            except errors.APIError as exc:
                code = getattr(exc, "code", None) or 0
                msg = str(exc)
                is_quota = code == 429 and ("PerDay" in msg or "quota" in msg.lower() or "RESOURCE_EXHAUSTED" in msg)
                is_unavailable = code in (500, 502, 503, 504) or "UNAVAILABLE" in msg or "high demand" in msg.lower()
                if (is_quota or is_unavailable) and hasattr(self, "_fallback_models") and self._fallback_models:
                    old_model = self.model
                    self.model = self._fallback_models.pop(0)
                    reason = "chạm hạn mức" if is_quota else "quá tải/tạm thời không khả dụng"
                    print(
                        f"  [fallback] Model {old_model} {reason} ({code}), chuyển sang {self.model}...",
                        file=sys.stderr,
                    )
                    continue
                if code not in (429,) and code < 500:
                    raise  # 4xx khac (sai project, bad request) thi khong retry
                if attempt == MAX_RETRIES:
                    raise
                _print_retry(attempt, exc, delay)
                time.sleep(delay)
                delay = min(delay * 2, 60)
        raise RuntimeError("unreachable")



    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def _print_retry(attempt: int, exc: Exception, delay: float) -> None:
    print(
        f"  [retry {attempt}/{MAX_RETRIES}] {type(exc).__name__}: {exc} — "
        f"chờ {delay:.0f}s...",
        file=sys.stderr,
    )


def _to_gemini_contents(messages: list[dict]):
    """Chuyen message (text + image base64) sang dinh dang google-genai."""
    from google.genai import types

    contents = []
    for msg in messages:
        parts = []
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(types.Part.from_text(text=content))
        else:
            for block in content:
                if block.get("type") == "text":
                    parts.append(types.Part.from_text(text=block["text"]))
                elif block.get("type") == "image":
                    src = block["source"]
                    parts.append(
                        types.Part.from_bytes(
                            data=base64.standard_b64decode(src["data"]),
                            mime_type=src["media_type"],
                        )
                    )
        role = "user" if msg.get("role") == "user" else "model"
        contents.append(types.Content(role=role, parts=parts))
    return contents


def _prefer_gcloud_login() -> None:
    """Uu tien tai khoan gcloud (ADC) hon service account tu bien moi truong.

    GOOGLE_APPLICATION_CREDENTIALS dung dau thu tu uu tien cua Google, nen mot
    service account do cong cu khac dat se lam vo hieu `gcloud auth
    application-default login`. Neu may da co ADC cua gcloud thi dung no.
    """
    sa_env = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not sa_env:
        return
    try:
        from google.auth import _cloud_sdk

        adc_path = _cloud_sdk.get_application_default_credentials_path()
    except Exception:
        return  # khong xac dinh duoc -> giu hanh vi mac dinh cua Google
    if not os.path.exists(adc_path):
        return  # khong co gcloud login -> service account la lua chon duy nhat
    os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
    print(
        f"  (bỏ qua service account {sa_env} — dùng tài khoản gcloud đã đăng nhập)",
        file=sys.stderr,
    )


def _make_vertex_client(project_id: str | None, region: str | None):
    from google import genai

    _prefer_gcloud_login()
    project = (
        project_id
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
    )
    if not project:
        raise SystemExit(SETUP_HINT)
    reg = (
        region
        or os.environ.get("GOOGLE_CLOUD_LOCATION")
        or os.environ.get("CLOUD_ML_REGION")
        or DEFAULT_REGION
    )
    try:
        return genai.Client(vertexai=True, project=project, location=reg)
    except Exception as exc:  # thieu ADC -> google.auth.DefaultCredentialsError
        raise SystemExit(
            f"Không khởi tạo được Gemini client: {exc}\n"
            "Kiểm tra đã đăng nhập Google Cloud chưa:\n"
            "  gcloud auth application-default login"
        )
