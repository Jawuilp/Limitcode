import os
import json
import socket
import time
import unittest
from unittest.mock import patch

from tests.package_loader import load_limitcode_package
from tests.sublime_stub import install_sublime_stub
install_sublime_stub()

import sublime
sublime.status_message = lambda msg: None

load_limitcode_package()
from Limitcode.lib.agent import Agent
from Limitcode.providers.base import BaseProvider


class FakeProvider:
    PROVIDER_NAME = "fake"


class RecordingToolManager:
    def __init__(self):
        self.executed = []

    def execute_tool(self, tool_name, **kwargs):
        self.executed.append(tool_name)
        return {"success": True, "result": "ok"}

    def get_available_tools(self):
        return {}


class ExecuteToolCancelTest(unittest.TestCase):
    def _make_agent(self):
        return Agent(
            provider=FakeProvider(),
            provider_type="fake",
            tool_manager=RecordingToolManager(),
            system_prompt="",
        )

    def test_cancelled_agent_does_not_execute_tool(self):
        agent = self._make_agent()
        agent.is_cancelled = True
        result = agent._execute_tool("write_to_file", {"file_path": "a.txt", "content": "hello"}, os.getcwd())
        self.assertFalse(result["success"])
        self.assertIn("Cancelled", result["error"])
        self.assertEqual(agent.tool_manager.executed, [])

    def test_text_only_model_strips_historical_image_parts(self):
        agent = self._make_agent()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "before"},
                    {"type": "image", "path": "a.png", "data": "..."},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
                    {"type": "input_image", "image_url": "data:image/png;base64,..."},
                ],
            }
        ]

        removed = agent._strip_unsupported_images_from_history(messages)

        self.assertEqual(removed, 3)
        content = messages[0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "before"})
        self.assertTrue(all(part.get("type") == "text" for part in content))
        self.assertIn("not supported by this model", content[1]["text"])

    def test_gemini_tool_results_are_json_serializable(self):
        agent = self._make_agent()
        agent.provider_type = "gemini"

        messages = agent._format_tool_results([{
            "tool_call_id": "call_1",
            "tool_name": "write_to_file",
            "result": {
                "success": True,
                "message": "ok",
                "callback": lambda: None,
            },
        }])

        json.dumps(messages)
        result = messages[0]["parts"][0]["functionResponse"]["response"]["result"]
        self.assertEqual(result["success"], True)
        self.assertEqual(result["message"], "ok")
        self.assertIsInstance(result["callback"], str)

    def test_executes_when_not_cancelled(self):
        agent = self._make_agent()
        result = agent._execute_tool("write_to_file", {"file_path": "a.txt", "content": "hello"}, os.getcwd())
        self.assertTrue(result.get("success"))
        self.assertEqual(agent.tool_manager.executed, ["write_to_file"])

    def test_provider_cancellation_closes_active_socket_and_response(self):
        provider = BaseProvider(api_key="test", model="test")

        class FakeSocket:
            def __init__(self):
                self.shutdown_how = None

            def shutdown(self, how):
                self.shutdown_how = how

        class FakeResponse:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        class FakeConnection:
            def __init__(self):
                self.sock = FakeSocket()
                self.response = FakeResponse()
                self.closed = False

            def request(self, *args):
                pass

            def getresponse(self):
                return self.response

            def close(self):
                self.closed = True

        connection = FakeConnection()
        with patch("http.client.HTTPSConnection", return_value=connection):
            response = provider._make_https_request(
                "api.example.com", 443, "POST", "/chat/completions", {}, "{}"
            )

        provider.cancel_active_request()

        self.assertIs(response, connection.response)
        self.assertEqual(connection.sock.shutdown_how, socket.SHUT_RDWR)
        self.assertTrue(connection.response.closed)
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
