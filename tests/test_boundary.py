"""Tests for repository ownership and task-manifest admission."""

import subprocess

import pytest

from gtx_broker import (
    OwnedRepository,
    RepositoryBoundaryError,
    RepositoryRegistry,
    TaskManifestError,
    validate_task_manifest,
)
from gtx_broker.scheduler import Scheduler, SchedulerConfig


def make_registry(tmp_path):
    return RepositoryRegistry((OwnedRepository(
        "demo",
        "https://github.com/example/demo.git",
        (tmp_path / "demo", tmp_path / "worktrees"),
    ),))


def make_manifest(tmp_path):
    return {
        "manifest_version": 1,
        "task_id": "TASK-001",
        "goal_id": "GOAL-001",
        "session_id": "SESSION-001",
        "repository": "demo",
        "repository_remote": "https://github.com/example/demo.git",
        "base_ref": "main",
        "branch": "automation/task-001",
        "worktree_path": str(tmp_path / "worktrees" / "task-001"),
        "context_documents": ["README.md", "INITIALISATION.md"],
        "scope": "Implement the bounded change described by the task.",
        "acceptance_criteria": ["The deterministic test command passes."],
        "test_command": "python -m pytest -q",
        "worker_profile": "p40-coding",
        "timeout_seconds": 1800,
        "context_size": 131072,
        "commit_required": True,
        "pull_request_required": True,
        "review_policy": {
            "air_review_required": True,
            "codex_final_review_required": True,
            "merge_on_approval": True,
        },
        "merge_policy": "air-review-then-codex",
    }


def test_valid_manifest_checks_declared_boundary_without_git(tmp_path):
    manifest = make_manifest(tmp_path)
    result = validate_task_manifest(
        manifest, registry=make_registry(tmp_path), check_worktree=False
    )
    assert result.task_id == "TASK-001"
    assert result.repository == "demo"


def test_manifest_rejects_unowned_repository_and_path(tmp_path):
    manifest = make_manifest(tmp_path)
    manifest["repository"] = "not-owned"
    with pytest.raises(TaskManifestError, match="repository is not owned"):
        validate_task_manifest(manifest, registry=make_registry(tmp_path), check_worktree=False)

    manifest = make_manifest(tmp_path)
    manifest["worktree_path"] = str(tmp_path / "outside")
    with pytest.raises(TaskManifestError, match="outside the owned roots"):
        validate_task_manifest(manifest, registry=make_registry(tmp_path), check_worktree=False)


@pytest.mark.parametrize("field", ["context_documents", "acceptance_criteria", "test_command"])
def test_manifest_rejects_incomplete_execution_contract(tmp_path, field):
    manifest = make_manifest(tmp_path)
    manifest[field] = [] if field != "test_command" else ""
    with pytest.raises(TaskManifestError):
        validate_task_manifest(manifest, registry=make_registry(tmp_path), check_worktree=False)


def test_manifest_rejects_unsafe_review_or_worker_policy(tmp_path):
    manifest = make_manifest(tmp_path)
    manifest["worker_profile"] = "air-review"
    with pytest.raises(TaskManifestError, match="worker_profile"):
        validate_task_manifest(manifest, registry=make_registry(tmp_path), check_worktree=False)

    manifest = make_manifest(tmp_path)
    manifest["merge_policy"] = "worker-decides"
    with pytest.raises(TaskManifestError, match="merge_policy"):
        validate_task_manifest(manifest, registry=make_registry(tmp_path), check_worktree=False)


def test_checkout_preflight_requires_clean_owned_branch(tmp_path):
    path = tmp_path / "worktrees" / "task-001"
    path.mkdir(parents=True)
    def git(*args):
        return subprocess.run(["git", "-C", str(path), *args], check=True,
                              capture_output=True, text=True)
    git("init", "-q")
    git("config", "user.email", "tests@example.invalid")
    git("config", "user.name", "Boundary Tests")
    git("switch", "-c", "automation/task-001")
    for document in ("README.md", "INITIALISATION.md"):
        (path / document).write_text(f"# {document}\n")
    git("add", ".")
    git("commit", "-qm", "test fixture")
    git("remote", "add", "origin", "https://github.com/example/demo.git")

    manifest = make_manifest(tmp_path)
    manifest["worktree_path"] = str(path)
    result = validate_task_manifest(manifest, registry=make_registry(tmp_path))
    assert result.preflight.branch == "automation/task-001"

    scheduler = Scheduler(SchedulerConfig(db_path=str(tmp_path / "tasks.db")))
    assert scheduler.add_manifest_task(manifest, registry=make_registry(tmp_path))
    queued = scheduler.get_task("TASK-001")
    assert queued["state"] == "queued"
    assert queued["payload"]["worker_profile"] == "p40-coding"

    (path / "dirty.txt").write_text("must fail\n")
    with pytest.raises(TaskManifestError, match="clean"):
        validate_task_manifest(manifest, registry=make_registry(tmp_path))


def test_repository_registry_rejects_cross_repo_remote(tmp_path):
    registry = make_registry(tmp_path)
    with pytest.raises(RepositoryBoundaryError):
        registry.validate_checkout("demo", tmp_path / "demo", expected_remote="https://github.com/other/repo.git")
