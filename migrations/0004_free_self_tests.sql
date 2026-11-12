-- 自由组卷范围与计分规则随锁卷保存；旧模板和管理场次保持兼容。
ALTER TABLE papers ADD COLUMN selection_json TEXT CHECK (selection_json IS NULL OR json_valid(selection_json));
