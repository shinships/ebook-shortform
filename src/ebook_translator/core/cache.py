"""Cache ban dich tung chunk xuong dia de resume duoc khi chay lai."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


class TranslationCache:
    def __init__(self, workdir: str | Path):
        self.dir = Path(workdir) / "chunks"
        self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(text: str, model: str, glossary_version: str) -> str:
        h = hashlib.sha256()
        h.update(text.encode("utf-8"))
        h.update(model.encode("utf-8"))
        h.update(glossary_version.encode("utf-8"))
        return h.hexdigest()[:32]

    def get(self, key: str) -> str | None:
        path = self.dir / f"{key}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))["translated"]
        except Exception:
            return None

    def put(self, key: str, translated: str) -> None:
        path = self.dir / f"{key}.json"
        path.write_text(
            json.dumps({"translated": translated}, ensure_ascii=False),
            encoding="utf-8",
        )
