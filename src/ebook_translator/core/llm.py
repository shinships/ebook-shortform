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
import re
import sys
import time

import httpx

NETWORK_ERRORS: tuple[type[BaseException], ...] = (
    httpx.HTTPError,
    ConnectionResetError,
    ConnectionRefusedError,
    ConnectionAbortedError,
    ConnectionError,
    TimeoutError,
    OSError,
)

try:
    import httpcore
    NETWORK_ERRORS = (
        httpx.HTTPError,
        httpcore.NetworkError,
        httpcore.ProtocolError,
        ConnectionResetError,
        ConnectionRefusedError,
        ConnectionAbortedError,
        ConnectionError,
        TimeoutError,
        OSError,
    )
except ImportError:
    pass


def _load_env_file(override: bool = True) -> None:
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
                    if k:
                        if override or k not in os.environ:
                            os.environ[k] = v
            except Exception:
                pass
            break

_load_env_file()

# --- Model mac dinh theo tung backend ---
VERTEX_DEFAULT_MODEL = "gemini-3.6-flash"
GOOGLE_AI_DEFAULT_MODEL = "gemini-3.7-flash"
GOOGLE_AI_FALLBACK_MODELS: list[str] = ["gemini-3.6-flash", "gemini-3.8-flash", "gemini-3.5-flash"]


