import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.package_loader import PACKAGE_ROOT, load_limitcode_package
from tests.sublime_stub import install_sublime_stub


install_sublime_stub()
load_limitcode_package()

import sublime
from Limitcode.prompts.manager import PromptManager


class ResourceLoadingTest(unittest.TestCase):
    def test_prompt_loads_from_lite_checkout(self):
        manager = PromptManager()

        self.assertEqual(Path(manager.prompts_dir).resolve(), PACKAGE_ROOT / "prompts")
        self.assertIn("strictly limited to files", manager._read_file("base.txt"))

    def test_prompt_uses_sublime_resource_when_package_is_packed(self):
        manager = PromptManager()
        requested = []

        def load_resource(resource_name):
            requested.append(resource_name)
            return "packed prompt"

        with patch("Limitcode.prompts.manager.os.path.isfile", return_value=False):
            with patch.object(sublime, "load_resource", side_effect=load_resource, create=True):
                prompt = manager._read_file("base.txt")

        self.assertEqual(prompt, "packed prompt")
        self.assertEqual(requested, ["Packages/Limitcode/prompts/base.txt"])

    def test_menu_uses_package_commands_instead_of_pro_paths(self):
        menu_path = PACKAGE_ROOT / "Main.sublime-menu"
        menu_text = menu_path.read_text(encoding="utf-8")
        menu = json.loads(menu_text)

        self.assertTrue(menu)
        self.assertNotIn("${packages}/Limitcode", menu_text)
        self.assertIn('"command": "limitcode_open_settings"', menu_text)
        self.assertIn('"command": "limitcode_open_key_bindings"', menu_text)

    def test_history_arrows_do_not_override_visible_autocomplete(self):
        keymap_path = PACKAGE_ROOT / "Default.sublime-keymap"
        bindings = json.loads(keymap_path.read_text(encoding="utf-8"))

        for command in ("limitcode_history_up", "limitcode_history_down"):
            binding = next(item for item in bindings if item.get("command") == command)
            contexts = {item["key"]: item.get("operand") for item in binding["context"]}
            self.assertIs(contexts.get("setting.limitcode_chat_view"), True)
            self.assertIs(contexts.get("auto_complete_visible"), False)

    def test_default_key_bindings_are_scoped_to_the_chat(self):
        keymap_path = PACKAGE_ROOT / "Default.sublime-keymap"
        bindings = json.loads(keymap_path.read_text(encoding="utf-8"))

        self.assertTrue(bindings)
        for binding in bindings:
            contexts = {item["key"]: item.get("operand") for item in binding["context"]}
            self.assertIs(contexts.get("setting.limitcode_chat_view"), True)

    def test_package_export_excludes_development_and_media_files(self):
        attributes = (PACKAGE_ROOT / ".gitattributes").read_text(encoding="utf-8")

        for path in ("/media", "/tests", "/.python-version", "/lsp/README.md"):
            self.assertIn(f"{path} export-ignore", attributes)

    def test_dead_code_block_controls_and_streaming_setting_are_absent(self):
        chat_source = (PACKAGE_ROOT / "chat.py").read_text(encoding="utf-8")
        settings_source = (PACKAGE_ROOT / "Limitcode.sublime-settings").read_text(encoding="utf-8")

        for symbol in (
            "_add_code_block_buttons",
            "LimitcodeCopyBlockCommand",
            "LimitcodeInsertBlockCommand",
            "LimitcodeNewFileBlockCommand",
        ):
            self.assertNotIn(symbol, chat_source)
        self.assertNotIn('"streaming"', settings_source)
        self.assertNotIn('"timeout"', settings_source)


if __name__ == "__main__":
    unittest.main()
