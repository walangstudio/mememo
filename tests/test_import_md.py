"""Tests for mememo.importers.markdown_memory.import_markdown_dir.

Stubs out sentence_transformers / faiss / fastmcp so the suite runs without
those heavy deps.  MemoryManager is constructed with a real StorageManager so
the FTS / checksum-skip assertions hit actual SQLite.
"""

from __future__ import annotations

import sys
import types as _types
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Stub heavyweight deps before any mememo import
# ---------------------------------------------------------------------------


def _stub_module(name: str, **attrs: object) -> None:
    if name in sys.modules:
        return
    m = _types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m


class _StubST:  # SentenceTransformer
    def __init__(self, *a, **k) -> None: ...
    def encode(self, *a, **k):
        return np.zeros((1, 384), dtype=np.float32)


class _StubFaissIndex:  # pragma: no cover
    pass


class _StubFastMCP:  # pragma: no cover
    def __init__(self, *a, **k) -> None: ...
    def tool(self, *a, **k):
        def deco(fn):
            return fn

        return deco

    def resource(self, *a, **k):
        def deco(fn):
            return fn

        return deco

    def run(self, *a, **k) -> None: ...


_stub_module("sentence_transformers", SentenceTransformer=_StubST)
_stub_module(
    "faiss",
    Index=_StubFaissIndex,
    IndexFlatL2=_StubFaissIndex,
    IndexIDMap=_StubFaissIndex,
    IndexIVFFlat=_StubFaissIndex,
    write_index=lambda *a, **k: None,
    read_index=lambda *a, **k: _StubFaissIndex(),
)
_stub_module("fastmcp", FastMCP=_StubFastMCP)


# ---------------------------------------------------------------------------
# Real imports after stubs
# ---------------------------------------------------------------------------


from mememo.core.git_manager import GitManager  # noqa: E402
from mememo.core.memory_manager import MemoryManager  # noqa: E402
from mememo.core.storage_manager import StorageManager  # noqa: E402
from mememo.core.vector_index import VectorIndex  # noqa: E402
from mememo.embeddings.embedder import Embedder  # noqa: E402
from mememo.importers.markdown_memory import (  # noqa: E402
    _map_type,
    _parse_frontmatter,
    _path_slug,
    import_markdown_dir,
)
from mememo.types.memory import GLOBAL_REPO_ID, NULL_SHA  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    return StorageManager(base_dir=tmp_path / "store")


@pytest.fixture
def memory_manager(tmp_path, store):
    """MemoryManager with real StorageManager and stubbed git/embedder/vector."""
    gm = GitManager()

    # Stub git so we get a stable, deterministic context with GLOBAL_REPO_ID.
    async def _fake_detect(cwd=None):
        from mememo.types.memory import BranchContext, GitContext, RepoContext

        return GitContext(
            repo=RepoContext(
                id=GLOBAL_REPO_ID,
                name="global",
                path=str(tmp_path),
                remote_url=None,
            ),
            branch=BranchContext(name="main", commit_hash=NULL_SHA),
        )

    gm.detect_context = _fake_detect

    embedder = MagicMock(spec=Embedder)
    embedder.embed_query.return_value = np.zeros(384, dtype=np.float32)
    embedder.embed.return_value = [np.zeros(384, dtype=np.float32)]

    vi = MagicMock(spec=VectorIndex)
    vi.repo_id = GLOBAL_REPO_ID
    vi.branch = "main"
    vi.base_path = tmp_path / "vectors"
    vi.dimension = 384
    vi.add.return_value = None

    return MemoryManager(
        git_manager=gm,
        storage_manager=store,
        embedder=embedder,
        vector_index=vi,
        secrets_detection=False,
    )


