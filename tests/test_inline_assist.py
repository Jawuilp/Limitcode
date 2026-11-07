import sys
import types
import unittest
from unittest.mock import Mock, patch

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

from Limitcode.lib.inline_assist import (
    clean_code_fences,
    build_diff_html,
    render_markdown_to_minihtml,
    extract_thinking_and_content,
    InlineAssistSession,
    LimitcodeInlineAssistCommand,
    LimitcodeApplyInlineEditCommand,
    LimitcodeCancelInlineAssistCommand,
    get_active_session,
    register_session,
    unregister_session,
)


class MockSelection:
    def __init__(self, regions):
        self.regions = list(regions)

    def __iter__(self):
        return iter(self.regions)

    def __getitem__(self, index):
        return self.regions[index]

    def __bool__(self):
        return bool(self.regions)

    def clear(self):
        self.regions.clear()

    def add(self, region):
        self.regions.append(region)


class MockView:
    def __init__(self, text="def hello():\n    return 'world'\n", file_name="/tmp/test.py"):
        self._text = text
        self._file_name = file_name
        self._id = 42
        self._sel = MockSelection([sublime.Region(0, len(text))])
        self._settings = {}
        self._phantoms = {}
        self._window = sublime.Window([], [self])

    def id(self):
        return self._id

    def file_name(self):
        return self._file_name

    def substr(self, region):
        return self._text[region.a:region.b]

    def line(self, point_or_region):
        return sublime.Region(0, len(self._text))

    def sel(self):
        return self._sel

    def settings(self):
        class S:
            def __init__(self, s):
                self._s = s
            def get(self, k, d=None):
                return self._s.get(k, d)
            def set(self, k, v):
                self._s[k] = v
            def erase(self, k):
                self._s.pop(k, None)
        return S(self._settings)

    def erase_phantoms(self, key):
        self._phantoms.pop(key, None)

    def is_valid(self):
        return True

    def viewport_extent(self):
        return (600.0, 800.0)

    def em_width(self):
        return 10.0

    def window(self):
        return self._window

    def show_at_center(self, region):
        pass

    def replace(self, edit, region, text):
        self._text = self._text[:region.a] + text + self._text[region.b:]

    def run_command(self, cmd, args=None):
        args = args or {}
        if cmd == "limitcode_apply_inline_edit":
            cmd_inst = LimitcodeApplyInlineEditCommand()
            cmd_inst.view = self
            cmd_inst.run(None, args["start"], args["end"], args["text"])
        elif cmd == "limitcode_cancel_inline_assist":
            cmd_inst = LimitcodeCancelInlineAssistCommand()
            cmd_inst.view = self
            cmd_inst.run(None)


