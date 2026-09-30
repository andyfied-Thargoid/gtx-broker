"""Owned-repository registry and checkout preflight checks.

The broker may receive work for several repositories, but it must never infer
ownership from an arbitrary filesystem path.  This module provides the small,
read-only boundary check used before a task can be admitted.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Iterable, Optional
from urllib.parse import urlparse


class RepositoryBoundaryError(ValueError):
    """Raised when a repository or worktree fails the ownership boundary."""


def normalize_remote(remote: str) -> str:
    """Normalize common HTTPS, SSH, and SCP-style Git remotes."""
    if not isinstance(remote, str) or not remote.strip():
        raise RepositoryBoundaryError("repository remote must be a non-empty string")
    value = remote.strip().rstrip("/")
    if value.startswith("git@") and ":" in value:
        host, path = value[4:].split(":", 1)
        value = f"ssh://git@{host}/{path}"
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        host = parsed.hostname or ""
        path = parsed.path.lstrip("/")
    else:
        raise RepositoryBoundaryError(f"unsupported repository remote: {remote}")
    if path.endswith(".git"):
        path = path[:-4]
    return f"{host.lower()}/{path.lower().strip('/') }"


@dataclass(frozen=True)
class OwnedRepository:
    """A repository identity and the filesystem roots it may use."""

    name: str
    canonical_remote: str
    allowed_roots: tuple[Path, ...]
    alternate_remotes: tuple[str, ...] = ()

    @property
    def allowed_remotes(self) -> tuple[str, ...]:
        return (self.canonical_remote, *self.alternate_remotes)

    def matches_remote(self, remote: str) -> bool:
        normalized = normalize_remote(remote)
        return any(normalized == normalize_remote(candidate) for candidate in self.allowed_remotes)

    def contains(self, path: Path) -> bool:
        candidate = path.resolve(strict=False)
        return any(
            candidate == root.resolve(strict=False)
            or root.resolve(strict=False) in candidate.parents
            for root in self.allowed_roots
        )


@dataclass(frozen=True)
class RepositoryPreflight:
    """Evidence returned by a successful checkout preflight."""

    repository: OwnedRepository
    worktree_path: Path
    remotes: tuple[str, ...]
    branch: str
    push_remote: str


class RepositoryRegistry:
    """Explicit allow-list for repositories the broker may modify."""

    def __init__(self, repositories: Iterable[OwnedRepository]):
        entries = tuple(repositories)
        names = [entry.name for entry in entries]
        if len(names) != len(set(names)):
            raise ValueError("repository names must be unique")
        self._repositories = {entry.name: entry for entry in entries}

    @classmethod
    def compute01_defaults(
        cls,
        source_root: Path | str = "/home/andyfied/src",
        worktree_root: Path | str = "/home/andyfied/src/worktrees",
    ) -> "RepositoryRegistry":
        """Return the explicitly owned compute01 application repositories."""
        source_root = Path(source_root)
        worktree_root = Path(worktree_root)
        return cls((
            OwnedRepository(
                "gtx-broker", "https://github.com/andyfied-agent/gtx-broker.git",
                (source_root / "gtx-broker", worktree_root),
                ("https://github.com/andyfied-Thargoid/gtx-broker.git",),
            ),
            OwnedRepository(
                "workstation", "https://github.com/andyfied-agent/workstation.git",
                (source_root / "workstation", worktree_root),
            ),
            OwnedRepository(
                "telegram-chat-bot", "https://github.com/andyfied-Thargoid/telegram-chat-bot.git",
                (source_root / "telegram-chat-bot", worktree_root),
                ("https://github.com/andyfied-agent/telegram-chat-bot.git",),
            ),
            OwnedRepository(
                "esp32-s3-monitor", "https://github.com/andyfied-agent/esp32-s3-monitor.git",
                (source_root / "esp32-s3-monitor", worktree_root),
            ),
            OwnedRepository(
                "rotary-trinkey-volume",
                "ssh://git@github-rotary-trinkey/andyfied-Thargoid/rotary-trinkey-volume.git",
                (source_root / "rotary-trinkey-volume", worktree_root),
                ("https://github.com/andyfied-Thargoid/rotary-trinkey-volume.git",),
            ),
            OwnedRepository(
                "WeatherPanel", "https://github.com/andyfied-agent/WeatherPanel.git",
                (source_root / "WeatherPanel", worktree_root),
            ),
        ))

    def get(self, name: str) -> OwnedRepository:
        try:
            return self._repositories[name]
        except KeyError as exc:
            raise RepositoryBoundaryError(f"repository is not owned: {name}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._repositories))

    def validate_checkout(
        self,
        name: str,
        worktree_path: Path | str,
        *,
        expected_remote: Optional[str] = None,
    ) -> RepositoryPreflight:
        """Verify path containment, Git identity, cleanliness, and branch."""
        repository = self.get(name)
        path = Path(worktree_path).expanduser().resolve(strict=False)
        if not repository.contains(path):
            raise RepositoryBoundaryError(
                f"worktree is outside the owned roots for {name}: {path}"
            )
        if not path.is_dir():
            raise RepositoryBoundaryError(f"worktree does not exist: {path}")

        def run_git(*args: str) -> str:
            result = subprocess.run(
                ["git", "-C", str(path), *args],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()
                raise RepositoryBoundaryError(f"Git preflight failed: {detail}")
            return result.stdout

        root = Path(run_git("rev-parse", "--show-toplevel").strip()).resolve()
        if root != path:
            raise RepositoryBoundaryError(
                f"worktree path is not the Git checkout root: {path} != {root}"
            )
        status = run_git("status", "--porcelain", "--untracked-files=all").strip()
        if status:
            raise RepositoryBoundaryError("worktree must be clean before task admission")
        branch = run_git("branch", "--show-current").strip()
        if not branch or branch in {"main", "master"}:
            raise RepositoryBoundaryError("task worktree must be on a non-default branch")

        remotes = []
        for line in run_git("remote", "-v").splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[1] not in remotes:
                remotes.append(fields[1])
        if not remotes:
            raise RepositoryBoundaryError(
                f"checkout has no configured remote for owned repository {name}"
            )

        def optional_git(*args: str) -> str:
            result = subprocess.run(
                ["git", "-C", str(path), *args],
                capture_output=True,
                text=True,
                check=False,
            )
            return result.stdout.strip() if result.returncode == 0 else ""

        # Git resolves an ordinary push target in this order: branch-specific
        # pushRemote, remote.pushDefault, the branch's remote, then origin.
        # Validate that effective target, rather than accepting an unrelated
        # approved remote.
        push_remote_name = optional_git("config", "--get", f"branch.{branch}.pushRemote")
        if not push_remote_name:
            push_remote_name = optional_git("config", "--get", "remote.pushDefault")
        if not push_remote_name:
            push_remote_name = optional_git("config", "--get", f"branch.{branch}.remote")
        push_remote_name = push_remote_name or "origin"
        if push_remote_name == ".":
            raise RepositoryBoundaryError("checkout push target is the local repository")
        push_urls = optional_git("config", "--get-all", f"remote.{push_remote_name}.pushurl")
        if push_urls:
            push_urls = push_urls.splitlines()
        else:
            push_urls = optional_git("config", "--get-all", f"remote.{push_remote_name}.url")
            push_urls = push_urls.splitlines() if push_urls else []
        if not push_urls:
            raise RepositoryBoundaryError(
                f"push remote {push_remote_name} has no configured push URL"
            )
        if not all(repository.matches_remote(remote) for remote in push_urls):
            raise RepositoryBoundaryError(
                f"push remote {push_remote_name} is not approved for owned repository {name}"
            )
        if expected_remote is not None:
            expected = normalize_remote(expected_remote)
            if not repository.matches_remote(expected_remote) or any(
                normalize_remote(remote) != expected for remote in push_urls
            ):
                raise RepositoryBoundaryError(
                    "manifest remote must match the configured push remote"
                )
        if not any(repository.matches_remote(remote) for remote in remotes):
            raise RepositoryBoundaryError(
                f"no configured remote belongs to owned repository {name}"
            )
        return RepositoryPreflight(
            repository, path, tuple(remotes), branch, push_urls[0]
        )
