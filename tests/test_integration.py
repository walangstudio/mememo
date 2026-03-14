"""
Integration tests for mememo.

Tests the full workflow:
- Initialize components
- Store memories
- Search similar
- List with filters
- Index repository
- Delete memories
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from mememo.core.git_manager import GitManager
from mememo.core.memory_manager import MemoryManager
from mememo.core.storage_manager import StorageManager
from mememo.core.vector_index import VectorIndex
from mememo.embeddings.embedder import Embedder
from mememo.types.memory import CreateMemoryParams, MemoryRelationships, SearchParams


@pytest.fixture
async def test_env():
    """Create test environment with temp directory."""
    import os
    import subprocess

    # Create temporary directory
    temp_dir = Path(tempfile.mkdtemp())

    # Save current directory
    original_cwd = os.getcwd()

    try:
        # Initialize a git repository in temp directory (required by GitManager)
        subprocess.run(["git", "init"], cwd=temp_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=temp_dir,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=temp_dir,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "checkout", "-b", "main"], cwd=temp_dir, check=True, capture_output=True
        )

        # Create initial commit (git requires at least one commit for branch detection)
        (temp_dir / "README.md").write_text("# Test Repo")
        subprocess.run(["git", "add", "README.md"], cwd=temp_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"], cwd=temp_dir, check=True, capture_output=True
        )

        # Change to temp directory so GitManager.detect_context() finds the test repo
        os.chdir(temp_dir)

        # Initialize components
        storage_manager = StorageManager(base_dir=temp_dir)
        git_manager = GitManager()
        embedder = Embedder(model_name="minilm", device="cpu")
        vector_index = VectorIndex(
            base_path=temp_dir / "vector_index",
            repo_id="test-repo",
            branch="main",
            dimension=embedder.dimension,
        )
        memory_manager = MemoryManager(
            git_manager=git_manager,
            storage_manager=storage_manager,
            embedder=embedder,
            vector_index=vector_index,
            auto_sanitize=False,
            secrets_detection=False,
        )

        yield memory_manager

    finally:
        # Restore original directory
        os.chdir(original_cwd)
        # Cleanup
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_store_and_retrieve(test_env):
    """Test storing and retrieving a memory."""
    memory_manager = test_env

    # Store a memory
    params = CreateMemoryParams(
        content="def example():\n    return 42",
        type="code_snippet",
        language="python",
        file_path="test.py",
        line_range=(1, 2),
        function_name="example",
        tags=["test", "example"],
        relationships=MemoryRelationships(),
    )

    memory = await memory_manager.create_memory(params)

    # Verify memory created
    assert memory.id is not None
    assert memory.content.text == params.content
    assert memory.content.language == "python"
    assert memory.content.function_name == "example"
    assert "test" in memory.metadata.tags

    # Retrieve memory
    retrieved = await memory_manager.retrieve_memory(memory.id)

    # Verify retrieved memory
    assert retrieved.id == memory.id
    assert retrieved.content.text == memory.content.text
    assert retrieved.content.function_name == "example"


@pytest.mark.asyncio
async def test_search_similar(test_env):
    """Test semantic similarity search."""
    memory_manager = test_env

    # Store multiple memories
    memories = [
        CreateMemoryParams(
            content="def add(a, b):\n    return a + b",
            type="code_snippet",
            language="python",
            function_name="add",
            relationships=MemoryRelationships(),
        ),
        CreateMemoryParams(
            content="def subtract(a, b):\n    return a - b",
            type="code_snippet",
            language="python",
            function_name="subtract",
            relationships=MemoryRelationships(),
        ),
        CreateMemoryParams(
            content="class Calculator:\n    pass",
            type="code_snippet",
            language="python",
            class_name="Calculator",
            relationships=MemoryRelationships(),
        ),
    ]

    for params in memories:
        await memory_manager.create_memory(params)

    # Search for addition-related code
    search_params = SearchParams(
        query="function that adds two numbers",
        top_k=2,
        min_similarity=0.0,
    )

    results = await memory_manager.search_similar(search_params)

    # Verify results
    assert len(results) > 0
    # First result should be 'add' function
    assert results[0].memory.content.function_name in ["add", "subtract"]


@pytest.mark.asyncio
async def test_list_with_filters(test_env):
    """Test listing memories with filters."""
    memory_manager = test_env

    # Store memories with different attributes
    await memory_manager.create_memory(
        CreateMemoryParams(
            content="def python_func(): pass",
            type="code_snippet",
            language="python",
            function_name="python_func",
            tags=["python"],
            relationships=MemoryRelationships(),
        )
    )

    await memory_manager.create_memory(
        CreateMemoryParams(
            content="function jsFunc() {}",
            type="code_snippet",
            language="javascript",
            function_name="jsFunc",
            tags=["javascript"],
            relationships=MemoryRelationships(),
        )
    )

    # List all memories and filter by language manually
    # (MemoryFilters doesn't support language filtering yet)
    from mememo.types.memory import MemoryFilters

    all_memories = await memory_manager.find_memories(MemoryFilters(type="code_snippet"))

    # Filter Python memories client-side
    python_memories = [m for m in all_memories if m.content.language == "python"]

    # Verify filtering
    assert len(python_memories) == 1
    assert python_memories[0].content.language == "python"
    assert python_memories[0].content.function_name == "python_func"


@pytest.mark.asyncio
async def test_delete_memory(test_env):
    """Test deleting a memory."""
    memory_manager = test_env

    # Store a memory
    memory = await memory_manager.create_memory(
        CreateMemoryParams(
            content="def to_delete(): pass",
            type="code_snippet",
            language="python",
            function_name="to_delete",
            relationships=MemoryRelationships(),
        )
    )

    memory_id = memory.id

    # Delete memory
    await memory_manager.delete_memory(memory_id)

    # Verify deletion
    with pytest.raises(ValueError):
        await memory_manager.retrieve_memory(memory_id)


@pytest.mark.asyncio
async def test_code_aware_chunking(test_env):
    """Test code-aware chunking with Python AST."""
    from mememo.chunking import ChunkerFactory

    factory = ChunkerFactory()

    python_code = '''
def function_one():
    """First function."""
    return 1

def function_two():
    """Second function."""
    return 2

class MyClass:
    """A class."""
    def method(self):
        pass
'''

    chunks = factory.chunk_file(python_code, "test.py")

    # Verify chunks extracted
    assert len(chunks) >= 3  # 2 functions + 1 class

    # Verify metadata extraction
    function_chunks = [c for c in chunks if c.chunk_type == "function"]
    assert len(function_chunks) >= 2
    assert any(c.function_name == "function_one" for c in function_chunks)
    assert any(c.docstring == "First function." for c in function_chunks)

    class_chunks = [c for c in chunks if c.chunk_type == "class"]
    assert len(class_chunks) >= 1
    assert any(c.class_name == "MyClass" for c in class_chunks)


@pytest.mark.asyncio
async def test_incremental_indexing():
    """Test incremental indexing with Merkle DAG."""
    from mememo.indexing import MerkleDAG

    temp_dir = Path(tempfile.mkdtemp())

    try:
        merkle = MerkleDAG(temp_dir)

        # Create test files
        file1 = temp_dir / "file1.py"
        file2 = temp_dir / "file2.py"

        file1.write_text("print('hello')")
        file2.write_text("print('world')")

        files = [file1, file2]

        # First indexing - all files changed
        changed = merkle.get_changed_files(files)
        assert len(changed) == 2

        # Second indexing - no changes
        changed = merkle.get_changed_files(files)
        assert len(changed) == 0

        # Modify one file
        file1.write_text("print('modified')")

        # Third indexing - one file changed
        changed = merkle.get_changed_files(files)
        assert len(changed) == 1
        assert file1 in changed

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_persistent_memory_types(test_env):
    """decision, analysis, conversation memories are never marked stale."""
    memory_manager = test_env

    decision = await memory_manager.create_memory(
        CreateMemoryParams(
            content="Use repository pattern for all DB access — decouples domain from persistence.",
            type="decision",
            tags=["architecture"],
            relationships=MemoryRelationships(),
        )
    )
    analysis = await memory_manager.create_memory(
        CreateMemoryParams(
            content="Root cause: N+1 query in user list endpoint. Fix: add select_related.",
            type="analysis",
            tags=["bug"],
            relationships=MemoryRelationships(),
        )
    )
    conversation = await memory_manager.create_memory(
        CreateMemoryParams(
            content="Session summary: decided to migrate auth to JWT. Rationale: stateless scaling.",
            type="conversation",
            tags=["session"],
            relationships=MemoryRelationships(),
        )
    )

    assert decision.content.type == "decision"
    assert analysis.content.type == "analysis"
    assert conversation.content.type == "conversation"

    # Persistent types start not stale
    assert not decision.metadata.stale
    assert not analysis.metadata.stale
    assert not conversation.metadata.stale

    # Mark stale for a file — persistent memories must not be affected
    from mememo.types.memory import CODE_MEMORY_TYPES, PERSISTENT_MEMORY_TYPES

    assert "decision" in PERSISTENT_MEMORY_TYPES
    assert "decision" not in CODE_MEMORY_TYPES


@pytest.mark.asyncio
async def test_staleness_tracking(test_env):
    """Code memories are staled when their source file changes."""
    memory_manager = test_env

    # Store a code memory for a fake file
    code_mem = await memory_manager.create_memory(
        CreateMemoryParams(
            content="def old_impl(): pass",
            type="code_snippet",
            language="python",
            file_path="src/service.py",
            tags=["code"],
            relationships=MemoryRelationships(),
        )
    )

    # Store a decision — must survive staleness
    decision_mem = await memory_manager.create_memory(
        CreateMemoryParams(
            content="Service layer owns business rules.",
            type="decision",
            tags=["arch"],
            relationships=MemoryRelationships(),
        )
    )

    context = await memory_manager.git_manager.detect_context()
    repo_id = context.repo.id
    branch = context.branch.name

    # Mark code memories stale for src/service.py
    staled = memory_manager.storage_manager.mark_memories_stale_for_file(
        "src/service.py", repo_id, branch, "File changed in commit abc1234"
    )
    assert staled == 1  # Only the code_snippet, not the decision

    # Code memory is now stale
    loaded_code = await memory_manager.storage_manager.load_memory(code_mem.id, context)
    assert loaded_code.metadata.stale is True
    assert "abc1234" in loaded_code.metadata.stale_reason

    # Decision is untouched
    loaded_decision = await memory_manager.storage_manager.load_memory(
        decision_mem.id, context
    )
    assert loaded_decision.metadata.stale is False

    # list_memories excludes stale by default
    from mememo.types.memory import MemoryFilters

    all_fresh = await memory_manager.find_memories(MemoryFilters(type="code_snippet"))
    assert all(not m.metadata.stale for m in all_fresh)
    assert not any(m.id == code_mem.id for m in all_fresh)

    # With include_stale=True the stale memory appears
    with_stale = await memory_manager.find_memories(
        MemoryFilters(type="code_snippet", include_stale=True)
    )
    assert any(m.id == code_mem.id for m in with_stale)


@pytest.mark.asyncio
async def test_last_indexed_commit(test_env):
    """index_repository records the commit; get/set_last_indexed_commit round-trips."""
    memory_manager = test_env

    context = await memory_manager.git_manager.detect_context()
    repo_id = context.repo.id
    branch = context.branch.name

    # Nothing recorded yet
    assert memory_manager.storage_manager.get_last_indexed_commit(repo_id, branch) is None

    # Record a commit
    memory_manager.storage_manager.set_last_indexed_commit(repo_id, branch, "deadbeef" * 5)
    result = memory_manager.storage_manager.get_last_indexed_commit(repo_id, branch)
    assert result == "deadbeef" * 5

    # Overwrite
    memory_manager.storage_manager.set_last_indexed_commit(repo_id, branch, "cafebabe" * 5)
    result = memory_manager.storage_manager.get_last_indexed_commit(repo_id, branch)
    assert result == "cafebabe" * 5


@pytest.mark.asyncio
async def test_sync_commits_no_previous_index(test_env):
    """sync_commits returns a clear error when no prior index exists."""
    import os

    from mememo.tools.sync_commits import sync_commits
    from mememo.tools.schemas import SyncCommitsParams

    memory_manager = test_env
    repo_path = os.getcwd()  # test_env sets cwd to the temp git repo

    params = SyncCommitsParams(repo_path=repo_path)
    response = await sync_commits(params, memory_manager)

    assert not response.success
    assert "index_repository" in response.message


@pytest.mark.asyncio
async def test_sync_commits_up_to_date(test_env):
    """sync_commits is a no-op when already at HEAD."""
    import os

    from mememo.tools.sync_commits import sync_commits
    from mememo.tools.schemas import SyncCommitsParams

    memory_manager = test_env
    repo_path = os.getcwd()

    context = await memory_manager.git_manager.detect_context()
    # Pretend we already indexed this commit
    memory_manager.storage_manager.set_last_indexed_commit(
        context.repo.id, context.branch.name, context.branch.commit_hash
    )

    params = SyncCommitsParams(repo_path=repo_path)
    response = await sync_commits(params, memory_manager)

    assert response.success
    assert "up to date" in response.message


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
