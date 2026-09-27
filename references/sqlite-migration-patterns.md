# SQLite Database Migration Patterns for GTX Scheduler

## Overview

This document captures the database migration system implemented for the GTX scheduler, including connection leak fixes, schema upgrade patterns, and testing strategies.

## Critical Patterns

### 1. Idempotent Migration Execution

Migrations must be safe to run multiple times:

```sql
-- Idempotent migration
CREATE TABLE IF NOT EXISTS batch_epochs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP NOT NULL,
    review_barrier TIMESTAMP,
    review_triggered INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes with IF NOT EXISTS
CREATE INDEX IF NOT EXISTS idx_tasks_review_tag ON tasks(review_tag);
CREATE INDEX IF NOT EXISTS idx_tasks_schedule_type ON tasks(schedule_type);
CREATE INDEX IF NOT EXISTS idx_tasks_batch_epoch ON tasks(batch_epoch_id);
```

### 2. Column Addition for Existing Databases

SQLite doesn't support `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. Use PRAGMA to check first:

```python
def _upgrade_columns_if_missing(self) -> None:
    """Add missing columns to existing tasks table."""
    conn = self._get_connection()
    cursor = conn.cursor()
    
    # Check current columns
    cursor.execute("PRAGMA table_info(tasks)")
    current_columns = {row[1] for row in cursor.fetchall()}
    
    # Add missing columns one at a time
    new_columns = [
        ("review_tag", "TEXT"),
        ("schedule_type", "TEXT"),
        ("batch_epoch_id", "TEXT"),
    ]
    
    for col_name, col_type in new_columns:
        if col_name not in current_columns:
            cursor.execute(f"ALTER TABLE tasks ADD COLUMN {col_name} {col_type}")
            logger.info(f"Added column {col_name} to tasks table")
    
    conn.commit()
    conn.close()
```

**Order matters**: Call `_upgrade_columns_if_missing()` BEFORE running migrations. Migration SQL references columns that may not exist yet.

### 3. Connection Leak Prevention

**Pattern 1: Finally block for cleanup**

```python
def run_migration(self, migration_name: str) -> bool:
    conn = self._get_connection()
    try:
        cursor = conn.cursor()
        
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
        return True
        
    except Exception as e:
        logger.error(f"Failed to apply migration {migration_name}: {e}")
        return False
    finally:
        conn.close()  # Always close, even on error
```

**Pattern 2: Close before return**

```python
def _get_applied_migrations(self) -> set:
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
            conn.close()  # CRITICAL: Close BEFORE return
            return migrations
        
        conn.close()
    except sqlite3.OperationalError:
        pass
    return set()
```

**Common pitfall**: Returning without closing connection first causes file descriptor leaks.

### 4. Multiple Statement Execution

SQLite's `cursor.execute()` only handles one statement. Split by semicolon:

```python
with open(migration_path, 'r') as f:
    sql = f.read()

for statement in sql.split(';'):
    statement = statement.strip()
    if statement:  # Skip empty statements
        cursor.execute(statement)
```

### 5. Package Data for SQL Files

Include SQL files in pip-installed packages:

```toml
[tool.setuptools.package-data]
"gtx_broker.scheduler" = ["migrations/*.sql"]
```

Without this, migrations will be missing from installed packages.

## Testing Strategy

### Test 1: Migration Idempotency

```python
def test_migration_idempotency(self, scheduler):
    runner = MigrationRunner(scheduler.config.db_path)
    
    # First run
    result1 = runner.run_all()
    assert result1 is True
    
    # Second run (should be no-op)
    result2 = runner.run_all()
    assert result2 is True
```

### Test 2: Old Schema Upgrade

```python
def test_upgrade_old_schema(self):
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "tasks.db"
        
        # Create old schema (without new columns)
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'queued',
                -- ... no review_tag, schedule_type, batch_epoch_id
            )
        """)
        
        # Insert test data
        cursor.execute("""
            INSERT INTO tasks (id, kind, state, payload)
            VALUES ('TEST-001', 'vision', 'queued', '{}')
        """)
        conn.commit()
        conn.close()
        
        # Upgrade by creating scheduler
        config = SchedulerConfig(db_path=str(db_path))
        scheduler = Scheduler(config)
        
        # Verify columns were added
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(tasks)")
        columns = {row[1] for row in cursor.fetchall()}
        
        assert 'review_tag' in columns
        assert 'schedule_type' in columns
        assert 'batch_epoch_id' in columns
        
        # Verify data survived
        cursor.execute("SELECT * FROM tasks WHERE id = 'TEST-001'")
        task = cursor.fetchone()
        assert task is not None
        assert task[1] == 'vision'
        
        conn.close()
```

### Test 3: Connection Leak Detection

```python
def test_migration_connections_closed(self, scheduler):
    runner = MigrationRunner(scheduler.config.db_path)
    
    # Run migrations many times
    for _ in range(100):
        result = runner.run_all()
        assert result is True
    
    # If connections leaked, would see file descriptor issues or SQLite locking errors
```

## Implementation Checklist

- [ ] Migration runner with idempotent execution
- [ ] Column upgrade function called before migrations
- [ ] Connection close in finally block
- [ ] Connection close before return
- [ ] SQL statement splitting (not all-at-once)
- [ ] Package data configuration for SQL files
- [ ] Tests for migration idempotency
- [ ] Tests for old schema upgrade
- [ ] Tests for connection cleanup
- [ ] Tests for exception handling

## Common Pitfalls

1. **Connection leaks**: Always close connections in finally blocks or before returns
2. **Column order**: `_upgrade_columns_if_missing()` must run BEFORE migrations
3. **SQL statement splitting**: SQLite only executes one statement at a time
4. **Package data**: SQL files won't be in pip-installed packages without `[tool.setuptools.package-data]`
5. **Index on missing columns**: Migrations that create indexes on non-existent columns will fail
6. **Early return**: Don't return from a method without closing the connection first

## Migration File Structure

```
gtx_broker/scheduler/migrations/
├── 001_add_tagging.sql      # Add review_tag, schedule_type, batch_epoch_id
├── 002_batch_epochs.sql     # Add batch_epochs table (if needed)
└── __init__.py
```

Migration file naming: `{sequence}_{description}.sql` (e.g., `001_add_tagging.sql`)

## References

- Original implementation: PR #4 and PR #5 in `andyfied-agent/gtx-broker`
- Test file: `tests/test_migrations.py` (10 comprehensive tests)
- Migration runner: `gtx_broker/scheduler/migrations.py`
- Upgrade function: `gtx_broker/scheduler/scheduler.py` `_upgrade_columns_if_missing()`

---

*Documented: 2026-09-27*  
*Version: 1.0.0*  
*Related: gtx-broker-architecture skill*
