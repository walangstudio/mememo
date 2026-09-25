import json
import os
import subprocess
import sys

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, SRC)

from mememo import cli, index, search, store  # noqa: E402

LONG = "\n\n".join(f"Paragraph {i} about routine release notes and build chores." * 3 for i in range(60))


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMEMO_HOME", str(tmp_path))
    return tmp_path


def write(rel, text):
    p = os.path.join(store.mem_root(), *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def test_buried_fact_in_long_file_is_found():
    write(
        "global/project_big.md",
        "---\ndescription: big project log\n---\n"
        + LONG
        + "\n\nThe ConPTY backend hangs on cmd ver under portable-pty 0.9.\n\n"
        + LONG,
    )
    con = index.connect()
    index.sweep(con)
    hits = search.search(con, "portable-pty ConPTY hangs")
    assert hits and "portable-pty 0.9" in hits[0]["body"]


def test_supersede_hides_old_card_but_keeps_it():
    old, _ = store.add_card("global", "db", "project", "Staging DB is on port 5433", "said by user")
    store.add_card("global", "db", "project", "Staging DB is on port 6543", "moved", supersedes=old)
    con = index.connect()
    index.sweep(con)
    live = search.search(con, "staging db port")
    assert [h["claim"] for h in live] == ["Staging DB is on port 6543"]
    everything = search.search(con, "staging db port", include_old=True)
    assert any(h["card_id"] == old and h["superseded"] for h in everything)


def test_deleted_file_stays_searchable_as_old_and_restorable():
    write("global/gone.md", "---\ndescription: gone\n---\nThe zebra deploy key rotates monthly.\n")
    con = index.connect()
    index.sweep(con)
    os.remove(os.path.join(store.mem_root(), "global", "gone.md"))
    index.sweep(con)
    assert not search.search(con, "zebra deploy key rotates")
    old = search.search(con, "zebra deploy key rotates", include_old=True)
    assert old and old[0]["current"] == 0
    index.restore_snapshot("global/gone.md")
    index.sweep(con)
    assert search.search(con, "zebra deploy key rotates")


def test_unrelated_prompt_injects_nothing():
    store.add_card("global", "ci", "feedback", "Run cargo clippy before every push", "user rule")
    con = index.connect()
    index.sweep(con)
    assert search.search(con, "write a haiku about autumn leaves") == []
    assert search.search(con, "cargo clippy before push")


def test_secret_is_refused():
    with pytest.raises(ValueError):
        store.add_card("global", "x", "project", "token", "key ghp_" + "a" * 36)


def test_normalize_remote_variants():
    for url in (
        "git@gh-kitty:walangstudio/Mememo.git",
        "https://github.com/walangstudio/mememo",
        "ssh://git@github.com/walangstudio/mememo.git",
    ):
        assert store.normalize_remote(url) == "walangstudio/mememo"


def run_hook(payload, env):
    hook = os.path.join(SRC, "mememo", "hook.py")
    r = subprocess.run(
        [sys.executable, "-I", "-S", hook], input=json.dumps(payload).encode(), capture_output=True, env=env, timeout=30
    )
    assert r.returncode == 0
    return json.loads(r.stdout) if r.stdout else None


def test_hook_envelope_and_fail_open(home):
    env = {**os.environ, "MEMEMO_HOME": str(home)}
    assert run_hook({"hook_event_name": "UserPromptSubmit", "prompt": "anything at all"}, env) is None  # no DB yet
    store.add_card("global", "ci", "feedback", "Run cargo clippy before every push", "user rule")
    start = run_hook({"hook_event_name": "SessionStart", "source": "startup", "cwd": str(home)}, env)
    assert start["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert 'hook.py" add --type' in start["hookSpecificOutput"]["additionalContext"]
    out = run_hook(
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "cargo clippy before push",
            "cwd": str(home),
            "session_id": "s1",
        },
        env,
    )
    assert "<mememo-recall>" in out["hookSpecificOutput"]["additionalContext"]
    again = run_hook(
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "cargo clippy before push",
            "cwd": str(home),
            "session_id": "s1",
        },
        env,
    )
    assert again is None  # already injected this session


