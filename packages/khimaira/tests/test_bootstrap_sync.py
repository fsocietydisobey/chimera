"""Tests for the new sync operations introduced in task #66.

Covers `git_pull_repo`, `check_unpushed`, and `maybe_run_uv_sync` —
the per-op helpers that `run_sync` orchestrates. Each test uses a
tmp git repo (no real network) so the suite stays hermetic +
deterministic.

Strategy: build a "remote" bare repo in tmp + a "local" clone that
tracks it. Push commits to the bare repo to simulate updates; the
local clone tests fetch + ff-only merge behavior + dep-change
detection.

The runner-level test (run_sync end-to-end with a real profile) is
deferred to integration tests once the profile fixture is stabilized
— the per-op tests below catch every behavior change without
needing a full profile shim.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from khimaira.bootstrap.operations import (
    OpResult,
    check_unpushed,
    git_pull_repo,
    maybe_run_uv_sync,
)
from khimaira.bootstrap.schema import RepoSpec


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Helper: run git with quiet output, raise on non-zero."""
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {args} failed: {proc.stderr}")
    return proc


def _seed_commit(repo: Path, filename: str, content: str, message: str) -> None:
    (repo / filename).write_text(content)
    _git(repo, "add", filename)
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", message)


@pytest.fixture
def remote_with_clone(tmp_path: Path):
    """Build a bare 'remote' repo + a local clone tracking it.

    Returns (remote_path, local_path) tuple. The local clone has one
    initial commit and is in sync with origin.
    """
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    local = tmp_path / "local"

    # Create the bare remote
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(remote)],
        check=True,
        capture_output=True,
    )

    # Seed with one initial commit via a throwaway working tree
    subprocess.run(
        ["git", "clone", str(remote), str(seed)],
        check=True,
        capture_output=True,
    )
    _seed_commit(seed, "README.md", "initial\n", "initial commit")
    _git(seed, "push", "origin", "main")
    shutil.rmtree(seed)

    # Clone into the local path that the test will operate on
    subprocess.run(
        ["git", "clone", str(remote), str(local)],
        check=True,
        capture_output=True,
    )
    return remote, local


def _spec_for(local_path: Path, name: str = "test-repo") -> RepoSpec:
    """Build a RepoSpec pointing at a tmp local clone."""
    return RepoSpec(name=name, url="file://unused", path=str(local_path))


# -------------------- git_pull_repo -------------------- #


def test_git_pull_repo_unchanged_when_in_sync(remote_with_clone):
    """No new commits on remote → pull is a no-op, status=unchanged."""
    _, local = remote_with_clone
    result = git_pull_repo(_spec_for(local))

    assert result.status == "unchanged"
    assert "up to date" in result.detail.lower()
    assert result.meta == {}


def test_git_pull_repo_picks_up_new_commits(remote_with_clone, tmp_path):
    """A commit on remote is pulled in via ff-only merge."""
    remote, local = remote_with_clone

    # Push a new commit via a throwaway clone of the bare remote
    pusher = tmp_path / "pusher"
    subprocess.run(["git", "clone", str(remote), str(pusher)], check=True, capture_output=True)
    _seed_commit(pusher, "feature.py", "x = 1\n", "add feature")
    _git(pusher, "push", "origin", "main")

    result = git_pull_repo(_spec_for(local))

    assert result.status == "updated"
    assert result.meta["commits_pulled"] == 1
    assert result.meta["deps_changed"] is False
    assert "1 commit" in result.detail
    # The pulled file should now exist locally
    assert (local / "feature.py").is_file()


def test_git_pull_repo_detects_deps_changed_pyproject(remote_with_clone, tmp_path):
    """A commit touching pyproject.toml flips meta.deps_changed=True."""
    remote, local = remote_with_clone

    pusher = tmp_path / "pusher"
    subprocess.run(["git", "clone", str(remote), str(pusher)], check=True, capture_output=True)
    _seed_commit(
        pusher,
        "pyproject.toml",
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        "add pyproject.toml",
    )
    _git(pusher, "push", "origin", "main")

    result = git_pull_repo(_spec_for(local))

    assert result.status == "updated"
    assert result.meta["deps_changed"] is True
    assert "pyproject/uv.lock touched" in result.detail


def test_git_pull_repo_detects_deps_changed_uv_lock(remote_with_clone, tmp_path):
    """A commit touching uv.lock flips meta.deps_changed=True (same path as pyproject)."""
    remote, local = remote_with_clone

    pusher = tmp_path / "pusher"
    subprocess.run(["git", "clone", str(remote), str(pusher)], check=True, capture_output=True)
    _seed_commit(pusher, "uv.lock", "# lockfile\n", "add uv.lock")
    _git(pusher, "push", "origin", "main")

    result = git_pull_repo(_spec_for(local))

    assert result.meta["deps_changed"] is True


