"""
Limitcode Inline Assistant.
Enables inline code transformations, generation, and explanations directly in the active editor buffer
with real-time streaming, compact responsive layout, visual diff preview, and accept/reject controls.
"""

import sublime
import sublime_plugin
import html
import re
import difflib
import threading
import time
from typing import Optional, Dict, Any, List

from .limitcode import get_provider
from .logger import log_info, log_error
from .chat import ChatView


# Store one active session per view ID
_ACTIVE_SESSIONS: Dict[int, "InlineAssistSession"] = {}
_SESSIONS_LOCK = threading.Lock()


def get_active_session(view: sublime.View) -> Optional["InlineAssistSession"]:
    with _SESSIONS_LOCK:
        return _ACTIVE_SESSIONS.get(view.id())


def register_session(view: sublime.View, session: "InlineAssistSession"):
    with _SESSIONS_LOCK:
        old = _ACTIVE_SESSIONS.get(view.id())
        if old:
            try:
                old.cancel()
            except Exception:
                pass
        _ACTIVE_SESSIONS[view.id()] = session
        view.settings().set("limitcode_inline_assist_active", True)


def unregister_session(view: sublime.View):
    with _SESSIONS_LOCK:
        _ACTIVE_SESSIONS.pop(view.id(), None)
        try:
            view.settings().erase("limitcode_inline_assist_active")
        except Exception:
            pass


SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


def extract_thinking_and_content(raw_text: str):
    """
    Separate <think>...</think> reasoning thoughts from visible output.
    Returns: (visible_content: str, thinking_text: str, is_currently_thinking: bool)
    """
    if not raw_text:
        return "", "", False

    is_currently_thinking = False
    thinking_text = ""
    visible_content = raw_text

    # Check if unclosed <think> tag is present (model is actively reasoning right now)
    if "<think>" in visible_content.lower() and "</think>" not in visible_content.lower():
        is_currently_thinking = True
        match = re.search(r"<think>(.*?)$", visible_content, flags=re.DOTALL | re.IGNORECASE)
        if match:
            thinking_text = match.group(1).strip()
        visible_content = re.sub(r"<think>.*?$", "", visible_content, flags=re.DOTALL | re.IGNORECASE).strip()

    # Check if closed <think>...</think> tags are present
    elif "<think>" in visible_content.lower():
        think_matches = re.findall(r"<think>(.*?)</think>", visible_content, flags=re.DOTALL | re.IGNORECASE)
        if think_matches:
            thinking_text = "\n".join(m.strip() for m in think_matches)
        visible_content = re.sub(r"<think>.*?</think>\s*", "", visible_content, flags=re.DOTALL | re.IGNORECASE).strip()

    return visible_content, thinking_text, is_currently_thinking


def clean_code_fences(text: str) -> str:
    """Extract code if model wrapped the entire response in markdown code blocks."""
    trimmed = text.strip()
    fence_pattern = r"^```[a-zA-Z0-9_\-\.\+]*\r?\n([\s\S]*?)\r?\n```$"
    match = re.match(fence_pattern, trimmed)
    if match:
        return match.group(1)
    return text


def build_diff_html(original: str, replacement: str, max_lines: int = 40) -> str:
    """Generate visual HTML diff with red deletions and green additions."""
    orig_lines = original.splitlines()
    repl_lines = replacement.splitlines()

    diff = list(difflib.ndiff(orig_lines, repl_lines))
    lines_html = []
    line_count = 0

    for item in diff:
        if line_count >= max_lines:
            remaining = len(diff) - max_lines
            lines_html.append(
                f'<div class="diff-line diff-ctx" style="opacity: 0.6;">... and {remaining} more lines</div>'
            )
            break

        prefix = item[:2]
        line_text = html.escape(item[2:])

        if prefix == "- ":
            lines_html.append(f'<div class="diff-line diff-del"><span class="sign">-</span>{line_text}</div>')
            line_count += 1
        elif prefix == "+ ":
            lines_html.append(f'<div class="diff-line diff-add"><span class="sign">+</span>{line_text}</div>')
            line_count += 1
        elif prefix == "  ":
            lines_html.append(f'<div class="diff-line diff-ctx"><span class="sign"> </span>{line_text}</div>')
            line_count += 1
        # skip "? " intra-line marker

    if not lines_html:
        lines_html.append('<div class="diff-line diff-ctx">(No changes detected)</div>')

    return "".join(lines_html)


