-- 001_init.sql —— 初始结构
-- 约定：时间统一存 ISO 文本（UTC），JSON 存 TEXT，枚举用 CHECK 约束。

-- ---------------------------------------------------------------- 候选人档案
CREATE TABLE profile (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    name                  TEXT NOT NULL DEFAULT '',
    phone                 TEXT,
    email                 TEXT,
    location              TEXT,
    links_json            TEXT NOT NULL DEFAULT '{}',
    preferred_cities_json TEXT NOT NULL DEFAULT '[]',   -- 搜索偏好，不参与匹配分
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ------------------------------------------------------------------ 简历文档
CREATE TABLE resume (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id     INTEGER NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL CHECK (kind IN ('imported', 'generated', 'tailored')),
    title          TEXT NOT NULL DEFAULT '',
    base_resume_id INTEGER REFERENCES resume(id) ON DELETE SET NULL,
    job_family     TEXT,                                  -- 后端 / 算法 / 数据 …
    template_id    TEXT NOT NULL DEFAULT 'single-column',
    theme          TEXT NOT NULL DEFAULT 'ink',           -- ink | graphite
    source_file    TEXT,
    parse_status   TEXT NOT NULL DEFAULT 'pending'
                   CHECK (parse_status IN ('pending', 'parsed', 'verified')),
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_resume_profile ON resume(profile_id, created_at DESC);

CREATE TABLE resume_section (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    resume_id      INTEGER NOT NULL REFERENCES resume(id) ON DELETE CASCADE,
    type           TEXT NOT NULL CHECK (type IN
                   ('education', 'experience', 'project', 'skill', 'award', 'cert', 'other')),
    title_override TEXT,
    order_index    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_section_resume ON resume_section(resume_id, order_index);

CREATE TABLE resume_item (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id  INTEGER NOT NULL REFERENCES resume_section(id) ON DELETE CASCADE,
    org         TEXT,
    role        TEXT,
    location    TEXT,
    start_date  TEXT,
    end_date    TEXT,
    meta_json   TEXT NOT NULL DEFAULT '{}',
    order_index INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_item_section ON resume_item(section_id, order_index);

CREATE TABLE resume_bullet (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id          INTEGER NOT NULL REFERENCES resume_item(id) ON DELETE CASCADE,
    text             TEXT NOT NULL,
    order_index      INTEGER NOT NULL DEFAULT 0,
    tags_json        TEXT NOT NULL DEFAULT '[]',          -- 技能标签，关键词匹配用
    strength         INTEGER NOT NULL DEFAULT 3 CHECK (strength BETWEEN 1 AND 5),
    metrics_json     TEXT NOT NULL DEFAULT '[]',          -- 抽出的数字，三守恒用
    source           TEXT NOT NULL DEFAULT 'parse'
                     CHECK (source IN ('parse', 'chat', 'generate', 'manual')),
    user_utterance   TEXT,                                -- 对话来源的原话
    source_bullet_id INTEGER REFERENCES resume_bullet(id) ON DELETE SET NULL
);
CREATE INDEX idx_bullet_item ON resume_bullet(item_id, order_index);

-- -------------------------------------------------------------------- 岗位
CREATE TABLE job (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    platform                TEXT NOT NULL DEFAULT 'manual',
    external_id             TEXT,
    url                     TEXT,
    title                   TEXT NOT NULL DEFAULT '',
    company                 TEXT,
    industry                TEXT,
    company_size            TEXT,
    city                    TEXT,
    salary_min              INTEGER,
    salary_max              INTEGER,
    salary_months           INTEGER,
    experience_years        INTEGER,
    education_req           TEXT,
    description_raw         TEXT,
    description_parsed_json TEXT NOT NULL DEFAULT '{}',
    posted_at               TEXT,
    fetched_at              TEXT NOT NULL DEFAULT (datetime('now')),
    source                  TEXT NOT NULL DEFAULT 'url'
                            CHECK (source IN ('url', 'search', 'manual')),
    dedupe_key              TEXT
);
CREATE UNIQUE INDEX idx_job_dedupe ON job(dedupe_key) WHERE dedupe_key IS NOT NULL;

-- -------------------------------------------------------------------- 匹配
CREATE TABLE match_result (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id           INTEGER NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    resume_id        INTEGER NOT NULL REFERENCES resume(id) ON DELETE CASCADE,
    total            REAL NOT NULL DEFAULT 0,
    dimensions_json  TEXT NOT NULL DEFAULT '{}',
    weights_json     TEXT NOT NULL DEFAULT '{}',
    education_status TEXT NOT NULL DEFAULT 'unknown'
                     CHECK (education_status IN ('below', 'meet', 'exceed', 'unknown')),
    education_soft   INTEGER NOT NULL DEFAULT 0,          -- 1 = JD 写的是「优先」
    strengths_json   TEXT NOT NULL DEFAULT '[]',
    gaps_json        TEXT NOT NULL DEFAULT '[]',
    model            TEXT,
    prompt_version   TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX idx_match_job_resume ON match_result(job_id, resume_id);

-- ------------------------------------------------------------ 投递与复盘
CREATE TABLE application (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id             INTEGER NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    resume_id          INTEGER NOT NULL REFERENCES resume(id) ON DELETE RESTRICT,
    status             TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN
                       ('draft', 'submitted', 'viewed', 'replied', 'interview',
                        'offer', 'rejected', 'withdrawn')),
    channel            TEXT,
    submitted_at       TEXT NOT NULL DEFAULT (datetime('now')),
    form_snapshot_json TEXT NOT NULL DEFAULT '{}',
    notes              TEXT,
    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_app_job ON application(job_id);
CREATE INDEX idx_app_status ON application(status, submitted_at DESC);

CREATE TABLE application_event (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES application(id) ON DELETE CASCADE,
    at             TEXT NOT NULL DEFAULT (datetime('now')),
    type           TEXT NOT NULL,
    note           TEXT
);
CREATE INDEX idx_event_app ON application_event(application_id, at);

-- ----------------------------------------------------------------- 对话补录
CREATE TABLE dialogue_session (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
    resume_id  INTEGER NOT NULL REFERENCES resume(id) ON DELETE CASCADE,
    mode       TEXT NOT NULL CHECK (mode IN ('supplement', 'scratch')),
    state_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE dialogue_turn (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES dialogue_session(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT NOT NULL,
    patch_json  TEXT,
    accepted    INTEGER,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_turn_session ON dialogue_turn(session_id, id);

-- --------------------------------------------------------------- LLM 配置
CREATE TABLE llm_provider (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    label           TEXT NOT NULL,
    base_url        TEXT NOT NULL,
    api_key_ref     TEXT,                                 -- 指向 data/secrets.json 的键名
    models_json     TEXT NOT NULL DEFAULT '[]',
    capabilities_json TEXT NOT NULL DEFAULT '{}',
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE llm_binding (
    task        TEXT PRIMARY KEY,
    provider_id INTEGER NOT NULL REFERENCES llm_provider(id) ON DELETE CASCADE,
    model       TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE prompt_version (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task       TEXT NOT NULL,
    version    TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (task, version)
);

-- ------------------------------------------------------------------ 其他
CREATE TABLE app_setting (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