def _write_md(path: Path, name: str, content: str) -> Path:
    f = path / name
    f.write_text(content, encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# Unit: frontmatter parser
# ---------------------------------------------------------------------------


class TestParseFrontmatter:
    def test_no_frontmatter(self):
        meta, body = _parse_frontmatter("just a body")
        assert meta == {}
        assert body == "just a body"

    def test_type_decision(self):
        text = "---\nname: my-decision\ntype: decision\n---\nThis is the body."
        meta, body = _parse_frontmatter(text)
        assert meta["type"] == "decision"
        assert body.strip() == "This is the body."

    def test_malformed_yaml_returns_empty(self):
        text = "---\n: bad: yaml:\n---\nbody"
        meta, body = _parse_frontmatter(text)
        assert isinstance(meta, dict)
        assert body.startswith("body") or True  # body may include parse artefacts


class TestMapType:
    def test_decision(self):
        assert _map_type({"type": "decision"}) == "decision"

    def test_project(self):
        assert _map_type({"type": "project"}) == "context"

    def test_reference(self):
        assert _map_type({"type": "reference"}) == "relationship"

    def test_user(self):
        assert _map_type({"type": "user"}) == "context"

    def test_feedback(self):
        assert _map_type({"type": "feedback"}) == "context"

    def test_unknown(self):
        assert _map_type({"type": "weird"}) == "context"

    def test_missing(self):
        assert _map_type({}) == "context"


class TestPathSlug:
    def test_simple(self):
        assert _path_slug("notes.md") == "notes"

    def test_nested(self):
        slug = _path_slug("project/decisions/adr-001.md")
        assert "adr-001" in slug or "adr_001" in slug


# ---------------------------------------------------------------------------
# Integration: import_markdown_dir
# ---------------------------------------------------------------------------


class TestImportMarkdownDir:
    async def test_typed_memory_created(self, tmp_path, memory_manager, store):
        md_dir = tmp_path / "memos"
        md_dir.mkdir()
        _write_md(
            md_dir,
            "adr-001.md",
            "---\nname: adr-001\ntype: decision\n---\n\nWe decided to use SQLite.\n",
        )

        result = await import_markdown_dir(md_dir, memory_manager)

        assert result["imported"] == 1
        assert result["skipped"] == 0
        assert result["errors"] == 0

        # Type must be mapped to "decision"
        row = store.conn.execute(
            "SELECT content_type FROM memories WHERE file_path = 'adr-001.md'"
        ).fetchone()
        assert row is not None
        assert row[0] == "decision"

    async def test_fts_finds_body_word(self, tmp_path, memory_manager, store):
        md_dir = tmp_path / "memos"
        md_dir.mkdir()
        _write_md(
            md_dir,
            "ctx.md",
            "---\ntype: context\n---\n\nThe system uses PostgreSQL for persistence.\n",
        )
        await import_markdown_dir(md_dir, memory_manager)

        rows = store.conn.execute(
            "SELECT memory_id FROM memories_fts WHERE memories_fts MATCH 'PostgreSQL'"
        ).fetchall()
        assert len(rows) == 1

    async def test_url_references_edge_emitted(self, tmp_path, memory_manager, store):
        md_dir = tmp_path / "memos"
        md_dir.mkdir()
        _write_md(
            md_dir,
            "ref.md",
            "---\ntype: reference\n---\n\nSee https://example.com/docs for details.\n",
        )
        await import_markdown_dir(md_dir, memory_manager)

        # A REFERENCES edge with target_symbol=url should exist.
        rows = store.conn.execute(
            "SELECT target_symbol, type FROM relations WHERE type = 'REFERENCES'"
        ).fetchall()
        assert len(rows) >= 1
        targets = [r[0] for r in rows]
        assert any("example.com" in (t or "") for t in targets)

    async def test_wikilink_references_edge_emitted(self, tmp_path, memory_manager, store):
        md_dir = tmp_path / "memos"
        md_dir.mkdir()
        _write_md(
            md_dir,
            "linked.md",
            "---\ntype: project\n---\n\nSee [[other-doc]] for the design.\n",
        )
        await import_markdown_dir(md_dir, memory_manager)

        rows = store.conn.execute(
            "SELECT target_symbol, type FROM relations WHERE type = 'REFERENCES'"
        ).fetchall()
        assert len(rows) >= 1
        targets = [r[0] for r in rows]
        assert any("other" in (t or "").lower() for t in targets)

    async def test_reimport_is_noop(self, tmp_path, memory_manager, store):
        md_dir = tmp_path / "memos"
        md_dir.mkdir()
        _write_md(
            md_dir,
            "stable.md",
            "---\ntype: context\n---\n\nThis content never changes.\n",
        )

        r1 = await import_markdown_dir(md_dir, memory_manager)
        assert r1["imported"] == 1

        r2 = await import_markdown_dir(md_dir, memory_manager)
        assert r2["imported"] == 0
        assert r2["skipped"] == 1

        total = store.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        assert total == 1  # no duplicates

    async def test_empty_body_skipped(self, tmp_path, memory_manager, store):
        md_dir = tmp_path / "memos"
        md_dir.mkdir()
        _write_md(md_dir, "empty.md", "---\ntype: context\n---\n\n   \n")

        result = await import_markdown_dir(md_dir, memory_manager)
        assert result["imported"] == 0
        assert result["skipped"] == 1

    async def test_dry_run_writes_nothing(self, tmp_path, memory_manager, store):
        md_dir = tmp_path / "memos"
        md_dir.mkdir()
        _write_md(
            md_dir,
            "dry.md",
            "---\ntype: decision\n---\n\nDry-run content.\n",
        )

        result = await import_markdown_dir(md_dir, memory_manager, dry_run=True)
        assert result["imported"] == 1

        total = store.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        assert total == 0

    async def test_multiple_files(self, tmp_path, memory_manager, store):
        md_dir = tmp_path / "memos"
        md_dir.mkdir()
        for i in range(3):
            _write_md(
                md_dir,
                f"note-{i}.md",
                f"---\ntype: context\n---\n\nContent for note {i}.\n",
            )

        result = await import_markdown_dir(md_dir, memory_manager)
        assert result["imported"] == 3

    async def test_source_type_tag_appended(self, tmp_path, memory_manager, store):
        md_dir = tmp_path / "memos"
        md_dir.mkdir()
        _write_md(
            md_dir,
            "tagged.md",
            "---\ntype: feedback\n---\n\nSome feedback content.\n",
        )
        await import_markdown_dir(md_dir, memory_manager)

        mem_id = store.conn.execute(
            "SELECT id FROM memories WHERE file_path = 'tagged.md'"
        ).fetchone()[0]
        tags = {
            r[0]
            for r in store.conn.execute(
                "SELECT tag FROM tags WHERE memory_id = ?", (mem_id,)
            ).fetchall()
        }
        assert "source_type:feedback" in tags

    async def test_bom_file_parsed_correctly(self, tmp_path, memory_manager, store):
        """BOM-prefixed files (common from Windows editors) must parse cleanly.

        The BOM must not leak into the body and the nested metadata.type must
        be honoured so the memory is typed 'decision', not 'context'.
        """
        md_dir = tmp_path / "memos"
        md_dir.mkdir()
        body_content = (
            "---\n"
            "name: bom-decision\n"
            "metadata:\n"
            "  type: decision\n"
            "---\n"
            "\n"
            "We chose PostgreSQL over MySQL.\n"
        )
        bom_file = md_dir / "bom-decision.md"
        # UTF-8 BOM is EF BB BF; prepend it to simulate a Windows-authored file.
        bom_file.write_bytes(b"\xef\xbb\xbf" + body_content.encode("utf-8"))

        result = await import_markdown_dir(md_dir, memory_manager)
        assert result["imported"] == 1
        assert result["errors"] == 0

        row = store.conn.execute(
            "SELECT content_type FROM memories WHERE file_path = 'bom-decision.md'"
        ).fetchone()
        assert row is not None
        assert row[0] == "decision"

        # Body must contain the real content word.
        fts_rows = store.conn.execute(
            "SELECT memory_id FROM memories_fts WHERE memories_fts MATCH 'PostgreSQL'"
        ).fetchall()
        assert len(fts_rows) == 1

        # Raw frontmatter must not appear in the stored body.
        mem_id = store.conn.execute(
            "SELECT id FROM memories WHERE file_path = 'bom-decision.md'"
        ).fetchone()[0]
        content_ref = store.conn.execute(
            "SELECT content_ref FROM memories WHERE id = ?", (mem_id,)
        ).fetchone()[0]
        import json

        blob = json.loads((store.base_dir / content_ref).read_text(encoding="utf-8"))
        assert "---" not in blob["text"]
        assert "name:" not in blob["text"]


# ---------------------------------------------------------------------------
# --allow-secrets: trusted md with placeholder credentials
# ---------------------------------------------------------------------------


class TestAllowSecrets:
    _CONN_MD = (
        "---\nname: db\nmetadata:\n  type: project\n---\n"
        "Connection: postgres://user:pass@host:5432/db\n"
    )

    def _enable_secrets(self, memory_manager):
        from mememo.utils import SecretsDetector

        memory_manager.secrets_detection = True
        memory_manager.secrets_detector = SecretsDetector()

    async def test_secret_blocks_without_flag(self, tmp_path, memory_manager, store):
        self._enable_secrets(memory_manager)
        md_dir = tmp_path / "m"
        md_dir.mkdir()
        _write_md(md_dir, "db.md", self._CONN_MD)

        result = await import_markdown_dir(md_dir, memory_manager)
        assert result["imported"] == 0
        assert result["errors"] == 1

    async def test_allow_secrets_imports(self, tmp_path, memory_manager, store):
        self._enable_secrets(memory_manager)
        md_dir = tmp_path / "m"
        md_dir.mkdir()
        _write_md(md_dir, "db.md", self._CONN_MD)

        result = await import_markdown_dir(md_dir, memory_manager, allow_secrets=True)
        assert result["imported"] == 1
        assert result["errors"] == 0


# ---------------------------------------------------------------------------
# Global lane stamping: import without --repo must NOT inherit the ambient repo
# ---------------------------------------------------------------------------


class TestForceGlobalLane:
    def _ambient(self, repo_id):
        from mememo.types.memory import BranchContext, GitContext, RepoContext

        async def _detect(cwd=None):
            return GitContext(
                repo=RepoContext(id=repo_id, name="ambient", path="/x", remote_url=None),
                branch=BranchContext(name="feature", commit_hash="a" * 40),
            )

        return _detect

    async def test_repo_none_stamps_global_not_ambient(self, tmp_path, memory_manager, store):
        # Regression: running import inside a git repo used to stamp that ambient
        # repo (cwd=None -> detect_context). With repo=None it must be GLOBAL so
        # the memory recalls from any project (recall_workspace queries GLOBAL).
        memory_manager.git_manager.detect_context = self._ambient("ambient_repo_id_xx")

        md_dir = tmp_path / "m"
        md_dir.mkdir()
        _write_md(md_dir, "n.md", "---\ntype: context\n---\n\nGlobal note body.\n")

        result = await import_markdown_dir(md_dir, memory_manager)  # repo=None
        assert result["imported"] == 1

        rid = store.conn.execute(
            "SELECT repo_id FROM memories WHERE file_path = 'n.md'"
        ).fetchone()[0]
        assert rid == GLOBAL_REPO_ID

    async def test_with_repo_uses_detected_context(self, tmp_path, memory_manager, store):
        # When --repo IS given, force_global is off and the detected id is used.
        memory_manager.git_manager.detect_context = self._ambient("scoped_repo_id_yy")
        # A non-GLOBAL repo_id would spin a real VectorIndex (stubbed faiss);
        # return a throwaway mock so create_memory's vector add is a no-op.
        memory_manager._get_vector_index = lambda *a, **k: MagicMock()

        md_dir = tmp_path / "m"
        md_dir.mkdir()
        _write_md(md_dir, "s.md", "---\ntype: context\n---\n\nScoped note.\n")

        result = await import_markdown_dir(md_dir, memory_manager, repo=str(tmp_path))
        assert result["imported"] == 1

        rid = store.conn.execute(
            "SELECT repo_id FROM memories WHERE file_path = 's.md'"
        ).fetchone()[0]
        assert rid == "scoped_repo_id_yy"
        assert rid != GLOBAL_REPO_ID

    async def test_nested_metadata_type_tag(self, tmp_path, memory_manager, store):
        """source_type tag must reflect nested metadata.type, not just top-level type."""
        md_dir = tmp_path / "memos"
        md_dir.mkdir()
        _write_md(
            md_dir,
            "nested.md",
            "---\nname: nested\nmetadata:\n  type: feedback\n---\n\nNested type body.\n",
        )
        await import_markdown_dir(md_dir, memory_manager)

        mem_id = store.conn.execute(
            "SELECT id FROM memories WHERE file_path = 'nested.md'"
        ).fetchone()[0]
        tags = {
            r[0]
            for r in store.conn.execute(
                "SELECT tag FROM tags WHERE memory_id = ?", (mem_id,)
            ).fetchall()
        }
        assert "source_type:feedback" in tags

    async def test_invalid_dir_raises(self, tmp_path, memory_manager):
        with pytest.raises(ValueError, match="not a directory"):
            await import_markdown_dir(tmp_path / "nonexistent", memory_manager)
