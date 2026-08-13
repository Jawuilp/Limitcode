"""
Limitcode - AI Agentic Coding Assistant for Sublime Text
Main plugin entry point.
"""

import sublime
import sublime_plugin
from typing import Optional

from .providers.provider_registry import ProviderRegistry
from .providers.base import BaseProvider


# Plugin lifecycle (plugin_loaded/plugin_unloaded) lives in __init__.py;
# this module only provides the factories below.

def get_provider(provider_name: Optional[str] = None, model: Optional[str] = None, session_id: Optional[str] = None) -> BaseProvider:
    """Create a provider from current settings or given parameters."""
    settings = sublime.load_settings("Limitcode.sublime-settings")
    
    if provider_name is None:
        provider_name = settings.get("default_provider", "openai")
    if model is None:
        model = settings.get("default_model", "gpt-5.5")
        
    api_keys_dict = settings.get("api_keys", {})
    api_key = api_keys_dict.get(provider_name, "")
    
    # Finally, fallback to global api_key if still empty
    if not api_key:
        api_key = settings.get("api_key", "")
        
    base_url = _get_provider_base_url(settings, provider_name)

    local_providers = ["ollama", "lm-studio"]
    if not api_key and provider_name not in local_providers:
        raise ValueError(f"No API key configured for {provider_name}. Run 'Limitcode: Setup Provider API Key' or set it in Limitcode.sublime-settings")

    return ProviderRegistry.create(
        provider_name=provider_name,
        api_key=api_key,
        model=model,
        base_url=base_url,
        extra_config={"provider_name": provider_name, "session_id": session_id}
    )


def is_provider_configured(provider_name: str, settings=None) -> bool:
    """
    Check if a provider has valid credentials without throwing.
    Returns True if the provider can be used, False otherwise.
    Accepts an optional pre-loaded settings object to avoid redundant load_settings() calls.
    """
    if settings is None:
        settings = sublime.load_settings("Limitcode.sublime-settings")

    local_providers = ["ollama", "lm-studio"]
    if provider_name in local_providers:
        default_provider = settings.get("default_provider")
        api_keys = settings.get("api_keys", {}) or {}
        provider_base_urls = settings.get("provider_base_urls", {}) or {}
        
        is_default = (default_provider == provider_name)
        has_api_key = (provider_name in api_keys)
        has_base_url = (isinstance(provider_base_urls, dict) and provider_name in provider_base_urls)
        
        return bool(is_default or has_api_key or has_base_url)

    # Standard API key check
    api_keys = settings.get("api_keys", {})
    provider_key = api_keys.get(provider_name, "").strip()
    return bool(provider_key)


def _get_provider_base_url(settings, provider_name: str) -> Optional[str]:
    """Return provider-specific base URL, with legacy custom fallback."""
    provider_base_urls = settings.get("provider_base_urls", {}) or {}
    if isinstance(provider_base_urls, dict):
        base_url = provider_base_urls.get(provider_name)
        if isinstance(base_url, str) and base_url.strip():
            return base_url.strip()

    return None
