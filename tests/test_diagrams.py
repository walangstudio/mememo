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


def test_class_diagram_renders_attributes_with_base_dir(store: StorageManager) -> None:
    import json

    # Derived's content_ref is "r2" (see _seed). Give it stored attributes.
    blob = store.base_dir / "r2"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_text(
        json.dumps({"text": "...", "attributes": ["owner: str", "balance: int"]}),
        encoding="utf-8",
    )
    result = class_diagram(store.conn, REPO, BRANCH, base_dir=store.base_dir)
    assert "+str owner" in result  # type rendered before the name (UML order)
    assert "+int balance" in result
    assert "+foo()" in result  # methods still listed alongside fields


def test_class_diagram_field_type_rendering(store: StorageManager) -> None:
    import json

    from mememo.diagrams import _attr_member

    # Simple types render; generics map [] / <> to Mermaid ~ … ~.
    assert _attr_member("balance: int", "balance") == "    +int balance"
    assert _attr_member("items: List[str]", "items") == "    +List~str~ items"
    assert _attr_member("m: dict[str, int]", "m") == "    +dict~str, int~ m"
    # No type -> name only.
    assert _attr_member("owner", "owner") == "    +owner"
    # Exotic types (unions, nested generics, callables) fall back to name only
    # so they can never break the Mermaid parse.
    assert _attr_member("x: Foo | None", "x") == "    +x"
    assert _attr_member("f: Callable[[int], str]", "f") == "    +f"
    # End-to-end: the rendered diagram for a generic field stays parse-safe.
    blob = store.base_dir / "r2"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_text(
        json.dumps({"text": "...", "attributes": ["tags: list[str]"]}), encoding="utf-8"
    )
    result = class_diagram(store.conn, REPO, BRANCH, base_dir=store.base_dir)
    assert "+list~str~ tags" in result


def test_class_diagram_no_attributes_without_base_dir(store: StorageManager) -> None:
    # Backward compatible: no base_dir => methods only, no blob reads.
    import json

    blob = store.base_dir / "r2"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_text(json.dumps({"text": "...", "attributes": ["owner: str"]}), encoding="utf-8")
    result = class_diagram(store.conn, REPO, BRANCH)
    assert "+owner" not in result
    assert "+foo()" in result


def test_is_empty_diagram() -> None:
    from mememo.diagrams import is_empty_diagram

    assert is_empty_diagram("classDiagram\n%% no data") is True
    assert is_empty_diagram("flowchart LR") is True  # header only
    assert is_empty_diagram("") is True
    assert is_empty_diagram("classDiagram\nclass A") is False
    assert is_empty_diagram("flowchart LR\n  A --> B") is False
    # A real diagram with a trailing "%% truncated" note must NOT read as empty.
    assert is_empty_diagram("flowchart LR\n  A --> B\n%% truncated") is False


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


