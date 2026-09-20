"""
LSP Post-Edit Diagnostics for Limitcode.
After file edits, collect LSP diagnostics and report them to the model for auto-correction.

Workflow:
1. Agent edits a file
2. Wait briefly for the LSP server to publish fresh diagnostics
3. Collect diagnostics for the edited file
4. If errors found, report to agent for correction
5. Agent retries with fix
"""

import time
from typing import Any, Dict, List, Optional

import sublime

# The LSP package is optional. Limitcode must keep working when it is missing
# or configured with a different version, so every access is guarded.
try:
    from LSP.plugin.core.registry import windows as _lsp_windows
    from LSP.plugin.core.views import MissingUriError as _MissingUriError
    from LSP.plugin.core.views import uri_from_view as _uri_from_view

    _LSP_AVAILABLE = True
except Exception:
    _lsp_windows = None
    _uri_from_view = None
    _MissingUriError = Exception
    _LSP_AVAILABLE = False


# LSP DiagnosticSeverity: 1=Error, 2=Warning, 3=Information, 4=Hint.
_SEVERITY_MAP = {1: "error", 2: "warning", 3: "info", 4: "hint"}


def _log_debug(message: str, metadata: Optional[Dict[str, Any]] = None) -> None:
    """Best-effort debug logging; never let logging break the caller."""
    try:
        from ..lib.logger import log_debug

        log_debug(message, metadata)
    except Exception:
        pass


class LSPDiagnostic:
    """Represents a single LSP diagnostic."""

    def __init__(self, file_path: str, row: int, col: int, message: str, severity: str, source: str = ""):
        self.file_path = file_path
        self.row = row
        self.col = col
        self.message = message
        self.severity = severity  # error, warning, info, hint
        self.source = source

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file": self.file_path,
            "row": self.row,
            "col": self.col,
            "message": self.message,
            "severity": self.severity,
            "source": self.source
        }

    def __repr__(self):
        return f"LSPDiagnostic({self.file_path}:{self.row+1}:{self.col+1} {self.severity}: {self.message[:50]})"


