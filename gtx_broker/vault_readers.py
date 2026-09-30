"""Obsidian vault reader and scoped writer for GTX broker.

Implements read-only access to entire vault with scoped write access to a
dedicated broker directory. Enforces path boundaries at filesystem level
and prevents traversal attacks.

Security boundaries:
- Read: entire vault, but never modifies
- Write: only to broker-owned subdirectory
- Path canonicalization prevents traversal attacks
- Symlink rejection prevents escape via links
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger(__name__)


class VaultSecurityError(ValueError):
    """Raised when a vault operation violates security boundaries."""
    pass


class VaultNotFoundError(FileNotFoundError):
    """Raised when a vault path does not exist."""
    pass


@dataclass(frozen=True)
class VaultOperationResult:
    """Result of a vault operation."""
    success: bool
    path: str
    content: Optional[str] = None
    error: Optional[str] = None
    bytes_read: int = 0
    is_cached: bool = False


@dataclass(frozen=True)
class VaultFileMetadata:
    """Metadata about a vault file."""
    path: str
    relative_path: str
    size_bytes: int
    modified_at: str  # ISO format
    is_markdown: bool
    content_hash: str


class ObsidianVaultReader:
    """Read-only access to Obsidian vault with security boundaries.
    
    Provides safe traversal and reading of vault files while preventing
    path traversal attacks and enforcing read-only semantics.
    """
    
    def __init__(self, vault_path: str, write_dir: str = "AI/GTX-Broker"):
        """Initialize vault reader.
        
        Args:
            vault_path: Absolute path to Obsidian vault root
            write_dir: Relative path within vault for broker writes (e.g., "AI/GTX-Broker")
        """
        self.vault_root = Path(vault_path).resolve(strict=False)
        self.write_dir = write_dir  # Keep as relative
        
        # Validate vault exists
        if not self.vault_root.exists():
            raise VaultNotFoundError(f"Vault does not exist: {vault_path}")
        
        # Validate write directory is within vault
        try:
            self.write_dir_resolved = self.vault_root / self.write_dir
            self.write_dir_resolved.mkdir(parents=True, exist_ok=True)
            # Verify write dir is actually under vault root
            self.write_dir_resolved.relative_to(self.vault_root)
        except ValueError as e:
            raise ValueError(
                f"Write directory {write_dir} is not within vault {vault_path}: {e}"
            )
        
        logger.info("Initialized ObsidianVaultReader for %s (write dir: %s)",
                   vault_path, self.write_dir)
    
    def _canonicalize_path(self, path: str) -> Path:
        """Canonicalize path and verify it's within vault.
        
        Args:
            path: Path string (can be relative or absolute)
            
        Returns:
            Absolute canonical path
            
        Raises:
            VaultSecurityError: If path escapes vault boundaries
        """
        # Handle absolute paths - must be within vault, reject all absolute paths
        p = Path(path)
        if p.is_absolute():
            raise VaultSecurityError(
                f"Absolute paths are not allowed: {path}"
            )
        
        # Handle relative paths - resolve from vault root
        candidate = (self.vault_root / p).resolve()
        
        # Verify candidate is within vault
        try:
            candidate.relative_to(self.vault_root)
            return candidate
        except ValueError:
            raise VaultSecurityError(
                f"Path {path} resolves outside vault: {candidate}"
            )
    
    def _validate_no_symlink_escape(self, path: Path) -> None:
        """Validate that path and all components are not symlinks escaping vault.
        
        Args:
            path: Path to validate
            
        Raises:
            VaultSecurityError: If path contains symlinks that escape vault
        """
        # Check the path itself
        if path.is_symlink():
            real_path = path.resolve()
            try:
                real_path.relative_to(self.vault_root)
            except ValueError:
                raise VaultSecurityError(
                    f"Symlink {path} resolves outside vault: {real_path}"
                )
        
        # Check all parent components
        for parent in path.parents:
            if parent.is_symlink():
                real_parent = parent.resolve()
                try:
                    real_parent.relative_to(self.vault_root)
                except ValueError:
                    raise VaultSecurityError(
                        f"Symlink parent {parent} resolves outside vault: {real_parent}"
                    )
    
    def _get_relative_path(self, path: Path) -> str:
        """Get relative path from vault root.
        
        Args:
            path: Absolute path
            
        Returns:
            Relative path string
        """
        return str(path.relative_to(self.vault_root))
    
    def read_file(self, path: str) -> VaultOperationResult:
        """Read file content from vault.
        
        Args:
            path: Path relative to vault root (e.g., "notes/example.md")
            
        Returns:
            VaultOperationResult with content or error
        """
        try:
            # Canonicalize and validate
            abs_path = self._canonicalize_path(path)
            
            # Check for symlink escape
            self._validate_no_symlink_escape(abs_path)
            
            # Check file exists and is not a directory
            if not abs_path.exists():
                return VaultOperationResult(
                    success=False,
                    path=self._get_relative_path(abs_path),
                    error=f"File not found: {path}"
                )
            
            if abs_path.is_dir():
                return VaultOperationResult(
                    success=False,
                    path=self._get_relative_path(abs_path),
                    error=f"Path is a directory: {path}"
                )
            
            # Check file size (limit to 1MB for safety)
            file_size = abs_path.stat().st_size
            if file_size > 1024 * 1024:
                return VaultOperationResult(
                    success=False,
                    path=self._get_relative_path(abs_path),
                    error=f"File too large: {file_size} bytes (max 1MB)"
                )
            
            # Read content
            content = abs_path.read_text(encoding='utf-8')
            
            return VaultOperationResult(
                success=True,
                path=self._get_relative_path(abs_path),
                content=content,
                bytes_read=file_size,
            )
            
        except VaultSecurityError as e:
            return VaultOperationResult(
                success=False,
                path=path,
                error=str(e)
            )
        except Exception as e:
            return VaultOperationResult(
                success=False,
                path=path,
                error=f"Read error: {e}"
            )
    
    def get_file_metadata(self, path: str) -> Optional[VaultFileMetadata]:
        """Get metadata about a vault file.
        
        Args:
            path: Path relative to vault root
            
        Returns:
            VaultFileMetadata if found, None otherwise
        """
        try:
            abs_path = self._canonicalize_path(path)
            
            if not abs_path.exists() or abs_path.is_dir():
                return None
            
            # Get relative path
            rel_path = self._get_relative_path(abs_path)
            
            # Check if markdown
            is_markdown = abs_path.suffix.lower() in {'.md', '.markdown', '.mdown'}
            
            # Calculate content hash
            content_hash = hashlib.sha256(abs_path.read_bytes()).hexdigest()
            
            # Get modification time
            mtime = abs_path.stat().st_mtime
            modified_at = datetime.fromtimestamp(mtime, timezone.utc).isoformat()
            
            return VaultFileMetadata(
                path=str(abs_path),
                relative_path=rel_path,
                size_bytes=abs_path.stat().st_size,
                modified_at=modified_at,
                is_markdown=is_markdown,
                content_hash=content_hash,
            )
            
        except Exception:
            return None
    
    def list_directory(self, path: str = "") -> List[str]:
        """List contents of a directory in the vault.
        
        Args:
            path: Path relative to vault root (default: root)
            
        Returns:
            List of relative paths to files and directories
        """
        try:
            abs_path = self._canonicalize_path(path)
            
            if not abs_path.exists() or not abs_path.is_dir():
                return []
            
            # Reject symlinks to directories
            if abs_path.is_symlink():
                return []
            
            entries = []
            for item in abs_path.iterdir():
                # Skip symlinks
                if item.is_symlink():
                    continue
                
                rel_path = self._get_relative_path(item)
                entries.append(rel_path)
            
            return sorted(entries)
            
        except Exception:
            return []
    
    def search_files(self, pattern: str, extension: Optional[str] = None) -> List[str]:
        """Search for files matching a pattern.
        
        Args:
            pattern: Glob pattern (e.g., "*.md", "notes/*")
            extension: Optional file extension filter
            
        Returns:
            List of matching relative paths
        """
        try:
            # Search from vault root
            search_path = self.vault_root / pattern
            
            results = []
            for file_path in search_path.parent.rglob(pattern):
                if file_path.is_symlink():
                    continue
                
                # Check extension if specified
                if extension and file_path.suffix.lower() != extension:
                    continue
                
                results.append(self._get_relative_path(file_path))
            
            return sorted(results)
            
        except Exception:
            return []
    
    def write_file(self, path: str, content: str, encoding: str = "utf-8") -> VaultOperationResult:
        """Write content to broker's dedicated directory.
        
        Args:
            path: Relative path within broker write directory
            content: Content to write
            encoding: File encoding (default: utf-8)
            
        Returns:
            VaultOperationResult indicating success or failure
        """
        try:
            # Canonicalize path
            abs_path = self._canonicalize_path(path)
            
            # Verify path is within broker write directory
            try:
                abs_path.relative_to(self.write_dir_resolved)
            except ValueError:
                return VaultOperationResult(
                    success=False,
                    path=path,
                    error=f"Path {path} is outside broker write directory {self.write_dir}"
                )
            
            # Check for symlink escape
            self._validate_no_symlink_escape(abs_path)
            
            # Ensure parent directory exists
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Atomic write: write to temp file, then rename
            temp_file = abs_path.with_suffix(f".{abs_path.suffix}.tmp")
            temp_file.write_text(content, encoding=encoding)
            
            # Atomic rename
            temp_file.rename(abs_path)
            
            return VaultOperationResult(
                success=True,
                path=self._get_relative_path(abs_path),
                bytes_read=len(content.encode(encoding)),
            )
            
        except VaultSecurityError as e:
            return VaultOperationResult(
                success=False,
                path=path,
                error=str(e)
            )
        except Exception as e:
            return VaultOperationResult(
                success=False,
                path=path,
                error=f"Write error: {e}"
            )


class ObsidianVault:
    """High-level Obsidian vault interface for GTX broker.
    
    Combines read and write operations with search and metadata capabilities.
    """
    
    def __init__(self, vault_path: str, write_dir: str = "AI/GTX-Broker"):
        """Initialize vault.
        
        Args:
            vault_path: Path to Obsidian vault
            write_dir: Relative path for broker writes
        """
        self.reader = ObsidianVaultReader(vault_path, write_dir)
        self.vault_path = vault_path
        self.write_dir = write_dir
    
    def read_note(self, note_path: str) -> VaultOperationResult:
        """Read a vault note.
        
        Args:
            note_path: Path to note (e.g., "AI/Broker/note.md")
            
        Returns:
            VaultOperationResult with content or error
        """
        return self.reader.read_file(note_path)
    
    def write_note(self, note_path: str, content: str) -> VaultOperationResult:
        """Write a vault note to broker directory.
        
        Args:
            note_path: Path within broker directory (e.g., "AI/Broker/note.md")
            content: Note content
            
        Returns:
            VaultOperationResult indicating success or failure
        """
        return self.reader.write_file(note_path, content)
    
    def search_vault(self, query: str, extension: str = ".md") -> List[str]:
        """Search vault for files matching query.
        
        Args:
            query: Search pattern (glob-style)
            extension: File extension filter (default: .md)
            
        Returns:
            List of matching relative paths
        """
        return self.reader.search_files(query, extension)
    
    def list_notes(self, directory: str = "") -> List[str]:
        """List all markdown notes in a directory.
        
        Args:
            directory: Directory path relative to vault root
            
        Returns:
            List of .md file relative paths
        """
        # List directory contents
        entries = self.reader.list_directory(directory)
        
        # Filter to markdown files only
        return [
            entry for entry in entries
            if entry.endswith(('.md', '.markdown', '.mdown'))
        ]
    
    def get_note_metadata(self, note_path: str) -> Optional[VaultFileMetadata]:
        """Get metadata about a vault note.
        
        Args:
            note_path: Path to note
            
        Returns:
            VaultFileMetadata or None
        """
        return self.reader.get_file_metadata(note_path)