def test_diagram_route_llm_type_passthrough(web_client: TestClient) -> None:
    # LLM types are now valid. With no provider configured (CI), the route
    # returns a grounded passthrough prompt instead of a rendered diagram.
    r = web_client.get(
        "/diagram",
        params={"type": "sequence", "scope": "foo", "repo_id": REPO, "branch": BRANCH},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "sequence"
    assert body["success"] is True
    # Either a provider rendered it, or we got a prompt to paste into a chat model.
    assert body["passthrough"] is False or body["passthrough_prompt"]
    if body["passthrough"]:
        assert "sequenceDiagram" in body["passthrough_prompt"]


def test_diagram_route_unknown_type(web_client: TestClient) -> None:
    r = web_client.get("/diagram", params={"type": "bogus"})
    assert r.status_code == 400


def test_diagram_route_empty_flag_for_no_data(web_client: TestClient) -> None:
    # A class diagram for a repo with no classes returns success+empty, not a
    # "%% no data" diagram the browser would fail to parse.
    r = web_client.get("/diagram", params={"type": "class", "repo_id": "nonexistent-repo"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["empty"] is True


async def test_tool_empty_diagram_returns_error(store: StorageManager) -> None:
    # MCP tool: an empty deterministic diagram is surfaced as success=False so the
    # chat renderer never tries to draw "%% no data".
    from mememo.tools.generate_diagram import GenerateDiagramParams, generate_diagram

    resp = await generate_diagram(
        GenerateDiagramParams(type="class", repo_id="nonexistent-repo", branch=BRANCH),
        _FakeMM(store),
        None,
    )
    assert resp.success is False
    assert "no class data" in resp.message.lower()


async def test_tool_empty_call_graph_isolated_node(store: StorageManager) -> None:
    # 'main' (a-main) has an IMPORTS edge but no outgoing CALLS -> call_graph is
    # "%% no data" -> the tool must report no-data, not return an empty diagram.
    from mememo.tools.generate_diagram import GenerateDiagramParams, generate_diagram

    resp = await generate_diagram(
        GenerateDiagramParams(type="call", scope="main", repo_id=REPO, branch=BRANCH),
        _FakeMM(store),
        None,
    )
    assert resp.success is False
    assert "no call data" in resp.message.lower()


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


# ---------- review-fix regressions ------------------------------------------


def test_esc_escapes_double_quote() -> None:
    from mememo.diagrams import _esc

    assert _esc('a"b') == "a#quot;b"
    assert "\n" not in _esc("a\nb")


def test_call_graph_renders_unresolved_external_call(store: StorageManager) -> None:
    # A CALLS edge to an unresolved symbol (target_memory_id NULL) must render as
    # a leaf labeled by target_symbol, not collapse to "%% no data".
    store.conn.execute(
        "INSERT INTO relations (id, repo_id, branch, source_memory_id, target_memory_id, "
        "target_symbol, type, confidence, created_at_sha, stale) "
        "VALUES ('rel-ext-call', ?, ?, 'a-main', NULL, 'external_lib_fn', 'CALLS', 'INFERRED', ?, 0)",
        (REPO, BRANCH, SHA),
    )
    store.conn.commit()
    result = call_graph(store.conn, "a-main", depth=2)
    assert "%% no data" not in result
    assert "external_lib_fn" in result


def test_call_graph_max_nodes_halts_bfs(store: StorageManager) -> None:
    # Chain a-main -> b-bar -> derived-foo via CALLS; max_nodes=1 must not expand
    # past the cap (BFS halts, not just the inner batch).
    store.conn.execute(
        "INSERT INTO relations (id, repo_id, branch, source_memory_id, target_memory_id, "
        "target_symbol, type, confidence, created_at_sha, stale) "
        "VALUES ('rel-chain', ?, ?, 'b-bar', 'derived-foo', NULL, 'CALLS', 'EXTRACTED', ?, 0)",
        (REPO, BRANCH, SHA),
    )
    store.conn.execute(
        "INSERT INTO relations (id, repo_id, branch, source_memory_id, target_memory_id, "
        "target_symbol, type, confidence, created_at_sha, stale) "
        "VALUES ('rel-am-bb', ?, ?, 'a-main', 'b-bar', NULL, 'CALLS', 'EXTRACTED', ?, 0)",
        (REPO, BRANCH, SHA),
    )
    store.conn.commit()
    # max_nodes=2: root + b-bar fit; expanding b-bar -> derived-foo exceeds -> halt.
    result = call_graph(store.conn, "a-main", depth=3, max_nodes=2)
    assert "%% truncated" in result
    assert "derived" not in result  # BFS halted before expanding into derived-foo


def test_diagram_route_call_by_kebab_function_name(store: StorageManager) -> None:
    # Regression: scope="get-user" (kebab) was misread as a UUID (any "-") and
    # the function lookup was skipped -> "not found". It must resolve.
    store.conn.execute(
        "INSERT INTO memories (id, repo_id, branch_name, content_type, file_path, "
        "function_name, class_name, language, chunk_type, checksum, content_ref, "
        "token_count, created_at, updated_at, stale) "
        "VALUES ('gu', ?, ?, 'code_snippet', 'u.py', 'get-user', NULL, 'clojure', "
        "'function', 'cg', 'rg', 5, 1, 1, 0)",
        (REPO, BRANCH),
    )
    store.conn.execute(
        "INSERT INTO relations (id, repo_id, branch, source_memory_id, target_memory_id, "
        "target_symbol, type, confidence, created_at_sha, stale) "
        "VALUES ('rel-gu', ?, ?, 'gu', 'b-bar', NULL, 'CALLS', 'EXTRACTED', ?, 0)",
        (REPO, BRANCH, SHA),
    )
    store.conn.commit()
    app = create_app(storage_getter=lambda: store)
    client = TestClient(app)
    r = client.get(
        "/diagram", params={"type": "call", "scope": "get-user", "repo_id": REPO, "branch": BRANCH}
    )
    assert r.status_code == 200
    assert "flowchart" in r.json()["mermaid"]
    assert "%% no data" not in r.json()["mermaid"]


# ---------- Phase 2: LLM / passthrough --------------------------------------


def _write_blob(store: StorageManager, content_ref: str, text: str) -> None:
    import json

    p = store.base_dir / content_ref
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"text": text}), encoding="utf-8")


