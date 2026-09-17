"""Chat status-bar style loading and user-facing overrides."""

import math
from typing import Any, Optional

import sublime


BASE_STYLE_RESOURCE = "Packages/Limitcode/assets/limitcode_chat.css"
USER_STYLE_RESOURCE = "Packages/User/Limitcode/chat.css"

_STYLE_VALUE_VARIABLES = {
    "chip_background": "--limitcode-chip-background",
    "chip_border": "--limitcode-chip-border",
    "chip_text": "--limitcode-chip-text",
    "link_color": "--limitcode-chip-link",
    "muted_text": "--limitcode-chip-muted",
}


def _load_resource(resource_name: str) -> str:
    try:
        return sublime.load_resource(resource_name)
    except Exception:
        return ""


def _safe_css_value(value: Any) -> Optional[str]:
    if isinstance(value, bool) or value is None:
        return None

    text = str(value).strip()
    if not text or any(character in text for character in "<> {};"):
        return None
    return text


def _number(value: Any, minimum: float, maximum: float) -> Optional[str]:
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    if value < minimum or value > maximum:
        return None
    return str(value)


def _dimension(value: Any) -> Optional[str]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return f"{value:g}px"
    return _safe_css_value(value)


def _settings_css(settings: Any) -> str:
    style = settings.get("chat_style", {})
    if not isinstance(style, dict):
        return ""

    declarations = []
    for setting_name, variable_name in _STYLE_VALUE_VARIABLES.items():
        value = _safe_css_value(style.get(setting_name))
        if value is not None:
            declarations.append(f"    {variable_name}: {value};")

    muted_text = _safe_css_value(style.get("muted_text"))
    if muted_text is None:
        opacity = _number(style.get("muted_opacity"), 0, 1)
        if opacity is not None:
            declarations.append(
                "    --limitcode-chip-muted: "
                f"color(var(--foreground) alpha({opacity}));"
            )

    radius = _dimension(style.get("chip_radius"))
    if radius is not None:
        declarations.append(f"    --limitcode-chip-radius: {radius};")

    padding = _safe_css_value(style.get("chip_padding"))
    if padding is not None:
        declarations.append(f"    --limitcode-chip-padding: {padding};")

    if not declarations:
        return ""
    return "#limitcode-status {\n" + "\n".join(declarations) + "\n}"


def load_chat_styles() -> str:
    """Return base, settings, and optional user CSS in cascade order."""
    settings = sublime.load_settings("Limitcode.sublime-settings")
    sections = [_load_resource(BASE_STYLE_RESOURCE)]

    settings_css = _settings_css(settings)
    if settings_css:
        sections.append(settings_css)

    user_css = _load_resource(USER_STYLE_RESOURCE)
    if user_css:
        sections.append(user_css)

    return "\n\n".join(section for section in sections if section)
