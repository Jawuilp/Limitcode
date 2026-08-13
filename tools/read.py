import os
import sublime
from typing import Dict, Any
from .base import Tool, _truncate_content, resolve_open_file_path

class ReadFileTool(Tool):
    def __init__(self):
        super().__init__("read_file", "Read the contents of a file")

    def execute(self, file_path: str, start_line: int = 1, end_line: int = None) -> Dict[str, Any]:
        try:
            resolved_path, resolution_error = resolve_open_file_path(file_path)
            if not resolved_path:
                return {
                    "success": False,
                    "error": resolution_error
                }
            file_path = resolved_path

            active_window = sublime.active_window()
            if not active_window:
                return {"success": False, "error": "No active window"}
            view = active_window.find_open_file(file_path)
            if not view:
                return {"success": False, "error": "File is not open in any tab"}

            content = view.substr(sublime.Region(0, view.size()))
            lines = content.splitlines(keepends=True)

            total_lines = len(lines)

            if end_line is None:
                end_line = total_lines

            # Clamp ranges
            start_line = max(1, start_line)
            end_line = min(end_line, total_lines)

            content = ''.join(lines[start_line - 1:end_line])

            # Truncate very large content
            content = _truncate_content(content)

            return {
                "success": True,
                "content": content,
                "total_lines": total_lines,
                "showing": f"lines {start_line}-{end_line}"
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
