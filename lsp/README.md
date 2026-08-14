# LSP integration

This module collects language server diagnostics after agent edits so the
agent can react to syntax and type errors before continuing.

It is intentionally passive and dependency-free:

1. It reuses the `LSP` package already installed and configured in the user's
   Sublime Text setup.
2. It reads diagnostics directly from the views that are open in Sublime Text
   through the native `view.diagnostics()` API.

After an edit, the collector checks the affected view for new diagnostics and
hands them back to the agent as feedback.
