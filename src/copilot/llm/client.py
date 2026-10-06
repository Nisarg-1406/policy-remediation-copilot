"""
LLM client — content-addressed cache, budget guard, retry/circuit-breaker.

The ONLY module in the codebase that is allowed to call the OpenAI API.
All other modules must go through call_llm().

Invariants enforced here:
- I2: Never put holdings/prices/balances into prompts (callers must not; we log prompt size but don't inspect)
- I8: LLM_MODE=replay means zero API calls
- Budget hard stop from config/policy.yaml
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent.parent
CACHE_DIR = REPO_ROOT / "cache" / "llm"
CONFIG_DIR = REPO_ROOT / "config"

# ---------------------------------------------------------------------------
# Global LLM mode — set once at startup
# ---------------------------------------------------------------------------

_LLM_MODE: str = "replay"  # replay | record | live


def set_llm_mode(mode: str) -> None:
    global _LLM_MODE
    assert mode in ("replay", "record", "live"), f"Unknown LLM mode: {mode}"
    _LLM_MODE = mode
    logger.info("LLM mode: %s", mode)


def get_llm_mode() -> str:
    return _LLM_MODE


# ---------------------------------------------------------------------------
# Budget guard
# ---------------------------------------------------------------------------

_TOTAL_COST_USD: float = 0.0
_BUDGET_CONFIG: Optional[dict] = None


def _load_policy() -> dict:
    global _BUDGET_CONFIG
    if _BUDGET_CONFIG is None:
        path = CONFIG_DIR / "policy.yaml"
        with path.open() as f:
            _BUDGET_CONFIG = yaml.safe_load(f)
    return _BUDGET_CONFIG


def _load_pricing() -> dict:
    path = CONFIG_DIR / "models.yaml"
    with path.open() as f:
        cfg = yaml.safe_load(f)
    return cfg.get("pricing", {})


def _estimate_cost(model: str, usage: dict) -> float:
    """Estimate cost in USD from usage dict returned by OpenAI."""
    pricing = _load_pricing()
    model_key = model.split("/")[-1]  # handle "openai/gpt-4o-mini" etc.
    rates = None
    for k in pricing:
        if k in model_key or model_key in k:
            rates = pricing[k]
            break
    if rates is None:
        # Unknown model — use a conservative estimate
        rates = {"input": 5.0, "output": 15.0, "cached_input": 2.5}

    prompt_tokens = usage.get("prompt_tokens", 0) or 0
    # prompt_tokens_details may be a Pydantic object or a dict; handle both
    ptd = usage.get("prompt_tokens_details") or {}
    if hasattr(ptd, "cached_tokens"):
        cached_tokens = ptd.cached_tokens or 0
    elif isinstance(ptd, dict):
        cached_tokens = ptd.get("cached_tokens", 0) or 0
    else:
        cached_tokens = 0
    non_cached = max(0, prompt_tokens - cached_tokens)
    completion_tokens = usage.get("completion_tokens", 0) or 0

    cost = (
        non_cached * rates["input"] / 1_000_000
        + cached_tokens * rates.get("cached_input", rates["input"] * 0.5) / 1_000_000
        + completion_tokens * rates["output"] / 1_000_000
    )
    return cost


def _check_budget(additional_cost: float) -> None:
    global _TOTAL_COST_USD
    policy = _load_policy()
    hard_stop = policy.get("budget_hard_stop_usd", 1.50)
    warn_at = policy.get("budget_warn_usd", 1.00)
    projected = _TOTAL_COST_USD + additional_cost
    if _TOTAL_COST_USD >= warn_at:
        logger.warning("Budget: $%.4f spent (warn threshold $%.2f)", _TOTAL_COST_USD, warn_at)
    if projected > hard_stop:
        raise RuntimeError(
            f"BudgetGuard: projected spend ${projected:.4f} exceeds hard stop ${hard_stop:.2f}. "
            f"Already spent: ${_TOTAL_COST_USD:.4f}. Aborting."
        )


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_key(request: dict) -> str:
    """SHA-256 of the normalized request dict (model, messages, schema, run_index, etc.)."""
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def _read_cache(key: str) -> Optional[dict]:
    path = _cache_path(key)
    if path.exists():
        return json.loads(path.read_text())
    return None


def _write_cache(key: str, entry: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(key).write_text(json.dumps(entry, indent=2))


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

_CONSECUTIVE_FAILURES: int = 0
_CIRCUIT_OPEN: bool = False


def _record_success() -> None:
    global _CONSECUTIVE_FAILURES, _CIRCUIT_OPEN
    _CONSECUTIVE_FAILURES = 0
    _CIRCUIT_OPEN = False


def _record_failure() -> None:
    global _CONSECUTIVE_FAILURES, _CIRCUIT_OPEN
    _CONSECUTIVE_FAILURES += 1
    policy = _load_policy()
    threshold = policy.get("circuit_breaker_threshold", 3)
    if _CONSECUTIVE_FAILURES >= threshold:
        _CIRCUIT_OPEN = True
        logger.error("Circuit breaker OPEN after %d consecutive failures", _CONSECUTIVE_FAILURES)


# ---------------------------------------------------------------------------
# Rate limiter (simple token bucket)
# ---------------------------------------------------------------------------

class _TokenBucket:
    def __init__(self, rate_per_sec: float) -> None:
        self._rate = rate_per_sec
        self._tokens = rate_per_sec
        self._last = time.monotonic()

    async def acquire(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        self._tokens = min(self._rate, self._tokens + elapsed * self._rate)
        self._last = now
        if self._tokens < 1:
            sleep_for = (1 - self._tokens) / self._rate
            await asyncio.sleep(sleep_for)
            self._tokens = 0
        else:
            self._tokens -= 1


def _load_limits() -> dict:
    path = CONFIG_DIR / "limits.yaml"
    with path.open() as f:
        return yaml.safe_load(f).get("openai", {})


_rpm_bucket: Optional[_TokenBucket] = None
_semaphore: Optional[asyncio.Semaphore] = None


def _get_rate_resources() -> tuple[_TokenBucket, asyncio.Semaphore]:
    global _rpm_bucket, _semaphore
    if _rpm_bucket is None:
        limits = _load_limits()
        rpm = limits.get("rpm", 500)
        concurrency = limits.get("concurrency", 4)
        _rpm_bucket = _TokenBucket(rpm / 60.0)
        _semaphore = asyncio.Semaphore(concurrency)
    return _rpm_bucket, _semaphore


def _get_api_key() -> Optional[str]:
    key = os.environ.get("OPENAI_API_KEY")
    if key and key.strip():
        return key.strip()
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("OPENAI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("sk-"):
                return line
    return None


# ---------------------------------------------------------------------------
# Main call function
# ---------------------------------------------------------------------------

async def call_llm(
    *,
    model: str,
    messages: list[dict],
    response_schema: Optional[dict] = None,
    temperature: Optional[float] = None,
    max_tokens: int = 8192,
    run_index: int = 0,
    prompt_version: str = "1.0",
    schema_version: str = "1.0",
    data_dict_hash: str = "",
) -> dict[str, Any]:
    """
    Call the LLM with caching, budget guard, and retry.

    Returns: {"response": <parsed dict>, "usage": {...}, "cache_hit": bool, "cost_usd": float}
    """
    from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential, retry_if_exception_type

    # Build cache key from everything that affects the output
    cache_request = {
        "model": model,
        "messages": messages,
        "response_schema": response_schema,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "run_index": run_index,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
        "data_dict_hash": data_dict_hash,
    }
    key = _cache_key(cache_request)

    # Replay mode — must hit cache
    if _LLM_MODE == "replay":
        entry = _read_cache(key)
        if entry is None:
            raise RuntimeError(
                f"LLM_MODE=replay but cache miss for key={key[:16]}…\n"
                f"Run with LLM_MODE=record first to populate the cache."
            )
        logger.debug("Cache hit: %s", key[:16])
        return {**entry, "cache_hit": True}

    # Check cache (record/live)
    if _LLM_MODE == "record":
        entry = _read_cache(key)
        if entry is not None:
            logger.debug("Cache hit (record mode): %s", key[:16])
            return {**entry, "cache_hit": True}

    if _CIRCUIT_OPEN:
        raise RuntimeError("LLM circuit breaker is open — too many consecutive failures")

    _check_budget(0.05)  # Pre-flight estimate; actual check after

    bucket, sem = _get_rate_resources()
    limits = _load_limits()
    timeout_s = limits.get("per_call_timeout_s", 120)
    max_retries = limits.get("max_retries", 3)

    import openai

    async with openai.AsyncOpenAI(api_key=_get_api_key()) as client:
        async with sem:
            await bucket.acquire()
            t0 = time.monotonic()

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(max_retries),
                wait=wait_exponential(multiplier=1, min=2, max=60),
                retry=retry_if_exception_type((openai.RateLimitError, openai.APIConnectionError)),
                reraise=True,
            ):
                with attempt:
                    call_kwargs: dict[str, Any] = {
                        "model": model,
                        "messages": messages,
                        "max_completion_tokens": max_tokens,
                    }
                    if temperature is not None:
                        call_kwargs["temperature"] = temperature
                    if response_schema is not None:
                        call_kwargs["response_format"] = {
                            "type": "json_schema",
                            "json_schema": {
                                "name": "extraction_output",
                                "strict": True,
                                "schema": response_schema,
                            },
                        }

                    # Robust completion executor supporting diverse model parameter requirements
                    current_kwargs = dict(call_kwargs)
                    while True:
                        try:
                            raw = await asyncio.wait_for(
                                client.chat.completions.create(**current_kwargs),
                                timeout=timeout_s,
                            )
                            break
                        except openai.BadRequestError as bad_req_err:
                            err_str = str(bad_req_err).lower()
                            changed = False
                            # Fallback between max_completion_tokens and max_tokens
                            if "max_completion_tokens" in current_kwargs and (
                                "unsupported parameter: 'max_completion_tokens'" in err_str
                                or "use 'max_tokens'" in err_str
                            ):
                                current_kwargs["max_tokens"] = current_kwargs.pop("max_completion_tokens")
                                changed = True
                            elif "max_tokens" in current_kwargs and (
                                "unsupported parameter: 'max_tokens'" in err_str
                                or "use 'max_completion_tokens'" in err_str
                            ):
                                current_kwargs["max_completion_tokens"] = current_kwargs.pop("max_tokens")
                                changed = True
                            # Fallback if model rejects temperature (e.g. reasoning models)
                            if not changed and "temperature" in current_kwargs and (
                                "temperature" in err_str and ("unsupported" in err_str or "not supported" in err_str or "invalid" in err_str)
                            ):
                                current_kwargs.pop("temperature")
                                changed = True
                            # Fallback if model rejects json_schema structured outputs
                            if not changed and "response_format" in current_kwargs and (
                                "response_format" in err_str or "json_schema" in err_str or "schema" in err_str
                            ):
                                rf = current_kwargs.get("response_format")
                                if isinstance(rf, dict) and rf.get("type") == "json_schema":
                                    current_kwargs["response_format"] = {"type": "json_object"}
                                    changed = True
                                else:
                                    current_kwargs.pop("response_format")
                                    changed = True
                            if changed:
                                logger.info(
                                    "Adjusted LLM parameters for model %s compatibility; retrying...",
                                    model,
                                )
                                continue
                            raise

        except Exception as exc:
            _record_failure()
            raise

    latency_s = time.monotonic() - t0
    _record_success()

    # Serialize usage fully to plain dict (OpenAI SDK returns Pydantic model)
    if raw.usage is None:
        usage = {}
    elif hasattr(raw.usage, "model_dump"):
        usage = raw.usage.model_dump()
    else:
        usage = dict(raw.usage)
    cost = _estimate_cost(model, usage)

    global _TOTAL_COST_USD
    _TOTAL_COST_USD += cost
    _check_budget(0)  # Check after spend

    content = raw.choices[0].message.content or "{}"
    clean_content = content.strip()
    if clean_content.startswith("```"):
        lines = clean_content.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        clean_content = "\n".join(lines).strip()
    parsed = json.loads(clean_content)

    entry = {
        "response": parsed,
        "usage": usage,
        "cost_usd": cost,
        "latency_s": latency_s,
        "model": model,
        "cache_hit": False,
        "cache_key": key,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
    }

    _write_cache(key, entry)
    logger.info(
        "LLM call complete: model=%s tokens_in=%s tokens_out=%s cost=$%.4f latency=%.1fs",
        model,
        usage.get("prompt_tokens", "?"),
        usage.get("completion_tokens", "?"),
        cost,
        latency_s,
    )
    return entry
