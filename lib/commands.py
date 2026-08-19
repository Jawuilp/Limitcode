"""
Limitcode Commands.
Main commands for the agentic coding assistant.
"""

import sublime
import sublime_plugin
import os
import time
import threading
from typing import Optional, Callable, Any

from .chat import ChatView
from .agent_runner import run_agent_async
from .storage import history_dir


def _run_in_background(target: Callable[[], None]) -> None:
    threading.Thread(target=target, daemon=True).start()


def _sanitize_api_key(key: str) -> str:
    """Strip whitespace and replace typographic quotes/dashes pasted from web UIs."""
    key = key.strip()
    replacements = {
        '“': '"', '”': '"',  # curly double quotes
        '‘': "'", '’': "'",  # curly single quotes / apostrophes
        '–': '-', '—': '-',  # en-dash, em-dash
        ' ': ' ',                 # non-breaking space
        '﻿': '',                  # BOM
    }
    for bad, good in replacements.items():
        key = key.replace(bad, good)
    return key


def _refresh_chat_status_bar(window: sublime.Window) -> None:
    try:
        chat = ChatView.get_instance(window)
        if chat:
            sublime.set_timeout(lambda: chat._render_status_bar(), 0)
    except Exception:
        pass




class LimitcodeOpenChatCommand(sublime_plugin.WindowCommand):
    """Open the Limitcode chat panel."""

    def run(self):
        chat = ChatView.get_instance(self.window)
        chat.show()


class LimitcodeOpenSettingsCommand(sublime_plugin.WindowCommand):
    """Open Limitcode settings for direct JSON configuration."""

    def run(self):
        package_name = (__package__ or "Limitcode").split(".", 1)[0]
        self.window.run_command("edit_settings", {
            "base_file": f"${{packages}}/{package_name}/Limitcode.sublime-settings",
            "default": "{\n}\n",
        })


class LimitcodeOpenKeyBindingsCommand(sublime_plugin.WindowCommand):
    """Open Limitcode key bindings in Sublime's split settings view."""

    def run(self):
        package_name = (__package__ or "Limitcode").split(".", 1)[0]
        self.window.run_command("edit_settings", {
            "base_file": f"${{packages}}/{package_name}/Default.sublime-keymap",
            "default": "[\n]\n",
        })



class LimitcodeListHistoryCommand(sublime_plugin.WindowCommand):
    """List and load past chat sessions."""

    def run(self):
        session_dir = history_dir()

        if not os.path.exists(session_dir):
            sublime.status_message("Limitcode: No history found")
            return

        files = [f for f in os.listdir(session_dir) if f.endswith(".md")]
        files.sort(reverse=True) # Newest first

        if not files:
            sublime.status_message("Limitcode: No previous chats")
            return

        items = []
        for f in files:
            path = os.path.join(session_dir, f)
            title = f
            date = ""
            try:
                with open(path, "r", encoding="utf-8") as file:
                    for line in file:
                        if line.startswith("title: "):
                            title = line[7:].strip()
                        if line.startswith("date: "):
                            date = line[6:].strip()
                            if "T" in date:
                                date = date.split("T")[0] + " " + date.split("T")[1][:5]
                        if line.strip() == "---" and date != "":
                            break
            except:
                pass
            items.append(sublime.QuickPanelItem(title, date))

        def on_done(index):
            if index == -1:
                return
            
            session_id = files[index].replace(".md", "")
            chat = ChatView.get_instance(self.window)
            if chat.session.load(session_id):
                chat.reset_runtime_state()
                # HISTORY CONSISTENCY: sync global settings to match loaded session
                # so new messages continue with the same provider+model
                if chat.session.provider and chat.session.model:
                    settings = sublime.load_settings("Limitcode.sublime-settings")
                    settings.set("default_provider", chat.session.provider)
                    settings.set("default_model", chat.session.model)
                    sublime.save_settings("Limitcode.sublime-settings")
                had_view = bool(chat._view and chat._view.is_valid())
                chat.show()
                if had_view:
                    chat.render()
                sublime.set_timeout(lambda: chat._render_status_bar(), 100)
            else:
                sublime.error_message("Failed to load session")

        self.window.show_quick_panel(items, on_done)


