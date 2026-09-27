-- Add tagging columns to tasks table
ALTER TABLE tasks ADD COLUMN review_tag BOOLEAN DEFAULT FALSE;
ALTER TABLE tasks ADD COLUMN schedule_type TEXT DEFAULT 'immediate';
ALTER TABLE tasks ADD COLUMN review_worker TEXT;
ALTER TABLE tasks ADD COLUMN batch_epoch_id TEXT;

-- Create batch_epochs table
CREATE TABLE IF NOT EXISTS batch_epochs (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'active',
    task_count INTEGER DEFAULT 0,
    review_result TEXT
);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_tasks_schedule ON tasks(schedule_type);
CREATE INDEX IF NOT EXISTS idx_tasks_review ON tasks(review_tag);
CREATE INDEX IF NOT EXISTS idx_tasks_epoch ON tasks(batch_epoch_id);
