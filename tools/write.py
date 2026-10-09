import os
import sublime
from typing import Dict, Any
from .base import Tool, resolve_open_file_path

class WriteToFileTool(Tool):
    def __init__(self):
        super().__init__("write_to_file", "Write content to a file")

    def execute(self, file_path: str, content: str, escape_gate=None) -> Dict[str, Any]:
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

            # Issue #9: decode escapes only when the session gate has proof
            # that the provider corrupts tool-call arguments.
            if escape_gate is not None:
                content = escape_gate.prepare_write_content(content)

            # The buffer keeps "\n" internally and Sublime writes the file's own
            # line endings on save; a "\r\n" here would be saved as "\r\r\n"
            # (issue #13).
            content = content.replace("\r\n", "\n")

            import threading
            completed = threading.Event()
            error_holder = []

            def update_buffer():
                try:
                    # Buffer only by default (issues #15/#23): Sublime's
                    # Incremental Diff shows the change against disk; the user
                    # saves when they decide to. auto_save_edits restores the
                    # old persist-after-edit behavior.
                    view.run_command("limitcode_write_buffer", {"content": content})
                    if sublime.load_settings("Limitcode.sublime-settings").get("auto_save_edits", False):
                        view.run_command("save")
                except Exception as e:
                    error_holder.append(str(e))
                finally:
                    completed.set()

            sublime.set_timeout(update_buffer, 0)
            completed.wait()
            if error_holder:
                return {"success": False, "error": error_holder[0]}
                
            if sublime.load_settings("Limitcode.sublime-settings").get("auto_save_edits", False):
                message = f"Successfully wrote and saved {file_path}"
            else:
                message = (
                    f"Successfully wrote to {file_path}. "
                    "Applied to the editor buffer only; not saved to disk yet."
                )
            
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

            return {"success": True, "message": message}
        except Exception as e:
            return {"success": False, "error": str(e)}