class LimitcodeNewChatCommand(sublime_plugin.WindowCommand):
    """Start a completely new chat session with fresh context."""

    def run(self):
        chat = ChatView.get_instance(self.window)
        # Save the current session before clearing
        chat.session.save()
        # Reset to a clean state
        chat.session.clear()
        # Always re-render — show() returns early if the view already exists
        if chat._view and chat._view.is_valid():
            chat.render()
        else:
            chat.show()
        sublime.status_message("Limitcode: New chat session started")


class LimitcodeRenameSessionCommand(sublime_plugin.WindowCommand):
    """Rename the current chat session."""

    def run(self):
        chat = ChatView.get_instance(self.window)
        
        def on_done(new_title):
            if new_title.strip():
                chat.session.rename(new_title.strip())
                chat.render()
                sublime.status_message(f"Limitcode: Session renamed to '{new_title}'")

        self.window.show_input_panel(
            "New Chat Title:",
            chat.session.title,
            on_done,
            None,
            None
        )


class LimitcodeDeleteSessionCommand(sublime_plugin.WindowCommand):
    """Delete the current chat session."""

    def run(self):
        chat = ChatView.get_instance(self.window)
        if not chat.session.messages and chat.session.title == "New Chat":
            sublime.status_message("Limitcode: Nothing to delete")
            return

        if sublime.ok_cancel_dialog(f"Are you sure you want to delete the session '{chat.session.title}'?", "Delete"):
            chat.session.delete()
            chat.render()
            sublime.status_message("Limitcode: Session deleted")


class LimitcodeSendToAgentCommand(sublime_plugin.TextCommand):
    """Send selected text or entire file to the agent."""

    def run(self, edit, prompt: Optional[str] = None):
        view = self.view
        window = view.window() or sublime.active_window()

        # Get context
        context = ""
        if view.sel() and not view.sel()[0].empty():
            context = view.substr(view.sel()[0])
        else:
            context = view.substr(sublime.Region(0, view.size()))

        # Get prompt
        if not prompt:
            window.show_input_panel(
                "Limitcode: What would you like to do?",
                "",
                lambda p: self._on_prompt(p, context, window),
                None,
                None
            )
        else:
            self._on_prompt(prompt, context, window)

    def _on_prompt(self, prompt: str, context: str, window: sublime.Window):
        if not prompt.strip():
            return

        chat = ChatView.get_instance(window)
        chat.show()
        chat.append_user_message(prompt)

        # Single shared execution route (see agent_runner.py)
        run_agent_async(window, chat, prompt, context, source="send_to_agent")


_CACHED_MODELS = {}             # provider_id → [model_id, ...]
_CACHED_MODELS_TIMESTAMPS = {}  # provider_id → unix timestamp of last fetch
_CACHE_LOCK = threading.Lock()
_FETCH_IN_FLIGHT = set()        # provider_ids currently being fetched
_MODELS_CACHE_TTL = 1800        # 30 minutes


def _get_cached_models(provider_id):
    """Return cached model list if within TTL, else empty list."""
    with _CACHE_LOCK:
        models = _CACHED_MODELS.get(provider_id)
        if models is not None:
            age = time.time() - _CACHED_MODELS_TIMESTAMPS.get(provider_id, 0)
            if age < _MODELS_CACHE_TTL:
                return models
    return []


def _set_cached_models(provider_id, models):
    """Store model list in cache."""
    with _CACHE_LOCK:
        _CACHED_MODELS[provider_id] = models
        _CACHED_MODELS_TIMESTAMPS[provider_id] = time.time()


