"""
Provider Registry for Limitcode.
Supports six providers through a unified interface.
"""

import sublime
from typing import Dict, Type, Optional
from .base import BaseProvider


class ProviderRegistry:
    """
    Registry of all supported AI providers.
    
    Providers are registered by name and instantiated on demand.
    """
    
    _providers: Dict[str, Type[BaseProvider]] = {}
    _provider_configs: Dict[str, Dict] = {}
    
    @classmethod
    def initialize(cls):
        """Register all built-in providers."""
        from .openai_compatible import OpenAICompatibleProvider
        
        cls.register("openai", OpenAICompatibleProvider)
        cls.register("deepseek", OpenAICompatibleProvider)
        cls.register("ollama", OpenAICompatibleProvider)
        cls.register("lm-studio", OpenAICompatibleProvider)
        
        from .anthropic import AnthropicProvider
        cls.register("anthropic", AnthropicProvider)
        
        from .gemini import GeminiProvider
        cls.register("gemini", GeminiProvider)
    
    @classmethod
    def register(cls, name: str, provider_class: Type[BaseProvider]):
        """Register a provider class."""
        cls._providers[name] = provider_class
    
    @classmethod
    def create(
        cls,
        provider_name: str,
        api_key: str,
        model: str,
        base_url: Optional[str] = None,
        extra_config: Optional[Dict] = None
    ) -> BaseProvider:
        """
        Create a provider instance.
        
        Args:
            provider_name: Provider identifier (e.g. "openai", "anthropic")
            api_key: API key
            model: Model name
            base_url: Optional custom base URL
            extra_config: Additional provider-specific config
        
        Returns:
            Provider instance
        """
        provider_class = cls._providers.get(provider_name)
        if not provider_class:
            available = ", ".join(sorted(cls._providers.keys()))
            raise ValueError(
                f"Unknown provider: {provider_name}. Available: {available}"
            )
        
        return provider_class(
            api_key=api_key,
            model=model,
            base_url=base_url,
            extra_config=extra_config or {}
        )
    
    @classmethod
    def get_provider_names(cls) -> list:
        """Get list of registered provider names."""
        return sorted(cls._providers.keys())
    
    @classmethod
    def get_compatible_providers(cls) -> list:
        """Get providers that use the OpenAI-compatible format."""
        from .openai_compatible import OpenAICompatibleProvider
        return [
            name for name, cls_type in cls._providers.items()
            if cls_type == OpenAICompatibleProvider
        ]
