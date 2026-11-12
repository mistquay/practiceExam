-- 0001_init：题库版本化 + 题目身份/版本分层 + 用户/授权 + 试卷/考试 + 审计
-- 约定：
--   * 只增不删：本文件及后续迁移禁止删表；回滚由 app/db/migrate.py 以「迁移前快照还原 + 当前库归档」实现。
--   * 时间统一 ISO-8601 UTC 文本；JSON 字段为 TEXT（json_valid 校验）。
--   * 业务表 user_id 一律 NOT NULL（多用户隔离的底线）。
--   * 由 migrate.py 在单个事务内执行，本文件不写 BEGIN/COMMIT。

-- ───────────────────────── 题库 ─────────────────────────

CREATE TABLE bank_versions (
    id             INTEGER PRIMARY KEY,
    code           TEXT    NOT NULL UNIQUE,          -- 2026-11 / legacy
    name           TEXT    NOT NULL,
    effective_date TEXT,
    source_dir     TEXT,
    imported_at    TEXT,
    status         TEXT    NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'archived')),
    published_by   TEXT,
    note           TEXT,
    report_hash    TEXT                              -- 最近一次导入报告的 sha1，用于对账
);

CREATE TABLE source_files (
    id                       INTEGER PRIMARY KEY,
    bank_version_id          INTEGER NOT NULL REFERENCES bank_versions(id),
    rel_path                 TEXT    NOT NULL,
    md5                      TEXT    NOT NULL,
    real_format              TEXT    NOT NULL,       -- ooxml / ole2 / sqlite
    sheet                    TEXT    NOT NULL DEFAULT '',
    header_row               INTEGER,
    header_variant           TEXT,
    person_scope             TEXT,                   -- 人员目录直属文件时为人员类别
    specialty_code           TEXT,
    is_professional_optional INTEGER NOT NULL DEFAULT 0 CHECK (is_professional_optional IN (0, 1)),
    row_count                INTEGER NOT NULL DEFAULT 0,
    question_count           INTEGER NOT NULL DEFAULT 0,
    skipped_rows             INTEGER NOT NULL DEFAULT 0,
    skip_reason              TEXT,
    UNIQUE (bank_version_id, rel_path, sheet)
);
CREATE INDEX ix_source_files_md5 ON source_files (md5);

-- 身份层：stable_id = sha1(规范化题干 \x1f 规范化选项)，不含答案/分值/解析
CREATE TABLE questions (
    stable_id          TEXT PRIMARY KEY CHECK (length(stable_id) = 40),
    canonical_stem     TEXT NOT NULL,
    canonical_options  TEXT NOT NULL,
    content_hash       TEXT NOT NULL,                -- sha256(同一输入)，防 sha1 碰撞的旁证
    first_seen_version TEXT NOT NULL,
    created_at         TEXT NOT NULL
);

