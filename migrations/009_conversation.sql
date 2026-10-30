-- 009_conversation.sql —— 对话式 agent
-- 持久化消息历史与 agent 状态（当前候选、队列、筛选条件）。

CREATE TABLE conversation (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    state_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE conversation_message (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user', 'agent', 'system')),
    content         TEXT NOT NULL,
    payload_json    TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_conversation_message ON conversation_message(conversation_id, id);
