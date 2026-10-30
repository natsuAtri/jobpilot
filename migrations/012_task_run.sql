-- 012_task_run.sql —— 后台任务进度持久化
-- 搜索/生成的运行状态从内存挪到库，重启后还能看到上次的结果。

CREATE TABLE task_run (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,                 -- search | generate
    status       TEXT NOT NULL DEFAULT 'running',
    step         TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
