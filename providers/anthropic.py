"""
Anthropic Provider for Limitcode.
Supports Claude 3/3.5/3.7/4 models with tool calling and streaming.
"""

import json
from typing import Dict, List, Optional, Iterator, Any

from .base import BaseProvider, ToolCall, StreamResponse


CLAUDE_MODELS = {
    "claude-sonnet-4-6": {"max_tokens": 8192, "context_window": 200000, "supports_images": True},
    "claude-sonnet-4-5": {"max_tokens": 8192, "context_window": 200000, "supports_images": True},
    "claude-opus-4-7": {"max_tokens": 8192, "context_window": 200000, "supports_images": True},
    "claude-opus-4-6": {"max_tokens": 8192, "context_window": 200000, "supports_images": True},
    "claude-haiku-4-5": {"max_tokens": 8192, "context_window": 200000, "supports_images": True},
    "claude-sonnet-4-20250514": {"max_tokens": 8192, "context_window": 200000, "supports_images": True},
    "claude-opus-4-20250514": {"max_tokens": 8192, "context_window": 200000, "supports_images": True},
    "claude-3-7-sonnet-20250219": {"max_tokens": 8192, "context_window": 200000, "supports_images": True},
    "claude-3-5-sonnet-20241022": {"max_tokens": 8192, "context_window": 200000, "supports_images": True},
    "claude-3-5-haiku-20241022": {"max_tokens": 8192, "context_window": 200000, "supports_images": True},
    "claude-3-opus-20240229": {"max_tokens": 4096, "context_window": 200000, "supports_images": True},
    "claude-3-sonnet-20240229": {"max_tokens": 4096, "context_window": 200000, "supports_images": True},
    "claude-3-haiku-20240307": {"max_tokens": 4096, "context_window": 200000, "supports_images": True},
}