def _fetch_and_cache_provider(provider_id):
    """Fetch models for one provider with stampede protection, then cache."""
    with _CACHE_LOCK:
        if provider_id in _FETCH_IN_FLIGHT:
            return  # another thread is already fetching this provider
        _FETCH_IN_FLIGHT.add(provider_id)
    try:
        from .limitcode import get_provider
        p = get_provider(provider_id, "placeholder")
        models = p.list_models()
        if models:
            _set_cached_models(provider_id, models)
    except Exception:
        pass
    finally:
        with _CACHE_LOCK:
            _FETCH_IN_FLIGHT.discard(provider_id)

def prefetch_models_in_background():
    """Prefetch per-provider model lists in the background."""

    def _prefetch_provider_models():
        from .logger import log_info
        from .limitcode import is_provider_configured
        from concurrent.futures import ThreadPoolExecutor

        configured = [
            pid for pid in LimitcodeChangeModelCommand.PROVIDER_LABELS.keys()
            if is_provider_configured(pid)
        ]
        log_info(f"[CACHE] Prefetching models for {len(configured)} providers in parallel")
        with ThreadPoolExecutor(max_workers=4) as executor:
            for provider_id in configured:
                executor.submit(_fetch_and_cache_provider, provider_id)

    threading.Thread(target=_prefetch_provider_models, daemon=True).start()


class LimitcodeChangeProviderCommand(sublime_plugin.WindowCommand):
    """Change the AI provider."""

    def run(self):
        from ..providers.provider_registry import ProviderRegistry
        providers = ProviderRegistry.get_provider_names()

        def on_select(idx):
            if idx >= 0:
                settings = sublime.load_settings("Limitcode.sublime-settings")
                settings.set("default_provider", providers[idx])
                sublime.save_settings("Limitcode.sublime-settings")
                sublime.status_message(f"Limitcode: Provider changed to {providers[idx]}")
                _refresh_chat_status_bar(self.window)

        self.window.show_quick_panel(providers, on_select)


class LimitcodeSetReasoningEffortCommand(sublime_plugin.WindowCommand):
    """Choose how much the model should think before answering."""

    LEVELS = ["off", "low", "medium", "high"]
    DESCRIPTIONS = {
        "off": "No extended reasoning (default)",
        "low": "Brief reasoning: faster and cheaper",
        "medium": "Balanced quality and cost",
        "high": "Deep reasoning: best for difficult tasks",
    }

    def run(self):
        settings = sublime.load_settings("Limitcode.sublime-settings")
        current = str(settings.get("reasoning_effort", "off")).lower()

        items = []
        for level in self.LEVELS:
            marker = "● " if level == current else "  "
            items.append(sublime.QuickPanelItem(
                f"{marker}{level.capitalize()}", self.DESCRIPTIONS[level]))

        def on_select(idx):
            if idx >= 0:
                settings.set("reasoning_effort", self.LEVELS[idx])
                sublime.save_settings("Limitcode.sublime-settings")
                sublime.status_message(
                    f"Limitcode: Reasoning effort = {self.LEVELS[idx]}")

        self.window.show_quick_panel(items, on_select)


class LimitcodeToggleShowThoughtsCommand(sublime_plugin.WindowCommand):
    """Toggle whether returned reasoning content is shown in chat."""

    def run(self):
        settings = sublime.load_settings("Limitcode.sublime-settings")
        enabled = not bool(settings.get("show_thoughts", False))
        settings.set("show_thoughts", enabled)
        sublime.save_settings("Limitcode.sublime-settings")
        state = "enabled" if enabled else "disabled"
        sublime.status_message(f"Limitcode: Show thoughts {state}")


