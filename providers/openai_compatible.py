"""
OpenAI-compatible transport for OpenAI, DeepSeek, Ollama and LM Studio.
"""

import json
from typing import Dict, List, Optional, Iterator, Any

from .base import BaseProvider, ToolCall, StreamResponse


# Provider configurations
PROVIDER_CONFIGS = {
    "openai": {
        "base_url": "api.openai.com",
        "path": "/v1/chat/completions",
        "headers": lambda key: {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}"
        }
    },
    "deepseek": {
        "base_url": "api.deepseek.com",
        "path": "/chat/completions",
        "headers": lambda key: {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}"
        }
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "path": "/chat/completions",
        "headers": lambda key: {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}" if key else ""
        }
    },
    "lm-studio": {
        "base_url": "http://localhost:1234/v1",
        "path": "/chat/completions",
        "headers": lambda key: {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}" if key else ""
        }
    },
}

# Model info for common models
MODEL_INFO = {
    # OpenAI
    "gpt-4o": {"max_tokens": 4096, "context_window": 128000, "supports_images": True},
    "gpt-4o-mini": {"max_tokens": 16384, "context_window": 128000, "supports_images": True},
    "gpt-4": {"max_tokens": 8192, "context_window": 128000, "supports_images": False},
    "gpt-4-turbo": {"max_tokens": 4096, "context_window": 128000, "supports_images": True},
    "gpt-3.5-turbo": {"max_tokens": 4096, "context_window": 16385, "supports_images": False},
    "o1": {"max_tokens": 100000, "context_window": 200000, "supports_images": True},
    "o3": {"max_tokens": 100000, "context_window": 200000, "supports_images": True},
    # DeepSeek
    "deepseek-reasoner": {"max_tokens": 8192, "context_window": 64000, "supports_images": False},
    "deepseek-v4-flash": {"max_tokens": 8192, "context_window": 128000, "supports_images": False},
    "deepseek-v4-pro": {"max_tokens": 8192, "context_window": 128000, "supports_images": False},
    # Default fallback
    "default": {"max_tokens": 4096, "context_window": 8192, "supports_images": False},
}


