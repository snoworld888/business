"""LLM 服务：多模型路由 + 缓存 + 离线规则引擎。

对齐技术文档 §5.5 模型路由与成本控制：
  简单任务 → LLM_MODEL_SIMPLE
  复杂分析 → LLM_MODEL_COMPLEX
  私有化   → LLM_MODEL_PRIVATE
  相同 Query 结果缓存 24 小时

provider=rule 时完全不调用外部模型，由确定性规则引擎产出**结构化 JSON**，
保证：① 无 API Key 也能端到端跑通；② 测试可断言；③ 换真实模型时业务零改动。
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import time
from typing import Any, Callable

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

COMPLEXITY_SIMPLE = "simple"
COMPLEXITY_COMPLEX = "complex"
COMPLEXITY_PRIVATE = "private"

_cache: dict[str, tuple[float, Any]] = {}


def _cache_key(model: str, prompt: str) -> str:
    return hashlib.md5(f"{model}::{prompt}".encode()).hexdigest()


def _cache_get(key: str) -> Any | None:
    hit = _cache.get(key)
    if not hit:
        return None
    ts, value = hit
    if time.time() - ts > settings.LLM_CACHE_TTL_HOURS * 3600:
        _cache.pop(key, None)
        return None
    return value


def _cache_put(key: str, value: Any) -> None:
    _cache[key] = (time.time(), value)


def _copy_with_meta(payload: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """浅拷贝并合并 `_meta`，避免污染缓存中的原对象。"""
    copied = dict(payload)
    meta = dict(copied.get("_meta") or {})
    meta.update(extra)
    copied["_meta"] = meta
    return copied


def _safe_return(payload: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """返回给调用方的副本：深拷贝 + 合并 `_meta`。

    必须深拷贝——否则调用方（Agent / API 层）对返回值做任何原地修改
    （例如往 `_meta` 里塞字段、给列表 append）都会污染缓存对象，
    导致后续命中缓存时拿到被改过的脏数据。
    """
    copied = copy.deepcopy(payload)
    if extra:
        meta = dict(copied.get("_meta") or {})
        meta.update(extra)
        copied["_meta"] = meta
    return copied


def extract_json(text: str) -> dict[str, Any] | None:
    """从 LLM 输出中抽取 JSON（容忍 markdown 代码块与前后缀噪声）。"""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        candidate = text[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


class LLMService:
    """统一入口。provider=rule 时走 rule_engine 回调。"""

    def __init__(self) -> None:
        self.provider = settings.LLM_PROVIDER.lower()

    # ------------------------------------------------------------ 路由
    def route(self, complexity: str) -> str:
        return {
            COMPLEXITY_SIMPLE: settings.LLM_MODEL_SIMPLE,
            COMPLEXITY_COMPLEX: settings.LLM_MODEL_COMPLEX,
            COMPLEXITY_PRIVATE: settings.LLM_MODEL_PRIVATE,
        }.get(complexity, settings.LLM_MODEL_SIMPLE)

    # ------------------------------------------------------------ 调用
    def complete(self, prompt: str, system: str = "",
                 complexity: str = COMPLEXITY_COMPLEX,
                 rule_engine: Callable[[], dict[str, Any]] | None = None) -> dict[str, Any]:
        """返回 dict。rule 模式或远端失败时回退到 rule_engine。

        ⚠️ 无论走哪条路径，返回值都必须带 `_meta`，且 `provider` 必须如实反映
        真实调用方（rule / openai / ollama）。否则上层 trace 会把真实大模型
        误报为规则引擎——这正是「系统到底有没有接大模型」判断错误的根源。
        """
        model = self.route(complexity)

        if self.provider == "rule" or not self._remote_ready():
            return self._fallback(prompt, model, rule_engine)

        key = _cache_key(model, f"{system}\n{prompt}")
        cached = _cache_get(key)
        if cached is not None:
            return _safe_return(cached, {"cached": True})

        started = time.perf_counter()
        try:
            text = self._remote(prompt, system, model)
            latency = round((time.perf_counter() - started) * 1000, 2)
            parsed = extract_json(text)
            if parsed is None:
                parsed = {"raw": text}
            stored = _copy_with_meta(parsed, {
                "provider": self.provider,
                "model": model,
                "prompt_chars": len(prompt),
                "latency_ms": latency,
                "cached": False,
                "parsed_json": "raw" not in parsed,
                "raw_text": text if "raw" in parsed else None,
            })
            _cache_put(key, stored)
            return _safe_return(stored)
        except Exception as exc:
            logger.warning("LLM 调用失败(model=%s)，回退规则引擎: %s", model, exc)
            out = self._fallback(prompt, model, rule_engine)
            out["_meta"]["error"] = f"{type(exc).__name__}: {exc}"[:200]
            return out

    def _remote_ready(self) -> bool:
        if self.provider == "ollama":
            return True
        return bool(settings.LLM_API_KEY)

    def clear_cache(self) -> int:
        """清空响应缓存（配置切换、测试、模型升级后调用）。返回清理条数。"""
        n = len(_cache)
        _cache.clear()
        return n

    def _remote(self, prompt: str, system: str, model: str) -> str:
        if self.provider == "ollama":
            payload: dict[str, Any] = {
                "model": model, "stream": False,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": prompt}],
                # 关掉思考链：Agent 任务是按 Schema 抽结构，thinking 只增加延迟
                "think": bool(settings.LLM_OLLAMA_THINK),
                "options": {
                    "temperature": 0.2,
                    "num_predict": int(settings.LLM_OLLAMA_NUM_PREDICT),
                },
            }
            if settings.LLM_OLLAMA_JSON_FORMAT:
                payload["format"] = "json"
            # trust_env=False：本机 http_proxy 会污染请求
            with httpx.Client(timeout=settings.LLM_TIMEOUT_SECONDS,
                              trust_env=False) as client:
                resp = client.post(
                    f"{settings.LLM_BASE_URL.split('/v1')[0]}/api/chat", json=payload)
        else:
            resp = httpx.post(
                f"{settings.LLM_BASE_URL.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
                json={"model": model, "temperature": 0.2,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": prompt}]},
                timeout=settings.LLM_TIMEOUT_SECONDS,
            )
        resp.raise_for_status()
        data = resp.json()
        if self.provider == "ollama":
            return data["message"]["content"]
        return data["choices"][0]["message"]["content"]

    def _fallback(self, prompt: str, model: str,
                  rule_engine: Callable[[], dict[str, Any]] | None) -> dict[str, Any]:
        started = time.perf_counter()
        result = rule_engine() if rule_engine else {"raw": ""}
        result.setdefault("_meta", {})
        result["_meta"].update({
            "provider": "rule" if self.provider == "rule" else f"{self.provider}-fallback",
            "model": model if self.provider != "rule" else "rule-engine-v1",
            "prompt_chars": len(prompt),
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "cached": False,
            "parsed_json": True,
            "raw_text": None,
        })
        return result

    def health(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "remote_ready": self._remote_ready(),
            "routes": {
                COMPLEXITY_SIMPLE: settings.LLM_MODEL_SIMPLE,
                COMPLEXITY_COMPLEX: settings.LLM_MODEL_COMPLEX,
                COMPLEXITY_PRIVATE: settings.LLM_MODEL_PRIVATE,
            },
            "cache_entries": len(_cache),
            "cache_ttl_hours": settings.LLM_CACHE_TTL_HOURS,
        }


llm = LLMService()