class LimitcodeChangeModelCommand(sublime_plugin.WindowCommand):
    """Unified model selector: shows ALL models from ALL configured providers in one list."""

    # Provider display labels
    PROVIDER_LABELS = {
        "openai":            "OpenAI",
        "deepseek":          "DeepSeek",
        "anthropic":         "Anthropic",
        "gemini":            "Google Gemini",
        "ollama":            "Ollama (Local)",
        "lm-studio":         "LM Studio (Local)",
    }

    # Emergency fallback if a provider's list_models() fails completely
    FALLBACK_MODELS = {
        "openai":            ["gpt-5.5", "gpt-4o", "gpt-4o-mini", "o1", "o3-mini"],
        "deepseek":          ["deepseek-chat", "deepseek-reasoner"],
        "anthropic":         ["claude-sonnet-4-5", "claude-3-5-sonnet-20241022", "claude-3-opus-20240229"],
        "gemini":            ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"],
        "ollama":            ["llama3", "mistral", "qwen2.5-coder", "phi3"],
        "lm-studio":         ["meta-llama-3-8b-instruct", "qwen2.5-coder-7b-instruct"],
    }

    def run(self):
        # Load settings once — avoids 60+ redundant sublime.load_settings() calls in the loop
        settings = sublime.load_settings("Limitcode.sublime-settings")
        current_model = settings.get("default_model", "")
        current_provider = settings.get("default_provider", "")

        sublime.status_message("Limitcode: Loading model list...")

        from .limitcode import is_provider_configured

        all_entries = []  # (model_id, provider_id, display_label)
        seen_keys = set()

        for provider_id, label in self.PROVIDER_LABELS.items():
            # Pass pre-loaded settings to avoid N redundant load_settings() calls
            if not is_provider_configured(provider_id, settings):
                continue

            # TTL-aware memory cache
            models = _get_cached_models(provider_id)

            # 3. Emergency hardcoded fallback
            if not models:
                models = self.FALLBACK_MODELS.get(provider_id, [])
                if models:
                    from .logger import log_info
                    log_info("[MODELS] Using hardcoded fallback", {
                        "provider": provider_id,
                        "count": len(models),
                    })

            for m in models:
                key = (m, provider_id)
                if key not in seen_keys:
                    seen_keys.add(key)
                    all_entries.append((m, provider_id, label))

        # Build QuickPanel items
        panel_items = []
        for model_id, provider_id, provider_label in all_entries:
            active_marker = "● " if (model_id == current_model and provider_id == current_provider) else "  "

            panel_items.append(sublime.QuickPanelItem(
                f"{active_marker}{model_id} [{provider_label}]",
                f"Model from {provider_label}"
            ))

        # Add manual input option
        panel_items.append(sublime.QuickPanelItem("✏️  Enter model name...", "Manual"))

        def on_done(index):
            if index == -1:
                return
            if index == len(all_entries):
                self.window.show_input_panel(
                    "Model Name:",
                    current_model,
                    lambda m: self._save_model_and_provider(m, current_provider),
                    None, None
                )
                return
            chosen_model, chosen_provider, _ = all_entries[index]
            self._save_model_and_provider(chosen_model, chosen_provider)

        # Show the panel instantly with whatever is cached
        self.window.show_quick_panel(panel_items, on_done)

        # Refresh cache in background — parallel fetches, stampede-protected
        def _refresh_cache_quietly():
            from .limitcode import is_provider_configured
            from concurrent.futures import ThreadPoolExecutor
            configured = [
                pid for pid in self.PROVIDER_LABELS.keys()
                if is_provider_configured(pid)
            ]
            with ThreadPoolExecutor(max_workers=4) as executor:
                for pid in configured:
                    executor.submit(_fetch_and_cache_provider, pid)

        threading.Thread(target=_refresh_cache_quietly, daemon=True).start()

    def _save_model_and_provider(self, model: str, provider: str):
        if not model.strip():
            return
        from .logger import log_info
        settings = sublime.load_settings("Limitcode.sublime-settings")
        settings.set("default_model", model.strip())
        settings.set("default_provider", provider)
        sublime.save_settings("Limitcode.sublime-settings")
        log_info(f"[COMMANDS] Model set to {model} via provider {provider}")
        sublime.status_message(f"Limitcode: {provider} → {model}")

        # Sync active session
        try:
            from .chat import ChatView
            chat = ChatView.get_instance(self.window)
            if chat:
                chat.session.provider = provider
                chat.session.model = model
                _refresh_chat_status_bar(self.window)
        except:
            pass



