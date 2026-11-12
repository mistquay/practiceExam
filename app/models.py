"""领域常量：ETL 与运行时共享的枚举值，DDL 中的 CHECK 约束与此保持一致。

只放「值域」，不放业务逻辑；表结构以 migrations/*.sql 为准。
"""
from __future__ import annotations

# 题型（归一后的 4 种）
QTYPE_SINGLE = "单选题"
QTYPE_MULTI = "多选题"
QTYPE_JUDGE = "判断题"
QTYPE_SHORT = "简答题"
QTYPES = (QTYPE_SINGLE, QTYPE_MULTI, QTYPE_JUDGE, QTYPE_SHORT)
# 可自动判分的题型；简答题不进自动抽题池
AUTO_GRADABLE_QTYPES = (QTYPE_SINGLE, QTYPE_MULTI, QTYPE_JUDGE)

# 判断题选项为空时的默认选项
JUDGE_DEFAULT_OPTIONS = {"A": "正确", "B": "错误"}

# 人员类别（与资料目录名一致）
PERSON_KEY = "重点人员"
PERSON_FRONTLINE = "一线人员"
PERSON_MANAGER = "专业管理人员"
PERSON_LEADER = "领导人员"
PERSON_CATEGORIES = (PERSON_KEY, PERSON_FRONTLINE, PERSON_MANAGER, PERSON_LEADER)

# 题库版本状态
BANK_DRAFT = "draft"
BANK_ACTIVE = "active"
BANK_ARCHIVED = "archived"
BANK_STATUSES = (BANK_DRAFT, BANK_ACTIVE, BANK_ARCHIVED)
LEGACY_BANK_CODE = "legacy"

# question_versions.retire_reason
RETIRE_SUPERSEDED = "superseded"        # 勘误：被新版本替代
RETIRE_REMOVED = "removed"              # 重导时源数据中已不存在
RETIRE_ANSWER_VARIANT = "answer_variant"  # 同 stable_id 在源数据中出现的少数派答案，留档不启用

# question_tags.tag_type
TAG_PERSON_SCOPE = "person_scope"   # 人员目录直属文件（公共题范围）
TAG_SPECIALTY = "specialty"         # 专业代码（specialties.code）
TAG_PROTECTED = "protected"         # 备注=保命题
TAG_REQUIRED = "required"           # 标黄必考
TAG_COLOR_GROUP = "color_group"     # 标黄必汇总文件中的非黄底色分组
TAG_DATA_ISSUE = "data_issue"       # 数据质量问题（答案冲突、遗留黄色标记等）
ETL_TAG_TYPES = (
    TAG_PERSON_SCOPE, TAG_SPECIALTY, TAG_PROTECTED, TAG_REQUIRED, TAG_COLOR_GROUP, TAG_DATA_ISSUE,
)

# 用户与考试
ROLE_ADMIN = "admin"
ROLE_EXAMINEE = "examinee"
ROLES = (ROLE_ADMIN, ROLE_EXAMINEE)
PAPER_TYPES = ("practice", "mock", "formal", "makeup")
TEMPLATE_STATUSES = ("draft", "active", "disabled")
BATCH_STATUSES = ("draft", "open", "closed", "cancelled")
ATTEMPT_STATUSES = ("in_progress", "submitted", "expired", "pending_review", "graded")
GRADED_RESULTS = ("right", "wrong", "pending", "blank")