class AnthropicProvider(BaseProvider):
    """Anthropic Claude provider using the Messages API."""
    
    PROVIDER_NAME = "anthropic"
    DEFAULT_BASE_URL = "api.anthropic.com"

    def __init__(self, api_key: str, model: str, base_url: str = None, extra_config: Dict = None):
        super().__init__(api_key, model, base_url or self.DEFAULT_BASE_URL, extra_config)
        self._model_info = CLAUDE_MODELS.get(model, CLAUDE_MODELS["claude-3-5-sonnet-20241022"])
        self._api_version = "2023-06-01"
    
    def _list_models_fallback(self) -> List[str]:
        """Fetch available Claude models from Anthropic API."""
        try:
            from ..lib.logger import log_info
            import json
            
            headers = self._get_headers()
            host, port, _ = self._parse_url(self.base_url)
            
            # Anthropic /v1/models endpoint
            response = self._make_https_request(
                host, port, "GET", "/v1/models", headers
            )
            
            if response.status != 200:
                return []
                
            data = json.loads(response.read().decode("utf-8"))
            if "data" in data:
                return sorted([m["id"] for m in data["data"]])
            return []
        except Exception:
            return []

    def _get_max_tokens(self) -> int:
        return self._model_info["max_tokens"]
    
    def supports_images(self) -> bool:
        return self._model_info["supports_images"]
    
    def _get_headers(self, include_beta: bool = False) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": self._api_version,
        }
        if include_beta:
            headers["anthropic-beta"] = "tools-2024-04-04,prompt-caching-2024-07-31"
        return headers

    # Models where the GA effort parameter is accepted; older/smaller models
    # reject it with a 400, so it is only sent when the model qualifies.
    _EFFORT_MODELS = ("opus-4-5", "opus-4-6", "opus-4-7", "sonnet-4-6")

    def _apply_reasoning_effort(self, payload):
        effort = self.get_reasoning_effort()
        if effort and any(m in self.model for m in self._EFFORT_MODELS):
            payload["output_config"] = {"effort": effort}

    def _raise_for_status(self, response):
        """Raise a clear error on non-200 responses (auth, rate limit, etc.).

        Without this, an error body (plain JSON, not SSE) would be silently
        skipped by the streaming loop and the user would see an empty reply.
        """
        if response.status == 200:
            return
        try:
            error_body = response.read().decode("utf-8", errors="replace")
        except Exception:
            error_body = ""
        finally:
            response.close()

        message = error_body[:300]
        try:
            error = json.loads(error_body).get("error", {})
            if error.get("message"):
                message = error["message"]
        except Exception:
            pass

        from .base import RateLimitError, CreditsError
        if response.status == 429:
            raise RateLimitError(f"Anthropic rate limit: {message}")
        if response.status in (401, 402, 403):
            raise CreditsError(f"Anthropic auth/credits error: {message}")
        raise Exception(f"Anthropic API error ({response.status}): {message}")


    def create_message(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        max_tokens: int = 4096,
        temperature: Optional[float] = None
    ) -> Iterator[str]:
        """Streaming text generation."""
        headers = self._get_headers(include_beta=True)
        
        payload = {
            "model": self.model,
            "system": system_prompt,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": True,
            "cache_control": {"type": "ephemeral"}
        }
        if temperature is not None:
            payload["temperature"] = temperature
        self._apply_reasoning_effort(payload)

        host, port, path = self._parse_url(self.base_url)
        response = self._make_https_request(
            host, port, "POST", "/v1/messages", headers, json.dumps(payload)
        )
        self._raise_for_status(response)

        try:
            for line in response:
                if line:
                    line = line.decode("utf-8") if isinstance(line, bytes) else line
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            event_type = data.get("type", "")
                            if event_type == "content_block_delta":
                                delta = data.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    yield delta.get("text", "")
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
        headers = self._get_headers(include_beta=True)
        
        # Convert tools to Anthropic format
        anthropic_tools = []
        for tool in tools:
            if tool.get("type") == "function":
                fn = tool["function"]
                anthropic_tools.append({
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}})
                })
        
        payload = {
            "model": self.model,
            "system": system_prompt,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": True,
            "tools": anthropic_tools,
            "cache_control": {"type": "ephemeral"}
        }
        if temperature is not None:
            payload["temperature"] = temperature
        self._apply_reasoning_effort(payload)


        result = StreamResponse()
        current_tool_call: Optional[Dict[str, Any]] = None
        
        host, port, path = self._parse_url(self.base_url)
        response = self._make_https_request(
            host, port, "POST", "/v1/messages", headers, json.dumps(payload)
        )
        self._raise_for_status(response)

        try:
            for line in response:
                if on_cancel and on_cancel():
                    break

                if line:
                    line = line.decode("utf-8") if isinstance(line, bytes) else line
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            event_type = data.get("type", "")
                            
                            if event_type == "content_block_start":
                                block = data.get("content_block", {})
                                block_type = block.get("type", "")
                                if block_type == "tool_use":
                                    current_tool_call = {
                                        "id": block.get("id", ""),
                                        "name": block.get("name", ""),
                                        "input": ""
                                    }

                            
                            elif event_type == "content_block_delta":
                                delta = data.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    text = delta.get("text", "")
                                    result.content += text
                                    if on_text_chunk:
                                        on_text_chunk(text)
                                elif delta.get("type") == "input_json_delta":
                                    if current_tool_call:
                                        current_tool_call["input"] += delta.get("partial_json", "")
                            
                            elif event_type == "content_block_stop":
                                if current_tool_call:
                                    try:
                                        args = json.loads(current_tool_call["input"]) if current_tool_call["input"] else {}
                                    except json.JSONDecodeError:
                                        args = {}
                                    result.tool_calls.append(ToolCall(
                                        id=current_tool_call["id"],
                                        name=current_tool_call["name"],
                                        arguments=args
                                    ))
                                    current_tool_call = None
                            
                            elif event_type == "message_delta":
                                stop_reason = data.get("delta", {}).get("stop_reason")
                                if stop_reason:
                                    result.finish_reason = stop_reason
                                usage = data.get("usage", {})
                                if usage:
                                    result.usage = usage
                            
                            elif event_type == "message_stop":
                                pass
                        except json.JSONDecodeError:
                            continue
        finally:
            response.close()
        
        return result
    
    def format_tool_result(self, tool_call_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
        """Format tool result for Anthropic Messages API."""
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": json.dumps(result) if not isinstance(result, str) else result
                }
            ]
        }
    
    def format_assistant_message(self, content: str, tool_calls: List[ToolCall]) -> Dict[str, Any]:
        """Format assistant message with tool use for Anthropic."""
        content_blocks = []
        
        if content:
            content_blocks.append({"type": "text", "text": content})
        
        for tc in tool_calls:
            content_blocks.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.arguments
            })
        
        return {"role": "assistant", "content": content_blocks}
    
    def format_user_tool_results(self, tool_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Format multiple tool results as a user message for Anthropic."""
        content = []
        for tr in tool_results:
            content.append({
                "type": "tool_result",
                "tool_use_id": tr.get("tool_use_id", ""),
                "content": tr.get("content", "")
            })
        return {"role": "user", "content": content}

