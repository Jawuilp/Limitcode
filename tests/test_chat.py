import sys
import types
import unittest
import json
import tempfile
import shutil

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
from Limitcode.chat import ChatSession, ChatView

class ChatSessionTest(unittest.TestCase):
    def setUp(self):
        # Create a temp directory for history
        self.temp_dir = tempfile.mkdtemp()
        sublime._packages_path = self.temp_dir
        
    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_sync_api_messages_empty(self):
        session = ChatSession(session_id="test_session")
        session._sync_api_messages()
        self.assertEqual(session.api_messages, [])

    def test_image_tokens_expand_to_internal_attachment_tags(self):
        chat = ChatView.__new__(ChatView)
        chat._image_attachments = {}
        chat._image_attachment_counter = 0

        token = chat.register_image_attachment(r"C:\tmp\clip.png")
        expanded = chat.expand_image_tokens(f"mira [{token}] y [img99]")

        self.assertEqual(token, "img1")
        self.assertIn(r"[Attached Image: C:\tmp\clip.png]", expanded)
        self.assertIn("[img99]", expanded)

    def test_deleted_image_token_is_not_sent_and_token_is_reused(self):
        chat = ChatView.__new__(ChatView)
        chat._image_attachments = {}
        chat._image_attachment_counter = 0

        first = chat.register_image_attachment(r"C:\tmp\first.png", "")
        second = chat.register_image_attachment(r"C:\tmp\second.png", "")
        expanded = chat.expand_image_tokens("mensaje sin imagen")
        reused = chat.register_image_attachment(r"C:\tmp\third.png", "")

        self.assertEqual(first, "img1")
        self.assertEqual(second, "img1")
        self.assertNotIn("Attached Image", expanded)
        self.assertEqual(reused, "img1")

    def test_sync_api_messages_rebuild_from_empty(self):
        session = ChatSession(session_id="test_session")
        session.messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"}
        ]
        session.api_messages = []
        session._sync_api_messages()
        
        expected = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"}
        ]
        self.assertEqual(session.api_messages, expected)

    def test_sync_api_messages_merge_missing_first_user_message(self):
        # Scenario: A session split occurs where api_messages starts later or misses the first message
        session = ChatSession(session_id="test_session")
        session.messages = [
            {"role": "user", "content": "First User Prompt"},
            {"role": "assistant", "content": "First Assistant Response"},
            {"role": "user", "content": "Second User Prompt"},
            {"role": "assistant", "content": "Second Assistant Response"}
        ]
        
        # api_messages has tool calls and responses but is missing the first prompt/response
        session.api_messages = [
            {"role": "user", "content": "Second User Prompt"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "command output"},
            {"role": "assistant", "content": "Second Assistant Response"}
        ]
        
        session._sync_api_messages()
        
        # We expect:
        # 1. "First User Prompt" and "First Assistant Response" inserted at the front
        # 2. "Second User Prompt" and subsequent tool calls/responses/assistant response preserved
        self.assertEqual(len(session.api_messages), 6)
        self.assertEqual(session.api_messages[0]["role"], "user")
        self.assertEqual(session.api_messages[0]["content"], "First User Prompt")
        self.assertEqual(session.api_messages[1]["role"], "assistant")
        self.assertEqual(session.api_messages[1]["content"], "First Assistant Response")
        self.assertEqual(session.api_messages[2]["role"], "user")
        self.assertEqual(session.api_messages[2]["content"], "Second User Prompt")
        self.assertEqual(session.api_messages[3]["role"], "assistant")
        self.assertIn("tool_calls", session.api_messages[3])

    def test_sync_api_messages_multimodal_list_content(self):
        # Scenario: api_messages content contains a list (e.g. Anthropic API format with text parts)
        session = ChatSession(session_id="test_session")
        session.messages = [
            {"role": "user", "content": "Find the bug"},
            {"role": "assistant", "content": "Let me look"}
        ]
        session.api_messages = [
            {"role": "user", "content": [{"type": "text", "text": "Find the bug in this code"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Let me look"}]}
        ]
        
        session._sync_api_messages()
        
        # Since "Find the bug" is a substring of "Find the bug in this code", they should match,
        # and the api_messages list format should be preserved instead of overwritten.
        self.assertEqual(len(session.api_messages), 2)
        self.assertEqual(session.api_messages[0]["content"], [{"type": "text", "text": "Find the bug in this code"}])
        self.assertEqual(session.api_messages[1]["content"], [{"type": "text", "text": "Let me look"}])
    def test_sync_api_messages_sanitize_orphan_tool_calls(self):
        # Scenario: User cancelled execution mid-tool-call, leaving an assistant
        # message with tool_calls but no corresponding tool response.
        # This causes API 400: "tool_calls must be followed by tool messages"
        session = ChatSession(session_id="test_session")
        session.messages = [
            {"role": "user", "content": "do something"},
            {"role": "assistant", "content": "I'll search for files"},
            {"role": "user", "content": "never mind"},
            {"role": "assistant", "content": "Ok, cancelled"}
        ]
        session.api_messages = [
            {"role": "user", "content": "do something"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
            # Missing tool response! User cancelled.
            {"role": "user", "content": "never mind"},
            {"role": "assistant", "content": "Ok, cancelled"}
        ]
        
        session._sync_api_messages()
        
        # The orphaned tool_calls should be stripped
        for msg in session.api_messages:
            if msg.get("role") == "assistant":
                self.assertNotIn("tool_calls", msg,
                    f"Orphaned tool_calls should have been stripped: {msg}")
        
        # All user messages should still be present
        user_contents = [m["content"] for m in session.api_messages if m["role"] == "user"]
        self.assertIn("do something", user_contents)
        self.assertIn("never mind", user_contents)

    def test_sync_api_messages_preserves_valid_tool_calls(self):
        # Scenario: Valid tool_calls sequence (assistant → tool) should be preserved
        session = ChatSession(session_id="test_session")
        session.messages = [
            {"role": "user", "content": "list files"},
            {"role": "assistant", "content": "Here are the files"}
        ]
        session.api_messages = [
            {"role": "user", "content": "list files"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "file1.py\nfile2.py"},
            {"role": "assistant", "content": "Here are the files"}
        ]
        
        session._sync_api_messages()
        
        # Valid tool_calls should remain
        assistant_with_tc = [m for m in session.api_messages if m.get("tool_calls")]
        self.assertEqual(len(assistant_with_tc), 1, "Valid tool_calls should be preserved")
        
        # Tool response should remain
        tool_msgs = [m for m in session.api_messages if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 1, "Tool response should be preserved")

    def test_sync_preserves_function_call_format_tool_results(self):
        # Regression (400 "No tool output found for function call"):
        # Some providers store tool calls as function_call parts inside the
        # assistant content list (no tool_calls key). Sync must recognize
        # them and keep their role="tool" responses instead of discarding
        # them as orphans.
        session = ChatSession(session_id="test_session")
        session.messages = [
            {"role": "user", "content": "lee el archivo"},
            {"role": "assistant", "content": "Listo, lo lei"},
        ]
        session.api_messages = [
            {"role": "user", "content": "lee el archivo"},
            {"role": "assistant", "content": [
                {"type": "output_text", "text": "Voy a leerlo"},
                {"type": "function_call", "call_id": "call_abc", "name": "read_file", "arguments": "{}"},
            ]},
            {"role": "tool", "tool_call_id": "call_abc", "content": "contenido"},
            {"role": "assistant", "content": "Listo, lo lei"},
        ]

        session._sync_api_messages()

        tool_msgs = [m for m in session.api_messages if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 1, "Responses-format tool response must be preserved")
        self.assertEqual(tool_msgs[0]["tool_call_id"], "call_abc")

        # The function_call part must also survive (the pair stays complete)
        fc_parts = [
            part
            for m in session.api_messages if isinstance(m.get("content"), list)
            for part in m["content"]
            if isinstance(part, dict) and part.get("type") == "function_call"
        ]
        self.assertEqual(len(fc_parts), 1)

    def test_sync_strips_responses_format_orphan_function_calls(self):
        # If the tool response was lost (cancellation, reload), the
        # function_call part must be stripped so the next request to the
        # Responses API is not rejected with a 400.
        session = ChatSession(session_id="test_session")
        session.messages = [
            {"role": "user", "content": "haz algo"},
            {"role": "assistant", "content": "Cancelado"},
        ]
        session.api_messages = [
            {"role": "user", "content": "haz algo"},
            {"role": "assistant", "content": [
                {"type": "output_text", "text": "Trabajando"},
                {"type": "function_call", "call_id": "call_huerfano", "name": "edit_file", "arguments": "{}"},
            ]},
            # Missing tool response for call_huerfano
            {"role": "assistant", "content": "Cancelado"},
        ]

        session._sync_api_messages()

        for m in session.api_messages:
            if isinstance(m.get("content"), list):
                for part in m["content"]:
                    self.assertNotEqual(
                        part.get("type"), "function_call",
                        "Orphan Responses API function_call must be stripped: %s" % m)


if __name__ == "__main__":
    unittest.main()
