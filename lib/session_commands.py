"""
Limitcode Session Commands.
Commands for request cancellation and session control.
"""

import sublime
import sublime_plugin


class LimitcodeStopAgentCommand(sublime_plugin.WindowCommand):
    """Stop the currently running agent."""
    
    def run(self):
        from .chat import ChatView
        chat = ChatView.get_instance(self.window)
        
        if hasattr(chat, '_current_agent') and chat._current_agent:
            if hasattr(chat._current_agent, "cancel"):
                chat._current_agent.cancel()
            else:
                chat._current_agent.is_cancelled = True
            chat._active_run_token = None
            chat.hide_loading()
            chat.append_text("\n\n*[Execution cancelled by user]*\n")
            chat.prepare_for_user()
            chat.on_stream_complete()
            chat._current_agent = None
            sublime.status_message("Limitcode: Agent stopped")
        else:
            sublime.status_message("Limitcode: No agent is currently running")
