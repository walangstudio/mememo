"""v0.6 batch 4 — FastAPI app routes + T033/T034 wiring smoke."""

from __future__ import annotations

import sys
import types as _types
from datetime import datetime
from pathlib import Path


def _stub_module(name: str, **attrs: object) -> None:
    if name in sys.modules:
        return
    m = _types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m


class _Stub:  # pragma: no cover
    def __init__(self, *a, **k) -> None: ...

    def tool(self, *a, **k):
        def deco(fn):
            return fn

        return deco

    def resource(self, *a, **k):
        def deco(fn):
            return fn

        return deco


_stub_module("sentence_transformers", SentenceTransformer=_Stub)
_stub_module("faiss", Index=_Stub, IndexFlatL2=_Stub, IndexIDMap=_Stub, IndexIVFFlat=_Stub)
_stub_module("fastmcp", FastMCP=_Stub)


import pytest  # noqa: E402

# FastAPI is the actual subject under test here — skip the whole module
# when it isn't installed.
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from mememo.core.storage_manager import StorageManager  # noqa: E402
from mememo.types import BranchState, MemoryEvent, Relation  # noqa: E402
from mememo.web.app import create_app  # noqa: E402

SHA = "a" * 40
SHA_B = "b" * 40


def _seed(storage: StorageManager) -> None:
    # 3 memories on the same repo+branch; 2 relations (one in community 0).
    storage.conn.execute(
        "INSERT INTO memories (id, repo_id, repo_name, repo_path, branch_name, "
        "  content_type, file_path, function_name, class_name, language, "
        "  checksum, content_ref, token_count, created_at, updated_at, "
        "  stale, risk_grade, created_at_sha) "
        "VALUES "
        f"('m1','r','demo','/tmp/demo','main','code_snippet','a.py','f','C','python','k','u',1,1,1,0,NULL,'{SHA}'),"
        f"('m2','r','demo','/tmp/demo','main','code_snippet','b.py','g',NULL,'python','k2','u2',1,2,2,1,'WILL_BREAK','{SHA}'),"
        f"('m3','r','demo','/tmp/demo','main','code_snippet','c.py','h',NULL,'python','k3','u3',1,3,3,0,NULL,'{SHA}')"
    )
    storage.upsert_branch_state(BranchState(repo_id="r", branch="main", last_indexed_sha=SHA))
    storage.insert_relations(
        [
            Relation(
                id="rel1",
                repo_id="r",
                branch="main",
                source_memory_id="m1",
                target_memory_id="m2",
                type="CALLS",
                confidence="EXTRACTED",
                created_at_sha=SHA,
                community=0,
            ),
            Relation(
                id="rel2",
                repo_id="r",
                branch="main",
                source_memory_id="m2",
                target_memory_id="m3",
                type="USES",
                confidence="EXTRACTED",
                created_at_sha=SHA,
                community=0,
            ),
        ]
    )
    # Events: m1 at ts=BASE+100, m2 at BASE+200, m3 at BASE+300 (under SHA_B).
    # Use a safe modern epoch base — Windows localtime barfs on
    # datetime.fromtimestamp(<small int>) for pre-1970 local times.
    base = 1_700_000_000  # 2023-11-14 UTC
    for mid, offset, sha in [
        ("m1", 100, SHA),
        ("m2", 200, SHA),
        ("m3", 300, SHA_B),
    ]:
        storage.append_event(
            MemoryEvent(
                commit_sha=sha,
                memory_id=mid,
                op="CREATED",
                content_sha=f"k_{mid}",
                branch="main",
                ts=datetime.fromtimestamp(base + offset),
            )
        )


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    storage = StorageManager(base_dir=tmp_path / "store")
    _seed(storage)
    app = create_app(storage_getter=lambda: storage)
    return TestClient(app)


# ---------- T033 routes -----------------------------------------------------


def test_repos_lists_indexed_repos(client: TestClient) -> None:
    r = client.get("/repos")
    assert r.status_code == 200
    repos = r.json()
    assert any(repo["repo_id"] == "r" and repo["memories"] == 3 for repo in repos)


def test_memories_paginates(client: TestClient) -> None:
    r = client.get("/memories", params={"repo_id": "r", "limit": 2, "offset": 0})
    assert r.status_code == 200
    payload = r.json()
    assert payload["total"] == 3
    assert payload["limit"] == 2
    assert len(payload["items"]) == 2


