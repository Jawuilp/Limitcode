import importlib.util
import sys
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def load_limitcode_package():
    """Load this checkout as `Limitcode`, regardless of its directory name."""
    existing = sys.modules.get("Limitcode")
    if existing:
        existing_file = getattr(existing, "__file__", None)
        if existing_file and Path(existing_file).resolve().parent == PACKAGE_ROOT:
            return existing

        for module_name in list(sys.modules):
            if module_name == "Limitcode" or module_name.startswith("Limitcode."):
                del sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(
        "Limitcode",
        PACKAGE_ROOT / "__init__.py",
        submodule_search_locations=[str(PACKAGE_ROOT)],
    )
    if not spec or not spec.loader:
        raise ImportError(f"Cannot load Lite package from {PACKAGE_ROOT}")

    package = importlib.util.module_from_spec(spec)
    sys.modules["Limitcode"] = package
    spec.loader.exec_module(package)
    return package
