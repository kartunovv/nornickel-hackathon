"""Клиент Yandex AI Studio (Foundation Models API).

Особенности:
- экспоненциальный backoff с джиттером на 429/5xx (квота на ключ общая,
  поэтому ретраи обязательны);
- дисковый кэш эмбеддингов и completion-ответов (повторные запуски
  конвейера не тратят квоту);
- строгий JSON-режим для генерации гипотез с автоповтором при невалидном JSON.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import time
from pathlib import Path
from typing import Any

import httpx

from . import config

COMPLETION_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
EMBEDDING_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/textEmbedding"

MAX_RETRIES = 8
BASE_DELAY = 1.5


class YandexLLM:
    def __init__(self, api_key: str | None = None, folder_id: str | None = None,
                 cache_dir: Path | None = None):
        self.api_key = api_key or config.YC_API_KEY
        self.folder_id = folder_id or config.YC_FOLDER_ID
        if not self.api_key or not self.folder_id:
            raise RuntimeError("Не заданы YC_API_KEY / YC_FOLDER_ID (см. .env)")
        self.cache_dir = cache_dir or config.CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.Client(timeout=120)

    # ------------------------------------------------------------------ utils
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Api-Key {self.api_key}", "x-folder-id": self.folder_id}

    def _cache_path(self, kind: str, payload: str) -> Path:
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
        return self.cache_dir / f"{kind}_{digest}.json"

    def _post_with_retry(self, url: str, body: dict) -> dict:
        last_err: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                r = self._client.post(url, headers=self._headers(), json=body)
                if r.status_code == 200:
                    return r.json()
                if r.status_code in (429, 500, 502, 503, 504):
                    delay = BASE_DELAY * (2 ** attempt) * (0.7 + 0.6 * random.random())
                    time.sleep(min(delay, 45))
                    continue
                raise RuntimeError(f"Yandex API {r.status_code}: {r.text[:300]}")
            except httpx.HTTPError as e:
                last_err = e
                time.sleep(BASE_DELAY * (2 ** attempt))
        raise RuntimeError(f"Yandex API: превышено число попыток ({last_err})")

    # ------------------------------------------------------------- completion
    def complete(self, messages: list[dict[str, str]], temperature: float = 0.3,
                 max_tokens: int = 2000, use_cache: bool = True) -> str:
        """messages: [{"role": "system"|"user"|"assistant", "text": ...}]"""
        body = {
            "modelUri": f"gpt://{self.folder_id}/{config.GPT_MODEL}",
            "completionOptions": {"temperature": temperature, "maxTokens": max_tokens},
            "messages": messages,
        }
        key = json.dumps(body, ensure_ascii=False, sort_keys=True)
        cache_file = self._cache_path("cmpl", key)
        if use_cache and cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8"))["text"]

        data = self._post_with_retry(COMPLETION_URL, body)
        text = data["result"]["alternatives"][0]["message"]["text"]
        cache_file.write_text(json.dumps({"text": text}, ensure_ascii=False), encoding="utf-8")
        return text

    def complete_json(self, messages: list[dict[str, str]], temperature: float = 0.3,
                      max_tokens: int = 4000, retries: int = 3) -> Any:
        """Completion с гарантией валидного JSON на выходе."""
        msgs = list(messages)
        for attempt in range(retries):
            text = self.complete(msgs, temperature=temperature, max_tokens=max_tokens,
                                 use_cache=(attempt == 0))
            parsed = _extract_json(text)
            if parsed is not None:
                return parsed
            msgs = messages + [
                {"role": "assistant", "text": text},
                {"role": "user", "text": "Ответ не является валидным JSON. Повтори ответ строго "
                                         "в виде одного JSON-объекта без пояснений и markdown."},
            ]
        raise ValueError("LLM не вернула валидный JSON")

    # ------------------------------------------------------------- embeddings
    def embed(self, text: str, kind: str = "doc") -> list[float]:
        model = config.EMB_DOC_MODEL if kind == "doc" else config.EMB_QUERY_MODEL
        text = text[:8000]
        cache_file = self._cache_path("emb", f"{model}|{text}")
        if cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8"))
        body = {"modelUri": f"emb://{self.folder_id}/{model}", "text": text}
        data = self._post_with_retry(EMBEDDING_URL, body)
        vec = [float(x) for x in data["embedding"]]
        cache_file.write_text(json.dumps(vec), encoding="utf-8")
        return vec

    def embed_many(self, texts: list[str], kind: str = "doc",
                   progress_cb=None) -> list[list[float]]:
        out = []
        for i, t in enumerate(texts):
            out.append(self.embed(t, kind=kind))
            if progress_cb:
                progress_cb(i + 1, len(texts))
        return out


def _extract_json(text: str) -> Any | None:
    """Достаёт JSON из ответа LLM (в т.ч. из ```json ...``` блоков)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        return None
    for end in range(len(text), start, -1):
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            continue
    return None
