"""Round-trip tests for ChatSession persistence in writable user storage."""

import os
import sys
import tempfile
import types
import unittest
import uuid
import shutil
from unittest.mock import patch

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
import Limitcode.storage as storage_module
from Limitcode.chat import ChatSession


class SessionRoundTripTest(unittest.TestCase):
    def setUp(self):
        self.session_id = "test-session-" + uuid.uuid4().hex[:8]
        self._package_dir = tempfile.mkdtemp()
        self._original_packages_path = sublime._packages_path
        sublime._packages_path = os.path.join(self._package_dir, "Packages")
        self._legacy_dir = os.path.join(self._package_dir, "legacy-history")
        os.makedirs(self._legacy_dir)
        self._legacy_patch = patch.object(
            storage_module,
            "_legacy_history_dir",
            return_value=self._legacy_dir,
        )
        self._legacy_patch.start()

    def tearDown(self):
        self._legacy_patch.stop()
        sublime._packages_path = self._original_packages_path
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
        history_path = storage_module.history_dir()
        shutil.rmtree(history_path, ignore_errors=True)

        session = ChatSession(self.session_id)
        session.add_message("user", "se guarda")

        self.assertTrue(os.path.exists(session.json_path))
        self.assertTrue(os.path.exists(session.file_path))

    def test_legacy_history_is_migrated_to_user_storage(self):
        legacy_file = os.path.join(self._legacy_dir, "old-session.json")
        with open(legacy_file, "w", encoding="utf-8") as file:
            file.write("{}")

        target = storage_module.history_dir()

        self.assertTrue(os.path.exists(os.path.join(target, "old-session.json")))
        self.assertIn(os.path.join("User", "Limitcode", "history"), target)

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
