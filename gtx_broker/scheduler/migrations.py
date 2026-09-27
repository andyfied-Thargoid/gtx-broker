"""Database migration runner for scheduler."""

import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class MigrationRunner:
    """Handles database migrations for the scheduler."""

    def __init__(self, db_path: str):
        """Initialize migration runner.

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path
        self._migrations = [
            "001_add_tagging",
            # Add more migrations here as needed
        ]

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_applied_migrations(self) -> set:
        """Get list of applied migrations."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='schema_migrations'
            """)
            if cursor.fetchone():
                cursor.execute("SELECT migration_name FROM schema_migrations")
                migrations = {row[0] for row in cursor.fetchall()}
                conn.close()
                return migrations
            conn.close()
        except sqlite3.OperationalError:
            pass
        return set()

    def _ensure_migrations_table(self):
        """Create schema_migrations table if it doesn't exist."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_name TEXT PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

    def run_migration(self, migration_name: str) -> bool:
        """Run a single migration.

        Args:
            migration_name: Name of migration file (without .sql extension)
            
        Returns:
            True if migration applied successfully, False on error
        """
        migrations_dir = Path(__file__).parent / "migrations"
        migration_path = migrations_dir / f"{migration_name}.sql"

        if not migration_path.exists():
            # Check with .sql extension
            migration_path_with_ext = migrations_dir / f"{migration_name}.sql"
            if not migration_path_with_ext.exists():
                logger.error(f"Migration file not found: {migration_path}")
                return False
            migration_path = migration_path_with_ext

        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # Execute each statement separately (SQLite limitation)
            with open(migration_path, 'r') as f:
                sql = f.read()
            
            for statement in sql.split(';'):
                statement = statement.strip()
                if statement:
                    cursor.execute(statement)
            
            cursor.execute(
                "INSERT INTO schema_migrations (migration_name) VALUES (?)",
                (migration_name,)
            )
            conn.commit()

            logger.info(f"Applied migration: {migration_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to apply migration {migration_name}: {e}")
            return False
        finally:
            if conn is not None:
                conn.close()

    def run_all(self) -> bool:
        """Run all pending migrations.

        Returns:
            True if all migrations applied successfully
        """
        # Ensure migrations table exists
        self._ensure_migrations_table()

        # Get applied migrations
        applied = self._get_applied_migrations()

        # Apply pending migrations
        success = True
        for migration_name in self._migrations:
            if migration_name not in applied:
                if not self.run_migration(migration_name):
                    success = False
                    break

        return success
