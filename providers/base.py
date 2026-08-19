"""
Base Provider for Limitcode.
Abstract base class that all providers must implement.
"""

import sublime
import socket
import threading
from typing import Dict, List, Optional, Iterator, Any

from ..lib.agent_types import ToolCall, StreamResponse

class RateLimitError(Exception):
    """Raised when the API returns a 429 Too Many Requests error."""
    pass

class CreditsError(Exception):
    """Raised when the API returns a 401/402 Auth or Insufficient Credits error."""
    pass

class HTMLResponseError(Exception):
    """Raised when the API returns an HTML page instead of JSON."""
    pass


class BaseProvider:
    """
    Abstract base class for all AI providers.
    
    Subclasses must implement:
    - create_message() for simple streaming
    - create_message_with_tools() for tool calling
    - count_tokens() for token estimation
    """
    
    # Provider metadata - override in subclasses
    PROVIDER_NAME = "base"
    DEFAULT_BASE_URL = ""
    DEFAULT_MODEL = ""
    
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: Optional[str] = None,
        extra_config: Optional[Dict[str, Any]] = None
    ):
        self.api_key = api_key
        self.model = model or self.DEFAULT_MODEL
        self.base_url = base_url or self.DEFAULT_BASE_URL
        self.is_secure = not (self.base_url and self.base_url.strip().lower().startswith("http://"))
        self.extra_config = extra_config or {}
        self.settings = sublime.load_settings("Limitcode.sublime-settings")
        self._active_request_lock = threading.Lock()
        self._active_connection = None
        self._active_response = None

    def get_reasoning_effort(self) -> Optional[str]:
        """User-selected reasoning effort level ("low"/"medium"/"high"),
        or None when off/unset. Each provider maps it to its own API knob."""
        try:
            effort = self.settings.get("reasoning_effort", "off")
        except Exception:
            return None
        effort = str(effort or "off").lower()
        return effort if effort in ("low", "medium", "high") else None

    @staticmethod
    def _mask_url(url: str) -> str:
        """Strip sensitive query params from a URL/path before logging."""
        import re
        return re.sub(
            r'([?&])(key|api_key|api-key|access_token|token|secret|client_secret)=([^&]*)',
            r'\1\2=***',
            url,
            flags=re.IGNORECASE
        )

    def list_models(self) -> List[str]:
        """Return a list of available models for this provider, trying live endpoint first, then database."""
        provider_id = self.extra_config.get("provider_name", self.PROVIDER_NAME)
        local_providers = {"ollama", "lm-studio"}

        # 1. Try live fallback first
        try:
            models = self._list_models_fallback()
            if models:
                return models
        except Exception:
            pass
            
        # 2. If it's a local provider and live fallback failed/was empty, DO NOT fall back to models.dev!
        # If the local server is down or has no models, we return an empty list.
        if provider_id in local_providers:
            return []

        return []

    def _list_models_fallback(self) -> List[str]:
        """Subclass fallback list_models implementation."""
        return []
    
    def create_message(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        max_tokens: int = 4096,
        temperature: Optional[float] = None
    ) -> Iterator[str]:
        """
        Simple streaming text generation.
        
        Yields text chunks as they arrive from the API.
        """
        raise NotImplementedError("Subclasses must implement create_message")
    
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
        """
        Streaming generation with tool calling support.
        
        Returns a StreamResponse containing text content and/or tool calls.
        Calls on_text_chunk for each text chunk during streaming.
        """
        raise NotImplementedError("Subclasses must implement create_message_with_tools")
    
    def count_tokens(self, text: str) -> int:
        """
        Estimate token count for text.
        
        Returns rough estimation (~4 chars per token for English).
        Subclasses can override with more accurate counting.
        """
        return int(len(text) / 4)
    
    def supports_streaming(self) -> bool:
        """Whether this provider supports streaming."""
        return True
    
    def supports_tools(self) -> bool:
        """Whether this provider supports tool calling."""
        return True
    
    def supports_images(self) -> bool:
        """Whether this provider supports image input."""
        return False
    
    def get_model_info(self) -> Dict[str, Any]:
        """Get information about the current model."""
        return {
            "id": self.model,
            "provider": self.PROVIDER_NAME,
            "max_tokens": self._get_max_tokens(),
            "supports_images": self.supports_images(),
            "supports_tools": self.supports_tools(),
            "supports_streaming": self.supports_streaming(),
        }
    
    def _get_max_tokens(self) -> int:
        """Get max output tokens for the current model."""
        return 4096

    
    def _make_https_request(
        self,
        host: str,
        port: int,
        method: str,
        path: str,
        headers: Dict[str, str],
        body: Optional[str] = None,
        timeout: int = 120
    ):
        """Make an HTTP or HTTPS request and return the response."""
        import http.client
        if getattr(self, "is_secure", True):
            conn = http.client.HTTPSConnection(host, port, timeout=timeout)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)
        with self._active_request_lock:
            self._active_connection = conn
            self._active_response = None
        try:
            if isinstance(body, str):
                body = body.encode("utf-8")
            conn.request(method, path, body, headers)
            response = conn.getresponse()
            with self._active_request_lock:
                if self._active_connection is conn:
                    self._active_response = response
            return response
        except Exception:
            conn.close()
            self._clear_active_request(connection=conn)
            raise

    def _clear_active_request(self, response=None, connection=None):
        """Forget a completed request without clearing a newer one."""
        with self._active_request_lock:
            if response is not None and self._active_response is not response:
                return
            if connection is not None and self._active_connection is not connection:
                return
            self._active_response = None
            self._active_connection = None

    def cancel_active_request(self):
        """Close the active socket so a blocked streaming read wakes immediately."""
        with self._active_request_lock:
            response = self._active_response
            connection = self._active_connection
            self._active_response = None
            self._active_connection = None

        sock = getattr(connection, "sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    def _safe_stream_handler(self, response, chunk_handler_fn):
        """
        Reads a stream line by line and allows the handler to signal completion.
        Ensures the connection is closed immediately upon completion or error.
        """
        from ..lib.logger import log_info
        try:
            # Set a shorter timeout for the socket during streaming 
            # so we don't hang if the server stops sending but doesn't close.
            if hasattr(response, 'fp') and hasattr(response.fp, 'raw') and hasattr(response.fp.raw, '_sock'):
                response.fp.raw._sock.settimeout(60.0) # 60 seconds of silence = done
            
            while True:
                try:
                    line = response.readline()
                except Exception as e:
                    log_info(f"[{self.PROVIDER_NAME}] Stream read timeout or error: {str(e)}")
                    break

                if not line:
                    break
                line_str = line.decode("utf-8").strip() if isinstance(line, bytes) else line.strip()
                if not line_str:
                    continue
                if chunk_handler_fn(line_str):
                    log_info(f"[{self.PROVIDER_NAME}] Stream interrupted by handler")
                    break
        finally:
            response.close()
            log_info(f"[{self.PROVIDER_NAME}] Stream connection closed")
    
    def _parse_url(self, url: str) -> tuple:
        """
        Parse a URL into (host, port, path).
        
        Handles URLs with or without protocol prefix.
        """
        url = url.strip()
        is_https = not url.lower().startswith("http://")
        
        # Remove protocol
        if url.startswith("https://"):
            url = url[8:]
        elif url.startswith("http://"):
            url = url[7:]
        
        # Split host:port from path
        if "/" in url:
            host_port, path = url.split("/", 1)
            path = "/" + path
        else:
            host_port = url
            path = "/"
        
        # Split host and port
        if ":" in host_port:
            host, port_str = host_port.split(":", 1)
            port = int(port_str)
        else:
            host = host_port
            port = 443 if is_https else 80
        
        return host, port, path
