-- 练习来源关系独立于版本代表行；同一题可属于多个文件/工作表。
CREATE TABLE question_sources (
    bank_version_id INTEGER NOT NULL REFERENCES bank_versions(id),
    stable_id TEXT NOT NULL REFERENCES questions(stable_id),
    source_file_id INTEGER NOT NULL REFERENCES source_files(id),
    PRIMARY KEY (bank_version_id, stable_id, source_file_id)
);
CREATE INDEX ix_question_sources_file ON question_sources (bank_version_id, source_file_id, stable_id);
INSERT OR IGNORE INTO question_sources (bank_version_id, stable_id, source_file_id)
SELECT bank_version_id, stable_id, source_file_id FROM question_versions WHERE source_file_id IS NOT NULL;
