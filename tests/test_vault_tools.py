#!/usr/bin/env python3
"""Tests for vault and repository tools."""

import json
import os
import sys
import tempfile
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from gtx_broker.vault_readers import ObsidianVault, VaultSecurityError


def test_vault_reader():
    """Test vault reader functionality."""
    # Create temporary vault
    with tempfile.TemporaryDirectory() as tmpdir:
        vault_path = Path(tmpdir) / "vault"
        vault_path.mkdir()
        
        # Create test notes
        (vault_path / "test.md").write_text("Test content")
        (vault_path / "notes").mkdir()
        (vault_path / "notes" / "nested.md").write_text("Nested content")
        
        # Create broker directory
        broker_dir = vault_path / "AI" / "GTX-Broker"
        broker_dir.mkdir(parents=True)
        
        # Initialize vault
        vault = ObsidianVault(str(vault_path), "AI/GTX-Broker")
        
        # Test read
        result = vault.read_note("test.md")
        assert result.success, f"Failed to read: {result.error}"
        assert result.content == "Test content"
        print("✓ Read note: PASS")
        
        # Test nested read
        result = vault.read_note("notes/nested.md")
        assert result.success, f"Failed to read nested: {result.error}"
        assert result.content == "Nested content"
        print("✓ Read nested note: PASS")
        
        # Test write to broker directory
        result = vault.write_note("AI/GTX-Broker/test.md", "Wrote content")
        assert result.success, f"Failed to write: {result.error}"
        print("✓ Write to broker dir: PASS")
        
        # Verify write
        result = vault.read_note("AI/GTX-Broker/test.md")
        assert result.success
        assert result.content == "Wrote content"
        print("✓ Verify write: PASS")
        
        # Test search
        results = vault.search_vault("*.md")
        assert len(results) >= 3, f"Expected 3+ .md files, got {len(results)}"
        print("✓ Search vault: PASS")
        
        # Test path escape prevention (absolute path outside vault)
        print("Testing absolute path escape...")
        result = vault.read_note("/etc/passwd")
        if result.success:
            print("ERROR: No error returned!")
            assert False, "Should have returned VaultOperationResult with success=False"
        elif "Absolute paths are not allowed" in result.error:
            print(f"✓ Absolute path escape prevention: PASS (got expected error: {result.error})")
        else:
            print(f"ERROR: Got unexpected error: {result.error}")
            assert False, f"Expected 'Absolute paths are not allowed' but got: {result.error}"
        
        # Test symlink escape prevention
        symlink_path = vault_path / "escape.md"
        symlink_path.symlink_to("/etc/passwd")
        result = vault.read_note("escape.md")
        if result.success:
            print("ERROR: No error returned for symlink!")
            assert False, "Should have returned VaultOperationResult with success=False"
        elif "outside vault" in result.error:
            print(f"✓ Symlink escape prevention: PASS (got expected error: {result.error})")
        else:
            print(f"ERROR: Got unexpected error: {result.error}")
            assert False, f"Expected 'outside vault' in error but got: {result.error}"
        
        print("\nAll vault tests passed!")


if __name__ == "__main__":
    test_vault_reader()
