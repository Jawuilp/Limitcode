"""Open-file restriction and buffer-backed tool tests for Lite."""

import unittest

from tests.package_loader import load_limitcode_package
from tests.sublime_stub import install_sublime_stub
install_sublime_stub()
load_limitcode_package()

class MockView:
    def __init__(self, file_name, content="", is_chat=False):
        self._file_name = file_name
        self._content = content
        self._settings = {"limitcode_chat_view": is_chat}

    def file_name(self):
        return self._file_name

    def size(self):
        return len(self._content)

    def substr(self, region):
        # region is a sublime.Region or a mock object with a and b properties
        a = getattr(region, "a", 0)
        b = getattr(region, "b", len(self._content))
        if hasattr(region, "begin"):
            a = region.begin()
        if hasattr(region, "end"):
            b = region.end()
        return self._content[a:b]

    def settings(self):
        class SettingsObj:
            def __init__(self, s):
                self._s = s
            def get(self, k, default=None):
                return self._s.get(k, default)
        return SettingsObj(self._settings)

    def run_command(self, command_name, args=None):
        if command_name == "limitcode_write_buffer":
            self._content = args.get("content", "")


class MockWindow:
    def __init__(self, folders=None, views=None):
        self._folders = folders or []
        self._views = views or []

    def folders(self):
        return self._folders

    def views(self):
        return self._views

    def find_open_file(self, file_path):
        import os
        norm_target = os.path.normcase(os.path.abspath(file_path))
        for v in self._views:
            if v.file_name():
                norm_v = os.path.normcase(os.path.abspath(v.file_name()))
                if norm_v == norm_target:
                    return v
        return None


class OpenFilesRestrictionTest(unittest.TestCase):

    def setUp(self):
        import sublime
        self.orig_window = getattr(sublime, "_active_window", None)

    def tearDown(self):
        import sublime
        if self.orig_window is not None:
            sublime._active_window = self.orig_window

    def test_get_open_files_paths(self):
        import sublime
        from Limitcode.tools.base import get_open_files_paths

        views = [
            MockView("c:/project/index.html"),
            MockView("c:/project/index.js"),
            MockView(None), # No file name
            MockView("c:/project/chat.md", is_chat=True), # Chat view
        ]
        sublime._active_window = MockWindow(views=views)

        paths = get_open_files_paths()
        self.assertEqual(len(paths), 2)
        self.assertIn("c:/project/index.html", paths)
        self.assertIn("c:/project/index.js", paths)

    def test_get_validated_active_file_path_matches_open_files(self):
        import sublime
        from Limitcode.tools.base import get_validated_active_file_path

        views = [
            MockView("c:/project/index.html"),
            MockView("c:/project/src/index.js"),
        ]
        sublime._active_window = MockWindow(folders=["c:/project"], views=views)

        # 1. Match absolute path
        res = get_validated_active_file_path("c:/project/index.html")
        self.assertEqual(res, "c:/project/index.html")

        # 2. Match relative to project folders
        res = get_validated_active_file_path("src/index.js")
        self.assertEqual(res, "c:/project/src/index.js")

        # 3. Match relative to open file dir
        res = get_validated_active_file_path("index.html")
        self.assertEqual(res, "c:/project/index.html")

        # 4. Fail when not open
        res = get_validated_active_file_path("c:/project/other.css")
        self.assertIsNone(res)

    def test_duplicate_basenames_require_a_specific_path(self):
        import sublime
        from Limitcode.tools.base import resolve_open_file_path

        views = [
            MockView("c:/project/frontend/index.js"),
            MockView("c:/project/backend/index.js"),
        ]
        sublime._active_window = MockWindow(folders=["c:/project"], views=views)

        resolved, error = resolve_open_file_path("index.js")

        self.assertIsNone(resolved)
        self.assertIn("Ambiguous open file path", error)
        self.assertIn("frontend/index.js", error.replace("\\", "/"))
        self.assertIn("backend/index.js", error.replace("\\", "/"))

    def test_absolute_path_resolves_duplicate_basename(self):
        import sublime
        from Limitcode.tools.base import resolve_open_file_path

        views = [
            MockView("c:/project/frontend/index.js"),
            MockView("c:/project/backend/index.js"),
        ]
        sublime._active_window = MockWindow(folders=["c:/project"], views=views)

        resolved, error = resolve_open_file_path("c:/project/backend/index.js")

        self.assertEqual(resolved, "c:/project/backend/index.js")
        self.assertIsNone(error)