-- 版本层：答案勘误 = 新增一行 + 旧行 retired，stable_id 不变
CREATE TABLE question_versions (
    id               INTEGER PRIMARY KEY,
    bank_version_id  INTEGER NOT NULL REFERENCES bank_versions(id),
    stable_id        TEXT    NOT NULL REFERENCES questions(stable_id),
    source_file_id   INTEGER REFERENCES source_files(id),
    source_row       INTEGER,
    seq_in_file      TEXT,
    outline_l1       TEXT,
    outline_l2       TEXT,
    question_class   TEXT,
    qtype            TEXT    NOT NULL CHECK (qtype IN ('单选题', '多选题', '判断题', '简答题')),
    stem             TEXT    NOT NULL,
    options          TEXT    NOT NULL CHECK (json_valid(options)),
    answer           TEXT    NOT NULL,
    answer_raw       TEXT,
    basis            TEXT,
    score            REAL,
    score_raw        TEXT,
    score_rule       TEXT,                           -- native / default_empty / placeholder / invalid
    code_id          TEXT,
    remark           TEXT,
    note             TEXT,
    analysis         TEXT,
    raw_option       TEXT,
    fill_color       TEXT,
    is_protected     INTEGER NOT NULL DEFAULT 0 CHECK (is_protected IN (0, 1)),
    is_required      INTEGER NOT NULL DEFAULT 0 CHECK (is_required IN (0, 1)),
    color_group      TEXT    CHECK (color_group IS NULL OR json_valid(color_group)),  -- JSON 数组，可多组
    auto_gradable    INTEGER NOT NULL DEFAULT 1 CHECK (auto_gradable IN (0, 1)),  -- 0 = 不进自动抽题池
    occurrence_count INTEGER NOT NULL DEFAULT 1,     -- 源数据中出现的行数
    version_hash     TEXT    NOT NULL,
    retired          INTEGER NOT NULL DEFAULT 0 CHECK (retired IN (0, 1)),
    retired_at       TEXT,
    retire_reason    TEXT,
    supersedes_id    INTEGER REFERENCES question_versions(id),
    created_at       TEXT    NOT NULL
);
CREATE UNIQUE INDEX ux_question_versions_active ON question_versions (bank_version_id, stable_id) WHERE retired = 0;
CREATE INDEX ix_question_versions_bank_qtype ON question_versions (bank_version_id, qtype);
CREATE INDEX ix_question_versions_outline ON question_versions (outline_l1, outline_l2);
CREATE INDEX ix_question_versions_stable ON question_versions (stable_id);

CREATE TABLE tags (
    tag_type    TEXT NOT NULL,
    tag_value   TEXT NOT NULL,
    description TEXT,
    PRIMARY KEY (tag_type, tag_value)
);

CREATE TABLE question_tags (
    bank_version_id INTEGER NOT NULL REFERENCES bank_versions(id),
    stable_id       TEXT    NOT NULL REFERENCES questions(stable_id),
    tag_type        TEXT    NOT NULL,
    tag_value       TEXT    NOT NULL,
    PRIMARY KEY (bank_version_id, stable_id, tag_type, tag_value),
    FOREIGN KEY (tag_type, tag_value) REFERENCES tags (tag_type, tag_value)
);
CREATE INDEX ix_question_tags_tag ON question_tags (tag_type, tag_value);

CREATE TABLE specialties (
    code                TEXT PRIMARY KEY,
    name                TEXT    NOT NULL,
    outline_l1          TEXT    NOT NULL,
    outline_l2          TEXT,
    source_file_pattern TEXT    NOT NULL,            -- 源文件名（basename 精确匹配）
    is_general          INTEGER NOT NULL DEFAULT 0 CHECK (is_general IN (0, 1)),
    enabled             INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    sort_order          INTEGER NOT NULL DEFAULT 0,
    note                TEXT
);

-- ───────────────────────── 用户与授权 ─────────────────────────

CREATE TABLE users (
    id                       INTEGER PRIMARY KEY,
    employee_no              TEXT    NOT NULL UNIQUE,
    name                     TEXT    NOT NULL,
    password_hash            TEXT    NOT NULL,
    role                     TEXT    NOT NULL DEFAULT 'examinee' CHECK (role IN ('admin', 'examinee')),
    current_authorization_id INTEGER REFERENCES person_authorizations(id),
    team                     TEXT,
    active                   INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    must_change_password     INTEGER NOT NULL DEFAULT 1 CHECK (must_change_password IN (0, 1)),
    created_at               TEXT    NOT NULL,
    updated_at               TEXT
);

-- 人员类别是带依据与生效期的授权记录，不是账号属性
CREATE TABLE person_authorizations (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    person_category TEXT    NOT NULL CHECK (person_category IN ('重点人员', '一线人员', '专业管理人员', '领导人员')),
    specialty_code  TEXT    REFERENCES specialties(code),
    effective_from  TEXT    NOT NULL,
    effective_to    TEXT,
    basis_doc       TEXT    NOT NULL,                -- 文号 / 岗位清单
    approver        TEXT    NOT NULL,
    decided_at      TEXT    NOT NULL,
    revoked_at      TEXT,
    revoked_by      TEXT,
    note            TEXT
);
CREATE INDEX ix_person_authorizations_user ON person_authorizations (user_id, effective_from);

