import os
import sublime
from typing import Optional


class PromptManager:
    """Simple single-prompt system."""

    def __init__(self):
        self.prompts_dir = os.path.dirname(__file__)

    def _read_file(self, filename: str) -> str:
        file_path = os.path.join(self.prompts_dir, filename)
        try:
            if os.path.isfile(file_path):
                with open(file_path, "r", encoding="utf-8") as f:
                    return f.read().strip()

            package_name = (__package__ or "Limitcode").split(".", 1)[0]
            return sublime.load_resource(
                f"Packages/{package_name}/prompts/{filename}"
            ).strip()
        except Exception:
            return ""

    def get_system_prompt(
        self,
        os_name: str,
        shell_name: str,
        project_name: str,
        directory: str,
        model_name: Optional[str] = None,
        custom_instructions: str = "",
        open_files_paths: str = "",
    ) -> str:
        base = self._read_file("base.txt")
        modules = [base]
        if custom_instructions:
            modules.append(f"## Additional Instructions\n{custom_instructions}")

        full_prompt = "\n\n".join(modules)

        try:
            full_prompt = full_prompt.format(
                os_name=os_name,
                shell_name=shell_name,
                project_name=project_name,
                directory=directory,
                open_files_paths=open_files_paths or "None",
            )
        except (KeyError, ValueError):
            pass

        return full_prompt
