"""Centralized DeepSeek API Client for Lộc Phát Securities.

Official documentation: https://api-docs.deepseek.com/quick_start/pricing/
Supported Models:
- deepseek-v4-flash: Default high-speed, cost-effective model ($0.007-$0.44/1M in, $0.66-$1.32/1M out, 1M context)
- deepseek-v4-pro: Advanced reasoning model ($0.022-$1.32/1M in, $1.98-$3.96/1M out, 1M context)
- deepseek-chat: Official backward-compatible alias for non-reasoning chat completions
- deepseek-reasoner: Official backward-compatible alias for reasoning completions
"""

import os
import json
import time
import logging
from typing import Dict, Any, List, Optional, Tuple
import requests

logger = logging.getLogger("deepseek_client")

# API Configuration
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_PRIMARY_MODEL = "deepseek-v4-flash"

# Model Tier Hierarchy & Fallback Order
SUPPORTED_MODELS = [
    "deepseek-v4-flash",
    "deepseek-chat",
    "deepseek-v4-pro",
    "deepseek-reasoner",
]


def get_deepseek_api_key() -> str:
    """Retrieve DEEPSEEK_API_KEY from environment or local .env files."""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    for env_path in [
        os.path.join(os.path.dirname(__file__), ".env"),
        "/etc/secrets/.env",
    ]:
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("DEEPSEEK_API_KEY"):
                            parts = line.split("=", 1)
                            if len(parts) == 2:
                                return parts[1].strip().strip('"\'')
            except Exception:
                pass
    return ""


def get_configured_model() -> str:
    """Retrieve configured model name or default to deepseek-v4-flash."""
    configured = os.environ.get("DEEPSEEK_MODEL", "").strip()
    if configured:
        return configured
    for env_path in [
        os.path.join(os.path.dirname(__file__), ".env"),
        "/etc/secrets/.env",
    ]:
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("DEEPSEEK_MODEL"):
                            parts = line.split("=", 1)
                            if len(parts) == 2:
                                val = parts[1].strip().strip('"\'')
                                if val:
                                    return val
            except Exception:
                pass
    return DEFAULT_PRIMARY_MODEL


def get_model_candidate_list(preferred_model: Optional[str] = None) -> List[str]:
    """Build prioritized list of candidate models with deduplication."""
    target = preferred_model or get_configured_model() or DEFAULT_PRIMARY_MODEL
    # Filter out known invalid legacy names
    if target in ("deepseek-v4-flash-0731", "deepseek-v4-pro-0813"):
        target = "deepseek-v4-flash"

    candidates = [target]
    for m in SUPPORTED_MODELS:
        if m not in candidates:
            candidates.append(m)
    return candidates


def clean_and_parse_json(raw_content: str) -> Dict[str, Any]:
    """Robustly clean and parse JSON from LLM output, handling markdown fences and partial formatting."""
    if not raw_content or not isinstance(raw_content, str):
        raise ValueError("Nội dung phản hồi trống hoặc không phải chuỗi.")

    text = raw_content.strip()

    # 1. Strip markdown code fences if present
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    # 2. Try direct JSON parse
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # 3. Fallback: locate first '{' and last '}'
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        snippet = text[first_brace : last_brace + 1]
        try:
            parsed = json.loads(snippet)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError as err:
            raise ValueError(f"Không thể parse JSON từ phản hồi DeepSeek: {err}")

    raise ValueError("Phản hồi từ DeepSeek không chứa JSON object hợp lệ.")