def test_git_pull_repo_skipped_when_no_git_dir(tmp_path):
    """A RepoSpec pointing at a non-git dir is skipped (not failed) —
    bootstrap hasn't run yet, sync surfaces that without erroring out."""
    not_a_repo = tmp_path / "empty"
    not_a_repo.mkdir()

    result = git_pull_repo(_spec_for(not_a_repo))

    assert result.status == "skipped"
    assert "bootstrap" in result.detail.lower()


def test_git_pull_repo_refuses_ff_merge_when_local_diverged(
    remote_with_clone, tmp_path
):
    """Local has its own commits AND remote has new commits → ff-only
    refuses. Sync surfaces this as `failed` with a "resolve manually"
    hint — never silently rewrites local work."""
    remote, local = remote_with_clone

    # Local-only commit
    _seed_commit(local, "local-only.txt", "local\n", "local commit")

    # Remote-only commit
    pusher = tmp_path / "pusher"
    subprocess.run(["git", "clone", str(remote), str(pusher)], check=True, capture_output=True)
    _seed_commit(pusher, "remote-only.txt", "remote\n", "remote commit")
    _git(pusher, "push", "origin", "main")

    result = git_pull_repo(_spec_for(local))

    assert result.status == "failed"
    assert "resolve manually" in result.detail.lower()
    # Local commit must not be lost
    assert (local / "local-only.txt").is_file()


# -------------------- check_unpushed -------------------- #


def test_check_unpushed_zero_when_in_sync(remote_with_clone):
    """A freshly cloned repo with no local commits → 0 ahead."""
    _, local = remote_with_clone

    result = check_unpushed(_spec_for(local))

    assert result.status == "unchanged"
    assert "in sync" in result.detail.lower()
    assert result.meta == {}


def test_check_unpushed_reports_local_commits_ahead(remote_with_clone):
    """Two local commits with no push → report 2 commits ahead."""
    _, local = remote_with_clone

    _seed_commit(local, "a.txt", "a\n", "first local")
    _seed_commit(local, "b.txt", "b\n", "second local")

    result = check_unpushed(_spec_for(local))

    assert result.status == "updated"
    assert result.meta["unpushed_count"] == 2
    assert "2 unpushed" in result.detail


def test_check_unpushed_skipped_without_upstream(tmp_path):
    """A repo with no upstream tracking → skipped, not failed."""
    no_upstream = tmp_path / "no_upstream"
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(no_upstream)],
        check=True,
        capture_output=True,
    )
    _seed_commit(no_upstream, "x.txt", "x\n", "init")

    result = check_unpushed(_spec_for(no_upstream))

    assert result.status == "skipped"
    assert "upstream" in result.detail.lower()


def test_check_unpushed_skipped_without_git_dir(tmp_path):
    """A non-git path is skipped (informational op never fails)."""
    not_a_repo = tmp_path / "nogit"
    not_a_repo.mkdir()

    result = check_unpushed(_spec_for(not_a_repo))

    assert result.status == "skipped"
    assert "no git" in result.detail.lower()


# -------------------- maybe_run_uv_sync -------------------- #


def test_maybe_run_uv_sync_skipped_when_no_deps_changed(tmp_path):
    """deps_changed=False → no-op, status=unchanged. Does NOT invoke uv."""
    result = maybe_run_uv_sync(tmp_path, deps_changed=False)

    assert result.status == "unchanged"
    assert "no pyproject/uv.lock changes" in result.detail


def test_maybe_run_uv_sync_failed_status_when_uv_errors(tmp_path):
    """If uv sync errors (e.g. broken pyproject), status=failed with stderr.

    Using an empty tmp dir as the workspace — uv sync there fails
    because there's no pyproject. The point is to exercise the
    failure path, not validate uv's behavior.
    """
    result = maybe_run_uv_sync(tmp_path, deps_changed=True)

    assert result.status == "failed"
    assert "uv sync failed" in result.detail.lower()


# -------------------- OpResult.meta backward-compat -------------------- #


def test_opresult_meta_defaults_to_empty_dict():
    """The new `meta` field on OpResult defaults to {} — existing callers
    don't break because they never pass it. The CLI renderer never reads
    meta (it's runner-internal), so no display regression."""
    r = OpResult(op="x", target="y", status="unchanged")

    assert r.meta == {}
    assert isinstance(r.meta, dict)


def test_opresult_meta_carries_arbitrary_payload():
    """meta is a plain dict — ops can stuff whatever the runner reads."""
    r = OpResult(
        op="x",
        target="y",
        status="updated",
        meta={"commits_pulled": 5, "deps_changed": True, "extra": "ok"},
    )

    assert r.meta["commits_pulled"] == 5
    assert r.meta["deps_changed"] is True
    assert r.meta["extra"] == "ok"
