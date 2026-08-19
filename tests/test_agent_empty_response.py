import tempfile
import unittest

from tests.package_loader import load_limitcode_package
from tests.sublime_stub import install_sublime_stub


install_sublime_stub()
load_limitcode_package()

from Limitcode.lib.agent import Agent
from Limitcode.lib.agent_types import StreamResponse


class FakeProvider:
    model = "fake-model"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def supports_images(self):
        return False

    def create_message_with_tools(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeToolManager:
    def execute_tool(self, tool_name, **kwargs):
        raise AssertionError(f"Unexpected tool call: {tool_name}")


def response(content="", reasoning="", finish_reason="stop"):
    result = StreamResponse()
    result.content = content
    result.reasoning_content = reasoning
    result.finish_reason = finish_reason
    return result


class EmptyResponseRecoveryTest(unittest.TestCase):
    def _agent(self, provider):
        return Agent(
            provider=provider,
            provider_type="openai",
            tool_manager=FakeToolManager(),
            system_prompt="",
            max_iterations=5,
        )

    def test_initial_empty_response_retries_without_tools(self):
        provider = FakeProvider([
            response(content=""),
            response(content="Hola, todo bien."),
        ])

        with tempfile.TemporaryDirectory() as directory:
            result = self._agent(provider).run("hola", directory=directory)

        self.assertEqual(result.content, "Hola, todo bien.")
        self.assertEqual(len(provider.calls), 2)
        self.assertNotEqual(provider.calls[0]["tools"], [])
        self.assertEqual(provider.calls[1]["tools"], [])

    def test_persistent_initial_empty_response_returns_visible_fallback(self):
        provider = FakeProvider([
            response(content="", reasoning="internal analysis"),
            response(content=""),
            response(content=""),
        ])

        with tempfile.TemporaryDirectory() as directory:
            result = self._agent(provider).run("hola", directory=directory)

        self.assertIn("no visible final answer", result.content)
        self.assertIn("internal analysis", result.content)
        self.assertEqual(len(provider.calls), 3)

    def test_cancel_closes_provider_request_and_suppresses_socket_error(self):
        class CancelledProvider(FakeProvider):
            def __init__(self):
                super().__init__([])
                self.agent = None
                self.cancel_called = False

            def cancel_active_request(self):
                self.cancel_called = True

            def create_message_with_tools(self, **kwargs):
                self.agent.cancel()
                raise OSError("socket closed by cancellation")

        provider = CancelledProvider()
        agent = self._agent(provider)
        provider.agent = agent

        with tempfile.TemporaryDirectory() as directory:
            result = agent.run("hola", directory=directory)

        self.assertTrue(provider.cancel_called)
        self.assertTrue(agent.is_cancelled)
        self.assertIsNone(result.error)


if __name__ == "__main__":
    unittest.main()