class LimitcodeSetupApiKeyCommand(sublime_plugin.WindowCommand):
    """Set up the API key for the current or chosen provider."""

    def run(self):
        from ..providers.provider_registry import ProviderRegistry
        providers = ProviderRegistry.get_provider_names()

        def on_select_provider(idx):
            if idx >= 0:
                provider = providers[idx]
                settings = sublime.load_settings("Limitcode.sublime-settings")
                existing_key = settings.get("api_keys", {}).get(provider, "")
                
                def on_key_entered(key):
                    key = _sanitize_api_key(key)
                    if key:
                        api_keys = settings.get("api_keys", {})
                        api_keys[provider] = key
                        settings.set("api_keys", api_keys)
                        
                        sublime.save_settings("Limitcode.sublime-settings")
                        sublime.status_message(f"Limitcode: API Key saved for {provider}")
                        _update_chat_status()

                def _update_chat_status():
                    _refresh_chat_status_bar(self.window)
                
                self.window.show_input_panel(
                    f"Enter API Key for {provider}:",
                    existing_key,
                    on_key_entered,
                    None,
                    None
                )

        self.window.show_quick_panel(providers, on_select_provider)






class LimitcodeClearChatCommand(sublime_plugin.WindowCommand):
    """Clear the current chat."""

    def run(self):
        chat = ChatView.get_instance(self.window)
        chat.clear()


class LimitcodeHistoryUpCommand(sublime_plugin.TextCommand):
    """Navigate up in prompt history."""

    def run(self, edit):
        chat = ChatView.get_instance(self.view.window())
        current = chat.get_input_text()
        navigated = chat.session.navigate_history(-1, current)
        chat.replace_input_text(navigated)


class LimitcodeHistoryDownCommand(sublime_plugin.TextCommand):
    """Navigate down in prompt history."""

    def run(self, edit):
        chat = ChatView.get_instance(self.view.window())
        current = chat.get_input_text()
        navigated = chat.session.navigate_history(1, current)
        chat.replace_input_text(navigated)


class LimitcodeProcessMessageCommand(sublime_plugin.WindowCommand):
    """Process a chat message through the agent."""

    def run(self, message: str):
        from .logger import log_info
        log_info(f"[COMMANDS] LimitcodeProcessMessageCommand run on window {self.window.id() if self.window else 'None'}")
        
        view = self.window.active_view()
        context = ""

        # Only get context from code files, NOT from the chat view
        if view and not view.settings().get("limitcode_chat_view"):
            if view.sel() and not view.sel()[0].empty():
                context = view.substr(view.sel()[0])
            elif view.file_name():
                # Only get full content for actual code files
                context = view.substr(sublime.Region(0, view.size()))

        chat = ChatView.get_instance(self.window)
        if not chat:
            log_info("[COMMANDS] LimitcodeProcessMessageCommand could not resolve ChatView!")
            return
        log_info(f"[COMMANDS] Processing message for window {self.window.id() if self.window else 'None'}, session={chat.session.session_id}")

        # Single shared execution route (see agent_runner.py)
        run_agent_async(self.window, chat, message, context, source="chat")


class LimitcodeCancelRequestCommand(sublime_plugin.WindowCommand):
    """Cancel the current running agent request."""
    
    def run(self):
        from .chat import ChatView
        chat = ChatView.get_instance(self.window)
        if hasattr(chat, "_current_agent") and chat._current_agent:
            if hasattr(chat._current_agent, "cancel"):
                chat._current_agent.cancel()
            else:
                chat._current_agent.is_cancelled = True
            chat._active_run_token = None
            sublime.status_message("Limitcode: Cancelling request...")
            chat.hide_loading()
            chat.append_text("\n\n[Cancelling...]")
            chat.prepare_for_user()
            chat.on_stream_complete()
            chat._current_agent = None
        else:
            sublime.status_message("Limitcode: No active request")


class LimitcodeWriteBufferCommand(sublime_plugin.TextCommand):
    """Replace the entire view buffer with the provided content."""
    def run(self, edit, content: str):
        self.view.replace(edit, sublime.Region(0, self.view.size()), content)
