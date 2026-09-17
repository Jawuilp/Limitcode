import unittest
from unittest.mock import patch

from tests.package_loader import load_limitcode_package
from tests.sublime_stub import install_sublime_stub

install_sublime_stub()
load_limitcode_package()

import sublime

from Limitcode.lib.chat_styles import BASE_STYLE_RESOURCE, USER_STYLE_RESOURCE, load_chat_styles


class ChatStylesTest(unittest.TestCase):
    def setUp(self):
        sublime._settings_store.clear()

    def test_loads_base_then_settings_then_user_css(self):
        sublime._settings_store["chat_style"] = {
            "link_color": "#ffcc66",
            "muted_opacity": 0.7,
            "chip_radius": 6,
        }
        resources = {
            BASE_STYLE_RESOURCE: "BASE_CSS",
            USER_STYLE_RESOURCE: "USER_CSS",
        }

        with patch.object(sublime, "load_resource", side_effect=resources.__getitem__, create=True):
            styles = load_chat_styles()

        self.assertLess(styles.index("BASE_CSS"), styles.index("#ffcc66"))
        self.assertLess(styles.index("#ffcc66"), styles.index("USER_CSS"))
        self.assertIn("--limitcode-chip-muted: color(var(--foreground) alpha(0.7));", styles)
        self.assertIn("--limitcode-chip-radius: 6px;", styles)

    def test_missing_user_css_keeps_base_styles(self):
        def load_resource(resource_name):
            if resource_name == BASE_STYLE_RESOURCE:
                return "BASE_CSS"
            raise OSError("resource not found")

        with patch.object(sublime, "load_resource", side_effect=load_resource, create=True):
            styles = load_chat_styles()

        self.assertEqual(styles, "BASE_CSS")

    def test_invalid_settings_are_ignored(self):
        sublime._settings_store["chat_style"] = {
            "link_color": "</style>",
            "muted_opacity": 2,
            "chip_radius": True,
            "chip_padding": "",
        }

        def load_resource(resource_name):
            if resource_name == BASE_STYLE_RESOURCE:
                return "BASE_CSS"
            raise OSError("resource not found")

        with patch.object(sublime, "load_resource", side_effect=load_resource, create=True):
            styles = load_chat_styles()

        self.assertEqual(styles, "BASE_CSS")


if __name__ == "__main__":
    unittest.main()
