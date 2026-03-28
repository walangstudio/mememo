"""Tests for cleanup_memory tool schemas and params."""

from mememo.tools.schemas import CleanupMemoryParams, CleanupMemoryResponse


def test_default_params():
    params = CleanupMemoryParams()
    assert params.dry_run is True
    assert params.dedup is False
    assert params.stale_only is False
    assert params.older_than_days is None
    assert params.type is None


def test_params_with_age():
    params = CleanupMemoryParams(older_than_days=30, type="conversation", dry_run=False)
    assert params.older_than_days == 30
    assert params.type == "conversation"
    assert params.dry_run is False


def test_params_stale_only():
    params = CleanupMemoryParams(stale_only=True)
    assert params.stale_only is True
    assert params.dry_run is True  # still defaults to dry run


def test_params_dedup():
    params = CleanupMemoryParams(dedup=True, dedup_similarity=0.9)
    assert params.dedup is True
    assert params.dedup_similarity == 0.9


def test_response_model():
    resp = CleanupMemoryResponse(
        success=True,
        message="Dry run: 5 memories would be deleted",
        candidates=[{"id": "abc", "type": "context", "file_path": "", "reason": "old"}],
        deleted_count=0,
    )
    assert resp.success
    assert len(resp.candidates) == 1
    assert resp.deleted_count == 0
