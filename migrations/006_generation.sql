-- 006_generation.sql —— 双 agent 生成提案
-- 提案独立于简历存储：生成一堆候选，人工终审后再决定落不落库。

CREATE TABLE generation_run (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    base_resume_id INTEGER NOT NULL REFERENCES resume(id) ON DELETE CASCADE,
    job_id         INTEGER NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    status         TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'saved')),
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (base_resume_id, job_id)
);

CREATE TABLE generation_item (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES generation_run(id) ON DELETE CASCADE,
    source_id   TEXT NOT NULL,
    label       TEXT,
    original    TEXT NOT NULL,
    rewritten   TEXT NOT NULL,
    checks_json TEXT NOT NULL DEFAULT '[]',
    blocked     INTEGER NOT NULL DEFAULT 0,
    critique    TEXT,
    accepted    INTEGER,                       -- NULL 待定 / 1 接受 / 0 拒绝
    manual      TEXT                           -- 用户在终审里直接改的文本
);
CREATE INDEX idx_generation_item_run ON generation_item(run_id, id);
