-- Raphael DB schema
-- Run: psql -U creator raphael < storage/migrations/001_init.sql

CREATE TABLE IF NOT EXISTS trends (
    id          SERIAL PRIMARY KEY,
    source      VARCHAR(50)  NOT NULL,       -- shutterstock|adobe|freepik
    keyword     VARCHAR(255) NOT NULL,
    score       FLOAT,
    captured_at TIMESTAMPTZ  DEFAULT NOW(),
    is_processed BOOLEAN     DEFAULT FALSE
);

-- expression-based unique — must be an index, not table constraint
CREATE UNIQUE INDEX IF NOT EXISTS idx_trends_unique
    ON trends (source, keyword, (captured_at::date));

CREATE INDEX idx_trends_unprocessed ON trends(is_processed) WHERE is_processed = FALSE;

CREATE TABLE IF NOT EXISTS jobs (
    id            SERIAL PRIMARY KEY,
    trend_id      INT REFERENCES trends(id) ON DELETE CASCADE,
    status        VARCHAR(50)  DEFAULT 'pending',  -- pending|generating|uploading|done|failed
    workflow_json JSONB,
    image_path    TEXT,
    title         TEXT,
    keywords      TEXT[],
    hashtags      TEXT[],
    category      VARCHAR(100),
    error_msg     TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW(),
    uploaded_at   TIMESTAMPTZ
);

CREATE INDEX idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS upload_results (
    id            SERIAL PRIMARY KEY,
    job_id        INT REFERENCES jobs(id) ON DELETE CASCADE,
    stock         VARCHAR(50) NOT NULL,           -- shutterstock|adobe
    external_id   VARCHAR(255),
    review_status VARCHAR(50) DEFAULT 'pending',  -- pending|approved|rejected
    reject_reason TEXT,
    checked_at    TIMESTAMPTZ
);
