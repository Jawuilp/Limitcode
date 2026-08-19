import sys
import types
import unittest

from tests.package_loader import load_limitcode_package
from tests.sublime_stub import install_sublime_stub


install_sublime_stub()

sublime_plugin = types.ModuleType("sublime_plugin")
sublime_plugin.WindowCommand = type("WindowCommand", (), {})
sublime_plugin.TextCommand = type("TextCommand", (), {})
sys.modules["sublime_plugin"] = sublime_plugin

load_limitcode_package()
from Limitcode.lib.commands import LimitcodeOpenKeyBindingsCommand, LimitcodeOpenSettingsCommand


class SettingsCommandTest(unittest.TestCase):
    def test_config_opens_user_override_through_edit_settings(self):
        class Window:
            def __init__(self):
                self.calls = []

            def run_command(self, name, args=None):
                self.calls.append((name, args))

        window = Window()
        command = LimitcodeOpenSettingsCommand()
        command.window = window

        command.run()

        self.assertEqual(window.calls, [(
            "edit_settings",
            {
                "base_file": "${packages}/Limitcode/Limitcode.sublime-settings",
                "default": "{\n}\n",
            },
        )])

    def test_key_bindings_open_user_override_through_edit_settings(self):
        class Window:
            def __init__(self):
                self.calls = []

            def run_command(self, name, args=None):
                self.calls.append((name, args))

        window = Window()
        command = LimitcodeOpenKeyBindingsCommand()
        command.window = window

        command.run()

        self.assertEqual(window.calls, [(
            "edit_settings",
            {
                "base_file": "${packages}/Limitcode/Default.sublime-keymap",
                "default": "[\n]\n",
            },
        )])


if __name__ == "__main__":
    unittest.main()