# Aliases giu tuong thich nguoc (import tu ngoai)
DEFAULT_MODEL = VERTEX_DEFAULT_MODEL
DEFAULT_REGION = "global"  # gemini-3.x-flash chi co o region "global"
MAX_RETRIES = 10

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
        model_str = model or os.environ.get("LLM_MODEL") or ""
        
        if model_str.startswith("deepseek") or model_str.startswith("gpt") or model_str.startswith("o1") or model_str.startswith("o3"):
            self.provider = "openai"
            self._init_openai(model_str)
        else:
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

        _load_env_file(override=True)
        keys_str = os.environ.get("GEMINI_API_KEYS") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not keys_str:
            raise SystemExit("Không tìm thấy GEMINI_API_KEY")
            
        self._api_keys = [k.strip() for k in keys_str.split(",") if k.strip()]
        self._current_key_idx = 0
        
        try:
            self.client = genai.Client(api_key=self._api_keys[self._current_key_idx])
        except Exception as exc:
            raise SystemExit(
                f"Không khởi tạo được Google AI client: {exc}\n"
                "Kiểm tra API key có đúng không (lấy tại aistudio.google.com)"
            )
        self.model = model or GOOGLE_AI_DEFAULT_MODEL
        self._fallback_models = [m for m in GOOGLE_AI_FALLBACK_MODELS if m != self.model]
        print(f"  Backend: Google AI Studio — model {self.model} (sẵn sàng {len(self._api_keys)} API keys)", file=sys.stderr)

    def _init_vertex(self, model: str | None, project_id: str | None, region: str | None) -> None:
        self.client = _make_vertex_client(project_id, region)
        self.model = model or VERTEX_DEFAULT_MODEL
        self._fallback_models = []
        print(f"  Backend: Vertex AI — model {self.model}", file=sys.stderr)

    def _init_openai(self, model: str) -> None:
        import openai
        _load_env_file(override=True)
        
        self.model = model
        
        if model.startswith("deepseek"):
            api_key = os.environ.get("DEEPSEEK_API_KEY")
            if not api_key:
                raise SystemExit("Không tìm thấy DEEPSEEK_API_KEY")
            self.client = openai.OpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1")
            print(f"  Backend: DeepSeek API — model {self.model}", file=sys.stderr)
        else:
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise SystemExit("Không tìm thấy OPENAI_API_KEY")
            self.client = openai.OpenAI(api_key=api_key)
            print(f"  Backend: OpenAI API — model {self.model}", file=sys.stderr)
            
        self._fallback_models = []

    # ---- API calls ----

    def complete(
        self,
        system: str,
        messages: list[dict],
        max_tokens: int = 8000,
        json_mode: bool = False,
    ) -> str:
        """Goi API voi retry/backoff; tra ve text cua response.

        json_mode=True: yeu cau model tra JSON hop le.
        """
        if self.provider == "openai":
            return self._complete_openai(system, messages, max_tokens, json_mode)
        return self._complete_gemini(system, messages, max_tokens, json_mode)

    def _complete_openai(
        self, system: str, messages: list[dict], max_tokens: int, json_mode: bool = False
    ) -> str:
        import openai
        
        # Chuyển đổi format messages
        oai_messages = [{"role": "system", "content": system}]
        for msg in messages:
            role = "user" if msg.get("role") == "user" else "assistant"
            content = msg.get("content", "")
            if isinstance(content, list):
                # Gửi text, nếu có ảnh thì tùy model có support Vision hay không
                # DeepSeek hiện không support Vision, OpenAI thì có
                oai_content = []
                for block in content:
                    if block.get("type") == "text":
                        oai_content.append({"type": "text", "text": block["text"]})
                    elif block.get("type") == "image":
                        src = block["source"]
                        oai_content.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"}
                        })
                oai_messages.append({"role": role, "content": oai_content})
            else:
                oai_messages.append({"role": role, "content": content})
                
        kwargs = {
            "model": self.model,
            "messages": oai_messages,
            "max_tokens": max_tokens,
        }
        
        if json_mode:
            # DeepSeek hỗ trợ response_format={"type": "json_object"}
            kwargs["response_format"] = {"type": "json_object"}
            
        delay = 5.0
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.client.chat.completions.create(**kwargs)
                if resp.usage:
                    self.input_tokens += resp.usage.prompt_tokens
                    self.output_tokens += resp.usage.completion_tokens
                return resp.choices[0].message.content or ""
            except (openai.RateLimitError, openai.APIError) as exc:
                last_exc = exc
                if attempt == MAX_RETRIES:
                    raise
                _print_retry(attempt, exc, delay)
                time.sleep(delay)
                delay = min(delay * 2, 60)
            except NETWORK_ERRORS as exc:
                last_exc = exc
                if attempt == MAX_RETRIES:
                    raise
                _print_retry(attempt, exc, delay)
                time.sleep(delay)
                delay = min(delay * 2, 60)
            except Exception as exc:
                last_exc = exc
                msg_lower = str(exc).lower()
                if any(w in msg_lower for w in ("connection reset", "broken pipe", "peer closed", "network", "timeout", "errno 54")):
                    if attempt == MAX_RETRIES:
                        raise
                    _print_retry(attempt, exc, delay)
                    time.sleep(delay)
                    delay = min(delay * 2, 60)
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("Đã vượt quá số lần thử lại tối đa")

    def _complete_gemini(
        self, system: str, messages: list[dict], max_tokens: int, json_mode: bool = False
    ) -> str:
        from google.genai import errors, types
        import google.genai as genai

        contents = _to_gemini_contents(messages)
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            response_mime_type="application/json" if json_mode else None,
        )
        delay = 5.0
        keys_tried = 0
        model_consecutive_failures = 0
        last_exc: Exception | None = None
        attempt = 0
        max_attempts = MAX_RETRIES

        while attempt < max_attempts:
            attempt += 1
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
                model_consecutive_failures = 0
                return resp.text or ""
            except errors.APIError as exc:
                last_exc = exc
                model_consecutive_failures += 1
                code = getattr(exc, "code", None) or 0
                msg = str(exc)
                is_daily_quota = code == 429 and any(x in msg.lower() for x in ("perday", "freetierperday", "daily", "requestsperday", "perdayperprojectpermodel"))
                is_rate_limit = code == 429 and not is_daily_quota
                is_unavailable = code in (500, 502, 503, 504) or "UNAVAILABLE" in msg or "high demand" in msg.lower()

                # 1. Neu la 429 hoac 503/server busy va con API key chua thu: xoay sang key tiep theo
                if (is_daily_quota or is_rate_limit or is_unavailable) and hasattr(self, "_api_keys") and len(self._api_keys) > 1 and keys_tried < len(self._api_keys) - 1:
                    keys_tried += 1
                    self._current_key_idx = (self._current_key_idx + 1) % len(self._api_keys)
                    print(f"  [fallback] API key chạm giới hạn / tải cao, chuyển sang key dự phòng (index: {self._current_key_idx})...", file=sys.stderr)
                    self.client = genai.Client(api_key=self._api_keys[self._current_key_idx])
                    continue

                # 2. Fallback sang model tiep theo khi tat ca key da duoc thu tren model hien tai
                if (is_daily_quota or keys_tried >= len(self._api_keys) - 1) and hasattr(self, "_fallback_models") and self._fallback_models:
                    old_model = self.model
                    self.model = self._fallback_models.pop(0)
                    reason = "hết quota ngày" if is_daily_quota else "tất cả API keys chạm giới hạn / tải cao"
                    print(
                        f"  [fallback] Model {old_model} {reason} ({code}), chuyển sang {self.model}...",
                        file=sys.stderr,
                    )
                    keys_tried = 0
                    model_consecutive_failures = 0
                    attempt = 0
                    continue

                # 3. Neu khong con model fallback va la rate limit phut (RPM/TPM) hoac 503 tam thoi: reset keys_tried va nghi cho qua dot gioi han
                if is_rate_limit or is_unavailable:
                    keys_tried = 0
                    sleep_time = max(delay, 25)
                    m_wait = re.search(r"retry in (\d+(?:\.\d+)?)s", msg, re.IGNORECASE)
                    if m_wait:
                        sleep_time = max(sleep_time, int(float(m_wait.group(1))) + 3)
                    print(f"  [rate-limit] Đã xoay hết các API key, tạm nghỉ {sleep_time}s chờ reset giới hạn phút...", file=sys.stderr)
                    time.sleep(sleep_time)
                    delay = min(delay * 2, 60)
                    continue

                if code not in (429,) and code < 500:
                    raise  # 4xx khac (sai project, bad request) thi khong retry
                if attempt >= max_attempts:
                    raise
                _print_retry(attempt, exc, delay)
                time.sleep(delay)
                delay = min(delay * 2, 60)
            except NETWORK_ERRORS as exc:
                last_exc = exc
                if attempt >= max_attempts:
                    raise
                _print_retry(attempt, exc, delay)
                # Lam moi client de reset connection pool cua httpx
                if hasattr(self, "_api_keys") and self._api_keys:
                    try:
                        self.client = genai.Client(api_key=self._api_keys[self._current_key_idx])
                    except Exception:
                        pass
                time.sleep(delay)
                delay = min(delay * 2, 60)
            except Exception as exc:
                last_exc = exc
                msg_lower = str(exc).lower()
                if any(w in msg_lower for w in ("connection reset", "broken pipe", "peer closed", "network", "timeout", "errno 54")):
                    if attempt >= max_attempts:
                        raise
                    _print_retry(attempt, exc, delay)
                    if hasattr(self, "_api_keys") and self._api_keys:
                        try:
                            self.client = genai.Client(api_key=self._api_keys[self._current_key_idx])
                        except Exception:
                            pass
                    time.sleep(delay)
                    delay = min(delay * 2, 60)
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("Đã vượt quá số lần thử lại tối đa")



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
