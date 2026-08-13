"""Unified agent execution route for chat and editor requests."""

import sublime
import math
import os
import threading
from typing import Optional

from .limitcode import get_provider
from .agent import Agent
from .tools.tools import ToolManager
from .logger import log_info


class StartingAgentPlaceholder:
    """Cancellable stand-in registered before the real Agent exists,
    so 'cancel' works during provider/agent setup."""

    def __init__(self):
        self.is_cancelled = False
        self._agent = None
        self._lock = threading.Lock()

    def attach_agent(self, agent):
        with self._lock:
            self._agent = agent
            is_cancelled = self.is_cancelled
        if is_cancelled:
            agent.cancel()

    def cancel(self):
        with self._lock:
            self.is_cancelled = True
            agent = self._agent
        if agent:
            agent.cancel()


def resolve_generation_settings(settings, provider):
    """Validate generation settings and return effective max tokens/temperature."""
    raw_temperature = settings.get("temperature", "auto")
    if raw_temperature is None or str(raw_temperature).strip().lower() == "auto":
        temperature = None
    else:
        if isinstance(raw_temperature, bool):
            raise ValueError("temperature must be 'auto' or a number between 0 and 2")
        try:
            temperature = float(raw_temperature)
        except (TypeError, ValueError):
            raise ValueError("temperature must be 'auto' or a number between 0 and 2")
        if not math.isfinite(temperature) or not 0 <= temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        if provider.PROVIDER_NAME == "anthropic" and temperature > 1:
            raise ValueError("Anthropic temperature must be between 0 and 1")

    raw_max_tokens = settings.get("max_tokens", 8192)
    if raw_max_tokens is None or str(raw_max_tokens).strip().lower() == "auto":
        max_tokens = int(provider.get_model_info().get("max_tokens", 8192))
    else:
        if isinstance(raw_max_tokens, bool):
            raise ValueError("max_tokens must be 'auto' or a positive integer")
        try:
            numeric_max_tokens = float(raw_max_tokens)
        except (TypeError, ValueError):
            raise ValueError("max_tokens must be 'auto' or a positive integer")
        if not numeric_max_tokens.is_integer() or numeric_max_tokens <= 0:
            raise ValueError("max_tokens must be 'auto' or a positive integer")
        max_tokens = int(numeric_max_tokens)

    return max_tokens, temperature


def resolve_working_directory(window: sublime.Window, view: Optional[sublime.View]) -> str:
    folders = window.folders()
    if folders:
        return folders[0]
    if view and view.file_name():
        return os.path.dirname(view.file_name())
    return os.getcwd()


# Permission asking is disabled in Lite version (handled implicitly via open tabs)


