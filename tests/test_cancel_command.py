"""Cancel command writes a resolved terminal state (issue #20)."""

import unittest
from unittest import mock

from tests.package_loader import load_limitcode_package
from tests.sublime_stub import install_sublime_stub

install_sublime_stub()
load_limitcode_package()

from Limitcode.lib import chat as chat_module  # noqa: E402
from Limitcode.lib.commands import LimitcodeCancelRequestCommand  # noqa: E402


class FakeAgent:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FakeChat:
    def __init__(self):
        self._current_agent = FakeAgent()
        self._active_run_token = "token"
        self.appended = []
        self.calls = []

    def hide_loading(self):
        self.calls.append("hide_loading")

    def append_text(self, text):
        self.appended.append(text)

    def prepare_for_user(self):
        self.calls.append("prepare_for_user")

    def on_stream_complete(self):
        self.calls.append("on_stream_complete")


class CancelCommandTest(unittest.TestCase):
    def test_cancel_appends_resolved_cancelled_state(self):
        chat = FakeChat()
        agent = chat._current_agent
        command = LimitcodeCancelRequestCommand()
        command.window = object()

        with mock.patch.object(
            chat_module.ChatView, "get_instance", return_value=chat
        ):
            command.run()

        self.assertTrue(agent.cancelled)
        self.assertEqual(chat.appended, ["\n\n[Cancelled]"])
        self.assertIsNone(chat._current_agent)
        self.assertIsNone(chat._active_run_token)
        self.assertEqual(
            chat.calls,
            ["hide_loading", "prepare_for_user", "on_stream_complete"],
        )

    def test_cancel_without_active_request_does_nothing(self):
        chat = FakeChat()
        chat._current_agent = None
        command = LimitcodeCancelRequestCommand()
        command.window = object()

        with mock.patch.object(
            chat_module.ChatView, "get_instance", return_value=chat
        ):
            command.run()

        self.assertEqual(chat.appended, [])


if __name__ == "__main__":
    unittest.main()
