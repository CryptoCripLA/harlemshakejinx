"""Neon AI Gateway client (owner order 2026-10-21: wire the Neon keys).

The gateway is OpenAI-compatible: `GET {base}/v1/models`, `POST {base}/v1/chat/completions`, bearer token
NEON_AI_GATEWAY_TOKEN (neon.com/docs/ai-gateway/get-started, read 2026-10-21). JINX's contract keeps any LLM out of
DETECT -> SIZE -> FIRE -> EXIT: this client is for asynchronous context only, and nothing in the execution path
imports it. Requests are bounded (at most `max_in_flight` at once, a timeout each) so a slow model cannot build an
unbounded queue. The token is held privately and never appears in health or logs.
"""
from __future__ import annotations

import asyncio
import time

import httpx


class NeonAiGateway:
    def __init__(self, base_url: str, token: str, timeout_s: float = 30.0, max_in_flight: int = 2,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.timeout_s = timeout_s
        self._slots = asyncio.Semaphore(max_in_flight)
        self._transport = transport
        self.stats = {"requests": 0, "failures": 0, "last_ok_ns": None, "last_error": None, "models": None}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_s, transport=self._transport,
                                 headers={"Authorization": f"Bearer {self._token}"})

    async def _call(self, method: str, path: str, **kw) -> dict:
        async with self._slots:
            self.stats["requests"] += 1
            try:
                async with self._client() as c:
                    r = await c.request(method, path, **kw)
                    r.raise_for_status()
                    body = r.json()
            except Exception as e:
                self.stats["failures"] += 1
                self.stats["last_error"] = (f"HTTP {e.response.status_code}" if isinstance(e, httpx.HTTPStatusError)
                                            else type(e).__name__)
                raise
            self.stats["last_ok_ns"], self.stats["last_error"] = time.time_ns(), None
            return body

    async def models(self) -> list[str]:
        ids = [m["id"] for m in (await self._call("GET", "/v1/models")).get("data", [])]
        self.stats["models"] = len(ids)
        return ids

    async def chat(self, model: str, messages: list[dict], max_tokens: int = 256) -> str:
        body = await self._call("POST", "/v1/chat/completions",
                                json={"model": model, "messages": messages, "max_tokens": max_tokens})
        return body["choices"][0]["message"]["content"]

    def health(self) -> dict:
        return {"adapter": "neonai", "connected": self.stats["last_ok_ns"] is not None and not self.stats["last_error"],
                "role": "asynchronous context only; not on the trading path", **self.stats}
