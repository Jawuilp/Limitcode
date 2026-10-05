"""Unit tests for EscapeGate — evidence-based unicode escape decoding (issue #9)."""

import unittest

from tests.package_loader import load_limitcode_package
from tests.sublime_stub import install_sublime_stub
install_sublime_stub()
load_limitcode_package()

from Limitcode.lib.escape_gate import EscapeGate  # noqa: E402


class EscapeGateTest(unittest.TestCase):

    def test_starts_without_evidence(self):
        gate = EscapeGate()
        self.assertFalse(gate.corruption_proven)

    def test_write_content_untouched_by_default(self):
        gate = EscapeGate()
        raw = r'{"arrow": "\u2192"}'
        self.assertEqual(gate.prepare_write_content(raw), raw)

    def test_write_content_decodes_after_proven_corruption(self):
        gate = EscapeGate()
        gate.mark_corruption_proven()
        self.assertEqual(gate.prepare_write_content(r"caf\u00e9"), "café")

    def test_write_content_unchanged_when_no_escapes(self):
        gate = EscapeGate()
        gate.mark_corruption_proven()
        self.assertEqual(gate.prepare_write_content("plain ascii"), "plain ascii")

    def test_write_content_non_string_passthrough(self):
        gate = EscapeGate()
        gate.mark_corruption_proven()
        value = 42
        self.assertEqual(gate.prepare_write_content(value), value)

    def test_corruption_flag_is_persistent(self):
        gate = EscapeGate()
        gate.mark_corruption_proven()
        self.assertTrue(gate.corruption_proven)
        gate.mark_corruption_proven()
        self.assertTrue(gate.corruption_proven)


if __name__ == "__main__":
    unittest.main()
