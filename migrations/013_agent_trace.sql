-- 013_agent_trace.sql —— agent 每一步的可观测记录
-- 排查「它为什么这么答」、以及后续做评估，都靠这张表。

CREATE TABLE IF NOT EXISTS agent_trace (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    step       INTEGER NOT NULL DEFAULT 0,
    kind       TEXT NOT NULL,              -- intent | tool | llm | error
    name       TEXT NOT NULL,              -- 意图名 / 工具名 / 任务名
    args_json  TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    latency_ms INTEGER,
    tokens     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_agent_trace_turn ON agent_trace(turn_id);
CREATE INDEX IF NOT EXISTS idx_agent_trace_created ON agent_trace(created_at);