def render_markdown_to_minihtml(text: str) -> str:
    """
    Convert LLM markdown response into clean, robust HTML compatible with Sublime's minihtml engine.
    Handles headings, bold, italics, inline code, fenced code blocks, and list items.
    """
    if not text:
        return ""

    # 1. Extract fenced code blocks first to protect them from inline line/bullet parsing
    code_blocks: List[str] = []

    def _replace_code_block(match):
        idx = len(code_blocks)
        lang = match.group(1) or ""
        code = match.group(2).strip("\r\n")
        escaped_code = html.escape(code)
        lang_header = f'<div class="code-header">{html.escape(lang)}</div>' if lang else ""
        block_html = (
            f'<div class="code-box">'
            f'{lang_header}'
            f'<pre><code>{escaped_code}</code></pre>'
            f'</div>'
        )
        code_blocks.append(block_html)
        return f"\x00BLOCK_{idx}\x00"

    fenced_pattern = r"```([a-zA-Z0-9_\-\.\+]*)\r?\n([\s\S]*?)\r?\n```"
    processed = re.sub(fenced_pattern, _replace_code_block, text.strip())

    # 2. Process line by line
    lines = processed.splitlines()
    html_lines: List[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            html_lines.append('<div class="spacer"></div>')
            continue

        # Check if line is a code block placeholder
        placeholder_match = re.match(r"^\x00BLOCK_(\d+)\x00$", stripped)
        if placeholder_match:
            idx = int(placeholder_match.group(1))
            html_lines.append(code_blocks[idx])
            continue

        # Check for headings (#, ##, ###)
        heading_match = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if heading_match:
            level = len(heading_match.group(1))
            heading_text = _format_inline_markdown(heading_match.group(2))
            font_size = "0.95rem" if level <= 2 else "0.88rem"
            html_lines.append(
                f'<div class="heading" style="font-size: {font_size}; font-weight: bold; margin: 0.35rem 0 0.15rem 0; color: var(--foreground);">'
                f'{heading_text}'
                f'</div>'
            )
            continue

        # Check for bullet list items (- item, * item)
        bullet_match = re.match(r"^[\-\*]\s+(.+)$", stripped)
        if bullet_match:
            item_text = _format_inline_markdown(bullet_match.group(1))
            html_lines.append(
                f'<div class="bullet-item"><span class="bullet">•</span>{item_text}</div>'
            )
            continue

        # Check for numbered list items (1. item)
        num_match = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if num_match:
            num = num_match.group(1)
            item_text = _format_inline_markdown(num_match.group(2))
            html_lines.append(
                f'<div class="bullet-item"><span class="bullet">{num}.</span>{item_text}</div>'
            )
            continue

        # Normal paragraph line
        para_text = _format_inline_markdown(stripped)
        html_lines.append(f'<div class="para-line">{para_text}</div>')

    return "".join(html_lines)


def _format_inline_markdown(text: str) -> str:
    """Format inline markdown elements: bold, italics, inline code."""
    escaped = html.escape(text)

    # 1. Bold code: **`code`** or `**code**`
    escaped = re.sub(
        r"\*\*`([^`]+)`\*\*",
        r'<b><code class="inline-code">\1</code></b>',
        escaped
    )
    escaped = re.sub(
        r"`\*\*([^*]+)\*\*`",
        r'<b><code class="inline-code">\1</code></b>',
        escaped
    )

    # 2. Inline code: `code`
    escaped = re.sub(
        r"`([^`]+)`",
        r'<code class="inline-code">\1</code>',
        escaped
    )

    # 3. Bold: **text** or __text__
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"__([^_]+)__", r"<b>\1</b>", escaped)

    # 4. Italics: *text* or _text_
    escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", escaped)
    escaped = re.sub(r"(?<!_)_([^_]+)_(?!_)", r"<i>\1</i>", escaped)

    return escaped