def test_install_is_idempotent(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other"}]}]}}))
    for _ in range(2):
        cli.main(["install", "--settings", str(settings)])
    cfg = json.loads(settings.read_text())
    assert cfg["autoMemoryEnabled"] is False
    assert len(cfg["hooks"]["UserPromptSubmit"]) == 1
    assert len(cfg["hooks"]["Stop"]) == 2


def test_heading_inside_card_body_stays_in_card():
    old, _ = store.add_card(
        "global", "tools", "project", "Install oldtool with pipx", "Run:\n# install\npipx install oldtool"
    )
    store.add_card("global", "tools", "project", "Install newtool with uv", "uv tool install newtool", supersedes=old)
    con = index.connect()
    index.sweep(con)
    assert not any("oldtool" in h["body"] for h in search.search(con, "pipx install oldtool tool"))


def test_hand_added_supersedes_is_applied():
    old, path = store.add_card("global", "db", "project", "Cache is redis 6", "said")
    new, _ = store.add_card("global", "db", "project", "Cache is redis 7", "said")
    con = index.connect()
    index.sweep(con)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.replace(f"id: {new} |", f"id: {new} | supersedes: {old} |"))
    index.sweep(con)
    assert [h["claim"] for h in search.search(con, "cache redis")] == ["Cache is redis 7"]


def test_stale_lock_is_recovered():
    _, path = store.add_card("global", "x", "project", "first fact", "")
    lock = path + ".lock"
    open(lock, "w").close()
    os.utime(lock, (0, 0))
    store.add_card("global", "x", "project", "second fact", "")
    assert not os.path.exists(lock)


def test_restore_keeps_unswept_live_edit():
    write("global/n.md", "---\ndescription: n\n---\nversion one\n")
    index.sweep()
    write("global/n.md", "---\ndescription: n\n---\nversion two, not swept\n")
    index.restore_snapshot("global/n.md", 0)
    snaps = os.listdir(os.path.join(store.home(), "history", "global", "n.md"))
    texts = [open(os.path.join(store.home(), "history", "global", "n.md", s), encoding="utf-8").read() for s in snaps]
    assert any("version two" in t for t in texts)


def test_appends_fold_into_one_snapshot():
    for i in range(5):
        store.add_card("global", "grow", "project", f"fact number {i}", "")
        index.sweep()
    assert len(os.listdir(os.path.join(store.home(), "history", "global", "grow.md"))) == 1


def test_lane_is_validated_and_ids_are_unique():
    with pytest.raises(ValueError):
        store.add_card("../../etc", "x", "project", "claim", "")
    assert len({store.new_id() for _ in range(2000)}) == 2000


def test_relative_gitdir_worktree(tmp_path):
    main = tmp_path / "main" / ".git"
    (main / "worktrees" / "wt").mkdir(parents=True)
    (main / "config").write_text('[remote "origin"]\n\turl = git@github.com:owner/proj.git\n')
    (main / "worktrees" / "wt" / "commondir").write_text("../..")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: ../main/.git/worktrees/wt\n")
    assert store.lane_for(str(wt)) == "repos/owner/proj"


def test_cli_prints_unicode_through_a_pipe(home):
    env = {**os.environ, "MEMEMO_HOME": str(home), "PYTHONPATH": SRC, "PYTHONIOENCODING": ""}

    def run(*a):
        return subprocess.run([sys.executable, "-m", "mememo", *a], capture_output=True, env=env, timeout=30)

    assert run("add", "--claim", "Deploy flow: build → test → ship", "--body", "arrows → everywhere").returncode == 0
    r = run("search", "deploy flow build test ship", "--full")
    assert r.returncode == 0 and "→" in r.stdout.decode("utf-8")


def test_compound_question_recalls_both_facts():
    store.add_card("global", "lh", "project", "Lighthouse production deploys to region eu-west-2", "GDPR")
    store.add_card("global", "lh", "project", "The lighthouse staging database is Postgres 16 on port 7000", "said")
    con = index.connect()
    index.sweep(con)
    q = "What database engine, version and port does lighthouse staging use, and which region does production deploy to?"
    claims = " ".join(h["claim"] for h in search.search(con, q))
    assert "eu-west-2" in claims and "port 7000" in claims


def test_hook_file_doubles_as_cli(home):
    env = {**os.environ, "MEMEMO_HOME": str(home)}
    hook = os.path.join(SRC, "mememo", "hook.py")
    r = subprocess.run(
        [sys.executable, "-I", "-S", hook, "add", "--claim", "CLI via hook path works"],
        capture_output=True,
        env=env,
        timeout=30,
    )
    assert r.returncode == 0 and b"saved m-" in r.stdout
    bad = subprocess.run([sys.executable, "-I", "-S", hook, "show", "m-nope"], capture_output=True, env=env, timeout=30)
    assert bad.returncode != 0
