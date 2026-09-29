"""Migration scripts for gtx-broker database schema and data updates."""

import sqlite3
from pathlib import Path
from typing import Optional


def migrate_worker_model_profiles(db_path: Path) -> int:
    """Migrate old model_profile values to new canonical names.
    
    This is a data migration for workers created before the model_profile
    naming convention was standardized. It updates the model_profile field
    for specific worker profiles without touching other fields.
    
    Args:
        db_path: Path to SQLite database
        
    Returns:
        Number of rows updated
    """
    conn = sqlite3.connect(str(db_path))
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
    
    conn.commit()
    conn.close()
    
    return updated


def migrate_worker_model_profiles_safe(db_path: Path, dry_run: bool = False) -> int:
    """Migrate worker model profiles with logging.
    
    Args:
        db_path: Path to SQLite database
        dry_run: If True, only log what would be updated
        
    Returns:
        Number of rows that would be updated
    """
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    updated = 0
    
    # Check existing values
    cursor.execute("""
        SELECT profile, model_profile 
        FROM workers 
        WHERE model_profile IN ('p40-coding', 'p40-vision-qwen35', 'p40-vision', 'p40-qwen35-coding')
    """)
    old_values = cursor.fetchall()
    
    if old_values:
        print(f"Found {len(old_values)} workers with old model_profile values:")
        for profile, mp in old_values:
            print(f"  {profile}: {mp}")
    
    if dry_run:
        return len(old_values)
    
    # Perform migration
    updated += migrate_worker_model_profiles(db_path)
    
    conn.close()
    return updated
