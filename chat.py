"""
Limitcode Chat View - UX al límite de Sublime Text.
"""

import sublime
import sublime_plugin
import re
import os
import html
import json
import subprocess
import datetime
from typing import List, Dict, Any, Optional, Tuple

class ChatSession:
    """Manages a single chat session with message history and persistence."""
    
    def __init__(self, session_id: Optional[str] = None):
        self.session_id = session_id or datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self.messages = []  # type: List[Dict[str, Any]]
        self.prompt_history: List[str] = []
        self.api_messages: List[Dict[str, Any]] = []  # API-format history for agent memory
        self._history_index: int = -1
        self._stash: str = ""  # Current input being typed while navigating history
        self.title = "New Chat"
        self.provider = ""
        self.model = ""
        
        self._update_paths()
    
    def _update_paths(self):
        package_path = os.path.dirname(__file__)
        self.file_path = os.path.join(package_path, "history", f"{self.session_id}.md")
        self.json_path = os.path.join(package_path, "history", f"{self.session_id}.json")

    @staticmethod
    def _extract_tool_call_ids(msg) -> set:
        """Collect tool-call ids from an assistant message in any stored format.

        - OpenAI Chat Completions: msg["tool_calls"] = [{"id": ...}]
        - Responses API: content list parts {"type": "function_call", "call_id": ...}
        """
        ids = set()
        for tc in msg.get("tool_calls") or []:
            if isinstance(tc, dict) and tc.get("id"):
                ids.add(tc["id"])
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "function_call" and part.get("call_id"):
                    ids.add(part["call_id"])
        return ids

    def _sync_api_messages(self):
        """
        Ensure that all messages in self.messages (the UI/human-readable history)
        are represented in self.api_messages (the LLM context history).
        Rebuilds or inserts missing messages if they got out of sync
        due to reloads, model switches, or session splits.
        """
        if not self.messages:
            return

        # If api_messages is completely empty, rebuild it entirely
        if not self.api_messages:
            self.api_messages = []
            for msg in self.messages:
                self.api_messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })
            return

        # Group self.api_messages into steps. Each step has a main_message and associated tool_messages
        steps = []
        current_step = None

        for msg in self.api_messages:
            role = msg.get("role")
            if role == "tool":
                if (current_step
                        and current_step["main_message"].get("role") == "assistant"
                        and self._extract_tool_call_ids(current_step["main_message"])):
                    current_step["tool_messages"].append(msg)
                else:
                    # Orphan tool message: discard it
                    pass
            else:
                current_step = {
                    "main_message": msg,
                    "tool_messages": []
                }
                steps.append(current_step)

        # Sanitize steps: strip orphaned or incomplete tool calls
        for step in steps:
            msg = step["main_message"]
            if msg.get("role") == "assistant":
                tc_ids = self._extract_tool_call_ids(msg)
                if not tc_ids:
                    continue

                # Check if we have responses for all of them in step["tool_messages"]
                response_ids = {t.get("tool_call_id") for t in step["tool_messages"] if t.get("tool_call_id")}

                if tc_ids.issubset(response_ids):
                    # Valid, keep tool calls and responses
                    pass
                else:
                    # Missing responses (e.g. cancelled execution). Strip the
                    # tool calls in whatever format they are stored: OpenAI
                    # Chat (tool_calls key) or Responses API
                    # (function_call parts inside the content list).
                    if "tool_calls" in msg:
                        del msg["tool_calls"]
                    content = msg.get("content")
                    if isinstance(content, list):
                        msg["content"] = [
                            part for part in content
                            if not (isinstance(part, dict) and part.get("type") == "function_call")
                        ]
                    if not msg.get("content"):
                        msg["content"] = "[Execution cancelled]"
                    step["tool_messages"] = []

        # Now, match self.messages against steps
        new_steps = []
        step_idx = 0
        n_steps = len(steps)

        for msg in self.messages:
            role = msg["role"]
            content = msg["content"]

            match_idx = -1
            for i in range(step_idx, n_steps):
                step = steps[i]
                main_msg = step["main_message"]
                if main_msg.get("role") == role:
                    api_content = main_msg.get("content") or ""
                    if isinstance(api_content, list):
                        api_text = "".join(part.get("text", "") for part in api_content if isinstance(part, dict))
                    else:
                        api_text = str(api_content)

                    # Match condition
                    if role == "user":
                        if (content.strip() and content.strip() in api_text) or (api_text.strip() and api_text.strip() in content):
                            match_idx = i
                            break
                    else:
                        if (content.strip() and content.strip() in api_text) or (api_text.strip() and api_text.strip() in content):
                            match_idx = i
                            break

            if match_idx != -1:
                # Copy skipped system and assistant messages to preserve them (e.g. intermediate tool calls)
                for i in range(step_idx, match_idx):
                    role_i = steps[i]["main_message"].get("role")
                    if role_i in ("system", "assistant"):
                        new_steps.append(steps[i])
                
                # Copy matched step as-is (preserving any list/multimodal formats)
                new_steps.append(steps[match_idx])
                step_idx = match_idx + 1
            else:
                # No match found, create a new step
                new_steps.append({
                    "main_message": {
                        "role": role,
                        "content": content
                    },
                    "tool_messages": []
                })

        # Append remaining system and assistant messages
        if step_idx < n_steps:
            for i in range(step_idx, n_steps):
                role_i = steps[i]["main_message"].get("role")
                if role_i in ("system", "assistant"):
                    new_steps.append(steps[i])

        # Flatten steps back into final_api_messages
        final_api_messages = []
        for step in new_steps:
            final_api_messages.append(step["main_message"])
            final_api_messages.extend(step["tool_messages"])

        self.api_messages = final_api_messages
    
    def rename(self, new_title: str):
        """Update the session title and save."""
        self.title = new_title
        self.save()

    def delete(self):
        """Delete the session files and reset state."""
        for path in (self.file_path, self.json_path):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except:
                    pass
        self.clear()


    def save(self):
        """Save the session to Markdown and JSON files."""
        if not self.messages:
            return
            
        self._sync_api_messages()
            
        try:
            os.makedirs(os.path.dirname(self.file_path), exist_ok=True)

            # Save MD for human readability
            content = "---\n"
            content += f"id: {self.session_id}\n"
            content += f"title: {self.title}\n"
            content += f"provider: {self.provider}\n"
            content += f"model: {self.model}\n"
            content += f"date: {datetime.datetime.now().isoformat()}\n"
            content += "---\n\n"
            content += f"# {self.title}\n\n"
            
            for msg in self.messages:
                role = msg["role"].upper()
                text = msg["content"]
                icon = "◎" if role == "USER" else "▶"
                content += f"## {icon} {role}\n\n{text}\n\n"
                
            with open(self.file_path, "w", encoding="utf-8") as f:
                f.write(content)
                
            # Save JSON for structural fidelity
            json_data = {
                "id": self.session_id,
                "title": self.title,
                "provider": self.provider,
                "model": self.model,
                "messages": self.messages,
                "api_messages": self.api_messages,
                "prompt_history": self.prompt_history
            }
            with open(self.json_path, "w", encoding="utf-8") as f:
                json.dump(json_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            from .logger import log_error
            log_error(f"[SESSION] Failed to save session {self.session_id}", {"error": str(e)})

    def add_message(self, role: str, content: str, parts: Optional[List[Dict]] = None):
        self.messages.append({"role": role, "content": content, "parts": parts or []})
        # Save after every new message to ensure no loss
        self.save()
    
    def add_prompt(self, prompt: str):
        """Add prompt to history, deduplicating."""
        prompt_lower = prompt.lower().strip()
        self.prompt_history = [
            p for p in self.prompt_history if p.lower().strip() != prompt_lower
        ]
        self.prompt_history.insert(0, prompt)
        # Keep last 50
        self.prompt_history = self.prompt_history[:50]
        self._history_index = -1
        self._stash = ""
        
        # Use first prompt as title if it's still the default
        if self.title == "New Chat" and len(prompt) > 0:
            self.title = prompt[:50] + ("..." if len(prompt) > 50 else "")
            self.save()
    
    def navigate_history(self, direction: int, current_input: str) -> str:
        """Navigate prompt history. Returns the prompt to display."""
        if direction < 0:  # Up
            if self._history_index == -1:
                self._stash = current_input
            if self._history_index < len(self.prompt_history) - 1:
                self._history_index += 1
                return self.prompt_history[self._history_index]
        elif direction > 0:  # Down
            if self._history_index > -1:
                self._history_index -= 1
                if self._history_index == -1:
                    return self._stash
                return self.prompt_history[self._history_index]
        return current_input
    
    def load(self, session_id: str):
        """Load a session from JSON (preferentially) or Markdown file (fallback)."""
        package_path = os.path.dirname(__file__)
        history_dir = os.path.join(package_path, "history")
        json_path = os.path.join(history_dir, f"{session_id}.json")
        file_path = os.path.join(history_dir, f"{session_id}.md")
        
        # 1. Try JSON load first for full structural fidelity
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.session_id = session_id
                self._update_paths()
                self.title = data.get("title", "New Chat")
                self.provider = data.get("provider", "")
                self.model = data.get("model", "")
                self.messages = data.get("messages", [])
                self.api_messages = data.get("api_messages", [])
                self.prompt_history = data.get("prompt_history", [])
                self._sync_api_messages()
                from .logger import log_info
                log_info(f"[SESSION] Loaded session {session_id} from JSON")
                return True
            except Exception as e:
                from .logger import log_error
                log_error(f"[SESSION] Failed to load JSON session {session_id}, falling back to MD", {"error": str(e)})

        # 2. Fall back to parsing Markdown
        if not os.path.exists(file_path):
            return False
            
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                
            self.messages = []
            self.api_messages = []
            
            # More resilient parser
            current_role = None
            current_content = []
            
            in_frontmatter = False
            iteration_count = 0
            for line in lines:
                clean_line = line.strip()
                if clean_line == "---":
                    in_frontmatter = not in_frontmatter
                    continue
                
                if in_frontmatter:
                    if clean_line.startswith("title: "):
                        self.title = clean_line[7:].strip()
                    elif clean_line.startswith("provider: "):
                        self.provider = clean_line[10:].strip()
                    elif clean_line.startswith("model: "):
                        self.model = clean_line[7:].strip()
                    continue
                
                # Look for role headers more flexibly (User: ◎, Assistant: ▶, etc)
                if ("USER" in clean_line or "◎" in clean_line) and (clean_line.startswith("##") or clean_line.startswith("#")):
                    if current_role:
                        self.messages.append({"role": current_role, "content": "\n".join(current_content).strip()})
                    current_role = "user"
                    current_content = []
                elif ("ASSISTANT" in clean_line or "▶" in clean_line) and (clean_line.startswith("##") or clean_line.startswith("#")):
                    if current_role:
                        self.messages.append({"role": current_role, "content": "\n".join(current_content).strip()})
                    current_role = "assistant"
                    current_content = []
                elif current_role and not (clean_line.startswith("# ") and iteration_count == 0): # Skip main title on first line only
                    current_content.append(line)
                
                iteration_count += 1
            
            # Last message
            if current_role:
                self.messages.append({"role": current_role, "content": "\n".join(current_content).strip()})
            
            self.session_id = session_id
            self._update_paths()
            
            # Rebuild api_messages for the agent
            for msg in self.messages:
                self.api_messages.append({"role": msg["role"], "content": msg["content"]})
            
            from .logger import log_info
            log_info(f"[SESSION] Loaded {len(self.messages)} messages from {session_id} via MD fallback")
            return len(self.messages) > 0
        except Exception as e:
            from .logger import log_error
            log_error(f"[SESSION] Failed to load session {session_id} from MD", {"error": str(e)})
            return False

    def clear(self):
        """Reset the session to a clean state."""
        self.session_id = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self.messages = []
        self.prompt_history = []
        self.api_messages = []
        self._history_index = -1
        self._stash = ""
        self.title = "New Chat"
        self.provider = ""
        self.model = ""
        self._update_paths()


class ChatView:
    """Manages the chat view UI with protected history and @-mentions."""
    
    _instances: Dict[int, "ChatView"] = {}
    
    @classmethod
    def get_instance(cls, window: Optional[sublime.Window]) -> Optional["ChatView"]:
        from .logger import log_debug
        if not window:
            log_debug("[CHAT] get_instance called with None window, falling back to active_window")
            window = sublime.active_window()
            if not window:
                log_debug("[CHAT] active_window is also None!")
                return None
        
        wid = window.id()
        if wid not in cls._instances:
            log_debug(f"[CHAT] Creating new ChatView instance for window {wid}")
            cls._instances[wid] = cls(window)
        
        inst = cls._instances[wid]
        log_debug(f"[CHAT] get_instance(window={wid}) returning ChatView session_id={inst.session.session_id}")
        return inst
    
    def __init__(self, window: sublime.Window):
        self.window = window
        self.session = ChatSession()
        self._view: Optional[sublime.View] = None
        self._is_streaming = False
        self._input_start: int = 0  # Point where input area begins
        self._tool_phantoms: Dict[int, sublime.Phantom] = {}
        self._tool_details: Dict[int, str] = {}
        self._tool_display_names: Dict[int, str] = {}
        self._tool_phantom_set: Optional[sublime.PhantomSet] = None
        self._loading_phantom_set: Optional[sublime.PhantomSet] = None
        self._status_phantom_set = None
        self._stop_phantom_set: Optional[sublime.PhantomSet] = None
        self._message_header_phantom_set: Optional[sublime.PhantomSet] = None
        self._input_prompt_phantom_set: Optional[sublime.PhantomSet] = None
        self._status_anchor: Optional[int] = None
        self._tool_counter = 0
        self._last_tool_id_by_name = {}
        self._current_assistant_response = ""
        self._current_agent = None
        self._input_header_pending = False
        self._input_status_pending = False
        self._image_attachments: Dict[str, str] = {}
        self._image_attachment_counter = 0

        # Self-healing: locate existing chat view in the window to recover state on plugin reload
        for v in window.views():
            if v.settings().get("limitcode_chat_view"):
                self._view = v
                saved_session_id = v.settings().get("limitcode_session_id")
                if saved_session_id:
                    self.session.load(saved_session_id)
                self._setup_view()
                break

    def _sync_image_attachments_from_input(self, text: str) -> None:
        """Drop image attachments whose visible [imgN] token was deleted."""
        visible_tokens = set(re.findall(r"\[(img\d+)\]", text or ""))
        self._image_attachments = {
            token: path
            for token, path in self._image_attachments.items()
            if token in visible_tokens
        }

    def register_image_attachment(self, path: str, current_input: str = "") -> str:
        """Register an image path and return its short visible token."""
        self._sync_image_attachments_from_input(current_input)

        index = 1
        while f"img{index}" in self._image_attachments or f"[img{index}]" in (current_input or ""):
            index += 1

        token = f"img{index}"
        self._image_attachment_counter = max(self._image_attachment_counter, index)
        self._image_attachments[token] = path
        return token

    def expand_image_tokens(self, text: str) -> str:
        """Expand visible [imgN] tokens into internal attached-image tags."""
        self._sync_image_attachments_from_input(text)

        def replace(match):
            token = match.group(1)
            path = self._image_attachments.get(token)
            if not path:
                return match.group(0)
            return f"[Attached Image: {path}]"

        return re.sub(r"\[(img\d+)\]", replace, text)

    def reset_runtime_state(self):
        """Clear transient UI/agent state before rendering a loaded session."""
        self._is_streaming = False
        self._is_processing = False
        self._tool_running = False
        self._current_assistant_response = ""
        self._current_agent = None
        self._input_header_pending = False
        self._input_status_pending = False
        self._assistant_header_added = False
        self._user_header_added_for_input = False
        self._loading_active = False

        if self._tool_phantom_set:
            self._tool_phantom_set.update([])
        if self._loading_phantom_set:
            self._loading_phantom_set.update([])
        if self._stop_phantom_set:
            self._stop_phantom_set.update([])
        if self._input_prompt_phantom_set:
            self._input_prompt_phantom_set.update([])
        if self._status_phantom_set:
            self._status_phantom_set.update([])
        if self._message_header_phantom_set:
            self._message_header_phantom_set.update([])

        self._status_anchor = None

        self._tool_phantoms.clear()
        self._tool_details.clear()
        self._tool_display_names.clear()
        self._last_tool_id_by_name.clear()

        if self._view and self._view.is_valid():
            self._view.set_read_only(False)

    def get_context_tokens(self) -> int:
        """Estimate the token count of the current conversation context."""
        total_chars = sum(len(str(m.get("content", ""))) for m in self.session.api_messages)
        return max(1, total_chars // 4)

    def _render_status_bar(self):
        """Render the model/provider controls beside the current input."""
        if not self._view or not self._view.is_valid():
            return

        if not self._status_phantom_set:
            self._status_phantom_set = sublime.PhantomSet(self._view, "limitcode_status")
            
        settings = sublime.load_settings("Limitcode.sublime-settings")
        provider_id = settings.get("default_provider", "openai")
        model = settings.get("default_model", "gpt-5.5")
        
        friendly_names = {
            "openai": "OpenAI", "anthropic": "Anthropic", "gemini": "Gemini",
            "deepseek": "DeepSeek", "ollama": "Ollama", "lm-studio": "LM Studio"
        }
        provider_display = friendly_names.get(provider_id, provider_id)

        # Shorten model name if too long
        display_model = model.split("/")[-1] if "/" in model else model
        if len(display_model) > 20:
            display_model = display_model[:17] + "..."
            
        display_model_escaped = display_model.replace(" ", "&nbsp;")
        provider_display_escaped = provider_display.replace(" ", "&nbsp;")

        html = f"""
        <body id="limitcode-status">
            <style>
                .chips {{
                    margin: 0.3rem 0 0.8rem 0;
                    font-family: var(--font-mono);
                    font-size: 0.82em;
                    line-height: 2.2;
                }}
                .chip {{
                    display: inline;
                    background-color: color(var(--background) blend(var(--foreground) 93%));
                    border: 1px solid color(var(--foreground) alpha(0.08));
                    border-radius: 4px;
                    padding: 3px 9px;
                    color: color(var(--foreground) alpha(0.75));
                    margin-right: 4px;
                }}
                .chip a {{
                    color: var(--accent, #58a6ff);
                    text-decoration: none;
                    font-weight: bold;
                }}
                .dim {{
                    color: color(var(--foreground) alpha(0.4));
                }}
            </style>
            <div class="chips">
                <span class="chip">🧠&nbsp;<a href="change_model">{display_model_escaped}</a>&nbsp;<span class="dim">·&nbsp;{provider_display_escaped}</span></span><span class="chip">⚙&nbsp;<a href="open_settings">Config</a></span>
            </div>
        </body>
        """

        def on_navigate(href):
            if href == "change_model":
                self.window.run_command("limitcode_change_model")
            elif href == "open_settings":
                self.window.run_command("limitcode_open_settings")
                
        anchor = self._status_anchor
        if anchor is None or anchor > self._view.size():
            anchor = self._view.size()
            self._status_anchor = anchor
        region = sublime.Region(anchor, anchor)
        phantom = sublime.Phantom(region, html, sublime.LAYOUT_BLOCK, on_navigate)
        self._status_phantom_set.update([phantom])

    def render(self):
        """Re-render the entire session history in the view."""
        if not self._view or not self._view.is_valid():
            return

        # Always reset phantom sets before re-rendering.
        # This is critical when loading from history: the view may already exist
        # but phantom sets could be stale, causing input_start to not be set correctly.
        self._setup_view()
        self.reset_runtime_state()

        # Clear the view using the internal command
        self._view.run_command("limitcode_internal_clear")

        # ALWAYS reset header state — prevents stale phantoms/flags from previous renders
        self._all_header_phantoms = []
        self._user_header_added_for_input = False
        self._input_header_pending = False
        self._input_status_pending = False
        self._assistant_header_added = False
        if self._message_header_phantom_set:
            self._message_header_phantom_set.update([])

        # Restore title
        self._view.set_name(f"Chat: {self.session.title}")

        # If the session is empty, show a welcome message
        if not self.session.messages:
            welcome = (
                "# Limitcode\n\n"
                "Type a message and press Enter to send.\n\n"
                "**Providers:** OpenAI, Anthropic, Gemini, DeepSeek, Ollama, LM Studio\n"
                "**Tools:** read_file, write_to_file, edit_file\n\n"
                "**Tips:**\n"
                "- Type `@` to reference files\n"
                "- Use Up/Down arrows to navigate prompt history\n"
            )
            self._append_text(welcome)
            self.prepare_for_user()
            return

        # Render each existing message with hybrid UI.
        # _is_rendering suppresses _set_input_area() during this loop so that
        # input_start is only set once at the end, by prepare_for_user().
        self._is_rendering = True
        try:
            for msg in self.session.messages:
                role = msg["role"].upper()
                content = msg["content"]

                if self._view.size() > 0:
                    self._append_text("\n")

                self._append_message_header(role)
                prefix = "> " if role.upper() == "USER" else ""
                self._append_text(f"{prefix}{content}\n")
        finally:
            self._is_rendering = False

        self.prepare_for_user()

    def show(self):
        """Show or focus the chat view in a side-by-side layout."""
        settings = sublime.load_settings("Limitcode.sublime-settings")
        use_side_chat = settings.get("side_chat", True)

        if self._view and self._view.is_valid():
            if use_side_chat:
                self._ensure_side_layout()
            self._setup_view()
            if not getattr(self, "_current_agent", None):
                self._is_processing = False
                self.hide_loading()
                self._view.set_read_only(False)
                self._move_cursor_to_input()
            self.window.focus_view(self._view)
            return
        
        if use_side_chat:
            self._ensure_side_layout()

        self._view = self.window.new_file()
        self._view.set_name("Limitcode Chat")
        self._view.settings().set("limitcode_chat_view", True)
        self._view.settings().set("word_wrap", True)
        self._view.settings().set("line_numbers", False)
        self._view.settings().set("gutter", False)
        # scroll_past_end adds real scrollable space below the last line so the
        # input never sits glued to the bottom edge. 0.7 = 70% of viewport height.
        self._view.settings().set("scroll_past_end", 0.7)
        self._view.set_scratch(True)
        
        if use_side_chat and self.window.num_groups() > 1:
            self.window.set_view_index(self._view, 1, 0)

        self._setup_view()
        self.render()
    def _setup_view(self):
        """Configure the chat view settings and syntax."""
        if not self._view or not self._view.is_valid():
            return
            
        self._view.erase_phantoms("limitcode_tools")
        self._view.erase_phantoms("limitcode_loading")
        self._view.erase_phantoms("limitcode_status")
        self._view.erase_phantoms("limitcode_msg_headers")
        self._view.erase_phantoms("limitcode_input_prompt")

        self._view.settings().set("limitcode_session_id", self.session.session_id)
        try:
            # Keep MarkdownEditing/native Markdown because syntax-specific
            # settings can control the chat theme/background in Sublime.
            sublime.load_resource("Packages/MarkdownEditing/Markdown.sublime-syntax")
            self._view.set_syntax_file("Packages/MarkdownEditing/Markdown.sublime-syntax")
        except Exception:
            self._view.set_syntax_file("Packages/Markdown/Markdown.sublime-syntax")

        self._view.settings().set("margin", 16)
        self._view.settings().set("rulers", [])
        self._view.settings().set("font_options", ["no_italic"])

        # Disable linters to avoid visual noise like "Unexpected character" errors
        self._view.settings().set("lsp_active", False)
        self._view.settings().set("SublimeLinter", False)
        self._view.settings().set("diagnostics_panel_active", False)

        self._tool_phantom_set = sublime.PhantomSet(self._view, "limitcode_tools")
        self._loading_phantom_set = sublime.PhantomSet(self._view, "limitcode_loading")
        self._status_phantom_set = sublime.PhantomSet(self._view, "limitcode_status")
        self._message_header_phantom_set = sublime.PhantomSet(self._view, "limitcode_msg_headers")
        self._input_prompt_phantom_set = sublime.PhantomSet(self._view, "limitcode_input_prompt")

        # Update status bar when settings change
        settings = sublime.load_settings("Limitcode.sublime-settings")
        settings.add_on_change(f"limitcode_chat_{self.window.id()}", self._render_status_bar)


    def _ensure_side_layout(self):
        """Ensure the window has a 2-column layout for side-chat."""
        if self.window.num_groups() == 1:
            # Split into 2 columns (75% / 25%)
            self.window.set_layout({
                "cols": [0.0, 0.75, 1.0],
                "rows": [0.0, 1.0],
                "cells": [[0, 0, 1, 1], [1, 0, 2, 1]]
            })
    
    def _set_input_area(self):
        """Mark the start of the input area and enable editing when idle."""
        # During history rendering, suppress updates — input_start is set only once
        # at the very end by prepare_for_user(), after all messages are rendered.
        if getattr(self, '_is_rendering', False):
            return
        if self._view and self._view.is_valid():
            self._input_start = self._view.size()
            self._view.settings().set("limitcode_input_start", self._input_start)
            self._view.set_read_only(bool(getattr(self, "_is_processing", False)))
            self._render_input_prompt()

    def _append_input_status_bar(self):
        """Place the active controls directly above the editable input."""
        if not self._view or not self._view.is_valid():
            return

        self._status_anchor = self._view.size()
        self._render_status_bar()
        self._append_text("\n")

    def _render_input_prompt(self):
        """Render the terminal-style `>` prompt without making it editable text."""
        if not self._view or not self._view.is_valid():
            return
        if not self._input_prompt_phantom_set:
            return

        if getattr(self, "_is_processing", False):
            return

        input_start = int(self._view.settings().get("limitcode_input_start", self._view.size()))
        html_content = """
        <body id="limitcode-input-prompt">
            <style>
                .input-prompt {
                    color: color(var(--foreground) alpha(0.85));
                    font-family: var(--font-mono);
                    font-size: 1rem;
                    font-weight: bold;
                    padding-right: 0.55rem;
                }
            </style>
            <span class="input-prompt">&gt;</span>
        </body>
        """
        layout_inline = getattr(sublime, "LAYOUT_INLINE", sublime.LAYOUT_BLOCK)
        phantom = sublime.Phantom(
            sublime.Region(input_start, input_start),
            html_content,
            layout_inline,
        )
        self._input_prompt_phantom_set.update([phantom])

    def _move_cursor_to_input(self):
        """Place the caret at the editable input area."""
        if not self._view or not self._view.is_valid():
            return

        pt = int(self._view.settings().get("limitcode_input_start", self._view.size()))
        self._view.sel().clear()
        self._view.sel().add(sublime.Region(pt))

    def _get_input_region_start(self) -> int:
        """Return the start of the physical editable input line."""
        if not self._view or not self._view.is_valid():
            return 0

        input_start = int(self._view.settings().get("limitcode_input_start", 0))
        return self._view.line(input_start).begin()

    def _append_text(self, text: str):
        """Append text to the view."""
        if self._view and self._view.is_valid():
            self._view.run_command("limitcode_internal_append", {"characters": text})

    def _append_message_header(self, role: str):
        """Append a premium Phantom header for a chat message, with real newlines as anchors."""
        if not self._view or not self._view.is_valid():
            return
        if not self._message_header_phantom_set:
            return

        is_user = role.upper() == "USER"
        icon = "◎" if is_user else "✦"

        if is_user:
            label = "YOU"
        else:
            label = "ASSISTANT"
        # Cursor-like minimalist header
        icon_color = "var(--accent)" if is_user else "var(--foreground)"
        text_color = "color(var(--foreground) min-contrast(var(--background) 4.5))"
        bg_color = "color(var(--background) blend(var(--foreground) 95%))" if not is_user else "transparent"
        padding = "0.2rem 0.5rem"

        header_html = f"""
        <body id="limitcode-msg-header">
            <style>
                .msg-header-wrap {{
                    color: {text_color};
                    padding: {padding};
                    font-family: var(--font-mono);
                    font-size: 0.9rem;
                    font-weight: bold;
                    margin-top: 1rem;
                    margin-bottom: 0;
                }}
                .icon {{
                    color: {icon_color};
                    font-size: 1.1rem;
                }}
            </style>
            <div class="msg-header-wrap"><span class="icon">{icon}</span> &nbsp;{label}</div>
        </body>
        """

        pt = self._view.size()
        # Asegurarnos de que estamos en una nueva línea, pero sin dejar líneas en blanco extra
        if pt > 0 and self._view.substr(pt - 1) != "\n":
            self._append_text("\n")
            pt = self._view.size()

        # Insertamos el phantom en la línea actual
        new_phantom = sublime.Phantom(
            sublime.Region(pt, pt),
            header_html,
            sublime.LAYOUT_BLOCK
        )
        if not hasattr(self, '_all_header_phantoms'):
            self._all_header_phantoms = []
        self._all_header_phantoms.append(new_phantom)
        self._message_header_phantom_set.update(self._all_header_phantoms)
        
        # Un solo salto de línea para que el cursor quede exactamente debajo del phantom
        self._append_text("\n")
        
        # IMPORTANT: Re-set input area AFTER the phantom's trailing newline
        # so _input_start always points to the true end of the buffer
        self._set_input_area()
    
    def _scroll_to_end(self):
        """Scroll so the input sits at ~60 % from the top, using the real
        scroll_past_end space for breathing room below."""
        if not self._view or not self._view.is_valid():
            return

        input_start = int(self._view.settings().get("limitcode_input_start", self._view.size()))
        try:
            x, _ = self._view.viewport_position()
            _, input_y = self._view.text_to_layout(input_start)
            _, viewport_h = self._view.viewport_extent()
            target_y = max(0.0, input_y - viewport_h * 0.60)
            self._view.set_viewport_position((x, target_y), False)
        except Exception:
            self._view.show(input_start, False)

    def prepare_for_user(self):
        """Prepare the chat for user input after assistant finish."""
        if not self._view or not self._view.is_valid():
            return
            
        # Save the assistant response to the session history if we have one
        if getattr(self, "_current_assistant_response", ""):
            self.session.add_message("assistant", self._current_assistant_response)
            self._current_assistant_response = ""

        # Reset state flags
        self._is_processing = False
        self._assistant_header_added = False
        self.hide_loading()

        # Keep exactly one visible input header at the end. This gives the user
        # a clear writing target without accumulating empty YOU blocks.
        self._user_header_added_for_input = False
        if not self._input_header_pending:
            self._append_message_header("USER")
            self._input_header_pending = True
        if not self._input_status_pending:
            self._append_input_status_bar()
            self._input_status_pending = True
        self._set_input_area()
        # macOS can drop phantoms when text is inserted at their anchor during
        # the final chat cleanup. Re-render after the buffer settles.
        sublime.set_timeout(lambda: self._render_status_bar(), 50)
        self._move_cursor_to_input()
        self._scroll_to_end()
        sublime.set_timeout(lambda: self._scroll_to_end(), 50)
        self.window.focus_view(self._view)
    
    def _protect_history(self):
        """Mark the area before _input_start as read-only by clearing selections there."""
        if not self._view or not self._view.is_valid():
            return
        
        input_start = int(self._view.settings().get("limitcode_input_start", 0))
        new_regions = []
        for sel in self._view.sel():
            if sel.begin() < input_start or sel.end() < input_start:
                # Move cursor to input area
                new_regions.append(sublime.Region(input_start))
            else:
                new_regions.append(sel)
        
        if new_regions != list(self._view.sel()):
            self._view.sel().clear()
            for r in new_regions:
                self._view.sel().add(r)
    
    def _humanize_image_tokens(self, text: str) -> str:
        """Replace [imgN] with a short visual label for chat display."""
        return re.sub(r"\[img\d+\]", "🖼️ Imagen adjunta", text)

    def append_user_message(self, message: str):
        """Append a user message cleanly."""
        if not self._view or not self._view.is_valid():
            return

        message = message.strip()
        if not message:
            return
        
        display_message = self._humanize_image_tokens(message)
        self.session.add_message("user", display_message)
        self.session.add_prompt(message)
        self._assistant_header_added = False # Reset for next response
        self._is_processing = True # Bloquear escritura mientras procesa

        if not self._input_header_pending:
            self._append_message_header("USER")
        self._append_text(f"> {display_message}\n")
        self._user_header_added_for_input = False  # Reset for next cycle
        self._input_header_pending = False
        self._input_status_pending = False
        self._status_anchor = None
        if self._status_phantom_set:
            self._status_phantom_set.update([])
        if self._input_prompt_phantom_set:
            self._input_prompt_phantom_set.update([])

        self._set_input_area()
        self._scroll_to_end()
        sublime.set_timeout(lambda: self._scroll_to_end(), 50)

    def append_text(self, text: str):
        """Append assistant text chunk safely."""
        if not self._view or not self._view.is_valid():
            return
        
        if not text:
            return
            
        # Only hide loading if it's currently showing to avoid phantom flickering
        if getattr(self, "_loading_active", False):
            self.hide_loading()
            
        if not getattr(self, "_assistant_header_added", False):
            self._append_message_header("ASSISTANT")
            self._assistant_header_added = True
            self._is_processing = True
            self._current_assistant_response = ""

        self._current_assistant_response += text
        self._append_text(text)
        self._set_input_area()
        self._scroll_to_end()

        # Timer to detect when text streaming stops but tool generation starts
        current_token = getattr(self, "_rescue_token", 0) + 1
        self._rescue_token = current_token
        sublime.set_timeout(lambda: self._check_tool_generation(current_token), 1000)

    def _check_tool_generation(self, token: int):
        if not self._view or not self._view.is_valid():
            return
        if getattr(self, "_rescue_token", 0) != token:
            return
            
        # If the stream is silent for 1 second, and no tool has been officially started,
        # but we are still in processing state, it means the model is generating JSON tool calls.
        if getattr(self, "_is_processing", False) and not getattr(self, "_tool_running", False):
            if not getattr(self, "_loading_active", False):
                self.show_loading("Limitcode is preparing tools...")

    def _build_tool_phantom_html(self, name: str, status: str, detail: str = "") -> str:
        if status == "success":
            color = "var(--greenish)"
            icon = "✓"
        elif status == "error":
            color = "var(--redish)"
            icon = "✗"
        elif status == "cancelled":
            color = "color(var(--foreground) alpha(0.45))"
            icon = "-"
        else:  # running
            color = "var(--accent)"
            icon = "⋯"

        detail_html = f'<div style="color: color(var(--foreground) alpha(0.5)); font-size: 0.8rem; margin-top: 0.2rem;">{html.escape(detail)}</div>' if detail else ""

        return f"""
        <body id="limitcode-tool-phantom">
            <style>
                .tool-card {{
                    background-color: color(var(--background) blend(var(--foreground) 96%));
                    border: 1px solid color(var(--foreground) alpha(0.1));
                    border-left: 3px solid {color};
                    border-radius: 0.3rem;
                    padding: 0.6rem 0.8rem;
                    font-family: var(--font-mono);
                    font-size: 0.85rem;
                    color: color(var(--foreground) min-contrast(var(--background) 3.5));
                    margin: 0.4rem 0;
                }}
                .tool-header {{
                    font-weight: bold;
                }}
            </style>
            <div class="tool-card">
                <div class="tool-header"><span style="color:{color}">{icon}</span> {name}</div>
                {detail_html}
            </div>
        </body>
        """

    def _format_detail_path(self, path_str: str, max_len: int = 50) -> str:
        """Format a file path to be human-readable and fit within max_len by replacing user home and truncating from left."""
        if not isinstance(path_str, str):
            path_str = str(path_str)
            
        path_str = path_str.replace("/", os.sep).replace("\\", os.sep)
        try:
            home = os.path.expanduser("~")
            if path_str.lower().startswith(home.lower()):
                path_str = "~" + path_str[len(home):]
        except Exception:
            pass
            
        if len(path_str) <= max_len:
            return path_str
            
        sep = os.sep
        parts = path_str.split(sep)
        if not parts:
            return path_str
            
        current = parts[-1]
        for part in reversed(parts[:-1]):
            if not part:
                continue
            candidate = part + sep + current
            if len("..." + sep + candidate) > max_len:
                break
            current = candidate
            
        return "..." + sep + current

    def append_tool_call(self, name: str, args, meta=None):
        if not self._view or not self._view.is_valid():
            return
        if self._tool_phantom_set is None:
            return
        
        self._tool_running = True  # Prevent rescue timer from firing mid-tool
        meta = meta or {}
        display_name = name
        self.show_loading(f"Limitcode is running {name}...")
             
        self._tool_counter += 1
        call_id = self._tool_counter
        self._last_tool_id_by_name[name] = call_id
        self._tool_display_names[call_id] = display_name
        
        # Build a short detail string from args
        detail = ""
        if isinstance(args, dict):
            for k, v in args.items():
                if k in ("file_path", "directory"):
                    detail = self._format_detail_path(str(v), 50)
                    break
                elif k in ("command", "pattern", "query", "url"):
                    val_str = str(v)
                    if len(val_str) > 50:
                        detail = val_str[:47] + "..."
                    else:
                        detail = val_str
                    break
        elif isinstance(args, str):
            detail = args[:50]

        self._append_text("\n")
        pt = self._view.size()
        region = sublime.Region(pt, pt)
        
        frame_html = self._build_tool_phantom_html(display_name, "running", detail)
        phantom = sublime.Phantom(region, frame_html, sublime.LAYOUT_BLOCK)
        
        self._tool_phantoms[call_id] = phantom
        self._tool_details[call_id] = detail
        self._tool_phantom_set.update(list(self._tool_phantoms.values()))
        self._scroll_to_end()
    
    def append_tool_result(self, name: str, result):
        if not self._view or not self._view.is_valid():
            return
        
        self._tool_running = False
        self.show_loading("Analyzing results...")
        if self._tool_phantom_set is None:
            return
        
        call_id = self._last_tool_id_by_name.get(name)
        if not call_id or call_id not in self._tool_phantoms:
            return
            
        status = "success" if result.get("success") else "error"
        old_phantom = self._tool_phantoms[call_id]

        display_name = self._tool_display_names.get(call_id, name)
        detail = self._tool_details.get(call_id, "")

        html_content = self._build_tool_phantom_html(display_name, status, detail)
        new_phantom = sublime.Phantom(old_phantom.region, html_content, sublime.LAYOUT_BLOCK)
        
        self._tool_phantoms[call_id] = new_phantom
        self._tool_phantom_set.update(list(self._tool_phantoms.values()))
        self._append_text("\n")
        self._tool_running = False  # Tool finished, rescue timer can fire again

        
        # Display error message if tool failed
        if not result.get("success") and status != "cancelled":
            error_msg = result.get("error") or result.get("output") or "Unknown error"
            self._append_text(f"\n> Error: {error_msg}\n\n")
                
        self._scroll_to_end()

    def show_loading(self, message: str = "Limitcode is working..."):
        if not self._view or not self._view.is_valid():
            return
        if self._loading_phantom_set is None:
            return
            
        self._loading_message = message
        self._loading_active = True
        self._animate_loading(0)

    def _animate_loading(self, frame_idx: int):
        if not getattr(self, "_loading_active", False) or not self._view or not self._view.is_valid():
            return
            
        frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        frame = frames[frame_idx % len(frames)]
        
        pt = self._view.size()
        region = sublime.Region(pt, pt)
        html = f"""
        <body id="limitcode-loading-phantom">
            <style>
                .loading-chip {{
                    color: color(var(--foreground) alpha(0.6));
                    font-family: var(--font-mono);
                    font-size: 0.85rem;
                    margin: 1.2rem 0 0.5rem 0;
                    padding-top: 0.3rem;
                }}
                .stop-btn {{
                    background-color: color(var(--redish) alpha(0.15));
                    color: var(--redish);
                    padding: 0.2rem 0.6rem;
                    border-radius: 0.2rem;
                    font-weight: bold;
                    text-decoration: none;
                    margin-left: 1rem;
                }}
            </style>
            <div class="loading-chip">
                <span style="color:var(--accent)">{frame}</span> {self._loading_message}
                <a href="limitcode_stop" class="stop-btn">Stop</a>
            </div>
        </body>
        """
        
        def on_navigate(href):
            if href == "limitcode_stop":
                self.window.run_command("limitcode_stop_agent")
                
        phantom = sublime.Phantom(region, html, sublime.LAYOUT_BLOCK, on_navigate)
        self._loading_phantom_set.update([phantom])
        self._scroll_to_end()
        
        sublime.set_timeout(lambda: self._animate_loading(frame_idx + 1), 100)

    def hide_loading(self):
        self._loading_active = False
        if self._loading_phantom_set is not None:
            self._loading_phantom_set.update([])
            
    def append_error(self, error: str):
        """Append an error message."""
        if not self._view or not self._view.is_valid():
            return
        
        self.hide_loading()
        self._append_text(f"\n⚠️ **Error:** {error}\n")
        self._scroll_to_end()
    
    def on_stream_complete(self):
        """Called when streaming is complete. Restore the input area."""
        if not self._view or not self._view.is_valid():
            return
        
        self._is_streaming = False
        # Solo añadimos un salto de línea para no crear espacios enormes
        self._append_text("\n")
        self._set_input_area()
        self._scroll_to_end()
    
    def clear(self):
        """Clear the chat session."""
        self.session.clear()
        if self._view and self._view.is_valid():
            self._view.run_command("select_all")
            self._view.run_command("left_delete")
            self._append_text("# Limitcode\n\nChat cleared. Type a message to start.\n\n---\n\n")
            self._set_input_area()
    
    def get_input_text(self) -> str:
        """Get the current input text (after _input_start)."""
        if not self._view or not self._view.is_valid():
            return ""
        
        input_start = self._get_input_region_start()
        return self._view.substr(sublime.Region(int(input_start), self._view.size()))
    
    def replace_input_text(self, text: str):
        """Replace the current input text using a TextCommand."""
        if not self._view or not self._view.is_valid():
            return
        
        # Store text in settings for the command to access
        self._view.settings().set("limitcode_replace_text", text)
        self._view.run_command("limitcode_internal_replace_input")
        self._view.settings().erase("limitcode_replace_text")


# ---- Sublime Text Commands ----

class LimitcodePasteCommand(sublime_plugin.TextCommand):
    """Command to handle image pasting from clipboard."""
    
    def run(self, edit):
        # 1. Check if we are in a Limitcode Chat
        if not self.view.settings().get("limitcode_chat_view"):
            self.view.run_command("paste")
            return

        # 2. Try to get image from clipboard using PowerShell (Windows)
        if os.name == 'nt':
            # Fast check with ctypes to see if clipboard actually has an image.
            # This avoids spawning a slow PowerShell subprocess when pasting text.
            has_image = False
            try:
                import ctypes
                CF_BITMAP = 2
                CF_DIB = 8
                user32 = ctypes.windll.user32
                if user32.IsClipboardFormatAvailable(CF_DIB) or user32.IsClipboardFormatAvailable(CF_BITMAP):
                    has_image = True
            except Exception:
                # Fallback to True if ctypes check fails
                has_image = True

            if not has_image:
                self.view.run_command("paste")
                return

            # Create clips directory in project
            folders = self.view.window().folders()
            if not folders:
                self.view.run_command("paste")
                return
            
            project_dir = folders[0]
            clips_dir = os.path.join(project_dir, ".limitcode", "clips")
            os.makedirs(clips_dir, exist_ok=True)
            
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"clip_{timestamp}.png"
            filepath = os.path.join(clips_dir, filename)
            
            # PowerShell script to save clipboard image
            ps_script = f'Add-Type -AssemblyName System.Windows.Forms; if ([System.Windows.Forms.Clipboard]::ContainsImage()) {{ $img = [System.Windows.Forms.Clipboard]::GetImage(); $img.Save("{filepath}", [System.Drawing.Imaging.ImageFormat]::Png); echo "SAVED" }}'
            
            try:
                # Run hidden PowerShell command
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                
                output = subprocess.check_output(
                    ["powershell", "-NoProfile", "-Command", ps_script],
                    startupinfo=startupinfo,
                    text=True
                ).strip()
                
                if "SAVED" in output:
                    # Insert a short visible token; ChatView expands it before sending.
                    chat = ChatView.get_instance(self.view.window())
                    current_input = chat.get_input_text() if chat else ""
                    token = chat.register_image_attachment(filepath, current_input) if chat else "img1"
                    tag = f"\n[{token}]\n"
                    self.view.insert(edit, self.view.sel()[0].begin(), tag)
                    sublime.status_message(f"Limitcode: Imagen pegada como [{token}]")
                    return
            except Exception as e:
                from .logger import log_error
                log_error("[CHAT] Failed to paste image from clipboard", {"error": str(e)})

        # 3. Fallback to normal text paste
        self.view.run_command("paste")


# Fallback for unit testing where sublime_plugin is mocked without EventListener
EventListener = getattr(sublime_plugin, "EventListener", object)

class LimitcodeChatInputListener(EventListener):
    """
    Handles chat view interactions.
    """
    
    def on_modified(self, view):
        """Trigger autocomplete when '@' is typed."""
        if not view.settings().get("limitcode_chat_view"):
            return
        
        sel = view.sel()
        if not sel:
            return
        pos = sel[0].begin()
        if pos <= 0:
            return
        
        # Check if the character just typed is '@'
        char = view.substr(sublime.Region(pos - 1, pos))
        if char == "@":
            sublime.set_timeout(lambda: view.run_command("auto_complete", {
                "disable_auto_insert": True,
                "api_completions_only": True
            }), 0)

    def on_activated(self, view):
        """Toggle the minimap visibility when a view gains focus."""
        window = view.window()
        if not window:
            return
            
        settings = sublime.load_settings("Limitcode.sublime-settings")
        if not settings.get("hide_minimap_in_chat", True):
            return
            
        if view.settings().get("limitcode_chat_view"):
            try:
                # Save preference and hide minimap only if we haven't stored it yet
                if not window.settings().has("limitcode_had_minimap"):
                    had_minimap = window.is_minimap_visible()
                    window.settings().set("limitcode_had_minimap", had_minimap)
                    if had_minimap:
                        window.set_minimap_visible(False)
            except Exception:
                pass
        else:
            try:
                # Restore preference when entering a normal view
                if window.settings().has("limitcode_had_minimap"):
                    had_minimap = window.settings().get("limitcode_had_minimap", True)
                    window.settings().erase("limitcode_had_minimap")
                    if had_minimap:
                        window.set_minimap_visible(True)
                elif not window.is_minimap_visible():
                    # Self-healing fallback: if minimap is hidden in a code view, force show it
                    window.set_minimap_visible(True)
            except Exception:
                pass
    
    def on_pre_close(self, view):
        """Clean up settings listener and restore minimap when chat view is closed."""
        if not view.settings().get("limitcode_chat_view"):
            return
        window = view.window()
        if window:
            try:
                settings = sublime.load_settings("Limitcode.sublime-settings")
                settings.clear_on_change(f"limitcode_chat_{window.id()}")
                
                # Restore minimap preference if it was stored
                if window.settings().has("limitcode_had_minimap"):
                    had_minimap = window.settings().get("limitcode_had_minimap", True)
                    window.settings().erase("limitcode_had_minimap")
                    if had_minimap:
                        window.set_minimap_visible(True)
            except Exception:
                pass

    def on_text_command(self, view, command_name: str, args: dict):
        """Intercept commands in chat view to protect history."""
        if not view.settings().get("limitcode_chat_view"):
            return None
            
        chat = ChatView.get_instance(view.window())
        if not chat:
            return None

        if getattr(chat, "_is_processing", False) and not getattr(chat, "_current_agent", None):
            chat._is_processing = False
            chat.hide_loading()
            view.set_read_only(False)
        
        # Bloquear escritura si el asistente está procesando
        if getattr(chat, "_is_processing", False):
            if command_name in ("insert", "left_delete", "right_delete", "cut", "paste", "limitcode_send_chat", "undo", "redo"):
                sublime.status_message("Limitcode: Espera a que el asistente termine de escribir...")
                return ("noop", {})

        # Use our custom paste for images
        if command_name == "paste":
            return ("limitcode_paste", None)

        input_start = int(view.settings().get("limitcode_input_start", 0))
        
        # Check all selections
        for sel in view.sel():
            cursor_pos = sel.begin()
            
            # Block any modification before input_start
            if command_name in ("insert", "left_delete", "right_delete", "cut", "paste", "undo", "redo"):
                # Special case: allow Enter if we want to send, but only if at/after input_start
                if command_name == "insert" and args and args.get("characters") == "\n":
                    if cursor_pos >= input_start:
                        input_text = chat.get_input_text().strip()
                        if input_text:
                            view.run_command("limitcode_send_chat")
                            return ("noop", {})
                        return None # Allow newline if empty? Or block?
                
                # Block typing before the designated input area
                if cursor_pos < input_start:
                    sublime.set_timeout(lambda: chat._move_cursor_to_input(), 0)
                    sublime.status_message("Limitcode: Chat history is read-only")
                    return ("noop", {})
                    
                # Special check for backspace (left_delete) right at the boundary
                if command_name == "left_delete" and cursor_pos <= input_start:
                    sublime.set_timeout(lambda: chat._move_cursor_to_input(), 0)
                    sublime.status_message("Limitcode: Chat history is read-only")
                    return ("noop", {})
                    
                # Special check for delete (right_delete) right before the boundary
                if command_name == "right_delete" and cursor_pos < input_start:
                    sublime.set_timeout(lambda: chat._move_cursor_to_input(), 0)
                    sublime.status_message("Limitcode: Chat history is read-only")
                    return ("noop", {})
        
        return None

    def on_query_completions(self, view, prefix: str, locations: List[int]) -> sublime.CompletionList:
        """Handle @-mentions for file autocomplete."""
        if not view.settings().get("limitcode_chat_view"):
            return None
        if not locations:
            return None
        
        pos = locations[0]
        line_start = view.line(pos).begin()
        line_text = view.substr(sublime.Region(line_start, pos))
        
        # Match if the line text ends with @ or @prefix (to support autocomplete as user types)
        if not (line_text.endswith("@") or (prefix and line_text.endswith("@" + prefix))):
            return None
        
        window = view.window()
        if not window:
            return None
        
        completions = []
        for v in window.views():
            if v.file_name() and not v.settings().get("limitcode_chat_view"):
                file_path = v.file_name()
                rel_path = None
                for folder in window.folders():
                    if file_path.startswith(folder):
                        rel_path = os.path.relpath(file_path, folder)
                        break
                if not rel_path:
                    rel_path = os.path.basename(file_path)
                
                completions.append((f"{rel_path}\tOpen Tab", f"{rel_path}"))
        
        seen = set()
        unique = []
        for item in completions:
            if item[0] not in seen:
                seen.add(item[0])
                unique.append(item)
        
        cl = sublime.CompletionList()
        cl.set_completions(unique[:100], sublime.COMPLETION_FORMAT_TEXT)
        return cl


class LimitcodeSendChatCommand(sublime_plugin.TextCommand):
    """Send the current chat input."""
    
    def run(self, edit):
        from .logger import log_info
        w = self.view.window()
        log_info(f"[COMMANDS] LimitcodeSendChatCommand run on view {self.view.id()} in window {w.id() if w else 'None'}")
        
        chat = ChatView.get_instance(w)
        if not chat:
            log_info("[COMMANDS] LimitcodeSendChatCommand could not resolve ChatView!")
            return
            
        if getattr(chat, "_is_processing", False) and not getattr(chat, "_current_agent", None):
            chat._is_processing = False
            chat.hide_loading()
            self.view.set_read_only(False)

        if getattr(chat, "_is_processing", False):
            sublime.status_message("Limitcode: Espera a que el asistente termine de trabajar...")
            return

        input_text = chat.get_input_text().strip()
        
        if not input_text:
            return

        agent_input = chat.expand_image_tokens(input_text)
        
        chat.replace_input_text("")
        chat.append_user_message(input_text)
        
        # Trigger the agent
        w.run_command("limitcode_process_message", {"message": agent_input})


class LimitcodeInternalClearCommand(sublime_plugin.TextCommand):
    """Internal command to clear the chat view safely."""
    def run(self, edit):
        self.view.set_read_only(False)
        self.view.erase(edit, sublime.Region(0, self.view.size()))
        self.view.settings().set("limitcode_input_start", 0)
        self.view.set_read_only(True)


class LimitcodeInternalAppendCommand(sublime_plugin.TextCommand):
    """Internal command to append text even if the view is read-only."""
    def run(self, edit, characters=""):
        old_read_only = self.view.is_read_only()
        self.view.set_read_only(False)
        self.view.insert(edit, self.view.size(), characters)
        self.view.set_read_only(old_read_only)
        
        self.view.settings().set("limitcode_input_start", self.view.size())


class LimitcodeInternalReplaceInputCommand(sublime_plugin.TextCommand):
    """Internal command to replace input text in chat view."""
    
    def run(self, edit):
        view = self.view
        input_start = int(view.settings().get("limitcode_input_start", 0))
        input_start = view.line(input_start).begin()
        text = view.settings().get("limitcode_replace_text", "")
        
        old_read_only = view.is_read_only()
        view.set_read_only(False)
        view.erase(edit, sublime.Region(input_start, view.size()))
        view.insert(edit, input_start, text)
        view.set_read_only(old_read_only)
        
        view.sel().clear()
        view.sel().add(sublime.Region(view.size()))
        view.show(view.size())
