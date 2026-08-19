import os
import threading
import unittest

from tests.package_loader import load_limitcode_package
from tests.sublime_stub import install_sublime_stub
install_sublime_stub()

import sublime
# agent_runner schedules UI work via set_timeout; run it synchronously in tests
sublime.set_timeout = lambda f, delay=0: f()
sublime.error_message = lambda msg: None
sublime.status_message = lambda msg: None

load_limitcode_package()
import Limitcode.lib.agent_runner as agent_runner


class FakeResult:
    def __init__(self, messages=None, error=None, content="ok"):
        self.messages = messages if messages is not None else []
        self.error = error
        self.content = content


class FakeAgent:
    next_result = None
    next_exception = None
    last_instance = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.is_cancelled = False
        FakeAgent.last_instance = self

    def run(self, **kwargs):
        self.run_kwargs = kwargs
        if FakeAgent.next_exception is not None:
            raise FakeAgent.next_exception
        return FakeAgent.next_result or FakeResult()

    def cancel(self):
        self.is_cancelled = True


class FakeProvider:
    PROVIDER_NAME = "fake"

    def get_model_info(self):
        return {"max_tokens": 12345}


class FakeSession:
    def __init__(self):
        self.session_id = "test_session"
        self.provider = None
        self.model = None
        self.api_messages = []


class FakeChat:
    def __init__(self):
        self.session = FakeSession()
        self._current_agent = None
        self.calls = []

    def append_text(self, text):
        self.calls.append(("text", text))

    def append_tool_call(self, name, args, meta=None):
        self.calls.append(("tool_call", name))

    def append_tool_result(self, name, result):
        self.calls.append(("tool_result", name))

    def append_error(self, error):
        self.calls.append(("error", error))

    def append_status(self, status):
        self.calls.append(("status", status))

    def show_loading(self, message=""):
        self.calls.append(("show_loading", message))

    def prepare_for_user(self):
        self.calls.append(("prepare_for_user", None))

    def on_stream_complete(self):
        self.calls.append(("on_stream_complete", None))

    def call_names(self):
        return [name for name, _ in self.calls]


class FakeWindow:
    def folders(self):
        return []

    def active_view(self):
        return None

    def id(self):
        return 1


