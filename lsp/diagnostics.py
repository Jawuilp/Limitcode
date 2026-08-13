"""
LSP Post-Edit Diagnostics for Limitcode.
After file edits, collect LSP diagnostics and report them to the model for auto-correction.

Workflow:
1. Agent edits a file
2. Wait briefly for LSP to process
3. Collect diagnostics for the edited file
4. If errors found, report to agent for correction
5. Agent retries with fix
"""

import sublime
import time
import threading
from typing import Dict, List, Any, Optional, Callable


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
    
    Uses Sublime Text's built-in diagnostic system (from the LSP package).
    Waits briefly after edits for LSP to process, then collects errors.
    """
    
    def __init__(self, wait_time: float = 1.0, max_diagnostics: int = 10):
        self.wait_time = wait_time
        self.max_diagnostics = max_diagnostics
    
    def collect_for_file(self, file_path: str, window: Optional[sublime.Window] = None) -> List[LSPDiagnostic]:
        """
        Collect LSP diagnostics for a specific file.
        
        Waits for LSP to process the edit, then collects errors.
        
        Args:
            file_path: Path to the file
            window: Sublime window (uses active window if None)
        
        Returns:
            List of LSPDiagnostic objects
        """
        window = window or sublime.active_window()
        
        # Find the view for this file
        view = window.find_open_file(file_path)
        if not view or not view.is_valid():
            return []
        
        # Wait for LSP to process
        time.sleep(self.wait_time)
        
        # Collect diagnostics from the view
        diagnostics = []
        
        # Sublime Text stores diagnostics in view's regions
        # The LSP package uses "region.sublimelinter" or similar keys
        # We try common keys used by LSP packages
        diagnostic_keys = [
            "sublimelinter.mark.error",
            "sublimelinter.mark.warning",
            "lsp_error",
            "lsp_warning",
        ]
        
        for key in diagnostic_keys:
            regions = view.get_regions(key)
            for region in regions:
                row, col = view.rowcol(region.begin())
                diagnostics.append(LSPDiagnostic(
                    file_path=file_path,
                    row=row,
                    col=col,
                    message=view.substr(region),
                    severity="error" if "error" in key else "warning",
                    source="LSP"
                ))
        
        # Also try the built-in diagnostics API (Sublime Text 4)
        if hasattr(view, "diagnostics"):
            for diag in view.diagnostics():
                row, col = view.rowcol(diag.region().begin())
                severity_map = {0: "error", 1: "warning", 2: "info", 3: "hint"}
                diagnostics.append(LSPDiagnostic(
                    file_path=file_path,
                    row=row,
                    col=col,
                    message=diag.message(),
                    severity=severity_map.get(diag.severity(), "error"),
                    source=diag.source() or "LSP"
                ))
        
        # Filter to errors only (most important for auto-correction)
        errors = [d for d in diagnostics if d.severity == "error"]
        
        # Limit to max_diagnostics
        return errors[:self.max_diagnostics]
    
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
