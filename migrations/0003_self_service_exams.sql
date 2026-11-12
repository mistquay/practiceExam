-- 自主模拟与管理场次分开标记；规则阈值冻结，不随模板编辑改变历史成绩。
ALTER TABLE attempts ADD COLUMN exam_mode TEXT NOT NULL DEFAULT 'managed'
    CHECK (exam_mode IN ('managed', 'self_service'));
ALTER TABLE attempts ADD COLUMN pass_score_snapshot REAL;
ALTER TABLE attempts ADD COLUMN duration_min_snapshot REAL;
