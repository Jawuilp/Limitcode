import unittest

from tests.package_loader import load_limitcode_package
from tests.sublime_stub import install_sublime_stub

install_sublime_stub()
load_limitcode_package()

from Limitcode.tools.edit_fuzzy import apply_edit, find_edit, similarity


class EditFuzzyTest(unittest.TestCase):
    def test_similarity_handles_empty_and_identical_strings(self):
        self.assertEqual(similarity("", ""), 1.0)
        self.assertEqual(similarity("abc", "abc"), 1.0)
        self.assertEqual(similarity("abc", ""), 0.0)

    def test_apply_edit_uses_exact_match_first(self):
        content = "alpha\nbeta\ngamma\n"

        new_content, strategy = apply_edit(content, "beta", "BETA")

        self.assertEqual(strategy, "simple")
        self.assertEqual(new_content, "alpha\nBETA\ngamma\n")

    def test_apply_edit_matches_lines_with_trimmed_whitespace(self):
        content = "def run():\n    return True\n"
        find = "def run():\nreturn True"

        new_content, strategy = apply_edit(content, find, "def run():\n    return False")

        self.assertEqual(strategy, "line_trimmed")
        self.assertEqual(new_content, "def run():\n    return False\n")

    def test_apply_edit_matches_indented_lines_with_trimmed_strategy(self):
        content = "if ready:\n        do_work()\n        finish()\n"
        find = "    do_work()\n    finish()"

        new_content, strategy = apply_edit(content, find, "        skip()")

        self.assertEqual(strategy, "line_trimmed")
        self.assertEqual(new_content, "if ready:\n        skip()\n")

    def test_apply_edit_matches_escaped_newlines(self):
        content = "start\nline one\nline two\nend\n"
        find = "line one\\nline two"

        new_content, strategy = apply_edit(content, find, "replacement")

        self.assertEqual(strategy, "escape_normalized")
        self.assertEqual(new_content, "start\nreplacement\nend\n")

    def test_apply_edit_preserves_crlf_line_endings(self):
        content = "alpha\r\nbeta\r\ngamma\r\n"

        new_content, strategy = apply_edit(content, "beta", "BETA")

        self.assertEqual(strategy, "simple")
        self.assertEqual(new_content, "alpha\r\nBETA\r\ngamma\r\n")

    def test_apply_edit_raises_when_no_strategy_matches(self):
        with self.assertRaises(ValueError) as cm:
            apply_edit("alpha\nbeta\n", "missing", "replacement")

        self.assertIn("Could not find", str(cm.exception))

    def test_find_edit_replace_all_returns_multiple_unique_matches(self):
        matches = find_edit("one\ntwo\none\n", "one", replace_all=True)

        self.assertEqual([(m.start, m.end, m.strategy) for m in matches], [
            (0, 3, "simple"),
            (8, 11, "simple"),
        ])


if __name__ == "__main__":
    unittest.main()
