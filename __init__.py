"""
Limitcode - AI Agentic Coding Assistant for Sublime Text.
Plugin lifecycle entry point: initialization and cleanup live here.
"""

import sublime
from .agent import Agent
from .chat import ChatView
from .providers.provider_registry import ProviderRegistry


def plugin_loaded():
    """Initialize the plugin."""
    ProviderRegistry.initialize()
    
    # Prefetch models in background to avoid 2s delay in UI
    try:
        from .commands import prefetch_models_in_background
        prefetch_models_in_background()
    except Exception as e:
        from .logger import log_error
        log_error("[PLUGIN] Failed to start model prefetcher", {"error": str(e)})
        
    sublime.status_message("Limitcode loaded")


def plugin_unloaded():
    """Clean up the plugin."""
    sublime.status_message("Limitcode unloaded")