class InlineAssistTest(unittest.TestCase):
    def setUp(self):
        sublime._settings_store.clear()
        sublime._settings_store["default_provider"] = "openai"
        sublime._settings_store["api_keys"] = {"openai": "test-key"}
        self.view = MockView()

    def tearDown(self):
        unregister_session(self.view)

    def test_clean_code_fences_strips_markdown_blocks(self):
        self.assertEqual(
            clean_code_fences("```python\ndef test():\n    pass\n```"),
            "def test():\n    pass"
        )
        self.assertEqual(
            clean_code_fences("```typescript\nconst a = 1;\n```"),
            "const a = 1;"
        )
        self.assertEqual(
            clean_code_fences("```\nplain block\n```"),
            "plain block"
        )
        self.assertEqual(
            clean_code_fences("just regular code"),
            "just regular code"
        )

    def test_build_diff_html_contains_add_and_del(self):
        orig = "def foo():\n    return 1"
        repl = "def foo():\n    return 2"
        html_diff = build_diff_html(orig, repl)

        self.assertIn("diff-del", html_diff)
        self.assertIn("diff-add", html_diff)
        self.assertIn("return 1", html_diff)
        self.assertIn("return 2", html_diff)

    def test_build_diff_html_no_changes(self):
        orig = "a = 1\n"
        repl = "a = 1\n"
        html_diff = build_diff_html(orig, repl)
        self.assertIn("diff-ctx", html_diff)

    def test_session_registration_lifecycle(self):
        region = sublime.Region(0, len(self.view._text))
        session = InlineAssistSession(self.view, region, "make it async")

        self.assertIsNone(get_active_session(self.view))
        register_session(self.view, session)
        self.assertEqual(get_active_session(self.view), session)
        self.assertTrue(self.view.settings().get("limitcode_inline_assist_active"))

        unregister_session(self.view)
        self.assertIsNone(get_active_session(self.view))
        self.assertIsNone(self.view.settings().get("limitcode_inline_assist_active"))

    def test_apply_replacement_updates_buffer_and_closes_session(self):
        region = sublime.Region(0, len(self.view._text))
        session = InlineAssistSession(self.view, region, "simplify")
        register_session(self.view, session)

        new_code = "print('hello simplified')\n"
        session.apply_replacement(new_code)

        self.assertEqual(self.view._text, new_code)
        self.assertIsNone(get_active_session(self.view))

    def test_on_navigate_accept_and_reject(self):
        region = sublime.Region(0, len(self.view._text))
        session = InlineAssistSession(self.view, region, "simplify")
        register_session(self.view, session)
        session.stream_content = "```python\nprint('new')\n```"

        # Navigate accept
        session.on_navigate("accept")
        self.assertEqual(self.view._text, "print('new')")
        self.assertIsNone(get_active_session(self.view))

        # Reject lifecycle
        session2 = InlineAssistSession(self.view, region, "test reject")
        register_session(self.view, session2)
        session2.on_navigate("reject")
        self.assertTrue(session2.is_cancelled)
        self.assertIsNone(get_active_session(self.view))

    def test_inline_assist_command_with_explicit_prompt(self):
        cmd = LimitcodeInlineAssistCommand()
        cmd.view = self.view

        with patch.object(InlineAssistSession, "start") as mock_start:
            cmd.run(None, prompt="refactor to async")
            self.assertTrue(mock_start.called)

    def test_cancel_inline_assist_command_cancels_active_session(self):
        region = sublime.Region(0, 10)
        session = InlineAssistSession(self.view, region, "test")
        register_session(self.view, session)

        cmd = LimitcodeCancelInlineAssistCommand()
        cmd.view = self.view
        cmd.run(None)

        self.assertTrue(session.is_cancelled)
        self.assertIsNone(get_active_session(self.view))

    def test_render_markdown_to_minihtml_formatting(self):
        md = (
            "Estas líneas sirven para **ordenar un arreglo**:\n\n"
            "- **`const sortedArray = bubbleSort(miArray);`**\n"
            "Invoca `bubbleSort` con argumento.\n\n"
            "```javascript\nconsole.log(sortedArray);\n```"
        )
        html_out = render_markdown_to_minihtml(md)
        self.assertIn("<b>ordenar un arreglo</b>", html_out)
        self.assertIn("<b><code class=\"inline-code\">const sortedArray = bubbleSort(miArray);</code></b>", html_out)
        self.assertIn("<code class=\"inline-code\">bubbleSort</code>", html_out)
        self.assertIn('<span class="bullet">•</span>', html_out)
        self.assertIn('<div class="code-box">', html_out)
        self.assertIn("console.log(sortedArray);", html_out)

    def test_is_likely_question_detection(self):
        region = sublime.Region(0, 10)
        session = InlineAssistSession(self.view, region, "me explicas q hace esto?")
        self.assertTrue(session._is_likely_question("me explicas q hace esto?", "Esta función ordena..."))
        self.assertTrue(session._is_likely_question("como funciona", "Esta función toma..."))
        self.assertTrue(session._is_likely_question("what is this?", "This is a helper..."))
        # Code transformation should NOT be treated as question
        self.assertFalse(session._is_likely_question("hazlo async", "async function foo() {\n}"))

    def test_responsive_card_cols_calculation(self):
        region = sublime.Region(0, 10)
        session = InlineAssistSession(self.view, region, "test")

        # Mock standard pane: 600px / 10px = 60 cols -> clamped to 50
        cols = session._get_card_cols()
        self.assertEqual(cols, 50)

        # Mock narrow split pane: 380px / 10px = 38 cols -> 38 - 6 = 32
        with patch.object(self.view, "viewport_extent", return_value=(380.0, 600.0)):
            narrow_cols = session._get_card_cols()
            self.assertEqual(narrow_cols, 32)

        # Mock ultra narrow pane: 200px / 10px = 20 cols -> clamped to min 28
        with patch.object(self.view, "viewport_extent", return_value=(200.0, 600.0)):
            ultra_narrow = session._get_card_cols()
            self.assertEqual(ultra_narrow, 28)

    def test_command_disabled_in_chat_view(self):
        cmd = LimitcodeInlineAssistCommand()
        cmd.view = self.view
        self.assertTrue(cmd.is_enabled())
        self.assertTrue(cmd.is_visible())

        self.view.settings().set("limitcode_chat_view", True)
        self.assertFalse(cmd.is_enabled())
        self.assertFalse(cmd.is_visible())

    def test_extract_thinking_and_content(self):
        # Unclosed think tag
        visible, think, is_thinking = extract_thinking_and_content("<think>analizando algoritmo")
        self.assertTrue(is_thinking)
        self.assertEqual(think, "analizando algoritmo")
        self.assertEqual(visible, "")

        # Closed think tag
        visible, think, is_thinking = extract_thinking_and_content("<think>analizado</think>\nEsta es la respuesta")
        self.assertFalse(is_thinking)
        self.assertEqual(think, "analizado")
        self.assertEqual(visible, "Esta es la respuesta")

        # Regular text
        visible, think, is_thinking = extract_thinking_and_content("Hola mundo")
        self.assertFalse(is_thinking)
        self.assertEqual(think, "")
        self.assertEqual(visible, "Hola mundo")

    def test_render_streaming_explanation(self):
        region = sublime.Region(0, 10)
        session = InlineAssistSession(self.view, region, "explica esta funcion")
        session.stream_content = "Esta función sirve para **ordenar**"

        with patch.object(session, "_update_phantom") as mock_update:
            session._render_streaming()
            self.assertTrue(mock_update.called)
            html_arg = mock_update.call_args[0][0]
            self.assertIn("Writing response...", html_arg)
            self.assertIn("<b>ordenar</b>", html_arg)
            self.assertIn("stream-cursor", html_arg)

    def test_send_to_chat_transfers_clean_conversation_and_activates_input(self):
        region = sublime.Region(0, len(self.view._text))
        session = InlineAssistSession(self.view, region, "explica este codigo")
        session.stream_content = "Esta función implementa ordenamiento burbuja"
        register_session(self.view, session)

        mock_chat = Mock()
        mock_chat._view = MockView()
        with patch("Limitcode.lib.chat.ChatView.get_instance", return_value=mock_chat):
            session.send_to_chat()

            # Verify user message does not contain [Inline Assist] or raw Context:
            user_msg = mock_chat.append_user_message.call_args[0][0]
            self.assertNotIn("[Inline Assist]", user_msg)
            self.assertNotIn("Context:", user_msg)
            self.assertIn("explica este codigo", user_msg)
            self.assertIn("```py\ndef hello():", user_msg)

            # Verify assistant message appended
            mock_chat.append_text.assert_called_with("Esta función implementa ordenamiento burbuja")
            mock_chat.on_stream_complete.assert_called_once()

            # Verify input area activated and session closed
            mock_chat.prepare_for_user.assert_called_once()
            mock_chat._move_cursor_to_input.assert_called_once()
            self.assertIsNone(get_active_session(self.view))


if __name__ == "__main__":
    unittest.main()
