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
from Limitcode.lib.commands import (
    LimitcodeDecreaseChatFontSizeCommand,
    LimitcodeIncreaseChatFontSizeCommand,
    LimitcodeOpenKeyBindingsCommand,
    LimitcodeOpenSettingsCommand,
    LimitcodeResetChatFontSizeCommand,
)


class SettingsCommandTest(unittest.TestCase):
    class ViewSettings:
        def __init__(self, values=None):
            self.values = values or {}

        def get(self, key, default=None):
            return self.values.get(key, default)

        def set(self, key, value):
            self.values[key] = value

        def erase(self, key):
            self.values.pop(key, None)

    class ChatView:
        def __init__(self, values=None):
            self._settings = SettingsCommandTest.ViewSettings(values)

        def settings(self):
            return self._settings

    def command_with_chat_view(self, command_class, values=None):
        command = command_class()
        command.view = self.ChatView(values)
        return command

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

    def test_chat_font_commands_only_change_chat_view_font_size(self):
        settings_store = {"chat_font_size": "auto"}
        install_sublime_stub(settings_store)

        increase = self.command_with_chat_view(
            LimitcodeIncreaseChatFontSizeCommand,
            {"limitcode_chat_view": True, "font_size": 13},
        )
        increase.run(None)

        self.assertEqual(settings_store["chat_font_size"], 14)
        self.assertEqual(increase.view.settings().get("font_size"), 14)

        decrease = self.command_with_chat_view(
            LimitcodeDecreaseChatFontSizeCommand,
            {"limitcode_chat_view": True, "font_size": 14},
        )
        decrease.run(None)
        self.assertEqual(settings_store["chat_font_size"], 13)
        self.assertEqual(decrease.view.settings().get("font_size"), 13)

        reset = self.command_with_chat_view(
            LimitcodeResetChatFontSizeCommand,
            {"limitcode_chat_view": True, "font_size": 13},
        )
        reset.run(None)
        self.assertEqual(settings_store["chat_font_size"], "auto")
        self.assertIsNone(reset.view.settings().get("font_size"))

    def test_chat_font_commands_are_disabled_outside_chat_view(self):
        command = self.command_with_chat_view(
            LimitcodeIncreaseChatFontSizeCommand,
            {"limitcode_chat_view": False, "font_size": 13},
        )

        self.assertFalse(command.is_enabled())


if __name__ == "__main__":
    unittest.main()