-- ───────────────────────── 试卷与考试 ─────────────────────────

CREATE TABLE paper_templates (
    id                    INTEGER PRIMARY KEY,
    code                  TEXT    NOT NULL,
    version               INTEGER NOT NULL DEFAULT 1,
    name                  TEXT    NOT NULL,
    paper_type            TEXT    NOT NULL DEFAULT 'formal' CHECK (paper_type IN ('practice', 'mock', 'formal', 'makeup')),
    bank_version_id       INTEGER NOT NULL REFERENCES bank_versions(id),
    person_category       TEXT,
    specialty_rule        TEXT    NOT NULL CHECK (json_valid(specialty_rule)),
    required_question_ids TEXT    NOT NULL CHECK (json_valid(required_question_ids)),
    exclude_question_ids  TEXT    NOT NULL CHECK (json_valid(exclude_question_ids)),
    exclude_rule          TEXT    CHECK (exclude_rule IS NULL OR json_valid(exclude_rule)),
    type_ratio            TEXT    NOT NULL CHECK (json_valid(type_ratio)),
    type_score            TEXT    CHECK (type_score IS NULL OR json_valid(type_score)),
    question_count        INTEGER NOT NULL,
    total_score           REAL    NOT NULL,
    duration_min          INTEGER NOT NULL,
    pass_score            REAL    NOT NULL,
    random_policy         TEXT    NOT NULL,
    short_answer_policy   TEXT    NOT NULL DEFAULT 'excluded',
    status                TEXT    NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'disabled')),
    pending_confirmation  TEXT,                       -- 待确认事项（JSON 或文本）
    created_at            TEXT    NOT NULL,
    updated_at            TEXT,
    UNIQUE (code, version)
);

CREATE TABLE exam_batches (
    id              INTEGER PRIMARY KEY,
    code            TEXT    NOT NULL UNIQUE,
    name            TEXT,
    template_id     INTEGER NOT NULL REFERENCES paper_templates(id),
    bank_version_id INTEGER NOT NULL REFERENCES bank_versions(id),
    start_at        TEXT,
    end_at          TEXT,
    max_attempts    INTEGER NOT NULL DEFAULT 1,
    allow_retake    INTEGER NOT NULL DEFAULT 0 CHECK (allow_retake IN (0, 1)),
    examinee_list   TEXT    CHECK (examinee_list IS NULL OR json_valid(examinee_list)),
    status          TEXT    NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'open', 'closed', 'cancelled')),
    created_by      TEXT,
    created_at      TEXT    NOT NULL
);

-- 锁卷快照
CREATE TABLE papers (
    id               INTEGER PRIMARY KEY,
    template_id      INTEGER NOT NULL REFERENCES paper_templates(id),
    template_version INTEGER NOT NULL,
    bank_version_id  INTEGER NOT NULL REFERENCES bank_versions(id),
    seed             TEXT    NOT NULL,
    person_category  TEXT,
    specialty_code   TEXT,
    question_count   INTEGER NOT NULL,
    total_score      REAL    NOT NULL,
    snapshot_hash    TEXT    NOT NULL,
    created_at       TEXT    NOT NULL
);

CREATE TABLE paper_questions (
    paper_id            INTEGER NOT NULL REFERENCES papers(id),
    seq                 INTEGER NOT NULL,
    stable_id           TEXT    NOT NULL REFERENCES questions(stable_id),
    question_version_id INTEGER NOT NULL REFERENCES question_versions(id),
    score               REAL    NOT NULL,
    is_required         INTEGER NOT NULL DEFAULT 0 CHECK (is_required IN (0, 1)),
    snapshot_json       TEXT    NOT NULL CHECK (json_valid(snapshot_json)),  -- 冻结题干/选项/答案/分值
    PRIMARY KEY (paper_id, seq),
    UNIQUE (paper_id, stable_id)
);

