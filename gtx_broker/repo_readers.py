"""Repository README discovery and reading for GTX broker.

Provides discovery and reading of README files from all accessible repositories.
Supports both local checkouts and remote GitHub repositories via API.
"""

from __future__ import annotations

import json
import logging
import os
import base64
import stat
from urllib.parse import quote, urlparse
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

import requests

logger = logging.getLogger(__name__)


@dataclass
class RepositoryReadme:
    """A discovered README file."""
    repo_name: str
    repo_path: str  # Local path or None for remote
    readme_path: str  # Path to README in repo (e.g., "README.md")
    content: str
    content_hash: str
    last_read: str  # ISO timestamp
    is_local: bool
    error: Optional[str] = None


@dataclass
class RepositoryRegistry:
    """Registry of repositories the broker can access."""
    repositories: Dict[str, str] = field(default_factory=dict)  # name -> path or URL
    github_token: Optional[str] = None
    last_updated: Optional[str] = None

    @classmethod
    def from_file(cls, path: str | Path) -> "RepositoryRegistry":
        """Load repository mappings from a JSON registry file."""
        registry_path = Path(path)
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        repositories = data.get("repositories", data) if isinstance(data, dict) else None
        if not isinstance(repositories, dict):
            raise ValueError("repository registry must contain an object of repositories")

        validated: Dict[str, str] = {}
        for name, location in repositories.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("repository names must be non-empty strings")
            if not isinstance(location, str) or not location.strip():
                raise ValueError(f"repository location for {name!r} must be a non-empty string")
            validated[name] = location
        return cls(validated, last_updated=datetime.now().isoformat())

    @classmethod
    def compute01_defaults(cls, source_root: str = "/home/andyfied/src") -> "RepositoryRegistry":
        """Return default repository registry for compute01 environment."""
        return cls({
            "gtx-broker": f"{source_root}/gtx-broker",
            "workstation": f"{source_root}/workstation",
            "telegram-chat-bot": f"{source_root}/telegram-chat-bot",
            "esp32-s3-monitor": f"{source_root}/esp32-s3-monitor",
            "WeatherPanel": f"{source_root}/WeatherPanel",
        })

    @classmethod
    def discover(
        cls,
        source_root: str | Path = "/home/andyfied/src",
        github_token: Optional[str] = None,
    ) -> "RepositoryRegistry":
        """Discover local checkouts and repositories authorized for the account."""
        root = Path(source_root).expanduser()
        repositories: Dict[str, str] = {}
        if root.is_dir():
            for checkout in sorted(root.iterdir()):
                if checkout.is_dir() and (checkout / ".git").exists():
                    repositories[checkout.name] = str(checkout)

        if github_token:
            headers = {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {github_token}",
            }
            page = 1
            while True:
                response = requests.get(
                    "https://api.github.com/user/repos",
                    headers=headers,
                    params={"per_page": 100, "page": page},
                    timeout=30,
                )
                if response.status_code != 200:
                    logger.warning("GitHub repository discovery failed: %s", response.status_code)
                    break
                page_repositories = response.json()
                if not isinstance(page_repositories, list):
                    break
                for repository in page_repositories:
                    name = repository.get("name")
                    location = repository.get("clone_url") or repository.get("html_url")
                    if isinstance(name, str) and isinstance(location, str):
                        repositories.setdefault(name, location)
                if len(page_repositories) < 100:
                    break
                page += 1

        return cls(repositories, github_token=github_token, last_updated=datetime.now().isoformat())


