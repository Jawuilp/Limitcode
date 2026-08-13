"""
Agent types shared across modules.
This module exists to prevent circular imports.
"""

from typing import Dict, List, Any, Optional


class AgentResponse:
    """Represents the final response from the agent."""
    def __init__(self):
        self.content: str = ""
        self.tool_calls_made: List[Dict[str, Any]] = []
        self.error: Optional[str] = None
        self.iterations: int = 0
        self.messages: List[Any] = []


class ToolCall:
    """Represents a tool call from the AI."""
    def __init__(self, id: str, name: str, arguments: Dict[str, Any], thought_signature: Optional[str] = None):
        self.id = id
        self.name = name
        self.arguments = arguments
        self.thought_signature = thought_signature


class StreamResponse:
    """Represents a streaming response from the provider."""
    def __init__(self):
        self.content: str = ""
        self.reasoning_content: str = ""
        self.tool_calls: List[ToolCall] = []
        self.has_tool_calls: bool = False
        self.finish_reason: Optional[str] = None
        self.usage: Optional[Dict[str, int]] = None
