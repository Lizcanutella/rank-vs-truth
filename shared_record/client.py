"""Chat client for an OpenAI-compatible endpoint (OpenRouter by default), with an append-only disk
cache and an optional spending cap.

Every completed call is written to data/cache/<model>.jsonl keyed by a hash of the request, so an
interrupted run resumes without repeating calls. The API key is read from the environment variable
named in models.toml (`api_key_env`, default OPENROUTER_API_KEY). Models whose id starts with `mock`
are answered offline by shared_record.mock and need no key.

Spending cap: `Client(cfg, max_cost=...)` adds up `usage.cost` (US dollars, as OpenRouter reports it)
over new calls. Once the total exceeds `max_cost`, no further request is sent and `chat` raises
SpendingCapReached; requests already in flight finish and are cached.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tomllib
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"


def load_models() -> dict[str, dict]:
    cfg = tomllib.loads((ROOT / "models.toml").read_text())
    return {name: {**cfg["defaults"], **m, "name": name} for name, m in cfg["models"].items()}


class SpendingCapReached(RuntimeError):
    pass


class Client:
    def __init__(self, cfg: dict, oracle: dict | None = None, max_cost: float | None = None,
                 cache_dir: Path | None = None):
        self.cfg = cfg
        self.mock = cfg["id"].startswith("mock")
        self.max_cost = max_cost
        self.oracle = oracle or {}
        cache_dir = cache_dir or CACHE
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.path = cache_dir / f"{cfg['name']}.jsonl"
        self.cache: dict[str, dict] = {}
        self._need_newline = False
        if self.path.exists():
            with self.path.open("rb") as f:
                last = b"\n"
                for raw in f:
                    last = raw
                    if not raw.strip():
                        continue
                    try:
                        row = json.loads(raw)
                        self.cache[row["key"]] = row["out"]
                    except (ValueError, KeyError, TypeError):
                        pass  # e.g. a final line cut by a crash
            self._need_newline = not last.endswith(b"\n")
        self.sem = asyncio.Semaphore(cfg["concurrency"])
        self.http: httpx.AsyncClient | None = None
        self.requests = 0   # chat() calls, cached or not
        self.calls = 0      # new (non-cached) calls
        self.cost = 0.0     # US dollars over new calls
        self.stopped = False

    def stats(self) -> str:
        return f"{self.requests} calls, {self.calls} new, ${self.cost:.4f} spent"

    def body(self, messages: list[dict], temperature: float | None = None) -> dict:
        """Request body. `send_temperature = false` (models.toml) omits `temperature` for endpoints that
        reject it; `temperature` overrides the model's setting for this call where one is sent."""
        c = self.cfg
        body = {"model": c["id"], "messages": messages}
        if c.get("send_temperature", True):
            body["temperature"] = c["temperature"] if temperature is None else temperature
        body |= {"max_tokens": c["max_tokens"], **c.get("extra", {})}
        return body

    def key(self, messages: list[dict], sample: int = 0, temperature: float | None = None) -> str:
        """Cache key: sha256 of the request body and the sample number."""
        return hashlib.sha256(json.dumps([self.body(messages, temperature), sample], sort_keys=True).encode()).hexdigest()

    def _check_cap(self):
        if self.max_cost is not None and self.cost > self.max_cost:
            self.stopped = True
        if self.stopped:
            raise SpendingCapReached(f"spending cap ${self.max_cost:.2f} reached (${self.cost:.4f} spent)")

    async def chat(self, messages: list[dict], sample: int = 0, temperature: float | None = None) -> dict:
        """Return {'content', 'reasoning', 'usage', 'finish'}. `sample` separates repeated samples of
        the same request in the cache (screening draws five)."""
        self.requests += 1
        body = self.body(messages, temperature)
        key = self.key(messages, sample, temperature)
        if key in self.cache:
            return self.cache[key]
        if self.mock:
            from .mock import mock_reply
            out = {"content": mock_reply(self.cfg["id"], messages, self.oracle, sample), "reasoning": None,
                   "usage": None, "finish": "stop"}
        else:
            self._check_cap()
            out = await self._post(body)
            cost = (out.get("usage") or {}).get("cost")
            if cost is not None:
                self.cost += float(cost)
        self.cache[key] = out
        self.calls += 1
        with self.path.open("a") as f:
            f.write(("\n" if self._need_newline else "") + json.dumps({"key": key, "out": out}) + "\n")
        self._need_newline = False
        return out

    async def _post(self, body: dict) -> dict:
        c = self.cfg
        if self.http is None:
            env = c.get("api_key_env") or ""
            token = os.environ.get(env, "") if env else ""
            if env and not token:
                raise SystemExit(f"Set the environment variable {env} to run model {c['name']}.")
            self.http = httpx.AsyncClient(base_url=c["base_url"], timeout=120,
                                          headers={"Authorization": f"Bearer {token}"} if token else {})
        async with self.sem:
            self._check_cap()  # the cap may have been reached while this call waited
            for attempt in range(8):
                try:
                    r = await self.http.post("/chat/completions", json=body)
                    if r.status_code in (408, 429) or r.status_code >= 500:
                        raise httpx.HTTPStatusError("retryable", request=r.request, response=r)
                    r.raise_for_status()
                    data = r.json()
                    if "choices" not in data:  # some upstream errors arrive with HTTP 200
                        raise httpx.TransportError(str(data.get("error"))[:200])
                    ch = data["choices"][0]
                    msg = ch["message"]
                    return {"content": msg.get("content") or "",
                            "reasoning": msg.get("reasoning") or msg.get("reasoning_content"),
                            "usage": data.get("usage"), "finish": ch.get("finish_reason")}
                except (httpx.TransportError, httpx.HTTPStatusError, json.JSONDecodeError, KeyError) as e:
                    if isinstance(e, httpx.HTTPStatusError) and str(e) != "retryable":
                        raise
                    if attempt == 7:
                        raise
                    await asyncio.sleep(min(60, 2 ** attempt))
        raise RuntimeError("unreachable")

    async def close(self):
        if self.http:
            await self.http.aclose()


async def gather_all(client: Client, label: str, jobs) -> list:
    """Run jobs; on a reached spending cap print how far it got and exit with status 2."""
    res = await asyncio.gather(*jobs, return_exceptions=True)
    errors = [r for r in res if isinstance(r, BaseException)]
    if errors:
        if all(isinstance(e, SpendingCapReached) for e in errors):
            print(f"{client.cfg['name']} {label}: STOPPED, {errors[0]}. {client.stats()}. "
                  f"{len(res) - len(errors)} of {len(res)} complete and cached. Re-run with a higher --max-cost "
                  f"to continue from the cache.")
            raise SystemExit(2)
        raise errors[0]
    return res