class _PassthroughLLM:
    def is_passthrough(self) -> bool:
        return True

    async def complete(self, system, user):
        return None


class _CompletingLLM:
    def __init__(self, out: str) -> None:
        self._out = out
        self.system = None
        self.user = None

    def is_passthrough(self) -> bool:
        return False

    async def complete(self, system, user):
        self.system, self.user = system, user
        return self._out


class _FailingLLM:
    def is_passthrough(self) -> bool:
        return False

    async def complete(self, system, user):
        return None


async def test_sequence_passthrough_returns_grounded_prompt(store: StorageManager) -> None:
    from mememo.tools.generate_diagram import GenerateDiagramParams, generate_diagram

    resp = await generate_diagram(
        GenerateDiagramParams(type="sequence", scope="foo", repo_id=REPO, branch=BRANCH),
        _FakeMM(store),
        _PassthroughLLM(),
    )
    assert resp.success
    assert resp.passthrough is True
    assert resp.mermaid == ""
    # The prompt must carry the diagram instruction AND the deterministic subgraph.
    assert "sequenceDiagram" in resp.passthrough_prompt
    assert "Deterministic subgraph" in resp.passthrough_prompt
    assert "flowchart" in resp.passthrough_prompt  # call_graph skeleton


async def test_phase2_passthrough_includes_source(store: StorageManager) -> None:
    from mememo.tools.generate_diagram import GenerateDiagramParams, generate_diagram

    _write_blob(store, "r3", "def foo(self):\n    return self.bar()  # SENTINEL_SRC")
    resp = await generate_diagram(
        GenerateDiagramParams(type="sequence", scope="foo", repo_id=REPO, branch=BRANCH),
        _FakeMM(store),
        _PassthroughLLM(),
    )
    assert resp.success and resp.passthrough
    assert "SENTINEL_SRC" in resp.passthrough_prompt


async def test_sequence_pulls_sibling_methods(store: StorageManager) -> None:
    # self.method() calls often aren't CALLS edges, so a sequence diagram must
    # still see the entry point's sibling methods to trace into them.
    from mememo.tools.generate_diagram import GenerateDiagramParams, generate_diagram

    store.conn.execute(
        "INSERT INTO memories (id, repo_id, branch_name, content_type, file_path, "
        "function_name, class_name, language, chunk_type, checksum, content_ref, "
        "token_count, created_at, updated_at, stale) "
        "VALUES ('derived-bar2', ?, ?, 'code_snippet', 'derived.py', 'bar2', 'Derived', "
        "'python', 'method', 'cb', 'rbar2', 5, 1, 1, 0)",
        (REPO, BRANCH),
    )
    store.conn.commit()
    _write_blob(store, "rbar2", "def bar2(self):\n    return 'SIBLING_BODY'")

    resp = await generate_diagram(
        GenerateDiagramParams(type="sequence", scope="foo", repo_id=REPO, branch=BRANCH),
        _FakeMM(store),
        _PassthroughLLM(),
    )
    assert resp.success and resp.passthrough
    assert "SIBLING_BODY" in resp.passthrough_prompt


async def test_phase2_llm_path_strips_fences(store: StorageManager) -> None:
    from mememo.tools.generate_diagram import GenerateDiagramParams, generate_diagram

    llm = _CompletingLLM("```mermaid\nsequenceDiagram\n  A->>B: call\n```")
    resp = await generate_diagram(
        GenerateDiagramParams(type="sequence", scope="foo", repo_id=REPO, branch=BRANCH),
        _FakeMM(store),
        llm,
    )
    assert resp.success
    assert resp.passthrough is False
    assert resp.mermaid == "sequenceDiagram\n  A->>B: call"
    assert llm.user and "foo" in llm.user  # grounding reached the model