CREATE TABLE attempts (
    id                       INTEGER PRIMARY KEY,
    user_id                  INTEGER NOT NULL REFERENCES users(id),
    batch_id                 INTEGER REFERENCES exam_batches(id),
    paper_id                 INTEGER NOT NULL REFERENCES papers(id),
    authorization_id         INTEGER REFERENCES person_authorizations(id),
    person_category_snapshot TEXT,
    specialty_snapshot       TEXT,
    started_at               TEXT    NOT NULL,
    deadline_at              TEXT    NOT NULL,
    submitted_at             TEXT,
    status                   TEXT    NOT NULL DEFAULT 'in_progress'
                             CHECK (status IN ('in_progress', 'submitted', 'expired', 'pending_review', 'graded')),
    score                    REAL,
    passed                   INTEGER CHECK (passed IS NULL OR passed IN (0, 1)),
    client_info              TEXT
);
CREATE INDEX ix_attempts_user_started ON attempts (user_id, started_at);
CREATE INDEX ix_attempts_batch ON attempts (batch_id);

CREATE TABLE attempt_answers (
    attempt_id    INTEGER NOT NULL REFERENCES attempts(id),
    stable_id     TEXT    NOT NULL REFERENCES questions(stable_id),
    user_answer   TEXT,
    score         REAL,
    graded_result TEXT    CHECK (graded_result IS NULL OR graded_result IN ('right', 'wrong', 'pending', 'blank')),
    graded_by     TEXT,
    graded_at     TEXT,
    PRIMARY KEY (attempt_id, stable_id)
);

CREATE TABLE audit_logs (
    id           INTEGER PRIMARY KEY,
    actor        TEXT NOT NULL CHECK (length(actor) > 0),   -- 0 条匿名写
    action       TEXT NOT NULL,
    target       TEXT,
    at           TEXT NOT NULL,
    before_value TEXT,
    after_value  TEXT,
    detail       TEXT
);
CREATE INDEX ix_audit_logs_at ON audit_logs (at);

CREATE TABLE grading_reviews (
    id           INTEGER PRIMARY KEY,
    attempt_id   INTEGER NOT NULL REFERENCES attempts(id),
    stable_id    TEXT    NOT NULL REFERENCES questions(stable_id),
    reviewer     TEXT    NOT NULL,
    score        REAL    NOT NULL,
    comment      TEXT,
    corrected_at TEXT    NOT NULL,
    audit_id     INTEGER REFERENCES audit_logs(id)
);
CREATE INDEX ix_grading_reviews_attempt ON grading_reviews (attempt_id);

-- ───────────────────────── 练习与错题 ─────────────────────────

CREATE TABLE practice_records (
    id                  INTEGER PRIMARY KEY,
    user_id             INTEGER NOT NULL REFERENCES users(id),
    stable_id           TEXT    NOT NULL REFERENCES questions(stable_id),
    bank_version_id     INTEGER NOT NULL REFERENCES bank_versions(id),
    question_version_id INTEGER REFERENCES question_versions(id),
    user_answer         TEXT,
    is_correct          INTEGER CHECK (is_correct IS NULL OR is_correct IN (0, 1)),
    answered_at         TEXT    NOT NULL
);
CREATE INDEX ix_practice_records_user ON practice_records (user_id, answered_at);

CREATE TABLE wrong_book (
    user_id         INTEGER NOT NULL REFERENCES users(id),
    stable_id       TEXT    NOT NULL REFERENCES questions(stable_id),
    bank_version_id INTEGER NOT NULL REFERENCES bank_versions(id),
    wrong_count     INTEGER NOT NULL DEFAULT 1,
    last_wrong_at   TEXT    NOT NULL,
    last_answer     TEXT,
    mastered        INTEGER NOT NULL DEFAULT 0 CHECK (mastered IN (0, 1)),
    PRIMARY KEY (user_id, stable_id)
);