class LSPDiagnosticsCollector:
    """
    Collects LSP diagnostics after file edits.

    Reads diagnostics from the LSP package the user already has installed and
    configured in Sublime Text. Sublime Text has no ``View.diagnostics()`` API:
    diagnostics live inside the LSP package and are reached through its session
    objects.

    Timing note: language servers publish diagnostics asynchronously (they react
    to ``didChange``). A fixed sleep is fragile: too short and we read the
    previous state, too long and every edit is slow. Instead we poll until the
    published diagnostics change from the first sample, and fall back to a
    bounded maximum wait when nothing changes (e.g. a clean file, where there is
    no version stamp to compare against for push-based servers like tsserver).
    """

    def __init__(self, wait_time: float = 1.5, max_diagnostics: int = 10, poll_interval: float = 0.15):
        # ``wait_time`` is now the maximum time we wait for fresh diagnostics,
        # not a fixed delay; we return as soon as the set changes.
        self.wait_time = wait_time
        self.poll_interval = poll_interval
        self.max_diagnostics = max_diagnostics

    def collect_for_file(self, file_path: str, window: Optional[sublime.Window] = None) -> List[LSPDiagnostic]:
        """
        Collect LSP diagnostics for a specific file.

        Polls for fresh diagnostics after an edit, then collects errors.

        Args:
            file_path: Path to the file
            window: Sublime window (uses active window if None)

        Returns:
            List of LSPDiagnostic objects
        """
        window = window or sublime.active_window()
        if window is None:
            return []

        # Find the view for this file. Diagnostics can only be read for files
        # that are open (the LSP package tracks open views).
        view = window.find_open_file(file_path)
        if not view or not view.is_valid():
            return []

        # Bail out before waiting if LSP is not available, so edits are not
        # penalized with a pointless delay.
        if not _LSP_AVAILABLE:
            _log_debug("[LSP-COLLECTOR] LSP package not available", {"file": file_path})
            return []

        try:
            uri = _uri_from_view(view)
        except _MissingUriError:
            _log_debug("[LSP-COLLECTOR] view has no lsp_uri yet", {"file": file_path})
            return []
        except Exception:
            return []

        sessions = self._lsp_sessions(view)
        if not sessions:
            _log_debug("[LSP-COLLECTOR] no LSP session for window", {"file": file_path})
            return []

        raw_diagnostics = self._wait_for_fresh(uri, sessions)
        diagnostics = [self._to_diagnostic(file_path, raw) for raw in raw_diagnostics]

        # Filter to errors only (most important for auto-correction)
        errors = [d for d in diagnostics if d.severity == "error"]

        _log_debug(
            "[LSP-COLLECTOR] collected",
            {
                "file": file_path,
                "sessions": len(sessions),
                "diagnostics": len(diagnostics),
                "errors": len(errors),
            },
        )

        # Limit to max_diagnostics
        return errors[:self.max_diagnostics]

    def _lsp_sessions(self, view: "sublime.View") -> List[Any]:
        try:
            wm = _lsp_windows.lookup(view.window())
        except Exception:
            wm = None
        if wm is None:
            return []
        try:
            return list(wm.get_sessions())
        except Exception:
            return []

    def _collect_raw(self, uri: str, sessions: List[Any]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for session in sessions:
            try:
                raw_diagnostics = session.diagnostics.get_diagnostics_for_uri(uri)
            except Exception:
                continue
            for raw in raw_diagnostics or []:
                results.append(raw)
        return results

    def _wait_for_fresh(self, uri: str, sessions: List[Any]) -> List[Dict[str, Any]]:
        """
        Poll the LSP diagnostics until they change from the first sample or the
        time budget runs out.

        Push-based servers (e.g. tsserver) do not expose a document version we
        can compare against, so the best available signal is that the published
        set changed after the edit. If nothing changes we keep waiting until
        ``wait_time`` and then return whatever is stored.
        """
        deadline = time.time() + self.wait_time
        latest = self._collect_raw(uri, sessions)
        first_signature = self._signature(latest)
        while time.time() < deadline:
            time.sleep(self.poll_interval)
            current = self._collect_raw(uri, sessions)
            if self._signature(current) != first_signature:
                return current
            latest = current
        return latest

    @staticmethod
    def _signature(diagnostics: List[Dict[str, Any]]) -> tuple:
        """Cheap, comparable fingerprint of a diagnostics set (no version info available)."""
        return tuple(sorted(
            "{severity}|{line}|{char}|{message}".format(
                severity=diag.get("severity"),
                line=(diag.get("range") or {}).get("start", {}).get("line"),
                char=(diag.get("range") or {}).get("start", {}).get("character"),
                message=diag.get("message"),
            )
            for diag in diagnostics
        ))

    @staticmethod
    def _to_diagnostic(file_path: str, raw: Dict[str, Any]) -> LSPDiagnostic:
        start = (raw.get("range") or {}).get("start") or {}
        row = int(start.get("line", 0))
        col = int(start.get("character", 0))
        severity = _SEVERITY_MAP.get(raw.get("severity", 1), "error")
        message = raw.get("message", "")
        if not isinstance(message, str):
            message = str(message)
        source = raw.get("source") or "LSP"
        return LSPDiagnostic(file_path, row, col, message, severity, source)

    def collect_for_recent_edits(self, edited_files: List[str], window: Optional[sublime.Window] = None) -> Dict[str, List[LSPDiagnostic]]:
        """
        Collect diagnostics for multiple recently edited files.

        Args:
            edited_files: List of file paths that were edited
            window: Sublime window

        Returns:
            Dict mapping file paths to their diagnostics
        """
        results = {}
        for file_path in edited_files:
            diagnostics = self.collect_for_file(file_path, window)
            if diagnostics:
                results[file_path] = diagnostics
        return results

    def format_for_llm(self, diagnostics: Dict[str, List[LSPDiagnostic]]) -> str:
        """
        Format diagnostics as a string for the LLM.

        Args:
            diagnostics: Dict of file paths to diagnostics

        Returns:
            Formatted string describing the errors
        """
        if not diagnostics:
            return ""

        parts = ["## LSP Diagnostics (errors found after edit)\n"]

        for file_path, diags in diagnostics.items():
            parts.append(f"\n### {file_path}")
            for diag in diags:
                parts.append(f"- Line {diag.row + 1}, Col {diag.col + 1}: {diag.message}")

        parts.append("\nPlease fix these errors.")

        return "\n".join(parts)