class ViewBasedToolsTest(unittest.TestCase):

    def setUp(self):
        import sublime
        self.orig_window = getattr(sublime, "_active_window", None)

    def tearDown(self):
        import sublime
        if self.orig_window is not None:
            sublime._active_window = self.orig_window

    def test_read_file_tool_from_buffer(self):
        import sublime
        from Limitcode.tools.read import ReadFileTool

        views = [MockView("c:/project/index.html", content="line 1\nline 2\nline 3")]
        sublime._active_window = MockWindow(folders=["c:/project"], views=views)

        tool = ReadFileTool()
        result = tool.execute("index.html")
        self.assertTrue(result.get("success"))
        self.assertEqual(result.get("content"), "line 1\nline 2\nline 3")

    def test_write_file_tool_to_buffer(self):
        import sublime
        from Limitcode.tools.write import WriteToFileTool

        view = MockView("c:/project/index.html", content="original")
        sublime._active_window = MockWindow(folders=["c:/project"], views=[view])

        tool = WriteToFileTool()
        result = tool.execute("index.html", "new content")
        self.assertTrue(result.get("success"))
        self.assertEqual(view._content, "new content")

    def test_ambiguous_write_does_not_modify_any_buffer(self):
        import sublime
        from Limitcode.tools.write import WriteToFileTool

        frontend = MockView("c:/project/frontend/index.js", content="frontend")
        backend = MockView("c:/project/backend/index.js", content="backend")
        sublime._active_window = MockWindow(
            folders=["c:/project"],
            views=[frontend, backend],
        )

        result = WriteToFileTool().execute("index.js", "replacement")

        self.assertFalse(result.get("success"))
        self.assertIn("Ambiguous open file path", result.get("error", ""))
        self.assertEqual(frontend._content, "frontend")
        self.assertEqual(backend._content, "backend")

    def test_edit_file_tool_on_buffer(self):
        import sublime
        from Limitcode.tools.edit import EditFileTool

        view = MockView("c:/project/index.html", content="hello world")
        sublime._active_window = MockWindow(folders=["c:/project"], views=[view])

        tool = EditFileTool()
        result = tool.execute("index.html", old_str="world", new_str="sublime")
        self.assertTrue(result.get("success"))
        self.assertEqual(view._content, "hello sublime")

    def test_write_decodes_unicode_escapes_without_changing_paths(self):
        import sublime
        from Limitcode.tools.write import WriteToFileTool

        view = MockView("c:/project/index.txt", content="original")
        sublime._active_window = MockWindow(folders=["c:/project"], views=[view])

        result = WriteToFileTool().execute(
            "index.txt",
            r"Espa\u00f1a \uD83D\uDE80 C:\new\test",
        )

        self.assertTrue(result.get("success"))
        self.assertEqual(view._content, "España 🚀 C:\\new\\test")

    def test_edit_matches_unicode_escapes(self):
        import sublime
        from Limitcode.tools.edit import EditFileTool

        view = MockView("c:/project/index.txt", content="España 🚀")
        sublime._active_window = MockWindow(folders=["c:/project"], views=[view])

        result = EditFileTool().execute(
            "index.txt",
            old_str=r"Espa\u00f1a \uD83D\uDE80",
            new_str=r"Edici\u00f3n lista",
        )

        self.assertTrue(result.get("success"))
        self.assertEqual(view._content, "Edición lista")


if __name__ == "__main__":
    unittest.main()