async def test_phase2_llm_empty_output_falls_back_to_passthrough(store: StorageManager) -> None:
    # Model returns only fences/whitespace -> stripped mermaid is "" -> must fall
    # back to passthrough, not return an empty diagram that breaks mermaid.run().
    from mememo.tools.generate_diagram import GenerateDiagramParams, generate_diagram

    resp = await generate_diagram(
        GenerateDiagramParams(type="sequence", scope="foo", repo_id=REPO, branch=BRANCH),
        _FakeMM(store),
        _CompletingLLM("```mermaid\n```"),
    )
    assert resp.success
    assert resp.passthrough is True
    assert resp.mermaid == ""
    assert resp.passthrough_prompt


async def test_phase2_llm_none_falls_back_to_passthrough(store: StorageManager) -> None:
    from mememo.tools.generate_diagram import GenerateDiagramParams, generate_diagram

    resp = await generate_diagram(
        GenerateDiagramParams(type="usecase", repo_id=REPO, branch=BRANCH),
        _FakeMM(store),
        _FailingLLM(),
    )
    assert resp.success
    assert resp.passthrough is True
    assert resp.passthrough_prompt


async def test_sequence_unresolved_scope_reports_no_data(store: StorageManager) -> None:
    from mememo.tools.generate_diagram import GenerateDiagramParams, generate_diagram

    resp = await generate_diagram(
        GenerateDiagramParams(type="sequence", scope="does_not_exist", repo_id=REPO, branch=BRANCH),
        _FakeMM(store),
        _PassthroughLLM(),
    )
    assert resp.success is False
    assert "index" in resp.message.lower()


async def test_unknown_diagram_type_rejected(store: StorageManager) -> None:
    from mememo.tools.generate_diagram import GenerateDiagramParams, generate_diagram

    resp = await generate_diagram(
        GenerateDiagramParams(type="bogus", repo_id=REPO, branch=BRANCH),
        _FakeMM(store),
        _PassthroughLLM(),
    )
    assert resp.success is False
    assert "sequence" in resp.message  # lists the valid types


def test_strip_fences_handles_plain_and_fenced() -> None:
    from mememo.tools.generate_diagram import _strip_fences

    assert _strip_fences("erDiagram\n  A ||--o{ B : has") == "erDiagram\n  A ||--o{ B : has"
    assert _strip_fences("```mermaid\nstateDiagram-v2\n  [*] --> Idle\n```") == (
        "stateDiagram-v2\n  [*] --> Idle"
    )


def test_strip_fences_truncates_trailing_prose() -> None:
    from mememo.tools.generate_diagram import _strip_fences

    out = _strip_fences(
        "```mermaid\nsequenceDiagram\n  A->>B: x\n```\nThis diagram shows the flow."
    )
    assert out == "sequenceDiagram\n  A->>B: x"
    assert "This diagram shows" not in out


async def test_phase2_no_data_for_empty_repo(store: StorageManager) -> None:
    # A "%% no data" skeleton must NOT count as grounding — the LLM must not be
    # prompted with an empty graph; the caller gets a clear error instead.
    from mememo.tools.generate_diagram import GenerateDiagramParams, generate_diagram

    resp = await generate_diagram(
        GenerateDiagramParams(type="usecase", repo_id="nonexistent-repo", branch=BRANCH),
        _FakeMM(store),
        _PassthroughLLM(),
    )
    assert resp.success is False
    assert "index" in resp.message.lower()


def test_scope_member_ids_escapes_like_wildcards(store: StorageManager) -> None:
    # scope="util_" must match the literal file 'util_x.py', not 'utility.py'.
    from mememo.tools.generate_diagram import _scope_member_ids

    store.conn.executemany(
        "INSERT INTO memories (id, repo_id, branch_name, content_type, file_path, "
        "function_name, class_name, language, chunk_type, checksum, content_ref, "
        "token_count, created_at, updated_at, stale) VALUES "
        "(?, ?, ?, 'code_snippet', ?, 'f', NULL, 'python', 'function', ?, ?, 5, 1, 1, 0)",
        [
            ("u1", REPO, BRANCH, "utility.py", "cu1", "ru1"),
            ("u2", REPO, BRANCH, "util_x.py", "cu2", "ru2"),
        ],
    )
    store.conn.commit()
    ids = _scope_member_ids(store.conn, REPO, BRANCH, "util_", only_classes=False)
    assert "u2" in ids
    assert "u1" not in ids
