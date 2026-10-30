-- 002_tailor.sql —— 改写提案
-- 提案与被改写的简历分离：生成一堆候选，用户逐条勾选，最后才生成定制版。

CREATE TABLE tailor_run (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    base_resume_id INTEGER NOT NULL REFERENCES resume(id) ON DELETE CASCADE,
    job_id         INTEGER NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    status         TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'saved')),
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (base_resume_id, job_id)
);

CREATE TABLE tailor_item (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES tailor_run(id) ON DELETE CASCADE,
    section_type  TEXT NOT NULL,
    item_index    INTEGER NOT NULL,
    bullet_index  INTEGER NOT NULL,
    original      TEXT NOT NULL,
    rewritten     TEXT NOT NULL,
    guard_json    TEXT NOT NULL DEFAULT '[]',
    blocked       INTEGER NOT NULL DEFAULT 0,
    accepted      INTEGER,                       -- NULL 待定 / 1 接受 / 0 拒绝
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_tailor_item_run ON tailor_item(run_id, id);
