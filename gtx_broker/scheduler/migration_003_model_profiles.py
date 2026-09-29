"""Migration 003: Worker model_profile migration."""

import sqlite3
from pathlib import Path


def migrate_worker_model_profiles(conn: sqlite3.Connection) -> int:
    """Migrate old model_profile values to new canonical names.
    
    This migration updates the model_profile field for specific worker profiles
    without touching other fields.
    
    Args:
        conn: SQLite database connection
        
    Returns:
        Number of rows updated
    """
    cursor = conn.cursor()
    
    updated = 0
    
    # Migrate p40-coding from old names to qwen35-coding
    cursor.execute("""
        UPDATE workers
        SET model_profile = 'qwen35-coding'
        WHERE profile = 'p40-coding'
        AND model_profile IN ('p40-coding', 'p40-qwen35-coding', 'qwen35-coding-old')
    """)
    updated += cursor.rowcount
    if cursor.rowcount > 0:
        print(f"Updated {cursor.rowcount} p40-coding workers to qwen35-coding")
    
    # Migrate p40-vision from old names to qwen35-vision
    cursor.execute("""
        UPDATE workers
        SET model_profile = 'qwen35-vision'
        WHERE profile = 'p40-vision'
        AND model_profile IN ('p40-vision-qwen35', 'p40-vision', 'qwen35-vision-old')
    """)
    updated += cursor.rowcount
    if cursor.rowcount > 0:
        print(f"Updated {cursor.rowcount} p40-vision workers to qwen35-vision")
    
    return updated