def call_deepseek_json(
    messages: List[Dict[str, str]],
    system_prompt: Optional[str] = None,
    preferred_model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 3500,
    enable_thinking: bool = False,
    timeout: float = 40.0,
    max_retries_per_model: int = 1,
) -> Dict[str, Any]:
    """Execute DeepSeek Chat Completion requesting JSON object output.

    Args:
        messages: List of message dicts ({'role': '...', 'content': '...'}).
        system_prompt: Optional system prompt to prepend if not already in messages.
        preferred_model: Specific model to try first (e.g. 'deepseek-v4-flash').
        temperature: Sampling temperature (ignored if thinking is enabled).
        max_tokens: Max output tokens.
        enable_thinking: Whether to enable DeepSeek-V4 Chain-of-Thought (default False for fast JSON).
        timeout: Request timeout in seconds.
        max_retries_per_model: Retries for transient 429/500/503 network errors.

    Returns:
        Dict containing parsed JSON payload, along with '_deepseek_meta' metadata.
    """
    api_key = get_deepseek_api_key()
    if not api_key:
        raise ValueError("Chưa cấu hình DEEPSEEK_API_KEY trong hệ thống.")

    # Prepare message payload
    full_messages: List[Dict[str, str]] = []
    if system_prompt:
        # Check if first message is already system
        if not messages or messages[0].get("role") != "system":
            full_messages.append({"role": "system", "content": system_prompt})
    full_messages.extend(messages)

    # Ensure the prompt contains the word 'json' for strict compliance with OpenAI / DeepSeek JSON mode
    has_json_keyword = any("json" in m.get("content", "").lower() for m in full_messages)
    if not has_json_keyword and full_messages:
        full_messages[-1]["content"] += "\n\n(Lưu ý: Xuất kết quả dưới định dạng JSON thuần túy)."

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    candidates = get_model_candidate_list(preferred_model)
    last_error = None

    for model_name in candidates:
        payload: Dict[str, Any] = {
            "model": model_name,
            "messages": full_messages,
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
        }

        # DeepSeek-V4 Thinking Mode parameter control
        if not enable_thinking:
            payload["thinking"] = {"type": "disabled"}
            payload["temperature"] = temperature
        else:
            payload["thinking"] = {"type": "enabled"}
            # temperature is not supported when thinking is enabled

        for attempt in range(max_retries_per_model + 1):
            t0 = time.time()
            try:
                logger.info(f"Đang gửi request tới DeepSeek ({model_name}, attempt {attempt+1})...")
                res = requests.post(
                    DEEPSEEK_API_URL,
                    json=payload,
                    headers=headers,
                    timeout=timeout,
                )
                latency = round(time.time() - t0, 3)

                if res.status_code == 200:
                    body = res.json()
                    choice = body.get("choices", [{}])[0]
                    content = choice.get("message", {}).get("content", "") or ""
                    parsed = clean_and_parse_json(content)

                    usage = body.get("usage", {})
                    parsed["_deepseek_meta"] = {
                        "model": model_name,
                        "latency_seconds": latency,
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                        "total_tokens": usage.get("total_tokens"),
                        "cached_tokens": usage.get("prompt_tokens_details", {}).get("cached_tokens", 0),
                    }
                    return parsed

                elif res.status_code == 400:
                    err_msg = res.text
                    logger.warning(f"DeepSeek 400 Bad Request ({model_name}): {err_msg}")
                    # Model not supported or invalid request: skip to next candidate immediately
                    last_error = RuntimeError(f"DeepSeek API 400: {err_msg}")
                    break

                elif res.status_code in (429, 500, 502, 503, 504):
                    logger.warning(f"DeepSeek {res.status_code} ({model_name}): {res.text}. Retrying...")
                    time.sleep(1.0 * (attempt + 1))
                    last_error = RuntimeError(f"DeepSeek API {res.status_code}: {res.text}")
                    continue
                else:
                    err_msg = res.text
                    logger.warning(f"DeepSeek status {res.status_code} ({model_name}): {err_msg}")
                    last_error = RuntimeError(f"DeepSeek API {res.status_code}: {err_msg}")
                    break

            except requests.exceptions.Timeout:
                logger.warning(f"DeepSeek timeout sau {timeout}s đối với model {model_name}.")
                last_error = TimeoutError(f"DeepSeek request timeout ({timeout}s)")
                continue
            except Exception as e:
                logger.warning(f"Lỗi kết nối DeepSeek ({model_name}): {e}")
                last_error = e
                continue

    raise last_error or RuntimeError("Không thể nhận phản hồi hợp lệ từ DeepSeek API sau khi thử tất cả model khả dụng.")
