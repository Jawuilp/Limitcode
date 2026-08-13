import os
import re
import sublime
from typing import Dict, List, Optional, Any, Tuple


_UNICODE_ESCAPE_RE = re.compile(r"\\(?:u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8})")


def decode_unicode_escapes(value: str) -> str:
    """Decode literal Unicode escapes without changing other backslashes."""
    if not isinstance(value, str) or not _UNICODE_ESCAPE_RE.search(value):
        return value

    def decode_match(match):
        try:
            return chr(int(match.group(0)[2:], 16))
        except ValueError:
            return match.group(0)

    decoded = _UNICODE_ESCAPE_RE.sub(
        decode_match,
        value,
    )

    result = []
    index = 0
    while index < len(decoded):
        codepoint = ord(decoded[index])
        if 0xD800 <= codepoint <= 0xDBFF and index + 1 < len(decoded):
            low = ord(decoded[index + 1])
            if 0xDC00 <= low <= 0xDFFF:
                result.append(chr(0x10000 + ((codepoint - 0xD800) << 10) + low - 0xDC00))
                index += 2
                continue
        result.append(decoded[index])
        index += 1

    return "".join(result)

def _truncate_content(content: str, max_chars: int = 30000) -> str:
    """Truncate content if too long, with a message."""
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + f"\n\n... [TRUNCATED — showing {max_chars} of {len(content)} characters. Use start_line/end_line for specific sections.]"


def get_active_file_path() -> Optional[str]:
    """Get the absolute path of the currently active code file in Sublime.
    
    Checks the currently focused active view, and if it's the chat view, falls back to
    active views of other groups or any open code files.
    """
    active_window = sublime.active_window()
    if not active_window:
        return None

    active_view = None
    window_active = active_window.active_view()
    if window_active and not window_active.settings().get("limitcode_chat_view"):
        active_view = window_active
    else:
        # Check active views of all groups to find a code view
        for g in range(active_window.num_groups()):
            v = active_window.active_view_in_group(g)
            if v and not v.settings().get("limitcode_chat_view") and v.file_name():
                active_view = v
                break
        if not active_view:
            # Fallback: check any view that has a filename and is not a chat view
            for v in active_window.views():
                if v.file_name() and not v.settings().get("limitcode_chat_view"):
                    active_view = v
                    break

    if not active_view:
        return None

    return active_view.file_name()


def get_open_files_paths() -> List[str]:
    """Get the absolute paths of all open code files in Sublime (active tabs/views)."""
    active_window = sublime.active_window()
    if not active_window:
        return []
    
    paths = []
    for v in active_window.views():
        if v.file_name() and not v.settings().get("limitcode_chat_view"):
            abs_path = os.path.normcase(os.path.abspath(v.file_name()))
            if abs_path not in [os.path.normcase(os.path.abspath(p)) for p in paths]:
                paths.append(v.file_name())
    return paths


def resolve_open_file_path(file_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Resolve a path to exactly one open file, rejecting ambiguous matches."""
    open_files = get_open_files_paths()
    if not open_files:
        return None, "Limitcode can only access files that are open in Sublime Text."

    norm_open_files = {os.path.normcase(os.path.abspath(f)): f for f in open_files}
    direct_path = os.path.normcase(os.path.abspath(file_path))

    if os.path.isabs(file_path):
        resolved = norm_open_files.get(direct_path)
        if resolved:
            return resolved, None
        return None, f"File is not open in Sublime Text: {file_path}"

    active_window = sublime.active_window()
    folders = active_window.folders() if active_window else []
    matches = set()

    for folder in folders:
        candidate = os.path.normcase(os.path.abspath(os.path.join(folder, file_path)))
        if candidate in norm_open_files:
            matches.add(candidate)

    for orig_path in open_files:
        open_dir = os.path.dirname(orig_path)
        candidate = os.path.normcase(os.path.abspath(os.path.join(open_dir, file_path)))
        if candidate in norm_open_files:
            matches.add(candidate)

    if len(matches) == 1:
        normalized = next(iter(matches))
        return norm_open_files[normalized], None

    if len(matches) > 1:
        candidates = sorted(norm_open_files[match] for match in matches)
        return None, (
            f"Ambiguous open file path '{file_path}'. Use a project-relative or absolute "
            f"path. Matches: {', '.join(candidates)}"
        )

    return None, f"File is not open in Sublime Text: {file_path}"


def get_validated_active_file_path(file_path: str) -> Optional[str]:
    """Return the uniquely matching open file path, if one exists."""
    resolved_path, _error = resolve_open_file_path(file_path)
    return resolved_path


def check_active_file(file_path: str) -> bool:
    """Check if the given path uniquely matches an open file in Sublime."""
    return get_validated_active_file_path(file_path) is not None


class Tool:
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description

    def execute(self, **kwargs) -> Dict[str, Any]:
        raise NotImplementedError("Subclasses must implement execute")

