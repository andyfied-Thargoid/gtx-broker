"""Repository README discovery and reading for GTX broker.

Provides discovery and reading of README files from all accessible repositories.
Supports both local checkouts and remote GitHub repositories via API.
"""

from __future__ import annotations

import json
import logging
import os
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
    def compute01_defaults(cls, source_root: str = "/home/andyfied/src") -> "RepositoryRegistry":
        """Return default repository registry for compute01 environment."""
        return cls({
            "gtx-broker": f"{source_root}/gtx-broker",
            "workstation": f"{source_root}/workstation",
            "telegram-chat-bot": f"{source_root}/telegram-chat-bot",
            "esp32-s3-monitor": f"{source_root}/esp32-s3-monitor",
            "WeatherPanel": f"{source_root}/WeatherPanel",
        })


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
            readme = self._read_repo_readme(repo_name, repo_location, refresh_cache)
            if readme and not readme.error:
                readmes.append(readme)
        
        return readmes
    
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
            if cached.error:
                return None
            # Check cache freshness (24 hours)
            if cache_key in self._cache_timestamps:
                age = datetime.now() - self._cache_timestamps[cache_key]
                if age.total_seconds() < 86400:
                    return cached
        
        # Determine if local or remote
        is_local = repo_location.startswith('/') or (
            '/' in repo_location and not repo_location.startswith('http')
        )
        
        try:
            if is_local:
                return self._read_local_readme(repo_name, repo_location)
            else:
                return self._read_remote_readme(repo_name, repo_location)
                
        except Exception as e:
            logger.warning(f"Failed to read README for {repo_name}: {e}")
            return RepositoryReadme(
                repo_name=repo_name,
                repo_path=repo_location,
                readme_path="",
                content="",
                content_hash="",
                last_read=datetime.now().isoformat(),
                is_local=is_local,
                error=str(e),
            )
    
    def _read_local_readme(self, repo_name: str, repo_path: str) -> RepositoryReadme:
        """Read README from local repository.
        
        Args:
            repo_name: Repository name
            repo_path: Local filesystem path
            
        Returns:
            RepositoryReadme
        """
        path = Path(repo_path)
        
        # Check if path exists and is a directory
        if not path.exists() or not path.is_dir():
            return RepositoryReadme(
                repo_name=repo_name,
                repo_path=repo_path,
                readme_path="",
                content="",
                content_hash="",
                last_read=datetime.now().isoformat(),
                is_local=True,
                error=f"Repository path does not exist: {repo_path}",
            )
        
        # Try common README filenames in order of preference
        readme_names = [
            "README.md", "README.markdown", "README.mdown",
            "readme.md", "readme.markdown",
            "README.txt", "README.rst"
        ]
        
        for name in readme_names:
            readme_file = path / name
            if readme_file.exists() and readme_file.is_file():
                try:
                    content = readme_file.read_text(encoding='utf-8')
                    return RepositoryReadme(
                        repo_name=repo_name,
                        repo_path=repo_path,
                        readme_path=name,
                        content=content,
                        content_hash=self._compute_hash(content),
                        last_read=datetime.now().isoformat(),
                        is_local=True,
                    )
                except Exception as e:
                    continue
        
        return RepositoryReadme(
            repo_name=repo_name,
            repo_path=repo_path,
            readme_path="",
            content="",
            content_hash="",
            last_read=datetime.now().isoformat(),
            is_local=True,
            error="No README file found",
        )
    
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
            # Handle both https and SSH URLs
            if repo_url.startswith('git@'):
                # SSH format: git@github.com:user/repo.git
                path = repo_url.split(':')[1].replace('.git', '')
            elif repo_url.endswith('.git'):
                # HTTPS with .git suffix
                path = repo_url.split('/')[-1].replace('.git', '')
            else:
                # Direct path
                path = repo_url.split('/')[-1]
            
            owner, repo = path.split('/')
            
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
            
            # Decode base64 content
            import base64
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