def test_memories_branch_filter(client: TestClient) -> None:
    r = client.get("/memories", params={"repo_id": "r", "branch": "other"})
    assert r.status_code == 200
    assert r.json()["total"] == 0


def test_memories_as_of_sha_filters_server_side(client: TestClient) -> None:
    """as_of_sha must affect both total AND items so pagination math stays
    consistent — fixes the snapshot-vs-pagination bug from the quality audit.
    """
    r = client.get("/memories", params={"repo_id": "r", "as_of_sha": SHA[:8]})
    assert r.status_code == 200
    payload = r.json()
    assert payload["total"] == 2  # m1 + m2 alive under SHA_A
    assert {item["id"] for item in payload["items"]} == {"m1", "m2"}


def test_memories_as_of_sha_validates_hex(client: TestClient) -> None:
    r = client.get("/memories", params={"as_of_sha": "--evil"})
    assert r.status_code == 400


def test_memories_search_filters_by_q(client: TestClient) -> None:
    # q matches file_path/function_name/class_name substrings.
    r = client.get("/memories", params={"repo_id": "r", "q": "a.py"})
    assert r.status_code == 200
    payload = r.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == "m1"
    # Matches function_name too (m1.function_name == 'f', m2 == 'g', m3 == 'h').
    assert {row["id"] for row in client.get("/memories", params={"repo_id": "r", "q": "h"}).json()["items"]} == {
        "m3"
    }
    # No match -> empty.
    assert client.get("/memories", params={"repo_id": "r", "q": "zzz"}).json()["total"] == 0


def test_relations_returns_edges(client: TestClient) -> None:
    r = client.get("/relations", params={"repo_id": "r", "branch": "main"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert {row["id"] for row in items} == {"rel1", "rel2"}
    # P2: endpoints carry memory labels via the JOIN (rel1: m1 -> m2).
    rel1 = next(row for row in items if row["id"] == "rel1")
    assert rel1["source_file"] == "a.py"
    assert rel1["source_class"] == "C"
    assert rel1["source_fn"] == "f"
    assert rel1["target_file"] == "b.py"
    assert rel1["target_fn"] == "g"


def test_relations_community_filter(client: TestClient) -> None:
    r = client.get("/relations", params={"repo_id": "r", "community": 0})
    assert r.status_code == 200
    assert r.json()["count"] == 2
    r2 = client.get("/relations", params={"repo_id": "r", "community": 99})
    assert r2.json()["count"] == 0


def test_communities_aggregates_by_community(client: TestClient) -> None:
    r = client.get("/communities", params={"repo_id": "r"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == 0
    assert items[0]["edges"] == 2


def test_snapshot_filters_to_alive_memory_ids(client: TestClient) -> None:
    # At a SHA-A-prefix snapshot, m1 + m2 are alive (created under SHA_A);
    # m3 was created at a later ts under SHA_B and is excluded.
    r = client.get(f"/snapshots/{SHA[:8]}", params={"repo_id": "r", "branch": "main"})
    assert r.status_code == 200
    payload = r.json()
    assert payload["target_ts"] is not None
    assert set(payload["alive_memory_ids"]) == {"m1", "m2"}


def test_snapshot_rejects_non_hex_sha(client: TestClient) -> None:
    """Defence-in-depth — the route must refuse anything that isn't a SHA
    so callers can't sneak git options past the route layer."""
    r = client.get("/snapshots/--upload-pack=evil")
    assert r.status_code == 400


def test_snapshot_unknown_sha_returns_empty_alive_set(client: TestClient) -> None:
    r = client.get("/snapshots/deadbeef")
    assert r.status_code == 200
    payload = r.json()
    assert payload["alive_memory_ids"] == []
    assert payload["target_ts"] is None


# ---------- T033 host-binding safety ----------------------------------------


def test_run_refuses_non_localhost_bind() -> None:
    from mememo.web import app as web_app

    with pytest.raises(ValueError):
        web_app.run(host="0.0.0.0", port=5757)


# ---------- T034 frontend assets are present --------------------------------


def test_static_assets_present() -> None:
    static = Path(__file__).resolve().parents[1] / "mememo" / "web" / "static"
    assert (static / "index.html").is_file()
    assert (static / "app.js").is_file()
    assert (static / "style.css").is_file()


def test_root_serves_index_html(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "mememo" in r.text.lower()
    assert "d3" in r.text.lower()


def test_static_app_js_served(client: TestClient) -> None:
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert "/relations" in r.text
    assert "/snapshots/" in r.text
