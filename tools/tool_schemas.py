"""
Tool schemas for OpenAI and Anthropic API formats.
These definitions tell the AI models what tools are available and how to use them.
"""

from typing import List, Dict, Any


DISABLED_TOOL_NAMES = set()


# OpenAI-compatible format
OPENAI_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file at the specified path. Use this to understand existing code, configuration files, or any text file in the project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The absolute or relative path to the file to read"
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Optional starting line number (1-indexed). Defaults to 1."
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Optional ending line number. Defaults to end of file."
                    }
                },
                "required": ["file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_to_file",
            "description": "Completely overwrite an existing file that is already open in Sublime Text. Use this when you need to replace the file's entire contents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The path of the open file to overwrite"
                    },
                    "content": {
                        "type": "string",
                        "description": "The complete content to write to the file"
                    }
                },
                "required": ["file_path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Edit a specific part of a file by replacing old content with new content. Use this for making targeted changes without rewriting the entire file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The path to the file to edit"
                    },
                    "old_str": {
                        "type": "string",
                        "description": "The exact string to find and replace (must match exactly)"
                    },
                    "new_str": {
                        "type": "string",
                        "description": "The new string to replace the old string with"
                    }
                },
                "required": ["file_path", "old_str", "new_str"]
            }
        }
    }
]


# Anthropic format (slightly different structure)
ANTHROPIC_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "read_file",
        "description": "Read the contents of a file at the specified path. Use this to understand existing code, configuration files, or any text file in the project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The absolute or relative path to the file to read"
                },
                "start_line": {
                    "type": "integer",
                    "description": "Optional starting line number (1-indexed). Defaults to 1."
                },
                "end_line": {
                    "type": "integer",
                    "description": "Optional ending line number. Defaults to end of file."
                }
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "write_to_file",
        "description": "Completely overwrite an existing file that is already open in Sublime Text. Use this when you need to replace the file's entire contents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The path of the open file to overwrite"
                },
                "content": {
                    "type": "string",
                    "description": "The complete content to write to the file"
                }
            },
            "required": ["file_path", "content"]
        }
    },
    {
        "name": "edit_file",
        "description": "Edit a specific part of a file by replacing old content with new content. Use this for making targeted changes without rewriting the entire file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The path to the file to edit"
                },
                "old_str": {
                    "type": "string",
                    "description": "The exact string to find and replace (must match exactly)"
                },
                "new_str": {
                    "type": "string",
                    "description": "The new string to replace the old string with"
                }
            },
            "required": ["file_path", "old_str", "new_str"]
        }
    }
]


def get_tools_for_provider(provider_type: str) -> List[Dict[str, Any]]:
    """Get the appropriate tool schema format for a given provider."""
    def filter_openai_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [tool for tool in tools if tool["function"]["name"] not in DISABLED_TOOL_NAMES]

    def filter_anthropic_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [tool for tool in tools if tool["name"] not in DISABLED_TOOL_NAMES]

    if provider_type in ("openai", "gemini"):
        tools = filter_openai_tools(OPENAI_TOOLS)
    elif provider_type == "anthropic":
        tools = filter_anthropic_tools(ANTHROPIC_TOOLS)
    else:
        tools = filter_openai_tools(OPENAI_TOOLS)

    return tools


def get_required_args_for_tool(tool_name: str) -> List[str]:
    """Return required argument names for a tool from canonical schemas."""
    for tool in OPENAI_TOOLS:
        fn = tool.get("function", {})
        if fn.get("name") == tool_name:
            return list(fn.get("parameters", {}).get("required", []))

    for tool in ANTHROPIC_TOOLS:
        if tool.get("name") == tool_name:
            return list(tool.get("input_schema", {}).get("required", []))

    return []


def get_tool_names() -> List[str]:
    """Get list of all available tool names."""
    return [tool["function"]["name"] for tool in OPENAI_TOOLS if tool["function"]["name"] not in DISABLED_TOOL_NAMES]


def get_anthropic_tool_names() -> List[str]:
    """Get list of all available tool names (Anthropic format)."""
    return [tool["name"] for tool in ANTHROPIC_TOOLS if tool["name"] not in DISABLED_TOOL_NAMES]
