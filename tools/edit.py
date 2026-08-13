import os
import sublime
from typing import Dict, Any
from .base import Tool, decode_unicode_escapes, resolve_open_file_path
from .edit_fuzzy import apply_edit

class EditFileTool(Tool):
    def __init__(self):
        super().__init__("edit_file", "Edit specific parts of a file")

    def execute(self, file_path: str, old_str: str, new_str: str) -> Dict[str, Any]:
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
            old_str = decode_unicode_escapes(old_str)
            new_str = decode_unicode_escapes(new_str)

            # Use advanced fuzzy matching
            try:
                new_content, strategy = apply_edit(content, old_str, new_str)
            except ValueError as e:
                return {"success": False, "error": str(e)}

            import threading
            completed = threading.Event()
            error_holder = []

            def update_buffer():
                try:
                    view.run_command("limitcode_write_buffer", {"content": new_content})
                    view.run_command("save")
                except Exception as e:
                    error_holder.append(str(e))
                finally:
                    completed.set()

            sublime.set_timeout(update_buffer, 0)
            completed.wait()
            if error_holder:
                return {"success": False, "error": error_holder[0]}

            message = f"Successfully edited {file_path} using '{strategy}' matching strategy."
            
            # Try to collect LSP diagnostics to notify agent of syntax/compilation errors
            try:
                from ..lsp import LSPDiagnosticsCollector
                collector = LSPDiagnosticsCollector()
                errors = collector.collect_for_file(file_path)
                if errors:
                    error_list = "\n".join([f"- Line {e.row + 1}, Col {e.col + 1}: {e.message}" for e in errors])
                    message += f"\n\nLSP errors detected in this file, please fix:\n{error_list}"
            except Exception:
                pass

            return {
                "success": True, 
                "message": message
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
