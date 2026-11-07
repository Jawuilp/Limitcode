import sys
import types


class Settings:
    def __init__(self, store):
        self._store = store

    def get(self, key, default=None):
        return self._store.get(key, default)

    def set(self, key, value):
        self._store[key] = value

    def erase(self, key):
        self._store.pop(key, None)


class CompletionList:
    def set_completions(self, completions, flags=None):
        self.completions = completions


class Region:
    def __init__(self, a, b):
        self.a = a
        self.b = b

    def empty(self):
        return self.a == self.b

    def begin(self):
        return min(self.a, self.b)

    def end(self):
        return max(self.a, self.b)

    def __eq__(self, other):
        return isinstance(other, Region) and self.a == other.a and self.b == other.b


class Phantom:
    def __init__(self, region, content, layout, on_navigate=None):
        self.region = region
        self.content = content
        self.layout = layout
        self.on_navigate = on_navigate


class PhantomSet:
    def __init__(self, view, key=""):
        self.view = view
        self.key = key
        self.phantoms = []

    def update(self, phantoms):
        self.phantoms = list(phantoms)


class Window:
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
            if getattr(v, "file_name", lambda: None)():
                norm_v = os.path.normcase(os.path.abspath(v.file_name()))
                if norm_v == norm_target:
                    return v
        return None

    def focus_view(self, view):
        self._active_view = view
        return None

    def show_input_panel(self, caption, initial_text, on_done, on_change, on_cancel):
        self._last_input_panel = {
            "caption": caption,
            "initial_text": initial_text,
            "on_done": on_done,
            "on_change": on_change,
            "on_cancel": on_cancel,
        }
        return None


def install_sublime_stub(store=None):
    """Install a minimal `sublime` module for unit tests."""
    settings_store = store if store is not None else {}
    module = sys.modules.get("sublime") or types.ModuleType("sublime")
    module._settings_store = settings_store
    module._packages_path = ""
    module._active_window = Window([], [])
    module.load_settings = lambda name: Settings(module._settings_store)
    module.save_settings = lambda name: None
    module.status_message = lambda message: None
    # Run scheduled callbacks synchronously so buffer writes/edits are
    # observable in tests without an event loop (previously only patched
    # ad-hoc by test_agent_runner, breaking standalone runs of other files).
    module.set_timeout = lambda f, delay=0: f()
    module.packages_path = lambda: module._packages_path
    module.active_window = lambda: module._active_window
    module.Window = getattr(module, "Window", Window)
    module.Region = getattr(module, "Region", Region)
    module.Phantom = getattr(module, "Phantom", Phantom)
    module.PhantomSet = getattr(module, "PhantomSet", PhantomSet)
    module.LAYOUT_INLINE = 0
    module.LAYOUT_BELOW = 1
    module.LAYOUT_BLOCK = 2
    module.View = getattr(module, "View", type("View", (), {}))
    module.CompletionList = getattr(module, "CompletionList", CompletionList)
    module.COMPLETION_FORMAT_TEXT = getattr(module, "COMPLETION_FORMAT_TEXT", 0)
    sys.modules["sublime"] = module

    # Stub sublime_plugin
    sublime_plugin = sys.modules.get("sublime_plugin") or types.ModuleType("sublime_plugin")
    sublime_plugin.WindowCommand = getattr(sublime_plugin, "WindowCommand", type("WindowCommand", (), {}))
    sublime_plugin.TextCommand = getattr(sublime_plugin, "TextCommand", type("TextCommand", (), {}))
    sublime_plugin.ViewEventListener = getattr(sublime_plugin, "ViewEventListener", type("ViewEventListener", (), {}))
    sublime_plugin.EventListener = getattr(sublime_plugin, "EventListener", type("EventListener", (), {}))
    sys.modules["sublime_plugin"] = sublime_plugin

    return settings_store
