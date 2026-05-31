"""Tests for mememo/diagrams.py — Phase 1 deterministic Mermaid generators."""

from __future__ import annotations

from pathlib import Path

import pytest

from mememo.core.storage_manager import StorageManager
from mememo.diagrams import call_graph, class_diagram, module_dependency

SHA = "a" * 40
REPO = "test-repo"
BRANCH = "main"


def _seed(storage: StorageManager) -> None:
    """Insert a minimal graph:

    Classes:
      Base (in base.py)
      Derived(Base) (in derived.py), with method foo()

    Relations:
      Derived EXTENDS Base
      foo() CALLS bar() (bar is an unresolved symbol)
      a.py IMPORTS b.py
    """
    storage.conn.executemany(
        "INSERT INTO memories (id, repo_id, branch_name, content_type, "
        "  file_path, function_name, class_name, language, chunk_type, "
        "  checksum, content_ref, token_count, created_at, updated_at, stale) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 0)",
        [
            # Base class row
            (
                "base-cls",
                REPO,
                BRANCH,
                "code_snippet",
                "base.py",
                None,
                "Base",
                "python",
                "class",
                "c1",
                "r1",
                5,
            ),
            # Derived class row
            (
                "derived-cls",
                REPO,
                BRANCH,
                "code_snippet",
                "derived.py",
                None,
                "Derived",
                "python",
                "class",
                "c2",
                "r2",
                5,
            ),
            # foo() method row — class_name = parent class 'Derived', function_name = 'foo'
            (
                "derived-foo",
                REPO,
                BRANCH,
                "code_snippet",
                "derived.py",
                "foo",
                "Derived",
                "python",
                "method",
                "c3",
                "r3",
                5,
            ),
            # bar() function in b.py
            (
                "b-bar",
                REPO,
                BRANCH,
                "code_snippet",
                "b.py",
                "bar",
                None,
                "python",
                "function",
                "c4",
                "r4",
                5,
            ),
            # top-level function in a.py
            (
                "a-main",
                REPO,
                BRANCH,
                "code_snippet",
                "a.py",
                "main",
                None,
                "python",
                "function",
                "c5",
                "r5",
                5,
            ),
        ],
    )
    storage.conn.executemany(
        "INSERT INTO relations (id, repo_id, branch, source_memory_id, "
        "  target_memory_id, target_symbol, type, confidence, created_at_sha, stale) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
        [
            # Derived EXTENDS Base
            ("rel-ext", REPO, BRANCH, "derived-cls", "base-cls", None, "EXTENDS", "EXTRACTED", SHA),
            # foo() CALLS bar() — resolved target
            ("rel-call", REPO, BRANCH, "derived-foo", "b-bar", None, "CALLS", "EXTRACTED", SHA),
            # a.py IMPORTS b.py
            ("rel-imp", REPO, BRANCH, "a-main", "b-bar", None, "IMPORTS", "EXTRACTED", SHA),
        ],
    )
    storage.conn.commit()


@pytest.fixture()
def store(tmp_path: Path) -> StorageManager:
    s = StorageManager(base_dir=tmp_path / "store")
    _seed(s)
    return s


# ---------- class_diagram ---------------------------------------------------


def test_class_diagram_header(store: StorageManager) -> None:
    result = class_diagram(store.conn, REPO, BRANCH)
    assert result.startswith("classDiagram")


def test_class_diagram_contains_both_classes(store: StorageManager) -> None:
    result = class_diagram(store.conn, REPO, BRANCH)
    assert "Base" in result
    assert "Derived" in result


def test_class_diagram_extends_edge(store: StorageManager) -> None:
    result = class_diagram(store.conn, REPO, BRANCH)
    # Base <|-- Derived (EXTENDS)
    assert "Base <|-- Derived" in result


def test_class_diagram_method_listed(store: StorageManager) -> None:
    result = class_diagram(store.conn, REPO, BRANCH)
    assert "foo()" in result


def test_class_diagram_scope_by_file(store: StorageManager) -> None:
    result = class_diagram(store.conn, REPO, BRANCH, scope="derived.py")
    assert "Derived" in result
    # Base should still appear as the extend target.
    assert "Base" in result


def test_class_diagram_scope_by_class(store: StorageManager) -> None:
    result = class_diagram(store.conn, REPO, BRANCH, scope="Derived")
    assert "Derived" in result


def test_class_diagram_empty_repo(store: StorageManager) -> None:
    result = class_diagram(store.conn, "nonexistent", BRANCH)
    assert "classDiagram" in result
    assert "%% no data" in result


# ---------- call_graph ------------------------------------------------------


def test_call_graph_header(store: StorageManager) -> None:
    result = call_graph(store.conn, "derived-foo", depth=2)
    assert "flowchart" in result


def test_call_graph_contains_arrow(store: StorageManager) -> None:
    result = call_graph(store.conn, "derived-foo", depth=2)
    assert "-->" in result


