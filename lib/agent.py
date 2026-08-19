"""
Agent module for Limitcode.
Agentic loop with tool calling support.

Flow:
1. User sends message
2. Model receives message + tool schemas
3. If model requests tools → execute → feed results back → repeat
4. When model responds with text only → return to user
"""

import sublime
import os
import re
import json
import platform
from typing import List, Dict, Any, Optional, Callable

from .agent_types import AgentResponse
from ..providers.base import BaseProvider
from ..tools import ToolManager
from ..tools.tool_schemas import get_tools_for_provider, get_required_args_for_tool
from ..prompts.manager import PromptManager


def log_info(message: str, metadata: dict = None):
    """Simple file logger."""
    from .logger import log_info as _log
    _log(message, metadata)


def log_error(message: str, metadata: dict = None):
    """Simple file logger."""
    from .logger import log_error as _log
    _log(message, metadata)


def _detect_image_mime_type(file_path: str) -> Optional[str]:
    """Return the MIME type when a file has a supported image signature."""
    try:
        with open(file_path, "rb") as image_file:
            header = image_file.read(12)
    except OSError:
        return None

    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    return None


class Agent:
    """
    Agentic coding assistant with tool-calling loop.

    The agent sends the user's message to the LLM along with tool schemas.
    If the model responds with tool calls, the agent executes them,
    feeds results back, and loops until the model produces a final text response.
    """

    def __init__(
        self,
        provider: BaseProvider,
        provider_type: str,
        tool_manager: ToolManager,
        system_prompt: str,
        on_text_chunk=None,
        on_tool_call=None,
        on_tool_result=None,
        on_status=None,
        max_iterations: int = 50,
        disabled_tool_names: Optional[List[str]] = None
    ):
        self.provider = provider
        self.provider_type = provider_type
        self.tool_manager = tool_manager
        self.custom_system_prompt = system_prompt or ""
        self.on_text_chunk = on_text_chunk
        self.on_tool_call = on_tool_call
        self.on_tool_result = on_tool_result
        self.on_status = on_status
        self.max_iterations = max_iterations
        self.is_cancelled = False
        self.disabled_tool_names = set(disabled_tool_names or [])
        self._active_child_agent = None
        self.window = None

        self.current_step = 0
        self.prompt_manager = PromptManager()

    def _json_safe(self, value: Any) -> Any:
        """Return a JSON-serializable copy of tool args/results."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(k): self._json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._json_safe(v) for v in value]
        return str(value)

    def _status(self, message: str):
        """Update status bar."""
        if self.on_status:
            self.on_status(message)

    def _parse_xml_tool_calls(self, text: str) -> list:
        """
        Parse XML-style tool calls from model text output.
        
        Some models (Nemotron, GLM, etc.) don't support native tool calling
        and instead emit tool calls as XML in their text response:
        
        <tool_call>
        <function=read_file>
        <parameter=file_path>some/path</parameter>
        </function>
        </tool_call>
        """
        from .agent_types import ToolCall
        
        tool_calls = []
        # Find all <tool_call>...</tool_call> blocks
        blocks = re.findall(r'<tool_call>(.*?)</tool_call>', text, re.DOTALL)
        
        for i, block in enumerate(blocks):
            # Extract function name: <function=NAME>
            func_match = re.search(r'<function=(\w+)>', block)
            if not func_match:
                continue
            
            func_name = func_match.group(1)
            
            # Extract parameters: <parameter=KEY>VALUE</parameter>
            params = {}
            for param_match in re.finditer(
                r'<parameter=(\w+)>(.*?)</parameter>', block, re.DOTALL
            ):
                key = param_match.group(1)
                value = param_match.group(2).strip()
                # Try to parse as JSON for booleans/numbers
                try:
                    value = json.loads(value)
                except (json.JSONDecodeError, ValueError):
                    pass
                params[key] = value
            
            tool_calls.append(ToolCall(
                id=f"xml_tc_{i}",
                name=func_name,
                arguments=params
            ))
        
        if tool_calls:
            log_info(f"[AGENT] Parsed {len(tool_calls)} XML tool call(s) from text", {
                "tools": [tc.name for tc in tool_calls]
            })
        
        return tool_calls


    def _resolve_path(self, path: str, directory: str) -> str:
        """Resolve a file path relative to the working directory."""
        if not path:
            return path
        if os.path.isabs(path):
            return path
        return os.path.normpath(os.path.join(directory, path))

    def _execute_tool(
        self,
        tool_name: str,
        arguments: Any,
        directory: str,
        finish_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute a single tool call."""
        try:
            normalized_finish_reason = str(finish_reason or "").lower()
            if (
                tool_name in ("write_to_file", "edit_file")
                and normalized_finish_reason in ("length", "max_tokens", "max_output_tokens")
            ):
                return {
                    "success": False,
                    "truncated": True,
                    "error": (
                        "The file was not modified because the model response reached its "
                        "output token limit. Re-read the open file and retry with smaller "
                        "edit_file changes."
                    ),
                }

            # Parse arguments if a compatible provider returns a JSON string.
            resolved_args = arguments
            if isinstance(resolved_args, str):
                try:
                    resolved_args = json.loads(resolved_args)
                except Exception as e:
                    return {"success": False, "error": f"Failed to parse tool arguments: {str(e)}"}

            if not isinstance(resolved_args, dict):
                resolved_args = {}

            required_args = get_required_args_for_tool(tool_name)
            missing_args = [
                arg for arg in required_args
                if arg not in resolved_args
                or resolved_args[arg] is None
                or (isinstance(resolved_args[arg], str) and not resolved_args[arg].strip())
            ]
            if missing_args:
                return {
                    "success": False,
                    "error": f"Missing required arguments for {tool_name}: {', '.join(missing_args)}"
                }

            # Resolve file paths in arguments
            path_args = ["file_path", "directory", "cwd"]
            
            for arg_name in path_args:
                if arg_name in resolved_args and resolved_args[arg_name]:
                    resolved_args[arg_name] = self._resolve_path(resolved_args[arg_name], directory)
                    
            # Never execute a tool after cancellation.
            if self.is_cancelled:
                return {
                    "success": False,
                    "error": f"Cancelled before executing {tool_name}"
                }

            # Execute the tool
            result = self.tool_manager.execute_tool(tool_name, **resolved_args)

            return result
        except Exception as e:
            return {"success": False, "error": str(e)}


    def cancel(self):
        """Cancel this agent."""
        self.is_cancelled = True
        cancel_request = getattr(self.provider, "cancel_active_request", None)
        if cancel_request:
            try:
                cancel_request()
            except Exception as e:
                log_info("[AGENT] Error closing cancelled provider request", {"error": str(e)})

    # ---- Provider-specific message formatting ----

    def _format_assistant_message(self, response) -> Dict[str, Any]:
        """Format the assistant's response (with tool calls) for the API conversation.
        
        IMPORTANT — reasoning_content handling for thinking models:
        
        Some reasoning models require that the assistant
        message includes a `reasoning_content` field when tool_calls are present.
        Without it, the API returns: "thinking is enabled but reasoning_content is missing".
        
        However, reasoning_content must ONLY be included in tool_call messages (this function).
        It must NOT be included in final response messages (no tool_calls). If reasoning is
        sent back in final messages, the model re-reads its own thoughts as conversation
        history and enters an infinite reasoning loop.
        
        Rule:
          - assistant message WITH tool_calls  → include reasoning_content (API requirement)
          - assistant message WITHOUT tool_calls (final) → DO NOT include reasoning_content
        """
        if self.provider_type == "anthropic":
            assistant_msg = self.provider.format_assistant_message(response.content, response.tool_calls)
            return assistant_msg

        elif self.provider_type == "gemini":
            msg = self.provider.format_assistant_tool_calls(response.tool_calls)
            # Gemini format uses "parts", add text if present
            if response.content and "parts" in msg:
                msg["parts"].insert(0, {"text": response.content})
            return msg

        else:
            # OpenAI-compatible format
            msg = {
                "role": "assistant",
                "content": response.content or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(self._json_safe(tc.arguments))
                            }
                        }
                    for tc in response.tool_calls
                ]
            }
            # Include reasoning_content for thinking models that require it in tool_call turns.
            # See docstring above for the full explanation.
            if getattr(response, "reasoning_content", None):
                msg["reasoning_content"] = response.reasoning_content
            elif "kimi" in getattr(self.provider, "model", "").lower():
                msg["reasoning_content"] = ""
                
            return msg

    def _format_tool_results(self, tool_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Format tool results as API messages for the conversation."""
        if self.provider_type == "anthropic":
            # Anthropic batches all tool results in one user message
            content = []
            for tr in tool_results:
                safe_result = self._json_safe(tr["result"])
                result_str = json.dumps(safe_result) if not isinstance(safe_result, str) else safe_result
                content.append({
                    "type": "tool_result",
                    "tool_use_id": tr["tool_call_id"],
                    "content": result_str
                })
            return [{"role": "user", "content": content}]

        elif self.provider_type == "gemini":
            # Gemini uses functionResponse
            parts = []
            for tr in tool_results:
                parts.append({
                    "functionResponse": {
                        "name": tr["tool_name"],
                        "response": {"result": self._json_safe(tr["result"])}
                    }
                })
            return [{"role": "user", "parts": parts}]

        else:
            # OpenAI-compatible APIs use one message per tool result.
            messages = []
            for tr in tool_results:
                safe_result = self._json_safe(tr["result"])
                result_str = json.dumps(safe_result) if not isinstance(safe_result, str) else safe_result
                messages.append({
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "content": result_str
                })
            return messages

    # ---- Main agentic loop ----

    def _process_multimodal_content(self, text: str) -> List[Dict[str, Any]]:
        """
        Scans text for [Attached Image: path] tags and returns a standardized list of 
        content parts (text and images) for all providers.
        """
        import re
        import base64
        
        parts = []
        image_pattern = r'\[Attached Image: (.*?)\]'
        last_end = 0
        
        for match in re.finditer(image_pattern, text):
            # Add text part before the image
            text_part = text[last_end:match.start()].strip()
            if text_part:
                parts.append({"type": "text", "text": text_part})
            
            # Add image part
            img_path = match.group(1).strip()
            if os.path.isfile(img_path):
                try:
                    mime_type = _detect_image_mime_type(img_path)
                    if not mime_type:
                        parts.append({
                            "type": "text",
                            "text": f"[Unsupported image file: {img_path}]",
                        })
                        last_end = match.end()
                        continue
                    with open(img_path, "rb") as f:
                        img_data = base64.b64encode(f.read()).decode("utf-8")
                    parts.append({
                        "type": "image",
                        "mime_type": mime_type,
                        "data": img_data,
                        "path": img_path
                    })
                except Exception as e:
                    parts.append({"type": "text", "text": f"[Error loading image {img_path}: {str(e)}]"})
            else:
                parts.append({"type": "text", "text": f"[Image not found: {img_path}]"})
            
            last_end = match.end()
            
        # Add remaining text
        remaining_text = text[last_end:].strip()
        if remaining_text:
            parts.append({"type": "text", "text": remaining_text})
            
        if not parts and text:
            parts = [{"type": "text", "text": text}]
            
        return parts

    def _strip_unsupported_images_from_history(self, messages: List[Dict[str, Any]]) -> int:
        """Replace historical image parts with text placeholders for text-only models."""
        image_types = {"image", "image_url", "input_image"}
        removed = 0

        for msg in messages:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue

            new_content = []
            for part in content:
                if isinstance(part, dict) and part.get("type") in image_types:
                    img_path = part.get("path") or "image"
                    new_content.append({
                        "type": "text",
                        "text": f"[Attached Image: {img_path} (not supported by this model)]"
                    })
                    removed += 1
                else:
                    new_content.append(part)
            msg["content"] = new_content

        return removed

    def run(
        self,
        user_message: str,
        context: Optional[str] = None,
        directory: Optional[str] = None,
        window: Optional[sublime.Window] = None,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 8192,
        temperature: Optional[float] = None,
        on_iteration_complete: Optional[Callable[[], None]] = None
    ) -> AgentResponse:
        """
        Run the agentic loop with tool calling.
        """
        self.window = window
        log_info("[AGENT] run() started", {
            "message": user_message[:80],
            "provider": self.provider.__class__.__name__,
            "model": getattr(self.provider, 'model', 'unknown'),
            "provider_id": getattr(self.provider, 'provider_id', getattr(self.provider, 'provider_name', 'unknown')),
        })

        result = AgentResponse()
        actual_dir = directory or os.getcwd()

        # Build full message with context
        full_message = user_message
        if context:
            full_message = f"File content:\n```\n{context}\n```\n\n{user_message}"

        # Compose final system prompt
        custom_block = self.custom_system_prompt
        if context:
            custom_block += f"\n\n## Current Selection/Context\n```\n{context}\n```"

        # Detect OS and shell dynamically
        os_name = platform.system()  # "Windows", "Linux", "Darwin"
        if os_name == "Windows":
            shell_name = "PowerShell"
        elif os_name == "Darwin":
            shell_name = "zsh"
        else:
            shell_name = "bash"

        try:
            model_name = getattr(self.provider, "model", None)
            from ..tools.base import get_open_files_paths
            open_files = get_open_files_paths()
            open_files_str = ", ".join(open_files) if open_files else "None"
            system_prompt = self.prompt_manager.get_system_prompt(
                os_name=os_name,
                shell_name=shell_name,
                project_name=os.path.basename(actual_dir),
                directory=actual_dir,
                model_name=model_name,
                custom_instructions=custom_block,
                open_files_paths=open_files_str
            )
        except Exception as e:
            log_info("[AGENT] Prompt generation error, using fallback", {"error": str(e)})
            system_prompt = f"You are an expert coding assistant. Working directory: {actual_dir}. OS: {os_name}, Shell: {shell_name}"


        # Get tool schemas for this provider
        tool_schemas = get_tools_for_provider(self.provider_type)
        if self.disabled_tool_names:
            def schema_name(schema):
                return schema.get("function", {}).get("name") or schema.get("name")

            tool_schemas = [
                schema for schema in tool_schemas
                if schema_name(schema) not in self.disabled_tool_names
            ]

        # Initialize API message history with previous conversation
        raw_api_messages = list(conversation_history) if conversation_history else []
        
        # Normalize legacy response items into standard chat messages.
        api_messages = []
        for msg in raw_api_messages:
            if not isinstance(msg, dict):
                api_messages.append(msg)
                continue
                
            role = msg.get("role")
            content = msg.get("content")
            
            if role == "assistant":
                new_msg = dict(msg)
                text_parts = []
                tool_calls = new_msg.get("tool_calls") or []
                if not isinstance(tool_calls, list):
                    tool_calls = list(tool_calls)
                else:
                    tool_calls = list(tool_calls)
                    
                if isinstance(content, list):
                    # Convert response content parts to standard chat format.
                    for part in content:
                        if not isinstance(part, dict):
                            continue
                        part_type = part.get("type")
                        if part_type in ("text", "output_text"):
                            text_parts.append(part.get("text", ""))
                        elif part_type == "function_call":
                            args_raw = part.get("arguments", "{}")
                            try:
                                if isinstance(args_raw, str):
                                    json.loads(args_raw)
                                else:
                                    args_raw = json.dumps(args_raw)
                            except Exception:
                                args_raw = "{}"
                            
                            tool_calls.append({
                                "id": part.get("call_id") or part.get("id") or f"call_{len(tool_calls)}",
                                "type": "function",
                                "function": {
                                    "name": part.get("name", ""),
                                    "arguments": args_raw
                                }
                            })
                    new_msg["content"] = "\n".join(text_parts).strip() or None
                else:
                    new_msg["content"] = content
                
                if tool_calls:
                    clean_tool_calls = []
                    for tc in tool_calls:
                        if not isinstance(tc, dict):
                            continue
                        func = tc.get("function", {})
                        args = func.get("arguments", "{}")
                        if isinstance(args, dict):
                            args = json.dumps(args)
                        clean_tool_calls.append({
                            "id": tc.get("id") or f"call_{len(clean_tool_calls)}",
                            "type": "function",
                            "function": {
                                "name": func.get("name", ""),
                                "arguments": args
                            }
                        })
                    new_msg["tool_calls"] = clean_tool_calls
                else:
                    new_msg.pop("tool_calls", None)
                api_messages.append(new_msg)
                
            elif role in ("user", "system") and isinstance(content, list):
                new_msg = dict(msg)
                new_content = []
                for part in content:
                    if isinstance(part, dict):
                        part_type = part.get("type")
                        if part_type in ("text", "input_text"):
                            new_content.append({
                                "type": "text",
                                "text": part.get("text", "")
                            })
                        else:
                            new_content.append(part)
                    else:
                        new_content.append(part)
                new_msg["content"] = new_content
                api_messages.append(new_msg)
            else:
                api_messages.append(msg)
        
        # Process multimodal content (images)
        content_parts = self._process_multimodal_content(full_message)
        
        # Check if model supports images (multimodal)
        supports_multimodal = False
        try:
            supports_multimodal = getattr(self.provider, "supports_images", lambda: False)()
        except Exception:
            pass
            
        if not supports_multimodal:
            stripped_count = self._strip_unsupported_images_from_history(api_messages)
            if stripped_count:
                log_info("[AGENT] Stripped unsupported image parts from history", {
                    "count": stripped_count,
                    "provider": self.provider_type,
                    "model": getattr(self.provider, "model", "unknown"),
                })

            filtered_parts = []
            for part in content_parts:
                if part.get("type") == "image":
                    img_path = part.get("path", "image")
                    filtered_parts.append({"type": "text", "text": f"[Attached Image: {img_path} (not supported by this model)]"})
                else:
                    filtered_parts.append(part)
            content_parts = filtered_parts
        
        # Avoid consecutive same-role messages (can happen after loading history)
        if api_messages and api_messages[-1].get("role") == "user":
            last_msg = api_messages[-1]
            if "content" in last_msg:
                if isinstance(last_msg["content"], str):
                    last_msg["content"] = [{"type": "text", "text": last_msg["content"]}]
                
                if isinstance(last_msg["content"], list):
                    last_msg["content"].extend([{"type": "text", "text": "\n\n---\n\n"}] + content_parts)
            else:
                api_messages.append({"role": "user", "content": content_parts})
        else:
            api_messages.append({"role": "user", "content": content_parts})


        log_info("[AGENT] Starting agentic loop", {
            "provider": self.provider_type,
            "tools_count": len(tool_schemas),
            "directory": actual_dir
        })

        all_content = ""
        all_reasoning = ""
        reasoning_header_shown = False
        displayed_reasoning_chars = 0
        max_displayed_reasoning_chars = 4000
        last_tool_key = None  # Track duplicate tool calls
        duplicate_count = 0
        empty_final_retries = 0
        max_empty_final_retries = 2
        force_no_tools_next_turn = False

        try:
            for iteration in range(self.max_iterations):
                if self.is_cancelled:
                    log_info("[AGENT] Execution cancelled by user")
                    all_content += "\n\n*[Execution cancelled by user]*"
                    result.content = all_content
                    result.iterations = self.current_step
                    
                    assistant_msg = {"role": "assistant", "content": all_content}
                    if all_reasoning:
                        assistant_msg["reasoning_content"] = all_reasoning
                    api_messages.append(assistant_msg)
                    
                    result.messages = api_messages
                    break
                    
                self.current_step = iteration + 1
                self._status(f"Thinking... (step {self.current_step})")

                log_info(f"[AGENT] Iteration {self.current_step}", {
                    "messages_count": len(api_messages)
                })

                # Inject step awareness when model is using many iterations
                if iteration >= self.max_iterations - 3:
                    step_hint = {"role": "user", "content": f"[SYSTEM: You are on step {self.current_step} of {self.max_iterations}. Wrap up your analysis and provide your final answer soon. Do NOT call any more tools. Summarize what you have found so far.]"}
                    current_messages = api_messages + [step_hint]
                else:
                    current_messages = api_messages

                # Call provider with tools
                # In the last iterations (or when recovering from empty finals),
                # don't pass tools to force a user-facing text response.
                if iteration >= self.max_iterations - 1 or force_no_tools_next_turn:
                    force_no_tools_next_turn = False
                    response = self.provider.create_message_with_tools(
                        system_prompt=system_prompt,
                        messages=current_messages,
                        tools=[],  # No tools — force text response
                        max_tokens=max_tokens,
                        temperature=temperature,
                        on_text_chunk=self.on_text_chunk,
                        on_cancel=lambda: self.is_cancelled
                    )
                else:
                    response = self.provider.create_message_with_tools(
                        system_prompt=system_prompt,
                        messages=current_messages,
                        tools=tool_schemas,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        on_text_chunk=self.on_text_chunk,
                        on_cancel=lambda: self.is_cancelled
                    )

                # --- Thinking content cleanup ---
                # The provider-level parser (e.g. openai_compatible.py) already splits
                # <think>...</think> blocks into reasoning_content during streaming.
                # This is a universal safety net to catch any residual <think> tags
                # that may slip through (e.g. non-streaming paths, custom endpoints,
                # or models that mix thinking with tool_call content).
                # We clean from .content only — never touch reasoning_content or tool_calls.
                if response.content and '<think>' in response.content.lower():
                    import re as _re
                    # Extract any leftover think blocks and move them to reasoning_content
                    think_matches = _re.findall(
                        r'<think>(.*?)</think>', response.content, flags=_re.DOTALL | _re.IGNORECASE
                    )
                    for think_text in think_matches:
                        response.reasoning_content += think_text.strip()
                    # Remove all <think>...</think> blocks from visible content
                    response.content = _re.sub(
                        r'<think>.*?</think>\s*',
                        '', response.content, flags=_re.DOTALL | _re.IGNORECASE
                    ).strip()
                # Also clean up unclosed <think> blocks (model was cut off mid-thought)
                if response.content and response.content.lower().count('<think>') > response.content.lower().count('</think>'):
                    import re as _re
                    unclosed = _re.search(r'<think>(.*?)$', response.content, flags=_re.DOTALL | _re.IGNORECASE)
                    if unclosed:
                        response.reasoning_content += unclosed.group(1).strip()
                        response.content = _re.sub(
                            r'<think>.*?$', '', response.content, flags=_re.DOTALL | _re.IGNORECASE
                        ).strip()

                # If the model emitted tool calls as XML text instead of native tool calling,
                # parse them from the text content
                if response.content and not response.tool_calls:
                    xml_tools = self._parse_xml_tool_calls(response.content)
                    if xml_tools:
                        response.tool_calls = xml_tools
                        response.has_tool_calls = True
                        # Remove the XML from the text shown to the user
                        response.content = re.sub(
                            r'<tool_call>.*?</tool_call>',
                            '', response.content, flags=re.DOTALL
                        ).strip()
                        if not response.content:
                            response.content = ""

                # Accumulate text content
                if response.content:
                    all_content += response.content
                # Accumulate reasoning content (DeepSeek R1 support)
                if response.reasoning_content:
                    all_reasoning += response.reasoning_content
                    # Check if user wants to see thoughts
                    import sublime
                    settings = sublime.load_settings("Limitcode.sublime-settings")
                    if settings.get("show_thoughts", False):
                        # Debug-only display. Keep it visually separate from the final answer.
                        reasoning_chunk = response.reasoning_content.strip()
                        remaining = max_displayed_reasoning_chars - displayed_reasoning_chars
                        if self.on_text_chunk and reasoning_chunk and remaining > 0:
                            if len(reasoning_chunk) > remaining:
                                reasoning_chunk = reasoning_chunk[:remaining].rstrip() + "..."
                            quoted = "\n".join(
                                f"> {line}" if line else ">"
                                for line in reasoning_chunk.splitlines()
                            )
                            prefix = "\n\n---\n\n> **Razonamiento**\n" if not reasoning_header_shown else "\n"
                            self.on_text_chunk(f"{prefix}{quoted}\n")
                            reasoning_header_shown = True
                            displayed_reasoning_chars += len(reasoning_chunk)

                # If no tool calls, model gave a final turn.
                # Some models return an empty final turn after tools (silent failure).
                # In that case, retry a couple of times forcing plain text.
                if not response.tool_calls:
                    visible_content = (all_content or "").strip()
                    last_role = api_messages[-1].get("role") if api_messages else None
                    last_was_tool_result = last_role == "tool"

                    if not visible_content:
                        if (self.current_step < self.max_iterations
                                and empty_final_retries < max_empty_final_retries):
                            empty_final_retries += 1
                            force_no_tools_next_turn = True
                            log_info("[AGENT] Empty final response. Retrying with forced text.", {
                                "retry": empty_final_retries,
                                "max_retries": max_empty_final_retries,
                                "iteration": self.current_step,
                                "after_tool_result": last_was_tool_result,
                                "finish_reason": getattr(response, "finish_reason", None),
                                "reasoning_chars": len(response.reasoning_content or ""),
                            })
                            api_messages.append({
                                "role": "user",
                                "content": (
                                    "[SYSTEM: Your previous response was empty. "
                                    "Respond now with a concise final answer for the user. "
                                    "Do NOT call tools.]"
                                )
                            })
                            continue

                        log_info("[AGENT] Empty final response persisted after retries. Returning fallback message.")
                        if all_reasoning.strip():
                            reasoning_preview = all_reasoning.strip()
                            if len(reasoning_preview) > 1500:
                                reasoning_preview = reasoning_preview[:1500] + "..."
                            all_content = (
                                "The model returned no visible final answer after multiple retries.\n\n"
                                "Reasoning preview:\n"
                                f"{reasoning_preview}"
                            )
                        else:
                            all_content = (
                                "The model returned an empty response after multiple retries. "
                                "Please try again or switch to a different model."
                            )

                    log_info("[AGENT] No tool calls, loop complete", {
                        "content_len": len(all_content),
                        "iterations": self.current_step
                    })
                    result.content = all_content
                    result.iterations = self.current_step

                    # Save final assistant message to history
                    # NOTE: reasoning_content is intentionally NOT included — it must
                    # never be sent back to the model as it causes reasoning loops.
                    api_messages.append({"role": "assistant", "content": all_content})
                    
                    if on_iteration_complete:
                        on_iteration_complete()
                    result.messages = api_messages
                    break

                # ---- Detect duplicate tool calls ----
                tool_key = str([(tc.name, tc.arguments) for tc in response.tool_calls])
                if tool_key == last_tool_key:
                    duplicate_count += 1
                    log_info("[AGENT] Duplicate tool call detected", {"count": duplicate_count})
                    if duplicate_count >= 2:
                        log_info("[AGENT] Breaking loop due to repeated tool calls")
                        all_content += "\n\n*[Agent stopped: repeated tool calls detected. The model appears to be looping.]*"
                        result.content = all_content
                        result.iterations = self.current_step
                        
                        assistant_msg = {"role": "assistant", "content": all_content}
                        if all_reasoning:
                            assistant_msg["reasoning_content"] = all_reasoning
                        api_messages.append(assistant_msg)
                        
                        result.messages = api_messages
                        break
                else:
                    duplicate_count = 0
                last_tool_key = tool_key

                # ---- Process tool calls ----
                log_info(f"[AGENT] Processing {len(response.tool_calls)} tool call(s)")

                # Add assistant message (with tool calls) to conversation history
                # NOTE: reasoning_content is intentionally NOT included here.
                # Sending the model's internal thoughts back as history causes it
                # to re-read and loop on its own reasoning (observed in Kimi/DeepSeek).
                assistant_msg = self._format_assistant_message(response)
                api_messages.append(assistant_msg)

                # Execute each tool call
                tool_results = []

                try:
                    total_tool_calls = len(response.tool_calls)
                    for tool_index, tc in enumerate(response.tool_calls, 1):
                        log_info(f"[AGENT] Executing tool: {tc.name}", {
                            "args": str(tc.arguments)[:200]
                        })

                        self._status(f"Running {tc.name}...")

                        # Notify UI about tool call
                        if self.on_tool_call:
                            self.on_tool_call(tc.name, tc.arguments, {
                                "index": tool_index,
                                "total": total_tool_calls,
                            })

                        # Execute the tool
                        try:
                            tool_result = self._execute_tool(
                                tc.name,
                                tc.arguments,
                                actual_dir,
                                finish_reason=getattr(response, "finish_reason", None),
                            )
                        except Exception as e:
                            log_error(f"[AGENT] Error executing {tc.name}", {"error": str(e)})
                            tool_result = {"success": False, "error": f"Internal agent error: {str(e)}"}

                        log_info(f"[AGENT] Tool result: {tc.name}", {
                            "success": tool_result.get("success", False),
                            "result_summary": str(tool_result)[:500]
                        })

                        # Notify UI about result
                        if self.on_tool_result:
                            self.on_tool_result(tc.name, tool_result)

                        tool_results.append({
                            "tool_call_id": tc.id,
                            "tool_name": tc.name,
                            "result": tool_result
                        })

                        result.tool_calls_made.append({
                            "name": tc.name,
                            "arguments": tc.arguments,
                            "result": tool_result
                        })

                finally:
                    # ALWAYS add tool results to api_messages if we added an assistant message with tool_calls.
                    # If we don't, the next request to OpenAI-compatible APIs will fail with a 400 error.
                    if tool_results or response.tool_calls:
                        # If tool_results is shorter than response.tool_calls (e.g. crash), 
                        # add dummy error results for the missing ones.
                        if len(tool_results) < len(response.tool_calls):
                            for tc in response.tool_calls[len(tool_results):]:
                                tool_results.append({
                                    "tool_call_id": tc.id,
                                    "tool_name": tc.name,
                                    "result": {"success": False, "error": "Execution interrupted"}
                                })
                        
                        result_messages = self._format_tool_results(tool_results)
                        api_messages.extend(result_messages)

                # Continue loop — model will see the tool results and respond

            else:
                # Reached max iterations without a final response
                log_info("[AGENT] Max iterations reached", {"max": self.max_iterations})
                if not all_content:
                    all_content = "I've reached the maximum number of steps. Please continue with a follow-up message."
                result.content = all_content
                result.iterations = self.max_iterations
                api_messages.append({"role": "assistant", "content": all_content})
                result.messages = api_messages

        except Exception as e:
            if self.is_cancelled:
                log_info("[AGENT] Provider request stopped after cancellation")
            else:
                log_info("[AGENT] Exception in agentic loop", {"error": str(e)})
                result.error = str(e)
            if all_content:
                result.content = all_content
            # Still save history even on error
            result.messages = api_messages

        log_info("[AGENT] Run complete", {
            "content_len": len(result.content) if result.content else 0,
            "tools_used": len(result.tool_calls_made),
            "iterations": result.iterations,
            "has_error": bool(result.error)
        })
        if on_iteration_complete:
            on_iteration_complete()
        return result
