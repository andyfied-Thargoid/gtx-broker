-- Migration 001: Add task tagging and batch epoch support
-- This migration is idempotent - safe to run multiple times

-- Add batch_epochs table (CREATE TABLE IF NOT EXISTS is idempotent)
CREATE TABLE IF NOT EXISTS batch_epochs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP NOT NULL,
    review_barrier TIMESTAMP,
    review_triggered INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes (IF NOT EXISTS makes this idempotent)
CREATE INDEX IF NOT EXISTS idx_tasks_review_tag ON tasks(review_tag);
CREATE INDEX IF NOT EXISTS idx_tasks_schedule_type ON tasks(schedule_type);
CREATE INDEX IF NOT EXISTS idx_tasks_batch_epoch ON tasks(batch_epoch_id);

