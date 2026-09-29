"""Database migration 003: Add migration tracking table."""

import sqlite3
from pathlib import Path


def create_migration_tracking_table(db_path: Path) -> None:
    """Create migration tracking table if it doesn't exist.
    
    Args:
        db_path: Path to SQLite database
    """
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS migrations (
            id INTEGER PRIMARY KEY,
            version INTEGER UNIQUE NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()


def get_applied_migrations(db_path: Path) -> list:
    """Get list of applied migrations.
    
    Args:
        db_path: Path to SQLite database
        
    Returns:
        List of applied migration versions
    """
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    cursor.execute("SELECT version FROM migrations ORDER BY version")
    versions = [row[0] for row in cursor.fetchall()]
    
    conn.close()
    return versions


def apply_migration(db_path: Path, version: int, migration_fn) -> None:
    """Apply a single migration.
    
    Args:
        db_path: Path to SQLite database
        version: Migration version number
        migration_fn: Migration function to execute
    """
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    try:
        # Check if already applied
        cursor.execute("SELECT 1 FROM migrations WHERE version = ?", (version,))
        if cursor.fetchone():
            print(f"Migration {version} already applied")
            return
        
        # Apply migration
        migration_fn(conn)
        
        # Record as applied
        cursor.execute("INSERT INTO migrations (version) VALUES (?)", (version,))
        conn.commit()
        print(f"Applied migration {version}")
        
    finally:
        conn.close()
