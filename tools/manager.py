from typing import Dict, List, Optional, Any
from .base import Tool
from .read import ReadFileTool
from .write import WriteToFileTool
from .edit import EditFileTool

class ToolManager:
    def __init__(self):
        self.tools = {
            "read_file": ReadFileTool(),
            "write_to_file": WriteToFileTool(),
            "edit_file": EditFileTool(),
        }

    def get_tool(self, tool_name: str) -> Optional[Tool]:
        return self.tools.get(tool_name)

    def execute_tool(self, tool_name: str, **kwargs) -> Dict[str, Any]:
        tool = self.get_tool(tool_name)
        if tool:
            return tool.execute(**kwargs)
        return {"success": False, "error": f"Tool not found: {tool_name}"}

    def get_available_tools(self) -> Dict[str, str]:
        return {name: tool.description for name, tool in self.tools.items()}
