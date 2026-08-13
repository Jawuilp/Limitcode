import json
import sys
import types
import unittest

from tests.package_loader import load_limitcode_package
from tests.sublime_stub import install_sublime_stub

install_sublime_stub()

sublime_plugin = types.ModuleType("sublime_plugin")
sublime_plugin.WindowCommand = type("WindowCommand", (), {})
sublime_plugin.TextCommand = type("TextCommand", (), {})
sublime_plugin.ViewEventListener = type("ViewEventListener", (), {})
sys.modules["sublime_plugin"] = sublime_plugin

load_limitcode_package()
import sublime
from Limitcode.limitcode import get_provider, is_provider_configured
from Limitcode.providers.openai_compatible import OpenAICompatibleProvider
from Limitcode.providers.provider_registry import ProviderRegistry


class ProviderConfigTest(unittest.TestCase):
    def setUp(self):
        sublime._settings_store.clear()
        ProviderRegistry._providers.clear()
        ProviderRegistry.initialize()

    def _capture_tool_payload(self, provider, temperature):
        captured_payloads = []

        class EmptyResponse:
            status = 200

            def read(self):
                return b""

            def close(self):
                pass

            def __iter__(self):
                return iter(())

        def mock_make_https_request(host, port, method, path, headers, body=None, **kwargs):
            if body:
                captured_payloads.append(json.loads(body))
            return EmptyResponse()

        provider._make_https_request = mock_make_https_request
        provider.create_message_with_tools(
            system_prompt="system",
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
            max_tokens=8192,
            temperature=temperature,
        )
        return captured_payloads[-1]

    def test_auto_temperature_is_omitted_across_providers(self):
        cases = [
            ("openai", "gpt-4o"),
            ("anthropic", "claude-sonnet-4-6"),
            ("gemini", "gemini-2.5-flash"),
        ]

        for provider_name, model in cases:
            provider = ProviderRegistry.create(
                provider_name,
                api_key="test",
                model=model,
                extra_config={"provider_name": provider_name},
            )
            payload = self._capture_tool_payload(provider, None)
            generation = payload.get("generationConfig", payload)
            self.assertNotIn("temperature", generation, provider_name)
            self.assertEqual(
                generation.get("maxOutputTokens", payload.get("max_tokens")),
                8192,
                provider_name,
            )

    def test_manual_temperature_is_sent_across_providers(self):
        cases = [
            ("openai", "gpt-4o"),
            ("anthropic", "claude-sonnet-4-6"),
            ("gemini", "gemini-2.5-flash"),
        ]

        for provider_name, model in cases:
            provider = ProviderRegistry.create(
                provider_name,
                api_key="test",
                model=model,
                extra_config={"provider_name": provider_name},
            )
            payload = self._capture_tool_payload(provider, 0.25)
            generation = payload.get("generationConfig", payload)
            self.assertEqual(generation.get("temperature"), 0.25, provider_name)

    def test_openai_reasoning_models_use_max_completion_tokens(self):
        provider = ProviderRegistry.create(
            "openai",
            api_key="test",
            model="gpt-5.5",
            extra_config={"provider_name": "openai"},
        )
        payload = {"model": "gpt-5.5", "max_tokens": 8192, "temperature": 0.7}

        provider._apply_provider_payload_options(payload)

        self.assertEqual(payload["max_completion_tokens"], 8192)
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("temperature", payload)

    def test_gpt_5_6_tools_disable_reasoning_on_chat_completions(self):
        sublime._settings_store["reasoning_effort"] = "high"
        provider = ProviderRegistry.create(
            "openai",
            api_key="test",
            model="gpt-5.6-luna",
            extra_config={"provider_name": "openai"},
        )
        payload = {
            "model": "gpt-5.6-luna",
            "max_tokens": 8192,
            "tools": [{"type": "function", "function": {"name": "read_file"}}],
        }

        provider._apply_provider_payload_options(payload)

        self.assertEqual(payload["reasoning_effort"], "none")
        self.assertEqual(payload["max_completion_tokens"], 8192)
        self.assertNotIn("max_tokens", payload)

    def test_openai_models_are_fetched_from_live_endpoint(self):
        provider = ProviderRegistry.create(
            "openai",
            api_key="test",
            model="gpt-4o",
            extra_config={"provider_name": "openai"},
        )
        calls = []

        class Response:
            status = 200

            def read(self):
                return json.dumps({"data": [{"id": "gpt-live-model"}]}).encode()

            def close(self):
                pass

            def getheader(self, name):
                return None

        def request(host, port, method, path, headers, body=None, **kwargs):
            calls.append((host, port, method, path))
            return Response()

        provider._make_https_request = request

        self.assertEqual(provider._list_models_fallback(), ["gpt-live-model"])
        self.assertEqual(calls[0][2:], ("GET", "/v1/models"))

    def test_deepseek_models_use_their_live_endpoint(self):
        provider = ProviderRegistry.create(
            "deepseek",
            api_key="test",
            model="deepseek-chat",
            extra_config={"provider_name": "deepseek"},
        )
        calls = []

        class Response:
            status = 200

            def read(self):
                return json.dumps({"data": [{"id": "deepseek-live-model"}]}).encode()

            def close(self):
                pass

            def getheader(self, name):
                return None

        def request(host, port, method, path, headers, body=None, **kwargs):
            calls.append((host, port, method, path))
            return Response()

        provider._make_https_request = request

        self.assertEqual(provider._list_models_fallback(), ["deepseek-live-model"])
        self.assertEqual(calls[0][2:], ("GET", "/models"))



    def test_openai_compatible_formats_image_messages(self):
        provider = ProviderRegistry.create(
            "openai",
            api_key="test",
            model="gpt-4o",
            extra_config={"provider_name": "openai"}
        )
        
        raw_messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is this?"},
                    {"type": "image", "mime_type": "image/png", "data": "base64data", "path": "test.png"}
                ]
            }
        ]
        
        formatted = provider._format_messages(raw_messages)
        content = formatted[0]["content"]
        
        self.assertEqual(len(content), 2)
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1]["type"], "image_url")
        self.assertEqual(content[1]["image_url"]["url"], "data:image/png;base64,base64data")

    def test_gemini_provider_reports_image_support_for_gemini_and_gemma(self):
        gemini = ProviderRegistry.create(
            "gemini",
            api_key="test",
            model="gemini-2.5-flash",
            extra_config={"provider_name": "gemini"}
        )
        gemma = ProviderRegistry.create(
            "gemini",
            api_key="test",
            model="gemma-4-26b-a4b-it",
            extra_config={"provider_name": "gemini"}
        )

        self.assertTrue(gemini.supports_images())
        self.assertTrue(gemma.supports_images())



    def test_anthropic_prompt_caching_payload(self):
        from Limitcode.providers.anthropic import AnthropicProvider
        import json
        from unittest.mock import Mock

        provider = ProviderRegistry.create(
            "anthropic",
            api_key="test-key",
            model="claude-sonnet-4-6",
            extra_config={"provider_name": "anthropic"}
        )

        self.assertIsInstance(provider, AnthropicProvider)

        # Mock _make_https_request to capture the body/payload sent
        captured_payloads = []

        def mock_make_https_request(host, port, method, path, headers, body=None):
            if body:
                captured_payloads.append(json.loads(body))
            # Return a mock response that can be iterated or read
            mock_resp = Mock()
            mock_resp.status = 200
            mock_resp.read.return_value = b'{"data": []}'
            mock_resp.readline.return_value = b''
            mock_resp.close = lambda: None
            mock_resp.__iter__ = lambda self: iter([])
            return mock_resp

        provider._make_https_request = mock_make_https_request

        # Test create_message
        list(provider.create_message(
            system_prompt="system test",
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=100,
            temperature=0.5
        ))

        self.assertEqual(len(captured_payloads), 1)
        self.assertEqual(captured_payloads[0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(captured_payloads[0]["model"], "claude-sonnet-4-6")

        # Test create_message_with_tools
        captured_payloads.clear()
        provider.create_message_with_tools(
            system_prompt="system test with tools",
            messages=[{"role": "user", "content": "hello"}],
            tools=[{
                "type": "function",
                "function": {
                    "name": "test_tool",
                    "description": "test desc",
                    "parameters": {"type": "object", "properties": {}}
                }
            }],
            max_tokens=100,
            temperature=0.5
        )

        self.assertEqual(len(captured_payloads), 1)
        self.assertEqual(captured_payloads[0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(captured_payloads[0]["tools"][0]["name"], "test_tool")

    def _make_anthropic_with_error_response(self, status, body):
        from unittest.mock import Mock

        provider = ProviderRegistry.create(
            "anthropic",
            api_key="bad-key",
            model="claude-sonnet-4-6",
            extra_config={"provider_name": "anthropic"},
        )
        mock_resp = Mock()
        mock_resp.status = status
        mock_resp.read.return_value = body
        mock_resp.close = lambda: None
        provider._make_https_request = lambda *a, **k: mock_resp
        return provider

    def test_anthropic_auth_error_raises_credits_error(self):
        from Limitcode.providers.base import CreditsError

        provider = self._make_anthropic_with_error_response(
            401,
            b'{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"}}',
        )
        with self.assertRaises(CreditsError) as ctx:
            provider.create_message_with_tools(
                system_prompt="s",
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
            )
        self.assertIn("invalid x-api-key", str(ctx.exception))

    def test_anthropic_rate_limit_raises_rate_limit_error(self):
        from Limitcode.providers.base import RateLimitError

        provider = self._make_anthropic_with_error_response(
            429,
            b'{"type":"error","error":{"type":"rate_limit_error","message":"slow down"}}',
        )
        with self.assertRaises(RateLimitError):
            provider.create_message_with_tools(
                system_prompt="s",
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
            )

    def test_anthropic_simple_stream_surfaces_auth_error(self):
        from Limitcode.providers.base import CreditsError

        provider = self._make_anthropic_with_error_response(
            403,
            b'{"type":"error","error":{"type":"permission_error","message":"forbidden"}}',
        )
        with self.assertRaises(CreditsError):
            list(provider.create_message(
                system_prompt="s",
                messages=[{"role": "user", "content": "hi"}],
            ))


class ReasoningEffortTest(unittest.TestCase):
    """The reasoning_effort setting maps to each provider's own API knob
    and is never sent to models that would reject it."""

    def setUp(self):
        sublime._settings_store.clear()
        ProviderRegistry._providers.clear()
        ProviderRegistry.initialize()

    def _capture_payload(self, provider):
        from unittest.mock import Mock
        captured = []

        def fake_request(host, port, method, path, headers, body=None, timeout=120):
            if body:
                captured.append(json.loads(body))
            resp = Mock()
            resp.status = 200
            resp.read.return_value = b"{}"
            resp.readline.return_value = b""
            resp.close = lambda: None
            resp.__iter__ = lambda self: iter([])
            return resp

        provider._make_https_request = fake_request
        return captured

    def test_anthropic_effort_applied_on_supported_model(self):
        sublime._settings_store["reasoning_effort"] = "high"
        provider = ProviderRegistry.create(
            "anthropic", api_key="k", model="claude-sonnet-4-6",
            extra_config={"provider_name": "anthropic"})
        captured = self._capture_payload(provider)

        provider.create_message_with_tools(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}], tools=[])

        self.assertEqual(captured[0]["output_config"], {"effort": "high"})

    def test_anthropic_effort_skipped_when_off_or_unsupported_model(self):
        provider = ProviderRegistry.create(
            "anthropic", api_key="k", model="claude-sonnet-4-6",
            extra_config={"provider_name": "anthropic"})
        captured = self._capture_payload(provider)
        provider.create_message_with_tools(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}], tools=[])
        self.assertNotIn("output_config", captured[0])

        sublime._settings_store["reasoning_effort"] = "high"
        provider = ProviderRegistry.create(
            "anthropic", api_key="k", model="claude-haiku-4-5",
            extra_config={"provider_name": "anthropic"})
        captured = self._capture_payload(provider)
        provider.create_message_with_tools(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}], tools=[])
        self.assertNotIn("output_config", captured[0],
                         "haiku does not accept the effort parameter")

    def test_openai_effort_only_for_reasoning_models(self):
        sublime._settings_store["reasoning_effort"] = "low"

        reasoning = ProviderRegistry.create(
            "openai", api_key="k", model="gpt-5.2",
            extra_config={"provider_name": "openai"})
        payload = {"model": "gpt-5.2", "temperature": 0.7}
        reasoning._apply_provider_payload_options(payload)
        self.assertEqual(payload["reasoning_effort"], "low")

        classic = ProviderRegistry.create(
            "openai", api_key="k", model="gpt-4o",
            extra_config={"provider_name": "openai"})
        payload = {"model": "gpt-4o", "temperature": 0.7}
        classic._apply_provider_payload_options(payload)
        self.assertNotIn("reasoning_effort", payload)

        other = ProviderRegistry.create(
            "deepseek", api_key="k", model="deepseek-chat",
            extra_config={"provider_name": "deepseek"})
        payload = {"model": "gpt-5.2", "temperature": 0.7}
        other._apply_provider_payload_options(payload)
        self.assertNotIn("reasoning_effort", payload,
                         "only the openai provider gets reasoning_effort")

    def test_deepseek_v4_models_get_thinking_and_user_effort(self):
        effort_cases = {
            "off": None,
            "low": "high",
            "medium": "high",
            "high": "high",
        }

        for effort, expected in effort_cases.items():
            sublime._settings_store["reasoning_effort"] = effort
            for model in ("deepseek-v4-flash", "deepseek-v4-pro", "deepseek-reasoner"):
                provider = ProviderRegistry.create(
                    "deepseek", api_key="k", model=model,
                    extra_config={"provider_name": "deepseek"})
                payload = {"model": model, "temperature": 0.7, "top_p": 0.9}
                provider._apply_provider_payload_options(payload)
                self.assertEqual(payload.get("thinking"), {"type": "enabled"}, model)
                self.assertEqual(payload.get("reasoning_effort"), expected, (effort, model))
                self.assertNotIn("temperature", payload)
                self.assertNotIn("top_p", payload)

        # Non-thinking legacy model stays untouched
        provider = ProviderRegistry.create(
            "deepseek", api_key="k", model="deepseek-chat",
            extra_config={"provider_name": "deepseek"})
        payload = {"model": "deepseek-chat", "temperature": 0.7}
        provider._apply_provider_payload_options(payload)
        self.assertNotIn("thinking", payload)

    def test_gemini_effort_sets_thinking_budget_on_supported_models(self):
        sublime._settings_store["reasoning_effort"] = "high"
        provider = ProviderRegistry.create(
            "gemini", api_key="k", model="gemini-2.5-pro",
            extra_config={"provider_name": "gemini"})

        payload = {"generationConfig": {"maxOutputTokens": 100}}
        provider._apply_reasoning_effort(payload)
        self.assertEqual(
            payload["generationConfig"]["thinkingConfig"], {"thinkingBudget": 24576})

        old = ProviderRegistry.create(
            "gemini", api_key="k", model="gemini-1.5-pro",
            extra_config={"provider_name": "gemini"})
        payload = {"generationConfig": {"maxOutputTokens": 100}}
        old._apply_reasoning_effort(payload)
        self.assertNotIn("thinkingConfig", payload["generationConfig"])



    def test_invalid_setting_values_mean_off(self):
        from Limitcode.providers.base import BaseProvider
        for value in ("off", "", None, "ultra", 42):
            sublime._settings_store["reasoning_effort"] = value
            provider = ProviderRegistry.create(
                "anthropic", api_key="k", model="claude-sonnet-4-6",
                extra_config={"provider_name": "anthropic"})
            self.assertIsNone(provider.get_reasoning_effort(), repr(value))


if __name__ == "__main__":
    unittest.main()
