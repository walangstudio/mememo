"""Tests for mememo.core.project_config."""

from mememo.core.project_config import load_project_config, load_workspace_config


class TestLoadProjectConfig:
    def test_missing_file_returns_empty(self, tmp_path):
        result = load_project_config(str(tmp_path))
        assert result == {}

    def test_valid_yaml(self, tmp_path):
        (tmp_path / ".mememo").mkdir()
        (tmp_path / ".mememo" / "project.yaml").write_text(
            "project_id: myproject\n", encoding="utf-8"
        )
        result = load_project_config(str(tmp_path))
        assert result == {"project_id": "myproject"}

    def test_malformed_yaml_returns_empty(self, tmp_path):
        (tmp_path / ".mememo").mkdir()
        (tmp_path / ".mememo" / "project.yaml").write_text("key: [unclosed", encoding="utf-8")
        result = load_project_config(str(tmp_path))
        assert result == {}

    def test_scalar_yaml_returns_empty(self, tmp_path):
        (tmp_path / ".mememo").mkdir()
        (tmp_path / ".mememo" / "project.yaml").write_text("just a string\n", encoding="utf-8")
        result = load_project_config(str(tmp_path))
        assert result == {}

    def test_empty_file_returns_empty(self, tmp_path):
        (tmp_path / ".mememo").mkdir()
        (tmp_path / ".mememo" / "project.yaml").write_text("", encoding="utf-8")
        result = load_project_config(str(tmp_path))
        assert result == {}

    def test_accepts_path_object(self, tmp_path):
        result = load_project_config(tmp_path)
        assert result == {}


class TestLoadWorkspaceConfig:
    def test_missing_file_returns_empty(self, tmp_path):
        result = load_workspace_config(str(tmp_path))
        assert result == {}

    def test_valid_yaml(self, tmp_path):
        (tmp_path / ".mememo").mkdir()
        (tmp_path / ".mememo" / "workspace.yaml").write_text(
            "projects:\n  - /path/a\n  - /path/b\n", encoding="utf-8"
        )
        result = load_workspace_config(str(tmp_path))
        assert result == {"projects": ["/path/a", "/path/b"]}

    def test_malformed_returns_empty(self, tmp_path):
        (tmp_path / ".mememo").mkdir()
        (tmp_path / ".mememo" / "workspace.yaml").write_text(": bad: yaml: [", encoding="utf-8")
        result = load_workspace_config(str(tmp_path))
        assert result == {}

    def test_accepts_path_object(self, tmp_path):
        result = load_workspace_config(tmp_path)
        assert result == {}