class OpenAICompatibleProvider(BaseProvider):
    """
    Shared provider implementation for the registered compatible services.
    """
    
    PROVIDER_NAME = "openai-compatible"
    
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: Optional[str] = None,
        extra_config: Optional[Dict[str, Any]] = None
    ):
        # Determine the actual provider name from extra_config
        self._provider_name = extra_config.get("provider_name", "openai") if extra_config else "openai"
        # Expose normalized provider id for logging/diagnostics
        self.provider_id = self._provider_name
        self.provider_name = self._provider_name
        self._config = PROVIDER_CONFIGS.get(self._provider_name, PROVIDER_CONFIGS["openai"])
        # OpenAI prompt_cache_key: improves cache hit routing for identical prompts
        self._prompt_cache_key = (extra_config or {}).get("session_id")
        
        # Allow per-provider endpoint overrides, including local services.
        actual_base_url = base_url or self._config["base_url"]

        super().__init__(api_key, model, actual_base_url, extra_config)
        self._model_info = MODEL_INFO.get(model, MODEL_INFO["default"])
    
    def _get_max_tokens(self) -> int:
        return self._model_info["max_tokens"]
    
    def supports_images(self) -> bool:
        return self._model_info["supports_images"]
    
    def _get_headers(self) -> Dict[str, str]:
        return self._config["headers"](self.api_key)
    
    def _get_path(self) -> str:
        return self._config["path"]

    def _apply_provider_payload_options(self, payload: Dict[str, Any]) -> None:
        """Apply provider-specific request extensions in one place."""
        effort = self.get_reasoning_effort()

        # OpenAI reasoning models accept reasoning_effort; non-reasoning
        # models reject it, so gate by model family.
        if self._provider_name == "openai":
            model = self.model.lower()
            if any(model.startswith(p) for p in ("o1", "o3", "o4", "gpt-5")):
                if "max_tokens" in payload:
                    payload["max_completion_tokens"] = payload.pop("max_tokens")
                if effort:
                    payload["reasoning_effort"] = effort
                # This Chat Completions route rejects non-none reasoning
                # effort when function tools are present for GPT-5.6 models.
                if model.startswith("gpt-5.6") and payload.get("tools"):
                    payload["reasoning_effort"] = "none"
                payload.pop("temperature", None)

        is_deepseek = "deepseek" in self.base_url.lower() or self._provider_name == "deepseek"
        if is_deepseek:
            # All v4 models (pro and flash) are advanced reasoning models;
            # "reasoner"/"pro" keeps legacy model names covered.
            is_thinking_model = any(m in self.model.lower() for m in ["reasoner", "pro", "v4"])
            if is_thinking_model:
                payload["thinking"] = {"type": "enabled"}
                deepseek_effort_map = {
                    "low": "high",
                    "medium": "high",
                    "high": "high",
                }
                sent_effort = deepseek_effort_map.get(effort) if effort else None
                if sent_effort:
                    payload["reasoning_effort"] = sent_effort
                payload.pop("temperature", None)
                payload.pop("top_p", None)

        # OpenAI prompt caching: improves cache hit rates by routing requests
        # with identical prompt_cache_key to the same server.
        # Only OpenAI supports this; DeepSeek and local providers reject it.
        if self._prompt_cache_key and self._provider_name == "openai":
            payload["prompt_cache_key"] = self._prompt_cache_key

    def _looks_like_html_document(self, text: str) -> bool:
        """Detect likely raw HTML documents returned by proxies/CDNs."""
        if not text:
            return False
        probe = text.lstrip().lower()
        return probe.startswith(("<!doctype html", "<html", "<head", "<body"))

    def _build_api_path(self) -> tuple:
        """Build request tuple (host, port, api_path) from base URL + provider path."""
        host, port, path = self._parse_url(self.base_url)
        default_path = self._get_path()
        api_path = path.rstrip("/") + default_path if path != "/" else default_path
        return host, port, api_path

    def _set_stream_timeout(self, response, timeout_seconds: float = 60.0) -> None:
        """Set socket timeout for streaming responses when possible."""
        try:
            if hasattr(response, "fp") and hasattr(response.fp, "raw") and hasattr(response.fp.raw, "_sock"):
                response.fp.raw._sock.settimeout(timeout_seconds)
        except Exception:
            pass

    def _decode_error_body(self, response) -> str:
        try:
            return response.read().decode("utf-8", errors="replace")
        except Exception:
            return f"HTTP {getattr(response, 'status', 'unknown')}"

    def _raise_for_http_error(self, response, logger, include_location: bool = False) -> None:
        """Log and raise typed errors for non-200 API responses."""
        if response.status == 200:
            return

        content_type = response.getheader("Content-Type", "") if hasattr(response, "getheader") else ""
        location = response.getheader("Location", "") if include_location and hasattr(response, "getheader") else ""
        error_body = self._decode_error_body(response)
        response.close()

        log_meta = {
            "content_type": content_type,
            "body": error_body[:1200],
        }
        if include_location:
            log_meta["location"] = location
        logger(f"[{self._provider_name.upper()}] API error {response.status}", log_meta)

        from .base import RateLimitError, CreditsError
        if response.status == 429:
            raise RateLimitError(f"Rate limited: {error_body[:300]}")
        if response.status in (401, 402, 403):
            raise CreditsError(f"Credits/Auth error: {error_body[:300]}")
        raise Exception(f"API error ({response.status}): {error_body[:300]}")

    def _list_models_fallback(self) -> List[str]:
        """Fetch available models from the provider's /models endpoint."""
        import json
        from urllib.parse import urlparse
        from ..logger import log_info, log_error
        
        host, port, base_path = self._parse_url(self.base_url)
        
        # Smartly construct models path based on the config path
        config_path = self._get_path()
        if "/chat/completions" in config_path:
            models_path = config_path.replace("/chat/completions", "/models")
        elif "deepseek" in host.lower():
            models_path = "/models"
        else:
            models_path = "/v1/models"
            
        api_path = base_path.rstrip("/") + models_path

        log_info(f"[{self.PROVIDER_NAME.upper()}] Fetching models from {host}{api_path}")
        headers = self._get_headers()
        
        # For Nvidia and some providers, GET /models requires Accept header
        if "Accept" not in headers:
            headers["Accept"] = "application/json"
            
        try:
            response = self._make_https_request(
                host, port, "GET", api_path, headers, timeout=10
            )

            # Some providers (e.g. DeepSeek) may redirect /models.
            # Follow a few redirects like curl -L.
            redirect_hops = 0
            while response.status in (301, 302, 303, 307, 308) and redirect_hops < 3:
                location = response.getheader("Location")
                response.close()
                if not location:
                    break

                redirect_hops += 1
                parsed = urlparse(location)
                if parsed.scheme in ("http", "https"):
                    host = parsed.hostname or host
                    port = parsed.port or (443 if parsed.scheme == "https" else 80)
                    redirect_path = parsed.path or "/"
                    if parsed.query:
                        redirect_path += f"?{parsed.query}"
                else:
                    redirect_path = location if location.startswith("/") else f"/{location}"

                response = self._make_https_request(
                    host, port, "GET", redirect_path, headers, timeout=10
                )

            try:
                if response.status != 200:
                    error_body = response.read().decode("utf-8", errors="replace")
                    log_error(
                        f"[{self.PROVIDER_NAME.upper()}] Models endpoint returned HTTP {response.status}",
                        {"provider": self.provider_name, "body": error_body[:500]},
                    )
                    return []

                data_raw = response.read().decode("utf-8")
                data = json.loads(data_raw)
                # OpenAI format returns a list of objects with an 'id' field in 'data'
                if "data" in data and isinstance(data["data"], list):
                    model_ids = []
                    for m in data["data"]:
                        if not isinstance(m, dict) or "id" not in m:
                            continue
                        m_id = m["id"]
                        model_ids.append(m_id)

                    log_info(
                        f"[{self.PROVIDER_NAME.upper()}] Models endpoint returned {len(model_ids)} models",
                        {"provider": self.provider_name, "path": api_path},
                    )
                    return sorted(model_ids)
                log_error(
                    f"[{self.PROVIDER_NAME.upper()}] Models response has no data list",
                    {"provider": self.provider_name, "path": api_path},
                )
                return []
            except Exception as e:
                log_error(f"[{self.PROVIDER_NAME.upper()}] Error parsing models response", {"error": str(e), "raw": data_raw[:500] if 'data_raw' in locals() else ""})
                return []
        except Exception as e:
            log_error(f"[{self.PROVIDER_NAME.upper()}] Error fetching models", {"error": str(e)})
            return []

    def _stream_request(self, messages: List[Dict], tools: Optional[List[Dict]] = None) -> Iterator[StreamResponse]:
        """Execute a streaming request to the OpenAI-compatible API."""
        from ..logger import log_info, log_error
        
        host, port, base_path = self._parse_url(self.base_url)
        # Combine base_path with config path
        api_path = base_path.rstrip("/") + self._get_path()

        log_info(f"[{self.provider_name.upper()}] Making HTTP request", {"path": api_path})
        
        headers = self._get_headers()
        payload = self._get_payload(messages, tools)
        
        response = self._make_https_request(
            host, port, "POST", api_path, headers, payload, timeout=60
        )
        return response

    def create_message(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        max_tokens: int = 4096,
        temperature: Optional[float] = None
    ) -> Iterator[str]:
        """Simple streaming text generation."""
        from ..logger import log_error
        headers = self._get_headers()
        
        formatted_messages = self._format_messages([{"role": "system", "content": system_prompt}] + messages)
        payload = {
            "model": self.model,
            "messages": formatted_messages,
            "max_tokens": max_tokens,
            "stream": True
        }
        if temperature is not None:
            payload["temperature"] = temperature
        
        self._apply_provider_payload_options(payload)
        
        host, port, api_path = self._build_api_path()
        
        response = self._make_https_request(
            host, port, "POST", api_path, headers, json.dumps(payload)
        )

        # Check HTTP status before trying to parse SSE stream
        self._raise_for_http_error(response, log_error)
        
        logged_json_decode = False
        try:
            for line in response:
                if not line:
                    continue

                line = line.decode("utf-8") if isinstance(line, bytes) else line
                line = line.strip()
                if not line:
                    continue

                if line.startswith("data: "):
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        if "choices" in data and len(data["choices"]) > 0:
                            delta = data["choices"][0].get("delta", {})
                            if "content" in delta and delta["content"]:
                                yield delta["content"]
                    except json.JSONDecodeError:
                        # HTML inside SSE JSON arguments (e.g. "<html" in tool args)
                        # is valid and should not be treated as transport HTML.
                        if self._looks_like_html_document(data_str):
                            log_error(f"[{self._provider_name.upper()}] HTML received in SSE payload", {
                                "chunk_preview": data_str[:1200]
                            })
                            from .base import HTMLResponseError
                            raise HTMLResponseError(
                                f"{self._provider_name} returned an HTML error page instead of JSON. "
                                "Likely proxy/CDN quota/auth blocking."
                            )
                        if not logged_json_decode:
                            log_error(f"[{self._provider_name.upper()}] Invalid JSON chunk in stream", {
                                "chunk_preview": data_str[:1200]
                            })
                            logged_json_decode = True
                        continue
                    continue

                if self._looks_like_html_document(line):
                    log_error(f"[{self._provider_name.upper()}] HTML received in stream", {
                        "line_preview": line[:1200]
                    })
                    from .base import HTMLResponseError
                    raise HTMLResponseError(
                        f"{self._provider_name} returned an HTML error page instead of JSON. "
                        "Likely proxy/CDN quota/auth blocking."
                    )
        finally:
            response.close()
    
    def create_message_with_tools(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        max_tokens: int = 4096,
        temperature: Optional[float] = None,
        on_text_chunk: Optional[callable] = None,
        on_cancel: Optional[callable] = None
    ) -> StreamResponse:
        """Streaming generation with tool calling."""
        headers = self._get_headers()
        
        all_messages = [{"role": "system", "content": system_prompt}] + messages
        formatted_messages = self._format_messages(all_messages)
        
        payload = {
            "model": self.model,
            "messages": formatted_messages,
            "max_tokens": max_tokens,
            "stream": True,
            "tools": tools,
            "tool_choice": "auto"
        }
        if temperature is not None:
            payload["temperature"] = temperature

        self._apply_provider_payload_options(payload)

        result = StreamResponse()
        tool_calls_data: Dict[int, Dict[str, Any]] = {}
        
        host, port, api_path = self._build_api_path()
        
        response = self._make_https_request(
            host, port, "POST", api_path, headers, json.dumps(payload), timeout=60
        )
        from ..logger import log_info, log_error
        log_info(f"[{self._provider_name.upper()}] Sent payload with messages", {
            "last_message": payload["messages"][-1] if len(payload["messages"]) > 0 else None,
            "assistant_message": payload["messages"][-2] if len(payload["messages"]) > 1 else None
        })

        # Streaming read timeout (independent from request timeout)
        # Prevents long hangs when a provider keeps the socket open without data.
        self._set_stream_timeout(response, 60.0)

        # Check HTTP status before trying to parse SSE stream
        self._raise_for_http_error(response, log_error, include_location=True)
        
        raw_preview = []
        logged_json_decode = False
        logged_non_sse = False
        try:
            for line in response:
                if on_cancel and on_cancel():
                    break

                if not line:
                    continue

                line = line.decode("utf-8") if isinstance(line, bytes) else line
                line = line.strip()
                if not line:
                    continue

                if len(raw_preview) < 8:
                    raw_preview.append(line[:500])

                if not line.startswith("data: "):
                    if self._looks_like_html_document(line):
                        log_error(f"[{self._provider_name.upper()}] HTML received in stream", {
                            "line_preview": line[:1200],
                            "stream_preview": "\n".join(raw_preview)
                        })
                        from .base import HTMLResponseError
                        raise HTMLResponseError(
                            f"{self._provider_name} returned an HTML error page instead of JSON. "
                            "Likely proxy/CDN quota/auth blocking."
                        )
                    if not logged_non_sse:
                        log_error(f"[{self._provider_name.upper()}] Non-SSE line received", {
                            "line_preview": line[:1200]
                        })
                        logged_non_sse = True
                    continue

                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    # If this chunk itself is HTML, transport likely got hijacked.
                    if self._looks_like_html_document(data_str):
                        log_error(f"[{self._provider_name.upper()}] HTML received in SSE payload", {
                            "chunk_preview": data_str[:1200],
                            "stream_preview": "\n".join(raw_preview)
                        })
                        from .base import HTMLResponseError
                        raise HTMLResponseError(
                            f"{self._provider_name} returned an HTML error page instead of JSON. "
                            "Likely proxy/CDN quota/auth blocking."
                        )
                    if not logged_json_decode:
                        log_error(f"[{self._provider_name.upper()}] Invalid JSON chunk in stream", {
                            "chunk_preview": data_str[:1200]
                        })
                        logged_json_decode = True
                    continue
                    
                if "choices" not in data or len(data["choices"]) == 0:
                    continue
                    
                choice = data["choices"][0]
                delta = choice.get("delta", {})
                    
                # Handle text content
                if "content" in delta and delta["content"]:
                    chunk = delta["content"]

                    # Some models (Kimi, Qwen, etc.) embed thinking inside <think>...</think>
                    # in the regular content stream. We need to strip them (or move to
                    # reasoning_content) depending on the show_thoughts setting.
                    import sublime
                    _settings = sublime.load_settings("Limitcode.sublime-settings")
                    _show_thoughts = _settings.get("show_thoughts", False)
                    
                    import re
                    think_parts = re.split(r'(<think>|</think>)', chunk, flags=re.IGNORECASE)
                    
                    if len(think_parts) == 1:
                        # No think tags — normal content, just track if we're inside a block
                        if not getattr(result, '_in_think', False):
                            result.content += chunk
                            if on_text_chunk:
                                on_text_chunk(chunk)
                        else:
                            # We're inside a think block; accumulate as reasoning
                            result.reasoning_content += chunk
                    else:
                        # Split chunk contains think markers
                        in_think = getattr(result, '_in_think', False)
                        for part in think_parts:
                            if part.lower() == '<think>':
                                in_think = True
                                continue
                            elif part.lower() == '</think>':
                                in_think = False
                                continue
                            if part:
                                if in_think:
                                    result.reasoning_content += part
                                else:
                                    result.content += part
                                    if on_text_chunk:
                                        on_text_chunk(part)
                        result._in_think = in_think
                            
                # Handle reasoning content (DeepSeek uses reasoning_content, Kimi uses reasoning)
                reasoning_chunk = delta.get("reasoning_content") or delta.get("reasoning")
                if reasoning_chunk:
                    result.reasoning_content += reasoning_chunk
                
                # Handle tool calls (streaming)
                if delta.get("tool_calls"):
                    for tc in delta["tool_calls"]:
                        idx = tc.get("index", 0)
                        
                        if idx not in tool_calls_data:
                            tool_calls_data[idx] = {
                                "id": tc.get("id", ""),
                                "name": "",
                                "arguments": ""
                            }
                        
                        if "id" in tc and tc["id"]:
                            tool_calls_data[idx]["id"] = tc["id"]
                        
                        if "function" in tc:
                            func = tc["function"]
                            if "name" in func and func["name"]:
                                tool_calls_data[idx]["name"] = func["name"]
                            if "arguments" in func:
                                tool_calls_data[idx]["arguments"] += func["arguments"]
                
                # Handle usage info
                if "usage" in data:
                    result.usage = data["usage"]
                
                # Check finish reason
                if "finish_reason" in choice and choice["finish_reason"]:
                    result.finish_reason = choice["finish_reason"]
        except TimeoutError:
            log_error(f"[{self._provider_name.upper()}] Stream timeout", {
                "stream_preview": "\n".join(raw_preview)
            })
            raise TimeoutError(
                f"{self._provider_name} stream timed out waiting for data. "
                "Try another model/provider or lower request complexity."
            )
        except OSError as e:
            if "timed out" in str(e).lower():
                log_error(f"[{self._provider_name.upper()}] Stream timeout (OSError)", {
                    "stream_preview": "\n".join(raw_preview)
                })
                raise TimeoutError(
                    f"{self._provider_name} stream timed out waiting for data. "
                    "Try another model/provider or lower request complexity."
                )
            raise
        finally:
            response.close()
        
        # Parse accumulated tool calls
        for idx in sorted(tool_calls_data.keys()):
            tc_data = tool_calls_data[idx]
            raw_args = tc_data["arguments"]
            try:
                args = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError:
                # Try to repair the JSON (common with large LLM code writes or truncation)
                try:
                    repaired_args = self._repair_json(raw_args)
                    args = json.loads(repaired_args) if repaired_args else {}
                except Exception as repair_err:
                    log_error(f"[{self._provider_name.upper()}] Failed to parse and repair tool call arguments", {
                        "tool": tc_data["name"],
                        "raw_arguments": raw_args[:2000],
                        "error": str(repair_err)
                    })
                    args = {}
            
            result.tool_calls.append(ToolCall(
                id=tc_data["id"],
                name=tc_data["name"],
                arguments=args
            ))
        
        return result

    def _repair_json(self, json_str: str) -> str:
        """Helper to repair raw newlines and auto-close unclosed JSON tags from LLM outputs."""
        json_str = json_str.strip()
        if not json_str:
            return "{}"
            
        chars = []
        in_string = False
        escape = False
        for char in json_str:
            if char == '"' and not escape:
                in_string = not in_string
                chars.append(char)
            elif in_string:
                if char == '\n':
                    chars.append('\\n')
                elif char == '\r':
                    chars.append('\\r')
                elif char == '\t':
                    chars.append('\\t')
                else:
                    if char == '\\':
                        escape = not escape
                    else:
                        escape = False
                    chars.append(char)
            else:
                escape = False
                chars.append(char)
                
        fixed_str = "".join(chars)
        
        try:
            json.loads(fixed_str)
            return fixed_str
        except json.JSONDecodeError:
            pass
            
        in_string = False
        escape = False
        stack = []
        
        for char in fixed_str:
            if char == '"' and not escape:
                in_string = not in_string
            elif in_string:
                if char == '\\':
                    escape = not escape
                else:
                    escape = False
            else:
                escape = False
                if char in ('{', '['):
                    stack.append(char)
                elif char in ('}', ']'):
                    if stack:
                        top = stack[-1]
                        if (char == '}' and top == '{') or (char == ']' and top == '['):
                            stack.pop()
                            
        closing = []
        if in_string:
            closing.append('"')
        for item in reversed(stack):
            if item == '{':
                closing.append('}')
            elif item == '[':
                closing.append(']')
                
        fixed_str += "".join(closing)
        return fixed_str
    
    def format_tool_result(self, tool_call_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
        """Format a tool result for the API."""
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps(result) if not isinstance(result, str) else result
        }
    
    def format_assistant_tool_calls(self, tool_calls: List[ToolCall]) -> Dict[str, Any]:
        """Format assistant message with tool calls."""
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments)
                    }
                }
                for tc in tool_calls
            ]
        }
    
    def _format_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Format message parts into OpenAI format (e.g. image -> image_url)."""
        formatted = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):

                new_content = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image":
                        mime = part.get("mime_type", "image/jpeg")
                        b64_data = part.get("data", "")
                        new_content.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime};base64,{b64_data}"
                            }
                        })
                    else:
                        new_content.append(part)
                
                formatted_msg = dict(msg)
                formatted_msg["content"] = new_content
                formatted.append(formatted_msg)
            else:
                formatted_msg = dict(msg)
                # Strip reasoning_content for providers that don't support it
                if "reasoning_content" in formatted_msg and self._provider_name not in ("openai", "deepseek"):
                    del formatted_msg["reasoning_content"]
                formatted.append(formatted_msg)
        return formatted