class InlineAssistSession:
    """Manages an active inline assistant generation and phantom lifecycle."""

    def __init__(self, view: sublime.View, region: sublime.Region, prompt: str):
        self.view = view
        self.region = region
        self.original_text = view.substr(region)
        self.prompt = prompt
        self.stream_content = ""
        self.is_cancelled = False
        self.is_complete = False
        self.provider = None
        self.provider_name = ""
        self.model_name = ""
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.phantom_set = sublime.PhantomSet(view, "limitcode_inline_assist")
        self._last_ui_update = 0.0
        self._spinner_idx = 0

    def start(self):
        """Register session, inject initial phantom, and spawn stream thread."""
        register_session(self.view, self)
        # Prefetch provider info early if possible
        try:
            settings = sublime.load_settings("Limitcode.sublime-settings")
            self.model_name = settings.get("default_model", "")
            self.provider_name = settings.get("default_provider", "")
        except Exception:
            pass

        self._render_loading()
        self._animate_loading()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _animate_loading(self):
        """Rotate spinner smoothly while waiting for first tokens from model."""
        if self.is_cancelled or self.is_complete or bool(self.stream_content):
            return
        self._spinner_idx += 1
        self._render_loading()
        sublime.set_timeout(lambda: self._animate_loading(), 120)

    def cancel(self):
        """Cancel in-flight generation and destroy phantom."""
        with self._lock:
            self.is_cancelled = True
        if self.provider and hasattr(self.provider, "cancel_active_request"):
            try:
                self.provider.cancel_active_request()
            except Exception:
                pass
        self.close()

    def close(self):
        """Remove phantoms and unregister session."""
        try:
            self.phantom_set.update([])
            self.view.erase_phantoms("limitcode_inline_assist")
        except Exception:
            pass
        unregister_session(self.view)

    def _get_card_cols(self) -> int:
        """Calculate responsive card width in ems based on the visible editor pane."""
        try:
            vp_width = self.view.viewport_extent()[0]
            em = self.view.em_width()
            if em > 0 and vp_width > 0:
                # Leave margin for gutter (~4-6em) and scrollbar
                cols = int(vp_width / em) - 6
                # Clamp between 28em (narrow split pane) and 50em (comfortable max reading width)
                return max(28, min(cols, 50))
        except Exception:
            pass
        return 44

    def _header_html(self) -> str:
        model_badge = ""
        if self.model_name:
            display_model = self.model_name.split("/")[-1] if "/" in self.model_name else self.model_name
            model_badge = f'&nbsp;<span class="model-badge">{html.escape(display_model)}</span>&nbsp;&nbsp;'

        prompt_display = self.prompt
        if len(prompt_display) > 36:
            prompt_display = prompt_display[:33] + "..."

        return f"""
        <div class="header">
            <b>Limitcode Inline</b>{model_badge}
            <span class="prompt-badge">"{html.escape(prompt_display)}"</span>
        </div>
        """

    def _render_loading(self, status: str = "Thinking..."):
        frame = SPINNER_FRAMES[self._spinner_idx % len(SPINNER_FRAMES)]
        content = f"""
        <body id="limitcode-inline-phantom">
            <style>
                {self._base_css()}
            </style>
            <div class="inline-card">
                {self._header_html()}
                <div class="status-msg">
                    <span class="spinner">{frame}</span> {html.escape(status)}
                </div>
                <div class="actions">
                    <a href="cancel" class="btn btn-cancel">Cancel (Esc)</a>
                </div>
            </div>
        </body>
        """
        self._update_phantom(content)

    def _render_streaming(self):
        visible_text, think_text, is_thinking = extract_thinking_and_content(self.stream_content)
        frame = SPINNER_FRAMES[self._spinner_idx % len(SPINNER_FRAMES)]
        self._spinner_idx += 1

        # Determine clear user-facing status label
        if is_thinking and not visible_text:
            status_text = f'<span class="spinner">{frame}</span> Reasoning...'
        elif not visible_text:
            status_text = f'<span class="spinner">{frame}</span> Thinking...'
        else:
            is_question = self._is_likely_question(self.prompt, visible_text)
            if is_question:
                status_text = f'<span class="spinner">{frame}</span> Writing response...'
            else:
                status_text = f'<span class="spinner">{frame}</span> Generating code...'

        # Render preview content cleanly without mid-word cutting
        if is_thinking and not visible_text:
            short_think = html.escape(think_text[-280:]) if think_text else "Analyzing context and code..."
            body_html = f"""
            <div class="thinking-box">
                <div class="thinking-title">💭 Thought process:</div>
                <div class="thinking-content">{short_think}<span class="stream-cursor">▌</span></div>
            </div>
            """
        elif visible_text:
            is_question = self._is_likely_question(self.prompt, visible_text)
            if is_question:
                # Render real live markdown from beginning with streaming cursor
                formatted = render_markdown_to_minihtml(visible_text)
                body_html = f"""
                <div class="explanation-box streaming-box">
                    {formatted}
                    <div style="margin-top: 0.2rem;"><span class="stream-cursor">▌</span></div>
                </div>
                """
            else:
                cleaned = clean_code_fences(visible_text)
                escaped_code = html.escape(cleaned)
                body_html = f"""
                <div class="code-box streaming-code">
                    <pre><code>{escaped_code}<span class="stream-cursor">▌</span></code></pre>
                </div>
                """
        else:
            body_html = ""

        content = f"""
        <body id="limitcode-inline-phantom">
            <style>
                {self._base_css()}
            </style>
            <div class="inline-card">
                {self._header_html()}
                <div class="status-msg">
                    {status_text}
                </div>
                {body_html}
                <div class="actions">
                    <a href="cancel" class="btn btn-cancel">Cancel (Esc)</a>
                </div>
            </div>
        </body>
        """
        self._update_phantom(content)

    def _render_diff_ready(self, final_text: str):
        # Strip any <think> tags before final rendering
        visible_final, _, _ = extract_thinking_and_content(final_text)
        final_text = visible_final if visible_final.strip() else final_text

        is_edit = bool(self.original_text.strip())
        cleaned_text = clean_code_fences(final_text)

        # Detect if it's informational vs code edit
        is_pure_question = self._is_likely_question(self.prompt, final_text)

        if is_pure_question or not is_edit:
            # Render as rich formatted markdown explanation with clean wrapping
            formatted_answer = render_markdown_to_minihtml(final_text)
            body_html = f"""
            <div class="explanation-box">
                {formatted_answer}
            </div>
            """
            actions_html = """
                <a href="reject" class="btn btn-accept">Close (Esc)</a>
                <a href="chat" class="btn">Open in Chat</a>
            """
        else:
            diff_html = build_diff_html(self.original_text, cleaned_text)
            body_html = f"""
            <div class="diff-container">
                <div class="diff-header">Preview changes:</div>
                <div class="diff-content">{diff_html}</div>
            </div>
            """
            actions_html = """
                <a href="accept" class="btn btn-accept">✔ Accept (Enter)</a>
                <a href="reject" class="btn btn-reject">✕ Reject (Esc)</a>
                <a href="chat" class="btn">Open in Chat</a>
                <a href="retry" class="btn">Retry</a>
            """

        content = f"""
        <body id="limitcode-inline-phantom">
            <style>
                {self._base_css()}
            </style>
            <div class="inline-card">
                {self._header_html()}
                {body_html}
                <div class="actions">
                    {actions_html}
                </div>
            </div>
        </body>
        """
        self._update_phantom(content)

    def _update_phantom(self, html_content: str):
        # Anchor phantom at end of the selected region
        phantom_region = sublime.Region(self.region.b, self.region.b)
        phantom = sublime.Phantom(
            phantom_region,
            html_content,
            sublime.LAYOUT_BLOCK,
            lambda href: self.on_navigate(href)
        )
        self.phantom_set.update([phantom])

    def _base_css(self) -> str:
        cols = self._get_card_cols()
        inner_cols = max(24, cols - 2)

        return f"""
            .inline-card {{
                background-color: color(var(--background) blend(var(--foreground) 94%));
                border: 1px solid color(var(--foreground) alpha(0.18));
                border-left: 4px solid var(--accent);
                border-radius: 4px;
                padding: 0.75rem 1rem;
                font-family: var(--font-mono);
                font-size: 0.85rem;
                color: var(--foreground);
                margin: 0.85rem 0;
                width: {cols}em;
            }}
            .header {{
                font-size: 0.85rem;
                color: var(--foreground);
                margin-bottom: 0.7rem;
                line-height: 1.4;
            }}
            .icon {{
                color: var(--accent);
                font-weight: bold;
                margin-right: 0.25rem;
            }}
            .model-badge {{
                background-color: color(var(--foreground) alpha(0.08));
                border: 1px solid color(var(--foreground) alpha(0.15));
                border-radius: 3px;
                padding: 0.12rem 0.5rem;
                font-size: 0.72rem;
                color: var(--accent);
                margin: 0 0.5rem 0 0.35rem;
                font-weight: normal;
                display: inline-block;
                vertical-align: middle;
            }}
            .prompt-badge {{
                color: color(var(--foreground) alpha(0.6));
                font-size: 0.78rem;
                margin-left: 0.6rem;
                display: inline-block;
                vertical-align: middle;
            }}
            .status-msg {{
                color: color(var(--foreground) alpha(0.7));
                padding: 0.4rem 0;
            }}
            .spinner {{
                color: var(--accent);
                font-weight: bold;
            }}
            .stream-preview pre {{
                background-color: color(var(--background) blend(var(--foreground) 98%));
                border: 1px solid color(var(--foreground) alpha(0.1));
                border-radius: 3px;
                padding: 0.4rem 0.6rem;
                margin: 0.4rem 0;
                max-height: 14rem;
                width: {inner_cols}em;
                white-space: pre-wrap;
                word-wrap: break-word;
            }}
            .diff-container {{
                margin: 0.4rem 0;
                border: 1px solid color(var(--foreground) alpha(0.12));
                border-radius: 3px;
                width: {inner_cols}em;
            }}
            .diff-header {{
                background-color: color(var(--background) blend(var(--foreground) 90%));
                padding: 0.25rem 0.5rem;
                font-size: 0.75rem;
                font-weight: bold;
                color: color(var(--foreground) alpha(0.7));
                border-bottom: 1px solid color(var(--foreground) alpha(0.1));
            }}
            .diff-content {{
                background-color: color(var(--background) blend(var(--foreground) 97%));
                max-height: 18rem;
                overflow-y: auto;
                font-family: var(--font-mono);
                font-size: 0.82rem;
                line-height: 1.35;
                white-space: pre-wrap;
                width: {inner_cols}em;
            }}
            .diff-line {{
                padding: 0.1rem 0.4rem;
                white-space: pre-wrap;
            }}
            .diff-add {{
                background-color: color(var(--greenish) alpha(0.15));
                color: var(--greenish);
            }}
            .diff-del {{
                background-color: color(var(--redish) alpha(0.15));
                color: var(--redish);
            }}
            .diff-ctx {{
                color: color(var(--foreground) alpha(0.8));
            }}
            .sign {{
                display: inline-block;
                width: 1.2rem;
                font-weight: bold;
            }}
            .explanation-box {{
                background-color: color(var(--background) blend(var(--foreground) 97%));
                border: 1px solid color(var(--foreground) alpha(0.1));
                border-radius: 4px;
                padding: 0.7rem 0.9rem;
                margin: 0.55rem 0;
                line-height: 1.45;
                font-size: 0.82rem;
                white-space: normal;
                width: {inner_cols}em;
            }}
            .para-line {{
                margin: 0.18rem 0;
                color: var(--foreground);
            }}
            .bullet-item {{
                margin: 0.25rem 0 0.18rem 0.3rem;
                color: var(--foreground);
            }}
            .bullet {{
                color: var(--accent);
                font-weight: bold;
                display: inline-block;
                width: 0.85rem;
            }}
            .spacer {{
                height: 0.35rem;
            }}
            code.inline-code, code {{
                background-color: color(var(--background) blend(var(--foreground) 88%));
                border: 1px solid color(var(--foreground) alpha(0.14));
                border-radius: 3px;
                padding: 0.05rem 0.3rem;
                font-family: var(--font-mono);
                font-size: 0.88em;
                color: var(--accent);
            }}
            b, strong {{
                font-weight: bold;
                color: var(--foreground);
            }}
            i, em {{
                font-style: italic;
            }}
            .code-box {{
                margin: 0.4rem 0;
                border: 1px solid color(var(--foreground) alpha(0.12));
                border-radius: 3px;
                background-color: color(var(--background) blend(var(--foreground) 98%));
            }}
            .code-header {{
                background-color: color(var(--background) blend(var(--foreground) 90%));
                padding: 0.15rem 0.45rem;
                font-size: 0.72rem;
                color: color(var(--foreground) alpha(0.6));
                border-bottom: 1px solid color(var(--foreground) alpha(0.08));
                font-weight: bold;
            }}
            .code-box pre {{
                margin: 0;
                padding: 0.4rem 0.6rem;
                white-space: pre-wrap;
                font-family: var(--font-mono);
                font-size: 0.82rem;
            }}
            .stream-cursor {{
                color: var(--accent);
                font-weight: bold;
                font-size: 0.9em;
            }}
            .streaming-box {{
                max-height: 18rem;
                overflow-y: hidden;
            }}
            .streaming-code {{
                max-height: 14rem;
                overflow-y: hidden;
            }}
            .thinking-box {{
                background-color: color(var(--background) blend(var(--foreground) 98%));
                border: 1px dashed color(var(--accent) alpha(0.35));
                border-radius: 4px;
                padding: 0.45rem 0.7rem;
                margin: 0.35rem 0;
                font-size: 0.8rem;
                line-height: 1.4;
                width: {inner_cols}em;
            }}
            .thinking-title {{
                color: var(--accent);
                font-size: 0.75rem;
                font-weight: bold;
                margin-bottom: 0.2rem;
            }}
            .thinking-content {{
                color: color(var(--foreground) alpha(0.7));
                font-style: italic;
                white-space: pre-wrap;
            }}
            .actions {{
                margin-top: 0.75rem;
                padding-top: 0.55rem;
                border-top: 1px solid color(var(--foreground) alpha(0.1));
            }}
            .btn {{
                text-decoration: none;
                padding: 0.25rem 0.65rem;
                border-radius: 3px;
                border: 1px solid color(var(--foreground) alpha(0.2));
                background-color: color(var(--background) blend(var(--foreground) 88%));
                color: var(--foreground);
                font-weight: bold;
                font-size: 0.8rem;
                margin-right: 0.4rem;
            }}
            .btn-accept {{
                background-color: color(var(--greenish) alpha(0.2));
                border-color: var(--greenish);
                color: var(--greenish);
            }}
            .btn-reject, .btn-cancel {{
                background-color: color(var(--redish) alpha(0.15));
                border-color: var(--redish);
                color: var(--redish);
            }}
        """

    def _is_likely_question(self, prompt: str, output: str) -> bool:
        p = prompt.strip().lower()
        question_words = (
            "what", "why", "how", "explain", "describe", "tell",
            "qué", "que", "cómo", "como", "por qué", "porque",
            "explica", "explicar", "explicame", "explícame",
            "dime", "entiendo", "significa", "ayuda", "resumen"
        )
        is_question_intent = p.endswith("?") or any(w in p for w in question_words)
        if is_question_intent:
            first_non_empty = next((line.strip() for line in output.splitlines() if line.strip()), "")
            code_starters = ("def ", "class ", "function ", "import ", "from ", "const ", "let ", "var ", "return ", "export ")
            if not any(first_non_empty.startswith(s) for s in code_starters):
                return True
        return False

    def _worker(self):
        try:
            self.provider = get_provider()
            self.model_name = getattr(self.provider, "model", "")
            self.provider_name = getattr(self.provider, "provider_name", getattr(self.provider, "PROVIDER_NAME", ""))

            file_name = self.view.file_name() or "untitled"

            system_prompt = (
                "You are an expert pair-programming AI assistant embedded directly inline into Sublime Text.\n"
                "The user has selected code in their editor and issued a request.\n"
                "GUIDELINES:\n"
                "1. If the user asks for an edit, refactoring, implementation, or transformation:\n"
                "   - Output ONLY the modified replacement code for the selected region.\n"
                "   - DO NOT include conversational chit-chat, greetings, or markdown explanations.\n"
                "   - DO NOT wrap the code in markdown code blocks (```lang ... ```) unless markdown is the target file type.\n"
                "2. If the user asks an informational question (e.g. 'explain this function', 'what is the bug?'):\n"
                "   - Provide a direct, concise, helpful explanation in markdown format."
            )

            user_msg = (
                f"File: {file_name}\n\n"
                f"Selected code:\n{self.original_text}\n\n"
                f"Instruction: {self.prompt}"
            )

            messages = [{"role": "user", "content": user_msg}]

            for chunk in self.provider.create_message(system_prompt, messages):
                if self.is_cancelled:
                    return
                self.stream_content += chunk
                now = time.time()
                if now - self._last_ui_update > 0.08:  # Throttle rendering ~12 fps
                    self._last_ui_update = now
                    sublime.set_timeout(lambda: self._render_streaming(), 0)

            if self.is_cancelled:
                return

            self.is_complete = True
            final_content = self.stream_content
            sublime.set_timeout(lambda: self._render_diff_ready(final_content), 0)

        except Exception as error:
            if not self.is_cancelled:
                log_error("[INLINE_ASSIST] Generation error", {"error": str(error)})
                err_msg = str(error)
                sublime.set_timeout(lambda: self._render_error(err_msg), 0)

    def _render_error(self, err_text: str):
        content = f"""
        <body id="limitcode-inline-phantom">
            <style>
                {self._base_css()}
            </style>
            <div class="inline-card" style="border-left-color: var(--redish);">
                <div class="header">
                    <span style="color: var(--redish); font-weight: bold;">✗ Limitcode Inline Assist Error</span>
                </div>
                <div class="explanation-box" style="color: var(--redish);">
                    {html.escape(err_text)}
                </div>
                <div class="actions">
                    <a href="reject" class="btn btn-cancel">Close (Esc)</a>
                    <a href="retry" class="btn">Retry</a>
                </div>
            </div>
        </body>
        """
        self._update_phantom(content)

    def on_navigate(self, href: str):
        """Handle clicks on the action buttons inside the inline phantom."""
        if href == "accept":
            cleaned = clean_code_fences(self.stream_content)
            self.apply_replacement(cleaned)
        elif href in ("reject", "cancel"):
            self.cancel()
        elif href == "chat":
            self.send_to_chat()
        elif href == "retry":
            self.close()
            sublime.set_timeout(
                lambda: self.view.window().run_command("limitcode_inline_assist", {"prompt": self.prompt}),
                50
            )

    def apply_replacement(self, replacement_text: str):
        """Apply replacement text to the buffer at the original region."""
        self.view.run_command("limitcode_apply_inline_edit", {
            "start": self.region.a,
            "end": self.region.b,
            "text": replacement_text
        })
        self.close()
        sublime.status_message("Limitcode: Inline change applied")

    def send_to_chat(self):
        """Transfer session prompt and assistant output to the main ChatView."""
        # Close inline assist phantom immediately so it doesn't linger in the buffer
        self.close()

        window = self.view.window() or sublime.active_window()
        chat = ChatView.get_instance(window)
        chat.show()

        # Format user prompt naturally like a direct question
        user_text = self.prompt.strip()
        orig_code = self.original_text.strip()
        if orig_code:
            file_name = self.view.file_name() or ""
            ext = file_name.rsplit(".", 1)[-1] if "." in file_name else ""
            user_msg = f"{user_text}\n\n```{ext}\n{orig_code}\n```"
        else:
            user_msg = user_text

        # Append user question with USER header
        chat.append_user_message(user_msg)

        # Append assistant response with ASSISTANT header
        if self.stream_content:
            visible_content, _, _ = extract_thinking_and_content(self.stream_content)
            resp = visible_content if visible_content.strip() else self.stream_content
            chat.append_text(resp)
            chat.on_stream_complete()

        # Prepare chat view so the user can immediately continue the conversation
        chat.prepare_for_user()
        if hasattr(chat, "_move_cursor_to_input"):
            chat._move_cursor_to_input()
        if chat._view and getattr(chat._view, "is_valid", lambda: True)():
            if hasattr(window, "focus_view"):
                window.focus_view(chat._view)


