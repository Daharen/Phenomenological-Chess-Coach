"""
Pluggable LLM client used by all four agents.

Providers:
  * local  -> llama-server (OpenAI-compatible /v1/chat/completions).  No paid
              tokens; the default so the whole system validates for free.
  * gemini -> Google Generative Language API (generateContent).
  * null   -> no model; callers fall back to deterministic Stockfish logic.

The interface is deliberately tiny: `.chat()` returns text, `.chat_json()`
returns a parsed dict (or None on failure).  Every method is fail-soft: on any
network/parse error it returns None so the agent layer can degrade gracefully
to its deterministic fallback rather than crash a live game.
"""

from __future__ import annotations

import json
import re
from typing import Any

import requests


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    text = text.strip()
    # strip ```json fences
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # grab the first balanced {...}
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


class LLMClient:
    provider = "null"
    available = False

    def chat(self, system: str, user: str, temperature: float = 0.4,
             max_tokens: int = 1024, json_mode: bool = False) -> str | None:
        return None

    def chat_json(self, system: str, user: str, temperature: float = 0.3,
                  max_tokens: int = 1024) -> dict | None:
        txt = self.chat(system, user, temperature=temperature,
                        max_tokens=max_tokens, json_mode=True)
        return _extract_json(txt) if txt else None

    def describe(self) -> str:
        return f"{self.provider} (available={self.available})"


class NullClient(LLMClient):
    provider = "null"
    available = False


class LocalClient(LLMClient):
    provider = "local"

    def __init__(self, endpoint: str, model: str, api_key: str = "sk-no-key-required",
                 timeout: int = 240, max_retries: int = 2, keepalive_seconds: int = 150):
        self.endpoint = endpoint
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.keepalive_seconds = keepalive_seconds
        self._ka_thread = None
        self.available = self._ping()

    # -- keep the 9B hot in VRAM ----------------------------------------------
    def warm(self) -> bool:
        """Fire a 1-token request so the model loads (once) and stays resident."""
        try:
            r = self.chat("You are a warmup probe.", "ok", temperature=0.0,
                          max_tokens=1)
            return r is not None
        except Exception:
            return False

    def start_keepalive(self):
        """Load the model at boot and periodically poke it so an idle timeout
        doesn't evict it from VRAM. Safe to call more than once."""
        if not self.keepalive_seconds or self._ka_thread is not None or not self.available:
            return
        import threading
        import time

        def _loop():
            self.warm()                      # pay the cold-load cost at boot
            while True:
                time.sleep(self.keepalive_seconds)
                self.warm()

        self._ka_thread = threading.Thread(target=_loop, daemon=True,
                                           name="local-keepalive")
        self._ka_thread.start()

    def _ping(self) -> bool:
        # The server is "present" if it answers at all -- including HTTP 503 while
        # the model is still loading (a cold 9B can take minutes to spin up). Any
        # HTTP response means we should route to it and let chat() wait.
        base = self.endpoint.split("/v1/")[0]
        for path in ("/health", "/v1/models", "/"):
            try:
                requests.get(base + path, timeout=5)
                return True
            except requests.RequestException:
                continue
        return False

    def chat(self, system, user, temperature=0.4, max_tokens=1024, json_mode=False):
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            # Qwen3.x are reasoning models: their <think> trace goes to a separate
            # field and burns the token budget, often leaving `content` empty.
            # Disable thinking so the answer lands in `content`. Needs the server
            # launched with --jinja; ignored harmlessly otherwise.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        for attempt in range(self.max_retries + 1):
            try:
                r = requests.post(self.endpoint, json=payload, headers=headers,
                                  timeout=self.timeout)
                r.raise_for_status()
                data = r.json()
                msg = data["choices"][0]["message"]
                content = msg.get("content")
                if not content:  # fall back to reasoning field if content is empty
                    content = msg.get("reasoning_content") or ""
                # strip any <think>...</think> that leaked into content
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
                return content or None
            except (requests.RequestException, KeyError, ValueError):
                if attempt >= self.max_retries:
                    return None
        return None


class GeminiClient(LLMClient):
    provider = "gemini"

    def __init__(self, endpoint: str, model: str, api_key: str,
                 model_fallbacks: list[str] | None = None,
                 timeout: int = 60, max_retries: int = 2):
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.model_fallbacks = model_fallbacks or []
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.available = bool(api_key)

    def _url(self, model: str) -> str:
        return f"{self.endpoint}/models/{model}:generateContent?key={self.api_key}"

    def chat(self, system, user, temperature=0.4, max_tokens=1024, json_mode=False):
        gen_cfg: dict[str, Any] = {"temperature": temperature,
                                   "maxOutputTokens": max_tokens}
        if json_mode:
            gen_cfg["responseMimeType"] = "application/json"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": gen_cfg,
        }
        models = [self.model, *self.model_fallbacks]
        for model in models:
            for attempt in range(self.max_retries + 1):
                try:
                    r = requests.post(self._url(model), json=body, timeout=self.timeout)
                    if r.status_code == 404:
                        break  # try next model id
                    r.raise_for_status()
                    data = r.json()
                    cands = data.get("candidates", [])
                    if not cands:
                        return None
                    parts = cands[0].get("content", {}).get("parts", [])
                    return "".join(p.get("text", "") for p in parts)
                except (requests.RequestException, KeyError, ValueError):
                    if attempt >= self.max_retries:
                        break
        return None


def make_client(cfg) -> LLMClient:
    """Build the client for the configured provider. Fail-soft to NullClient."""
    if not cfg.llm.get("enabled", True):
        return NullClient()
    provider = cfg.provider
    llm = cfg.llm
    timeout = llm.get("timeout_seconds", 60)
    retries = llm.get("max_retries", 2)
    try:
        if provider == "local":
            lc = llm.get("local", {})
            return LocalClient(lc.get("endpoint"), lc.get("model", "local-9b"),
                               lc.get("api_key", "sk-no-key-required"),
                               timeout=lc.get("timeout_seconds", max(timeout, 240)),
                               max_retries=retries,
                               keepalive_seconds=lc.get("keepalive_seconds", 150))
        if provider == "gemini":
            gc = llm.get("gemini", {})
            key = cfg.gemini_key()
            if not key:
                return NullClient()
            return GeminiClient(gc.get("endpoint"), gc.get("model"), key,
                                gc.get("model_fallbacks", []),
                                timeout=timeout, max_retries=retries)
    except Exception:
        return NullClient()
    return NullClient()
