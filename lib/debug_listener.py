"""Optional console debugging helpers.

Disabled by default. Set `"debug_console_log": true` in
Limitcode.sublime-settings to mirror the plugin host's stdout/stderr to
history/sublime_console.log and trace text commands in the chat view.
"""

import sublime
import sublime_plugin
import os
import sys

from .storage import data_path

_enabled = False


class LogWriter:
    def __init__(self, original, filepath):
        self.original = original
        self.filepath = filepath

    def write(self, buf):
        if self.original:
            try:
                self.original.write(buf)
            except:
                pass
        try:
            with open(self.filepath, "a", encoding="utf-8") as f:
                f.write(buf)
        except:
            pass

    def flush(self):
        if self.original:
            try:
                self.original.flush()
            except:
                pass


def plugin_loaded():
    global _enabled
    settings = sublime.load_settings("Limitcode.sublime-settings")
    _enabled = bool(settings.get("debug_console_log", False))
    if not _enabled:
        return

    log_file = data_path("logs", "sublime_console.log")
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    if not isinstance(sys.stdout, LogWriter):
        sys.stdout = LogWriter(sys.stdout, log_file)
    if not isinstance(sys.stderr, LogWriter):
        sys.stderr = LogWriter(sys.stderr, log_file)

    print(f"[DEBUG_LISTENER] Console redirection active. Output mirrored to {log_file}")


class LimitcodeDebugListener(sublime_plugin.EventListener):
    def on_text_command(self, view, command_name, args):
        if not _enabled:
            return None
        if not view.settings().get("limitcode_chat_view"):
            return None

        try:
            input_start = view.settings().get("limitcode_input_start", -1)
            sel_start = view.sel()[0].begin() if view.sel() else -1
            print(f"[DEBUG_LISTENER] Command intercepted: {command_name}, Args: {args}, InputStart: {input_start}, SelStart: {sel_start}")
        except Exception as e:
            print(f"[DEBUG_LISTENER] LOGGING ERROR: {str(e)}")
        return None