class LimitcodeInlineAssistCommand(sublime_plugin.TextCommand):
    """Trigger inline assistant for selected code or current line."""

    def is_enabled(self):
        return not bool(self.view.settings().get("limitcode_chat_view", False))

    def is_visible(self):
        return not bool(self.view.settings().get("limitcode_chat_view", False))

    def run(self, edit, prompt: Optional[str] = None):
        view = self.view
        window = view.window() or sublime.active_window()

        # Determine target region
        sel = view.sel()
        if sel and not sel[0].empty():
            region = sel[0]
        else:
            # Use current line if no selection
            region = view.line(sel[0] if sel else 0)

        if prompt is not None and prompt.strip():
            session = InlineAssistSession(view, region, prompt.strip())
            session.start()
        else:
            window.show_input_panel(
                "Limitcode Inline:",
                "",
                lambda user_prompt: self._on_prompt_entered(user_prompt, region),
                None,
                None
            )

    def _on_prompt_entered(self, prompt: str, region: sublime.Region):
        if not prompt or not prompt.strip():
            return
        session = InlineAssistSession(self.view, region, prompt.strip())
        session.start()


class LimitcodeApplyInlineEditCommand(sublime_plugin.TextCommand):
    """Helper command to apply inline edit cleanly to the buffer."""

    def run(self, edit, start: int, end: int, text: str):
        target_region = sublime.Region(start, end)
        self.view.replace(edit, target_region, text)
        # Select the newly replaced code
        new_region = sublime.Region(start, start + len(text))
        self.view.sel().clear()
        self.view.sel().add(new_region)
        self.view.show_at_center(new_region)


class LimitcodeCancelInlineAssistCommand(sublime_plugin.TextCommand):
    """Cancel any active inline assist session on current view."""

    def run(self, edit):
        session = get_active_session(self.view)
        if session:
            session.cancel()