class RepositoryReadmeReader:
    """Read README files from repositories."""

    def __init__(self, registry: RepositoryRegistry, github_token: Optional[str] = None):
        """Initialize README reader.

        Args:
            registry: Repository registry with repo names and paths
            github_token: GitHub API token for remote access (optional)
        """
        self.registry = registry
        self.github_token = github_token or os.getenv("GITHUB_TOKEN")
        self._cache: Dict[str, RepositoryReadme] = {}
        self._cache_timestamps: Dict[str, datetime] = {}

    def _compute_hash(self, content: str) -> str:
        """Compute SHA256 hash of content."""
        import hashlib
        return hashlib.sha256(content.encode('utf-8')).hexdigest()

    def discover_readmes(self, refresh_cache: bool = False) -> List[RepositoryReadme]:
        """Discover README files in all accessible repositories.

        Args:
            refresh_cache: Force re-read even if cached

        Returns:
            List of discovered READMEs
        """
        readmes = []

        for repo_name, repo_location in self.registry.repositories.items():
            readmes.extend(self._discover_repo_readmes(repo_name, repo_location))

        return readmes

    def _discover_repo_readmes(
        self, repo_name: str, repo_location: str
    ) -> List[RepositoryReadme]:
        """Discover every README* file in one local or remote repository."""
        is_remote = repo_location.startswith(("http://", "https://", "git@", "ssh://"))
        try:
            if is_remote:
                return self._read_remote_readmes(repo_name, repo_location)
            return self._read_local_readmes(repo_name, repo_location)
        except Exception as exc:
            logger.warning("Failed to discover READMEs for %s: %s", repo_name, exc)
            return [self._error_readme(repo_name, repo_location, not is_remote, str(exc))]

    @staticmethod
    def _error_readme(
        repo_name: str, repo_location: str, is_local: bool, error: str,
        readme_path: str = "",
    ) -> RepositoryReadme:
        return RepositoryReadme(
            repo_name=repo_name,
            repo_path=repo_location,
            readme_path=readme_path,
            content="",
            content_hash="",
            last_read=datetime.now().isoformat(),
            is_local=is_local,
            error=error,
        )

    def _read_repo_readme(self, repo_name: str, repo_location: str,
                          force_refresh: bool = False) -> Optional[RepositoryReadme]:
        """Read README from a single repository.

        Args:
            repo_name: Repository name (for logging)
            repo_location: Path or GitHub URL
            force_refresh: Force re-read

        Returns:
            RepositoryReadme or None if error
        """
        # Check cache
        cache_key = f"{repo_name}:{repo_location}"
        if cache_key in self._cache and not force_refresh:
            cached = self._cache[cache_key]
            timestamp = self._cache_timestamps.get(cache_key)
            if timestamp and (datetime.now() - timestamp).total_seconds() < 86400:
                return cached

        # Determine if local or remote
        is_remote = repo_location.startswith(("http://", "https://", "git@", "ssh://"))
        is_local = not is_remote

        try:
            if is_local:
                readme = self._read_local_readme(repo_name, repo_location)
            else:
                readme = self._read_remote_readme(repo_name, repo_location)

        except Exception as e:
            logger.warning(f"Failed to read README for {repo_name}: {e}")
            readme = RepositoryReadme(
                repo_name=repo_name,
                repo_path=repo_location,
                readme_path="",
                content="",
                content_hash="",
                last_read=datetime.now().isoformat(),
                is_local=is_local,
                error=str(e),
            )

        self._cache[cache_key] = readme
        self._cache_timestamps[cache_key] = datetime.now()
        return readme

    def _read_local_readme(self, repo_name: str, repo_path: str) -> RepositoryReadme:
        """Read README from local repository.

        Args:
            repo_name: Repository name
            repo_path: Local filesystem path

        Returns:
            RepositoryReadme
        """
        readmes = self._read_local_readmes(repo_name, repo_path)
        for readme in readmes:
            if readme.readme_path == "README.md":
                return readme
        return readmes[0]

    def _read_local_readmes(self, repo_name: str, repo_path: str) -> List[RepositoryReadme]:
        """Read every README* file below a local repository without following symlinks."""
        root = Path(repo_path)
        if not root.exists() or not root.is_dir():
            return [self._error_readme(
                repo_name, repo_path, True,
                f"Repository path does not exist: {repo_path}",
            )]

        readmes: List[RepositoryReadme] = []

        def visit(directory: Path, relative: str = "") -> None:
            try:
                entries = sorted(os.scandir(directory), key=lambda entry: entry.name.lower())
            except PermissionError as exc:
                readmes.append(self._error_readme(
                    repo_name, repo_path, True,
                    f"Permission denied reading directory {relative or '.'}: {exc}",
                    relative,
                ))
                return
            except OSError as exc:
                readmes.append(self._error_readme(
                    repo_name, repo_path, True,
                    f"Unable to read directory {relative or '.'}: {exc}",
                    relative,
                ))
                return

            for entry in entries:
                entry_path = f"{relative}/{entry.name}" if relative else entry.name
                try:
                    if entry.is_symlink():
                        if entry.name.lower().startswith("readme"):
                            readmes.append(self._error_readme(
                                repo_name, repo_path, True,
                                "README resolves through a symlink", entry_path,
                            ))
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        visit(Path(entry.path), entry_path)
                    elif entry.name.lower().startswith("readme") and entry.is_file(follow_symlinks=False):
                        try:
                            content = self._read_local_file_no_follow(directory, entry.name)
                        except PermissionError as exc:
                            readmes.append(self._error_readme(
                                repo_name, repo_path, True,
                                f"Permission denied reading {entry_path}: {exc}", entry_path,
                            ))
                            continue
                        except OSError as exc:
                            readmes.append(self._error_readme(
                                repo_name, repo_path, True,
                                f"Unable to read {entry_path}: {exc}", entry_path,
                            ))
                            continue
                        readmes.append(RepositoryReadme(
                            repo_name=repo_name,
                            repo_path=repo_path,
                            readme_path=entry_path,
                            content=content,
                            content_hash=self._compute_hash(content),
                            last_read=datetime.now().isoformat(),
                            is_local=True,
                        ))
                except PermissionError as exc:
                    readmes.append(self._error_readme(
                        repo_name, repo_path, True,
                        f"Permission denied inspecting {entry_path}: {exc}", entry_path,
                    ))
                except OSError as exc:
                    readmes.append(self._error_readme(
                        repo_name, repo_path, True,
                        f"Unable to inspect {entry_path}: {exc}", entry_path,
                    ))

        visit(root)
        if not readmes:
            readmes.append(self._error_readme(repo_name, repo_path, True, "No README file found"))
        return readmes

    @staticmethod
    def _read_local_file_no_follow(repository: Path, name: str) -> str:
        """Read a repository child through descriptors without symlink follows."""
        root_fd = RepositoryReadmeReader._open_directory_no_follow(repository)
        try:
            file_fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=root_fd)
        finally:
            os.close(root_fd)
        try:
            file_stat = os.fstat(file_fd)
            if not stat.S_ISREG(file_stat.st_mode):
                raise OSError(20, "Not a regular file")
            chunks = []
            while chunk := os.read(file_fd, 1024 * 1024):
                chunks.append(chunk)
            return b"".join(chunks).decode("utf-8")
        finally:
            os.close(file_fd)

    @staticmethod
    def _open_directory_no_follow(path: Path) -> int:
        """Open an absolute or relative directory without symlink follows."""
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        parts = list(path.parts)
        if path.is_absolute():
            fd = os.open(path.anchor or "/", directory_flags | nofollow)
            parts = [part for part in parts if part not in {path.anchor, ""}]
        else:
            fd = os.open(".", directory_flags | nofollow)
        try:
            for part in parts:
                next_fd = os.open(part, directory_flags | nofollow, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            return fd
        except Exception:
            os.close(fd)
            raise

    def _read_remote_readmes(self, repo_name: str, repo_url: str) -> List[RepositoryReadme]:
        """Read every README* blob from a GitHub repository tree."""
        if not self.github_token:
            return [self._error_readme(
                repo_name, repo_url, False, "GitHub token not configured",
            )]

        owner, repo = self._github_owner_repo(repo_url)
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.github_token}",
        }
        repository_url = f"https://api.github.com/repos/{owner}/{repo}"
        repository_response = requests.get(repository_url, headers=headers, timeout=30)
        if repository_response.status_code != 200:
            return [self._error_readme(
                repo_name, repo_url, False,
                f"GitHub repository lookup failed: HTTP {repository_response.status_code}",
            )]
        default_branch = repository_response.json().get("default_branch")
        if not isinstance(default_branch, str) or not default_branch:
            return [self._error_readme(repo_name, repo_url, False, "GitHub repository has no default branch")]

        tree_url = f"{repository_url}/git/trees/{quote(default_branch, safe='')}"
        tree_response = requests.get(
            tree_url, headers=headers, params={"recursive": "1"}, timeout=30,
        )
        if tree_response.status_code != 200:
            return [self._error_readme(
                repo_name, repo_url, False,
                f"GitHub tree lookup failed: HTTP {tree_response.status_code}",
            )]

        readmes: List[RepositoryReadme] = []
        tree = tree_response.json().get("tree", [])
        for item in tree:
            path = item.get("path", "")
            if item.get("type") != "blob" or not Path(path).name.lower().startswith("readme"):
                continue
            blob_url = item.get("url")
            if not isinstance(blob_url, str):
                readmes.append(self._error_readme(
                    repo_name, repo_url, False, "GitHub tree entry has no blob URL", path,
                ))
                continue
            blob_response = requests.get(blob_url, headers=headers, timeout=30)
            if blob_response.status_code != 200:
                readmes.append(self._error_readme(
                    repo_name, repo_url, False,
                    f"GitHub README lookup failed: HTTP {blob_response.status_code}", path,
                ))
                continue
            payload = blob_response.json()
            try:
                content = base64.b64decode(payload["content"]).decode("utf-8")
            except (KeyError, ValueError, UnicodeDecodeError) as exc:
                readmes.append(self._error_readme(
                    repo_name, repo_url, False, f"Invalid GitHub README content: {exc}", path,
                ))
                continue
            readmes.append(RepositoryReadme(
                repo_name=repo_name,
                repo_path=repo_url,
                readme_path=path,
                content=content,
                content_hash=self._compute_hash(content),
                last_read=datetime.now().isoformat(),
                is_local=False,
            ))

        if not readmes:
            readmes.append(self._error_readme(repo_name, repo_url, False, "No README file found"))
        return readmes

    @staticmethod
    def _github_owner_repo(repo_url: str) -> tuple[str, str]:
        if repo_url.startswith("git@"):
            path = repo_url.split(":", 1)[1]
        else:
            parsed = urlparse(repo_url)
            path = parsed.path if parsed.scheme and parsed.netloc else repo_url
        path = path.strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        owner, repo = path.split("/", 1)
        return owner, repo

    def _read_remote_readme(self, repo_name: str, repo_url: str) -> RepositoryReadme:
        """Read README from remote GitHub repository via API.

        Args:
            repo_name: Repository name
            repo_url: GitHub URL (e.g., "https://github.com/user/repo")

        Returns:
            RepositoryReadme
        """
        if not self.github_token:
            return RepositoryReadme(
                repo_name=repo_name,
                repo_path=repo_url,
                readme_path="",
                content="",
                content_hash="",
                last_read=datetime.now().isoformat(),
                is_local=False,
                error="GitHub token not configured",
            )

        # Parse repo from URL
        try:
            if repo_url.startswith("git@"):
                # SSH format: git@github.com:user/repo.git
                path = repo_url.split(":", 1)[1]
            else:
                parsed = urlparse(repo_url)
                path = parsed.path if parsed.scheme and parsed.netloc else repo_url
            path = path.strip("/")
            if path.endswith(".git"):
                path = path[:-4]
            owner, repo = path.split("/", 1)

        except Exception as e:
            return RepositoryReadme(
                repo_name=repo_name,
                repo_path=repo_url,
                readme_path="",
                content="",
                content_hash="",
                last_read=datetime.now().isoformat(),
                is_local=False,
                error=f"Failed to parse GitHub URL: {e}",
            )

        # Use GitHub API
        url = f"https://api.github.com/repos/{owner}/{repo}/readme"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"token {self.github_token}",
        }

        try:
            response = requests.get(url, headers=headers, timeout=30)

            if response.status_code == 404:
                return RepositoryReadme(
                    repo_name=repo_name,
                    repo_path=repo_url,
                    readme_path="",
                    content="",
                    content_hash="",
                    last_read=datetime.now().isoformat(),
                    is_local=False,
                    error="Repository not found",
                )

            if response.status_code != 200:
                return RepositoryReadme(
                    repo_name=repo_name,
                    repo_path=repo_url,
                    readme_path="",
                    content="",
                    content_hash="",
                    last_read=datetime.now().isoformat(),
                    is_local=False,
                    error=f"GitHub API error: {response.status_code}",
                )

            content = base64.b64decode(response.json()['content']).decode('utf-8')

            return RepositoryReadme(
                repo_name=repo_name,
                repo_path=repo_url,
                readme_path="README.md",
                content=content,
                content_hash=self._compute_hash(content),
                last_read=datetime.now().isoformat(),
                is_local=False,
            )

        except Exception as e:
            return RepositoryReadme(
                repo_name=repo_name,
                repo_path=repo_url,
                readme_path="",
                content="",
                content_hash="",
                last_read=datetime.now().isoformat(),
                is_local=False,
                error=f"Failed to fetch README: {e}",
            )

    def get_readme(self, repo_name: str, force_refresh: bool = False) -> Optional[RepositoryReadme]:
        """Get README for a specific repository.

        Args:
            repo_name: Repository name
            force_refresh: Force re-read

        Returns:
            RepositoryReadme or None if not found
        """
        if repo_name not in self.registry.repositories:
            return None

        location = self.registry.repositories[repo_name]
        return self._read_repo_readme(repo_name, location, force_refresh)

    def get_all_readmes(self) -> Dict[str, str]:
        """Get all discovered READMEs as a dictionary.

        Returns:
            Dict mapping repo_name -> README content
        """
        readmes = self.discover_readmes()
        return {
            readme.repo_name: readme.content
            for readme in readmes
            if readme.content and not readme.error
        }

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all discovered READMEs.

        Returns:
            Summary dict with repo info and content previews
        """
        readmes = self.discover_readmes()

        summary = {
            "timestamp": datetime.now().isoformat(),
            "total_repos": len(self.registry.repositories),
            "successful": 0,
            "failed": 0,
            "readmes": [],
        }

        for readme in readmes:
            if readme.error:
                summary["failed"] += 1
                summary["readmes"].append({
                    "repo": readme.repo_name,
                    "status": "error",
                    "error": readme.error,
                })
            else:
                summary["successful"] += 1
                summary["readmes"].append({
                    "repo": readme.repo_name,
                    "status": "ok",
                    "path": readme.repo_path,
                    "readme_file": readme.readme_path,
                    "size": len(readme.content),
                    "preview": readme.content[:500] + "..." if len(readme.content) > 500 else readme.content,
                })

        return summary