def test_call_graph_no_data_for_isolated_node(store: StorageManager) -> None:
    # base-cls has no CALLS outgoing edges
    result = call_graph(store.conn, "base-cls", depth=2)
    assert "flowchart" in result
    assert "%% no data" in result


def test_call_graph_max_nodes_note(store: StorageManager) -> None:
    # With max_nodes=1 and one edge, it should still succeed or truncate.
    result = call_graph(store.conn, "derived-foo", depth=2, max_nodes=1)
    assert "flowchart" in result


# ---------- module_dependency -----------------------------------------------


def test_module_dependency_header(store: StorageManager) -> None:
    result = module_dependency(store.conn, REPO, BRANCH)
    assert "flowchart" in result


def test_module_dependency_import_edge(store: StorageManager) -> None:
    result = module_dependency(store.conn, REPO, BRANCH)
    # a.py -> b.py import edge must appear as --> arrow
    assert "-->" in result
    assert "a_py" in result or "a.py" in result


def test_module_dependency_empty_repo(store: StorageManager) -> None:
    result = module_dependency(store.conn, "nonexistent", BRANCH)
    assert "flowchart" in result
    assert "%% no data" in result


# ---------- web route -------------------------------------------------------


pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from mememo.web.app import create_app  # noqa: E402


@pytest.fixture()
def web_client(store: StorageManager) -> TestClient:
    app = create_app(storage_getter=lambda: store)
    return TestClient(app)


def test_diagram_route_class(web_client: TestClient) -> None:
    r = web_client.get("/diagram", params={"type": "class", "repo_id": REPO, "branch": BRANCH})
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "class"
    assert "classDiagram" in body["mermaid"]
    assert isinstance(body["truncated"], bool)


def test_diagram_route_module(web_client: TestClient) -> None:
    r = web_client.get("/diagram", params={"type": "module", "repo_id": REPO, "branch": BRANCH})
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "module"
    assert "flowchart" in body["mermaid"]


def test_diagram_route_call(web_client: TestClient) -> None:
    r = web_client.get(
        "/diagram",
        params={"type": "call", "scope": "derived-foo", "repo_id": REPO, "branch": BRANCH},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "call"
    assert "flowchart" in body["mermaid"]


def test_diagram_route_call_by_function_name(web_client: TestClient) -> None:
    r = web_client.get(
        "/diagram",
        params={"type": "call", "scope": "foo", "repo_id": REPO, "branch": BRANCH},
    )
    assert r.status_code == 200
    assert "flowchart" in r.json()["mermaid"]


def test_diagram_route_invalid_type(web_client: TestClient) -> None:
    r = web_client.get("/diagram", params={"type": "erd"})
    assert r.status_code == 400


def test_diagram_route_unknown_type(web_client: TestClient) -> None:
    r = web_client.get("/diagram", params={"type": "bogus"})
    assert r.status_code == 400


def test_diagram_route_call_missing_scope(web_client: TestClient) -> None:
    r = web_client.get("/diagram", params={"type": "call"})
    assert r.status_code == 400


def test_scopes_route(web_client: TestClient) -> None:
    r = web_client.get("/scopes", params={"repo_id": REPO, "branch": BRANCH})
    assert r.status_code == 200
    body = r.json()
    assert "files" in body
    assert "classes" in body
    assert any("py" in f for f in body["files"])


def test_diagram_route_defaults_repo_when_omitted(web_client: TestClient) -> None:
    # Regression: with no repo_id/branch the route must default to the store's
    # busiest repo, not query "" -> "%% no data" (the single-repo web UI case).
    r = web_client.get("/diagram", params={"type": "class"})
    assert r.status_code == 200
    body = r.json()
    assert "Base <|-- Derived" in body["mermaid"]
    assert "%% no data" not in body["mermaid"]


# ---------- MCP tool: git-context resolution --------------------------------


class _FakeGit:
    async def detect_context(self, cwd=None):
        from mememo.types.memory import BranchContext, GitContext, RepoContext

        return GitContext(
            repo=RepoContext(id=REPO, name="r", path="/x", remote_url=None),
            branch=BranchContext(name=BRANCH, commit_hash="a" * 40),
        )


class _FakeMM:
    def __init__(self, store: StorageManager) -> None:
        self.storage_manager = store
        self.git_manager = _FakeGit()


async def test_tool_resolves_context_when_repo_id_omitted(store: StorageManager) -> None:
    # Regression: the tool used a nonexistent git_manager.get_context()/ctx.repo_id,
    # which raised, got swallowed, and left repo_id="" -> "%% no data". With repo_id
    # omitted it must resolve via detect_context and produce a real diagram.
    from mememo.tools.generate_diagram import GenerateDiagramParams, generate_diagram

    resp = await generate_diagram(GenerateDiagramParams(type="class"), _FakeMM(store), None)
    assert resp.success
    assert "Base <|-- Derived" in resp.mermaid
    assert "%% no data" not in resp.mermaid