class AgentRunnerTest(unittest.TestCase):
    def setUp(self):
        self._orig_agent = agent_runner.Agent
        self._orig_get_provider = agent_runner.get_provider
        agent_runner.Agent = FakeAgent
        agent_runner.get_provider = lambda *a, **k: FakeProvider()
        FakeAgent.next_result = None
        FakeAgent.next_exception = None
        FakeAgent.last_instance = None

        sublime._settings_store.clear()
        sublime._settings_store.update({
            "default_provider": "openai",
            "default_model": "test-model",
            "default_mode": "code",
        })

        self.chat = FakeChat()
        self.window = FakeWindow()

    def tearDown(self):
        agent_runner.Agent = self._orig_agent
        agent_runner.get_provider = self._orig_get_provider

    def _run(self, message="hola", context=""):
        thread = agent_runner.run_agent_async(self.window, self.chat, message, context)
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())

    def test_syncs_session_provider_and_model(self):
        self._run()
        self.assertEqual(self.chat.session.provider, "openai")
        self.assertEqual(self.chat.session.model, "test-model")

    def test_persists_api_messages_from_result(self):
        FakeAgent.next_result = FakeResult(messages=[
            {"role": "user", "content": "hola"},
            {"role": "assistant", "content": "ok"},
        ])
        self._run()
        self.assertEqual(len(self.chat.session.api_messages), 2)

    def test_cleanup_always_runs_on_success(self):
        self._run()
        names = self.chat.call_names()
        self.assertIn("prepare_for_user", names)
        self.assertIn("on_stream_complete", names)
        self.assertIsNone(self.chat._current_agent)

    def test_cleanup_and_error_report_on_exception(self):
        FakeAgent.next_exception = RuntimeError("boom")
        self._run()
        names = self.chat.call_names()
        self.assertIn("error", names)
        self.assertIn("prepare_for_user", names)
        self.assertIn("on_stream_complete", names)
        self.assertIsNone(self.chat._current_agent)

    def test_result_error_is_appended(self):
        FakeAgent.next_result = FakeResult(error="provider exploded")
        self._run()
        self.assertIn(("error", "provider exploded"), self.chat.calls)


    def test_user_system_prompt_is_appended(self):
        sublime._settings_store["system_prompt"] = "MIS INSTRUCCIONES"
        self._run()
        agent = FakeAgent.last_instance
        self.assertIn("MIS INSTRUCCIONES", agent.kwargs["system_prompt"])

    def test_session_fallback_when_settings_empty(self):
        sublime._settings_store["default_provider"] = ""
        sublime._settings_store["default_model"] = ""
        self.chat.session.provider = "anthropic"
        self.chat.session.model = "claude-sonnet-4-5"
        self._run()
        self.assertEqual(self.chat.session.provider, "anthropic")
        self.assertEqual(self.chat.session.model, "claude-sonnet-4-5")

    def test_generation_defaults_are_passed_to_agent(self):
        self._run()

        self.assertEqual(FakeAgent.last_instance.run_kwargs["max_tokens"], 8192)
        self.assertIsNone(FakeAgent.last_instance.run_kwargs["temperature"])

    def test_custom_generation_settings_are_passed_to_agent(self):
        sublime._settings_store["max_tokens"] = 4096
        sublime._settings_store["temperature"] = 0.25

        self._run()

        self.assertEqual(FakeAgent.last_instance.run_kwargs["max_tokens"], 4096)
        self.assertEqual(FakeAgent.last_instance.run_kwargs["temperature"], 0.25)

    def test_auto_max_tokens_uses_provider_model_limit(self):
        sublime._settings_store["max_tokens"] = "auto"

        self._run()

        self.assertEqual(FakeAgent.last_instance.run_kwargs["max_tokens"], 12345)

    def test_invalid_generation_setting_stops_before_agent_creation(self):
        sublime._settings_store["temperature"] = 3

        self._run()

        self.assertIsNone(FakeAgent.last_instance)
        self.assertTrue(any(call[0] == "error" for call in self.chat.calls))

    def test_cancel_before_start_skips_agent(self):
        placeholder = agent_runner.StartingAgentPlaceholder()
        placeholder.cancel()
        self.assertTrue(placeholder.is_cancelled)

    def test_cancelled_run_cannot_update_or_cleanup_a_newer_run(self):
        first_started = threading.Event()
        release_first = threading.Event()
        instances = []

        class RacingAgent(FakeAgent):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.index = len(instances)
                instances.append(self)

            def run(self, **kwargs):
                if self.index == 0:
                    first_started.set()
                    release_first.wait(timeout=10)
                    self.kwargs["on_text_chunk"]("stale text")
                    return FakeResult(
                        messages=[{"role": "assistant", "content": "stale"}],
                        error="stale error",
                        content="stale",
                    )
                return FakeResult(
                    messages=[{"role": "assistant", "content": "current"}],
                    content="current",
                )

        agent_runner.Agent = RacingAgent
        first_thread = agent_runner.run_agent_async(self.window, self.chat, "first")
        self.assertTrue(first_started.wait(timeout=10))

        run_controller = self.chat._current_agent
        run_controller.cancel()
        self.assertTrue(instances[0].is_cancelled)
        self.chat._active_run_token = None
        self.chat._current_agent = None

        second_thread = agent_runner.run_agent_async(self.window, self.chat, "second")
        second_thread.join(timeout=10)
        self.assertFalse(second_thread.is_alive())

        release_first.set()
        first_thread.join(timeout=10)
        self.assertFalse(first_thread.is_alive())

        self.assertNotIn(("text", "stale text"), self.chat.calls)
        self.assertNotIn(("error", "stale error"), self.chat.calls)
        self.assertEqual(
            self.chat.session.api_messages,
            [{"role": "assistant", "content": "current"}],
        )
        self.assertEqual(self.chat.call_names().count("prepare_for_user"), 1)
        self.assertEqual(self.chat.call_names().count("on_stream_complete"), 1)
        self.assertIsNone(self.chat._current_agent)

    def test_both_commands_delegate_to_unified_route(self):
        commands_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "Limitcode", "lib", "commands.py",
        )
        if not os.path.exists(commands_path):
            commands_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "..", "lib", "commands.py")
        with open(commands_path, "r", encoding="utf-8") as f:
            source = f.read()
        # Both entry points must delegate; no command may build an Agent itself
        self.assertGreaterEqual(source.count("run_agent_async("), 2)
        self.assertNotIn("Agent(", source.replace("run_agent_async(", ""))


if __name__ == "__main__":
    unittest.main()
