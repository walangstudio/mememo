"""
Project and workspace config loaders for mememo.

Reads .mememo/project.yaml (per-repo) and .mememo/workspace.yaml (workspace).
Both are optional; missing or malformed files return empty dicts with a warning.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_project_config(repo_path: str | Path) -> dict:
    """Read .mememo/project.yaml from the given repo root.

    Keys of interest: project_id (optional str).
    Returns {} on missing file, parse error, or any other failure.
    """
    path = Path(repo_path) / ".mememo" / "project.yaml"
    if not path.exists():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Failed to load project config from %s: %s", path, exc)
        return {}


def load_workspace_config(directory: str | Path) -> dict:
    """Read .mememo/workspace.yaml from the given directory.

    Keys of interest: projects (optional list of paths).
    Returns {} on missing file, parse error, or any other failure.
    """
    path = Path(directory) / ".mememo" / "workspace.yaml"
    if not path.exists():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Failed to load workspace config from %s: %s", path, exc)
        return {}
