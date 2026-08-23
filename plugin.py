"""
Limitcode - AI Agentic Coding Assistant for Sublime Text.
Plugin lifecycle entry point: initialization and cleanup live here.
"""

import sublime
from .lib.agent import Agent
from .lib.chat import ChatView
from .providers.provider_registry import ProviderRegistry

# Re-export Sublime command/listener classes so the plugin host registers them.
# (Sublime only scans top-level modules of a package for plugin classes.)
from .lib.commands import *  # noqa: F401,F403
from .lib.session_commands import *  # noqa: F401,F403
from .lib.debug_listener import *  # noqa: F401,F403
from .lib.chat import (  # noqa: F401
    LimitcodePasteCommand,
    LimitcodeChatInputListener,
    LimitcodeSendChatCommand,
    LimitcodeInternalClearCommand,
    LimitcodeInternalAppendCommand,
    LimitcodeInternalReplaceInputCommand,
)



def plugin_loaded():
    """Initialize the plugin."""
    ProviderRegistry.initialize()

    # Prefetch models in background to avoid 2s delay in UI
    try:
        from .lib.commands import prefetch_models_in_background
        prefetch_models_in_background()
    except Exception as e:
        from .lib.logger import log_error
        log_error("[PLUGIN] Failed to start model prefetcher", {"error": str(e)})

    sublime.status_message("Limitcode loaded")


def plugin_unloaded():
    """Clean up the plugin."""
    sublime.status_message("Limitcode unloaded")