def run_agent_async(window: sublime.Window, chat, message: str, context: str = "",
                    source: str = "chat") -> threading.Thread:
    """Run the agent in a background thread through the single shared route.

    The caller must have already appended the user message to the chat view.
    Returns the started thread (useful for tests).
    """
    placeholder = StartingAgentPlaceholder()
    run_token = object()
    if chat:
        previous_agent = getattr(chat, "_current_agent", None)
        chat._active_run_token = None
        if previous_agent and hasattr(previous_agent, "cancel"):
            previous_agent.cancel()
        chat._active_run_token = run_token
        chat._current_agent = placeholder

    view = window.active_view()
    agent = None

    def _run_is_active() -> bool:
        return bool(chat and getattr(chat, "_active_run_token", None) is run_token)

    def _agent_is_active() -> bool:
        return _run_is_active() and agent is not None and chat._current_agent is placeholder and not agent.is_cancelled

    def _run_can_report() -> bool:
        return _run_is_active() and chat._current_agent is placeholder and not placeholder.is_cancelled

    def _dispatch_if_active(callback):
        def _guarded_callback():
            if _agent_is_active():
                callback()
        sublime.set_timeout(_guarded_callback)

    def _run():
        nonlocal agent
        try:
            if placeholder.is_cancelled or not _run_is_active():
                return

            # ALWAYS read current settings first so 'Change Provider' and
            # 'Change Model' take effect immediately.
            settings = sublime.load_settings("Limitcode.sublime-settings")
            provider_name = settings.get("default_provider", "openai")
            model = settings.get("default_model", "gpt-5.5")

            # If no global setting, fall back to what the session stored
            if not provider_name:
                provider_name = chat.session.provider or "openai"
            if not model:
                model = chat.session.model or "gpt-5.5"

            # Keep session in sync for persistence/history
            chat.session.provider = provider_name
            chat.session.model = model

            provider = get_provider(provider_name, model, session_id=chat.session.session_id)
            provider_type = provider.PROVIDER_NAME
            max_tokens, temperature = resolve_generation_settings(settings, provider)

            tool_manager = ToolManager()
            system_prompt = ""
            user_system_prompt = settings.get("system_prompt", "")
            if user_system_prompt:
                system_prompt += f"\n\nAdditional User Instructions:\n{user_system_prompt}"

            max_iterations = settings.get("max_iterations", 50)

            log_info(f"[AGENT_RUNNER] Starting agent run ({source})",
                     {"prompt": message[:50], "provider": provider_name, "model": model,
                      "max_tokens": max_tokens, "temperature": temperature})

            agent = Agent(
                provider=provider,
                provider_type=provider_type,
                tool_manager=tool_manager,
                system_prompt=system_prompt,
                on_text_chunk=lambda chunk: _dispatch_if_active(lambda: chat.append_text(chunk)),
                on_tool_call=lambda name, args, meta=None: _dispatch_if_active(lambda: chat.append_tool_call(name, args, meta)),
                on_tool_result=lambda name, result: _dispatch_if_active(lambda: chat.append_tool_result(name, result)),
                on_status=lambda msg: _dispatch_if_active(lambda: sublime.status_message(f"Limitcode: {msg}")),
                max_iterations=max_iterations,
            )

            if placeholder.is_cancelled or not _run_is_active():
                return

            # Keep the placeholder as the stable run controller. It delegates
            # cancellation once the real agent has been constructed.
            placeholder.attach_agent(agent)
            if placeholder.is_cancelled or not _run_is_active():
                agent.cancel()
                return

            _dispatch_if_active(lambda: chat.show_loading("Limitcode is thinking..."))

            # Use project folder as working directory for full project access
            directory = resolve_working_directory(window, view)

            result = agent.run(
                user_message=message,
                context=context,
                directory=directory,
                window=window,
                conversation_history=chat.session.api_messages,
                max_tokens=max_tokens,
                temperature=temperature,
                on_iteration_complete=None,
            )

            if result is None and _agent_is_active():
                log_info("[AGENT_RUNNER] ERROR: agent.run() returned None")
                _dispatch_if_active(lambda: chat.append_error("Agent returned no result"))
                return
            if result is None or not _agent_is_active():
                return

            log_info("[AGENT_RUNNER] agent.run() completed",
                     {"content_len": len(result.content) if result.content else 0})

            # Save conversation history for memory
            if result.messages:
                chat.session.api_messages = result.messages

            if result.error:
                _dispatch_if_active(lambda: chat.append_error(result.error))

        except Exception as e:
            error_msg = str(e)
            log_info(f"[AGENT_RUNNER] Exception in agent thread: {error_msg}")
            if "not JSON serializable" in error_msg:
                error_msg = "Error interno de comunicacion con el proveedor. El plugin ya aplico una correccion; reinicia Sublime Text para cargarla."
            if _run_can_report():
                sublime.set_timeout(lambda: sublime.error_message(f"Limitcode Error: {error_msg}"))
                sublime.set_timeout(lambda: chat.append_error(error_msg))
        finally:
            def _finish_chat_ui():
                if not _run_is_active():
                    return
                chat.on_stream_complete()
                chat.prepare_for_user()
                chat._current_agent = None
                chat._active_run_token = None
            sublime.set_timeout(_finish_chat_ui)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread
