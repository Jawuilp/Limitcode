"""Round-trip tests for ChatSession persistence: save/load fidelity,
rename, delete, prompt history and title derivation.

Sessions are written to the package history/ folder (gitignored);
each test uses a unique id and removes its files on teardown.
"""

import os
import sys
import tempfile
import types
import unittest
import uuid
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
import Limitcode.chat as chat_module
from Limitcode.chat import ChatSession


class SessionRoundTripTest(unittest.TestCase):
    def setUp(self):
        self.session_id = "test-session-" + uuid.uuid4().hex[:8]
        self._package_dir = tempfile.mkdtemp()
        self._original_module_file = chat_module.__file__
        chat_module.__file__ = os.path.join(self._package_dir, "chat.py")
        os.makedirs(os.path.join(self._package_dir, "history"))

    def tearDown(self):
        chat_module.__file__ = self._original_module_file
        shutil.rmtree(self._package_dir, ignore_errors=True)

    def _make_saved_session(self):
        session = ChatSession(self.session_id)
        session.provider = "anthropic"
        session.model = "claude-sonnet-4-6"
        session.add_prompt("hola que tal")
        session.add_message("user", "hola que tal")
        session.add_message("assistant", "bien, gracias")
        return session

    def test_save_and_load_round_trip(self):
        original = self._make_saved_session()
        self.assertTrue(os.path.exists(original.json_path))
        self.assertTrue(os.path.exists(original.file_path))

        loaded = ChatSession()
        self.assertTrue(loaded.load(self.session_id))

        self.assertEqual(loaded.session_id, self.session_id)
        self.assertEqual(loaded.title, original.title)
        self.assertEqual(loaded.provider, "anthropic")
        self.assertEqual(loaded.model, "claude-sonnet-4-6")
        self.assertEqual(
            [(m["role"], m["content"]) for m in loaded.messages],
            [("user", "hola que tal"), ("assistant", "bien, gracias")],
        )
        # api_messages survive the round trip so the agent keeps its memory
        roles = [m["role"] for m in loaded.api_messages]
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)
        self.assertEqual(loaded.prompt_history, ["hola que tal"])

    def test_save_creates_missing_history_directory(self):
        shutil.rmtree(os.path.join(self._package_dir, "history"))

        session = ChatSession(self.session_id)
        session.add_message("user", "se guarda")

        self.assertTrue(os.path.exists(session.json_path))
        self.assertTrue(os.path.exists(session.file_path))

    def test_title_derived_from_first_prompt(self):
        session = self._make_saved_session()
        self.assertEqual(session.title, "hola que tal")

    def test_rename_persists(self):
        self._make_saved_session().rename("mi sesion")

        loaded = ChatSession()
        self.assertTrue(loaded.load(self.session_id))
        self.assertEqual(loaded.title, "mi sesion")

    def test_delete_removes_files_and_resets_state(self):
        session = self._make_saved_session()
        json_path, file_path = session.json_path, session.file_path

        session.delete()

        self.assertFalse(os.path.exists(json_path))
        self.assertFalse(os.path.exists(file_path))
        self.assertEqual(session.messages, [])
        self.assertEqual(session.title, "New Chat")
        self.assertNotEqual(session.session_id, self.session_id)

    def test_load_missing_session_returns_false(self):
        session = ChatSession()
        self.assertFalse(session.load("no-existe-" + uuid.uuid4().hex))

    def test_continue_loaded_session_appends_after_history(self):
        self._make_saved_session()

        loaded = ChatSession()
        self.assertTrue(loaded.load(self.session_id))

        loaded.add_message("user", "seguimos?")
        loaded.add_message("assistant", "claro")

        reloaded = ChatSession()
        self.assertTrue(reloaded.load(self.session_id))
        self.assertEqual(len(reloaded.messages), 4)
        self.assertEqual(reloaded.messages[-1]["content"], "claro")


if __name__ == "__main__":
    unittest.main()
