"""
Google Gemini Provider for Limitcode.
Supports Gemini 1.5/2/2.5 models with tool calling and streaming.
"""

import json
import uuid
from typing import Dict, List, Optional, Iterator, Any

from .base import BaseProvider, ToolCall, StreamResponse


GEMINI_MODELS = {
    "gemini-3-flash-preview": {"max_tokens": 65536, "context_window": 1000000, "supports_images": True},
    "gemini-3-pro-preview": {"max_tokens": 65536, "context_window": 1000000, "supports_images": True},
    "gemini-2.5-pro": {"max_tokens": 65536, "context_window": 1000000, "supports_images": True},
    "gemini-2.5-flash": {"max_tokens": 65536, "context_window": 1000000, "supports_images": True},
    "gemini-2.0-flash": {"max_tokens": 8192, "context_window": 1000000, "supports_images": True},
    "gemini-1.5-pro": {"max_tokens": 8192, "context_window": 2000000, "supports_images": True},
    "gemini-1.5-flash": {"max_tokens": 8192, "context_window": 1000000, "supports_images": True},
    "gemma-4-26b-a4b-it": {"max_tokens": 32768, "context_window": 262144, "supports_images": True},
}


class GeminiProvider(BaseProvider):
    """Google Gemini provider using the generateContent API with context caching."""
    
    PROVIDER_NAME = "gemini"
    DEFAULT_BASE_URL = "generativelanguage.googleapis.com"
    
    def __init__(self, api_key: str, model: str, base_url: str = None, extra_config: Dict = None):
        super().__init__(api_key, model, base_url, extra_config)
        self._model_info = GEMINI_MODELS.get(model, GEMINI_MODELS["gemini-2.5-flash"])
        self._cache_name = None  # cachedContents resource name for explicit caching
        self._cached_prompt = None  # track which system prompt was cached

    def supports_images(self) -> bool:
        return bool(self._model_info.get("supports_images", False))
    
    def _list_models_fallback(self) -> List[str]:
        """Fetch models from Google API."""
        import urllib.request
        from urllib.parse import urlencode

        try:
            models = set()
            page_token = None

            while True:
                params = {"pageSize": 100, "key": self.api_key}
                if page_token:
                    params["pageToken"] = page_token

                url = f"https://{self.base_url}/v1beta/models?{urlencode(params)}"
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=5) as response:
                    data = json.loads(response.read().decode())

                for model in data.get("models", []):
                    methods = model.get("supportedGenerationMethods", [])
                    model_id = model["name"].split("/")[-1]
                    if any("generateContent" in method for method in methods):
                        models.add(model_id)

                page_token = data.get("nextPageToken")
                if not page_token:
                    break

            if models:
                return sorted(models)
        except Exception:
            pass
        return sorted(GEMINI_MODELS.keys())
    
    def _create_cache(self, system_prompt: str) -> Optional[str]:
        """Create a cachedContent on Google servers for explicit context caching.

        Caches the system instruction so subsequent calls only send the
        cachedContent reference instead of the full system prompt.
        TTL defaults to 1 hour (3600s). Returns the cache resource name
        (e.g. 'cachedContents/abc123') or None on failure.
        """
        import urllib.request

        payload = {
            "model": f"models/{self.model}",
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "ttl": "3600s",
        }

        host, port, _ = self._parse_url(self.base_url)
        path = f"/v1beta/cachedContents?key={self.api_key}"

        try:
            response = self._make_https_request(
                host, port, "POST", path,
                {"Content-Type": "application/json"},
                json.dumps(payload)
            )
            data = json.loads(response.read().decode("utf-8"))
            response.close()
            cache_name = data.get("name")
            if cache_name:
                from ..lib.logger import log_info
                log_info(f"[GEMINI] Created explicit cache: {cache_name} (ttl=3600s)")
                return cache_name
        except Exception as e:
            from ..lib.logger import log_info
            log_info(f"[GEMINI] Cache creation failed, falling back to uncached: {e}")
        return None

    def _ensure_cache(self, system_prompt: str) -> None:
        """Create or reuse cache for the current system prompt.

        Only creates a new cache if one doesn't exist or the system prompt
        has changed between calls.
        """
        if self._cache_name is not None and self._cached_prompt == system_prompt:
            return  # cache still valid
        self._cache_name = self._create_cache(system_prompt)
        self._cached_prompt = system_prompt if self._cache_name else None

    # Token budgets per effort level for thinkingConfig (2.5+/3 models only;
    # older models reject the field).
    _THINKING_BUDGETS = {"low": 1024, "medium": 8192, "high": 24576}

    def _apply_reasoning_effort(self, payload):
        effort = self.get_reasoning_effort()
        if not effort:
            return
        model = self.model.lower()
        if "2.5" not in model and not model.startswith("gemini-3"):
            return
        payload["generationConfig"]["thinkingConfig"] = {
            "thinkingBudget": self._THINKING_BUDGETS[effort]
        }

    def create_message(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        max_tokens: int = 4096,
        temperature: Optional[float] = None
    ) -> Iterator[str]:
        """Simple streaming text generation."""
        from ..lib.logger import log_info
        
        gemini_messages = []
        for msg in messages:
            role = "model" if msg["role"] == "assistant" else "user"
            gemini_messages.append({"role": role, "parts": [{"text": msg["content"]}]})

        self._ensure_cache(system_prompt)

        payload_tools = []
        generation_config = {"maxOutputTokens": max_tokens}
        if temperature is not None:
            generation_config["temperature"] = temperature

        if self._cache_name:
            log_info("[GEMINI] Using explicit cached content", {"cache": self._cache_name})
            payload = {
                "contents": gemini_messages,
                "cachedContent": self._cache_name,
                "generationConfig": generation_config,
            }
        else:
            payload = {
                "contents": gemini_messages,
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "generationConfig": generation_config,
            }
        if payload_tools:
            payload["tools"] = payload_tools
        self._apply_reasoning_effort(payload)

        host, port, _ = self._parse_url(self.base_url)
        path = f"/v1beta/models/{self.model}:streamGenerateContent?alt=sse&key={self.api_key}"

        response = self._make_https_request(
            host, port, "POST", path,
            {"Content-Type": "application/json"},
            json.dumps(payload)
        )

        line_count = 0
        try:
            for line in response:
                if line:
                    line = line.decode("utf-8") if isinstance(line, bytes) else line
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        try:
                            data = json.loads(data_str)
                            candidates = data.get("candidates", [])
                            if candidates:
                                part = candidates[0].get("content", {}).get("parts", [{}])[0]
                                text = part.get("text", "")
                                if text and not part.get("thought", False):
                                    yield text
                                    line_count += 1
                        except json.JSONDecodeError:
                            continue
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
        """Streaming with tool calling."""
        from ..lib.logger import log_info, log_error
        
        log_info("[GEMINI] Starting create_message_with_tools")
        
        gemini_messages = []
        for msg in messages:
            # Handle structured content (multimodal parts from agent)
            if isinstance(msg.get("content"), list):
                role = "model" if msg["role"] == "assistant" else "user"
                gemini_parts = []
                for p in msg["content"]:
                    if p.get("type") == "text":
                        gemini_parts.append({"text": p["text"]})
                    elif p.get("type") == "image":
                        gemini_parts.append({
                            "inline_data": {
                                "mime_type": p["mime_type"],
                                "data": p["data"]
                            }
                        })
                gemini_messages.append({"role": role, "parts": gemini_parts})
            elif "parts" in msg:
                # Already in Gemini format (from tool loop)
                gemini_messages.append(msg)
            elif isinstance(msg.get("content"), str):
                role = "model" if msg["role"] == "assistant" else "user"
                gemini_messages.append({"role": role, "parts": [{"text": msg["content"]}]})
            else:
                role = "model" if msg["role"] == "assistant" else "user"
                gemini_messages.append({"role": role, "parts": [{"text": ""}]})
        
        gemini_tools = {"function_declarations": []}
        for tool in tools:
            if tool.get("type") == "function":
                fn = tool["function"]
                gemini_tools["function_declarations"].append({
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {"type": "object", "properties": {}})
                })

        # When custom function_declarations are present, use only those.
        # In pure-text mode (no function_declarations), enable grounding for
        # Gemini models only.
        if gemini_tools["function_declarations"]:
            payload_tools = [gemini_tools]
        else:
            payload_tools = []
        
        # Attempt explicit context caching — on first call creates the cache,
        # on subsequent calls reuses it (transparent to the agent).
        self._ensure_cache(system_prompt)
        generation_config = {"maxOutputTokens": max_tokens}
        if temperature is not None:
            generation_config["temperature"] = temperature

        if self._cache_name:
            log_info("[GEMINI] Using explicit cached content", {"cache": self._cache_name})
            payload = {
                "contents": gemini_messages,
                "cachedContent": self._cache_name,
                "generationConfig": generation_config,
            }
        else:
            payload = {
                "contents": gemini_messages,
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "generationConfig": generation_config,
            }
        if payload_tools:
            payload["tools"] = payload_tools
        self._apply_reasoning_effort(payload)

        result = StreamResponse()

        host, port, _ = self._parse_url(self.base_url)
        path = f"/v1beta/models/{self.model}:streamGenerateContent?alt=sse&key={self.api_key}"
        
        log_info("[GEMINI] Making HTTP request", {"path": self._mask_url(path)})
        
        try:
            response = self._make_https_request(
                host, port, "POST", path,
                {"Content-Type": "application/json"},
                json.dumps(payload)
            )
            
            if response.status != 200:
                error_body = response.read().decode('utf-8')
                log_info("[GEMINI] HTTP Error", {"status": response.status, "body": error_body})
                try:
                    error_json = json.loads(error_body)
                    if "error" in error_json:
                        msg = error_json["error"].get("message", "Unknown error")
                        raise Exception(f"Gemini API Error ({response.status}): {msg}")
                except json.JSONDecodeError:
                    pass
                raise Exception(f"Gemini HTTP Error {response.status}: {error_body}")
                
            log_info("[GEMINI] HTTP response received, starting stream parse")
            
            def process_gemini_line(line_str):
                if on_cancel and on_cancel():
                    return True  # signals _safe_stream_handler to stop

                # DEBUG: Log the first 100 chars of each line to see what Google is actually sending
                log_info(f"[GEMINI RAW] Line: {line_str[:100]}")
                
                if not line_str.startswith("data: "):
                    return False
                
                data_str = line_str[6:].strip()
                try:
                    data = json.loads(data_str)
                    candidates = data.get("candidates", [])
                    if not candidates:
                        return False
                        
                    candidate = candidates[0]
                    content = candidate.get("content", {})
                    parts = content.get("parts", [])
                    finish_reason = candidate.get("finishReason")
                    if finish_reason:
                        result.finish_reason = finish_reason
                    
                    for part in parts:
                        is_thought = bool(part.get("thought", False))
                        thought_text = part.get("text", "") if is_thought else ""

                        if is_thought:
                            if thought_text:
                                result.reasoning_content += thought_text
                        elif "text" in part:
                            text_chunk = part["text"]
                            if text_chunk:
                                result.content += text_chunk
                                if on_text_chunk:
                                    on_text_chunk(text_chunk)

                        # Handle tool calls
                        elif "functionCall" in part:
                            fc = part["functionCall"]
                            result.tool_calls.append(ToolCall(
                                id=fc.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                                name=fc.get("name", ""),
                                arguments=fc.get("args", {}),
                                thought_signature=part.get("thoughtSignature") or part.get("thought_signature")
                            ))
                    
                    if "usageMetadata" in data:
                        result.usage = data["usageMetadata"]
                    
                    # Stop if we have a finish reason
                    if finish_reason and finish_reason in ["STOP", "MAX_TOKENS", "SAFETY", "RECITATION", "OTHER"]:
                        log_info(f"[GEMINI] Finish reason detected: {finish_reason}")
                        return True
                        
                except json.JSONDecodeError:
                    pass
                return False

            self._safe_stream_handler(response, process_gemini_line)
        except Exception as e:
            log_error("[GEMINI] Request error", {"error": str(e)})
            raise
        finally:
            if 'response' in locals():
                response.close()
        
        log_info("[GEMINI] Stream complete", {
            "final_content_len": len(result.content),
            "tool_calls": len(result.tool_calls)
        })
        
        return result
    
    def format_tool_result(self, tool_call_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
        """Format tool result for Gemini."""
        return {
            "role": "user",
            "parts": [{
                "functionResponse": {
                    "name": tool_call_id,
                    "response": {"result": result}
                }
            }]
        }
    
    def format_assistant_tool_calls(self, tool_calls: List[ToolCall]) -> Dict[str, Any]:
        """Format assistant message with function calls."""
        parts = []
        if tool_calls:
            parts.append({"text": ""})
            for tc in tool_calls:
                part = {
                    "functionCall": {
                        "name": tc.name,
                        "args": json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments
                    }
                }
                thought_signature = getattr(tc, "thought_signature", None)
                if thought_signature:
                    part["thoughtSignature"] = thought_signature
                parts.append(part)
        return {"role": "model", "parts": parts}

