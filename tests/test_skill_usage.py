"""Phase C: per-skill usage tracking (feeds prune-never-used). No heavy deps."""

from __future__ import annotations

import os
import time

from mememo.context.skill_store import SkillStore


def _store(tmp_path):
    ss = SkillStore(base_dir=tmp_path)
    return ss


def test_get_usage_zero_for_unseen(tmp_path) -> None:
    ss = _store(tmp_path)
    ss.create_skill("a", "coding", "body")
    assert ss.get_usage("a") == {"count": 0, "last_used": None}


def test_record_use_increments_and_stamps(tmp_path) -> None:
    ss = _store(tmp_path)
    ss.create_skill("a", "coding", "body")
    ss.record_use(["a"])
    ss.record_use(["a"])
    u = ss.get_usage("a")
    assert u["count"] == 2
    assert u["last_used"] is not None


def test_record_use_persists_across_instances(tmp_path) -> None:
    ss = _store(tmp_path)
    ss.create_skill("a", "coding", "body")
    ss.record_use(["a"])
    # A fresh store (new process) sees the persisted count.
    assert SkillStore(base_dir=tmp_path).get_usage("a")["count"] == 1


def test_record_use_empty_is_noop(tmp_path) -> None:
    ss = _store(tmp_path)
    ss.create_skill("a", "coding", "body")
    ss.record_use([])
    ss.record_use([""])
    assert ss.get_usage("a")["count"] == 0


def test_usage_map_returns_all(tmp_path) -> None:
    ss = _store(tmp_path)
    ss.create_skill("a", "coding", "body")
    ss.create_skill("b", "coding", "body2")
    ss.record_use(["a"])
    ss.record_use(["b"])
    ss.record_use(["b"])
    m = ss.usage_map()
    assert m["a"]["count"] == 1 and m["b"]["count"] == 2


def test_delete_purges_usage(tmp_path) -> None:
    ss = _store(tmp_path)
    ss.create_skill("g", "coding", "body")
    ss.record_use(["g"])
    assert ss.get_usage("g")["count"] == 1
    ss.delete_skill("g")
    assert ss.get_usage("g") == {"count": 0, "last_used": None}
    assert "g" not in ss.usage_map()


def test_usage_file_is_not_loaded_as_a_skill(tmp_path) -> None:
    ss = _store(tmp_path)
    ss.create_skill("a", "coding", "body")
    ss.record_use(["a"])  # writes usage.json into the skills dir
    names = {s.name for s in SkillStore(base_dir=tmp_path).list_skills()}
    assert names == {"a"}  # usage.json must not appear as a skill


def test_stale_unused_lists_old_never_used(tmp_path) -> None:
    ss = _store(tmp_path)
    ss.create_skill("old", "coding", "p1")
    ss.create_skill("used", "coding", "p2")
    ss.record_use(["used"])
    future = time.time() + 60 * 86400  # 60 days hence → both files exceed 30d age
    assert ss.stale_unused_skills(stale_days=30, _now=future) == ["old"]


def test_stale_unused_spares_recent(tmp_path) -> None:
    ss = _store(tmp_path)
    ss.create_skill("fresh", "coding", "p")
    assert ss.stale_unused_skills(stale_days=30) == []  # age ~0 < 30d


def test_stale_unused_disabled_when_zero(tmp_path) -> None:
    ss = _store(tmp_path)
    ss.create_skill("x", "coding", "p")
    far = time.time() + 999 * 86400
    assert ss.stale_unused_skills(stale_days=0, _now=far) == []


def test_usage_key_is_canonical(tmp_path) -> None:
    # A name needing sanitization ("git ops" -> "gitops") must key usage canonically,
    # so record_use / get_usage / stale checks all agree regardless of the raw spelling.
    ss = _store(tmp_path)
    ss.create_skill("git ops", "coding", "body")  # stored as gitops.yaml
    ss.record_use(["git ops"])  # raw name
    assert ss.get_usage("git ops")["count"] == 1
    assert ss.get_usage("gitops")["count"] == 1
    assert ss.stale_unused_skills(stale_days=30, _now=time.time() + 60 * 86400) == []


def test_stale_unused_respects_file_mtime(tmp_path) -> None:
    ss = _store(tmp_path)
    ss.create_skill("aged", "coding", "p")
    path = tmp_path / "skills" / "aged.yaml"
    old = time.time() - 40 * 86400
    os.utime(path, (old, old))
    assert ss.stale_unused_skills(stale_days=30) == ["aged"]
