import os
import tempfile
import unittest

from tests.package_loader import load_limitcode_package
from tests.sublime_stub import install_sublime_stub


install_sublime_stub()
load_limitcode_package()

from Limitcode.lib.agent import Agent


class FakeProvider:
    PROVIDER_NAME = "fake"


class RecordingToolManager:
    def __init__(self):
        self.executed = []

    def execute_tool(self, tool_name, **kwargs):
        self.executed.append((tool_name, kwargs))
        return {"success": True}

    def get_available_tools(self):
        return {}


class AgentSafetyTest(unittest.TestCase):
    def setUp(self):
        self.tool_manager = RecordingToolManager()
        self.agent = Agent(
            provider=FakeProvider(),
            provider_type="fake",
            tool_manager=self.tool_manager,
            system_prompt="",
        )
        self.temp_files = []

    def tearDown(self):
        for file_path in self.temp_files:
            try:
                os.remove(file_path)
            except OSError:
                pass

    def _temp_file(self, content: bytes, suffix: str) -> str:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(content)
            self.temp_files.append(temp_file.name)
            return temp_file.name

    def test_manual_attachment_accepts_supported_image_signature(self):
        image_path = self._temp_file(b"\x89PNG\r\n\x1a\nimage-data", ".png")

        parts = self.agent._process_multimodal_content(
            f"describe this [Attached Image: {image_path}]"
        )

        image = next(part for part in parts if part["type"] == "image")
        self.assertEqual(image["mime_type"], "image/png")
        self.assertEqual(image["path"], image_path)

    def test_manual_attachment_rejects_non_image_file(self):
        file_path = self._temp_file(b"API_KEY=secret", ".png")

        parts = self.agent._process_multimodal_content(
            f"inspect this [Attached Image: {file_path}]"
        )

        self.assertFalse(any(part["type"] == "image" for part in parts))
        self.assertIn("Unsupported image file", parts[-1]["text"])

    def test_truncated_write_does_not_reach_tool_manager(self):
        result = self.agent._execute_tool(
            "write_to_file",
            {"file_path": "open.py", "content": "partial"},
            os.getcwd(),
            finish_reason="length",
        )

        self.assertFalse(result["success"])
        self.assertTrue(result["truncated"])
        self.assertEqual(self.tool_manager.executed, [])

    def test_truncated_edit_does_not_reach_tool_manager(self):
        result = self.agent._execute_tool(
            "edit_file",
            {"file_path": "open.py", "old_str": "a", "new_str": "b"},
            os.getcwd(),
            finish_reason="MAX_TOKENS",
        )

        self.assertFalse(result["success"])
        self.assertTrue(result["truncated"])
        self.assertEqual(self.tool_manager.executed, [])


if __name__ == "__main__":
    unittest.main()
