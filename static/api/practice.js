// 练习接口（练习专用 DTO：可含参考答案与解析，与考试接口完全隔离）
import { get, post, listOf } from "./http.js";

/**
 * @param {object} p bankId / personCategory / specialtyCode / qtypes / count / order / source（兼容 qtype）
 */
export async function fetchPracticeQuestions(p) {
  const data = await get("/api/practice/questions", {
    bank_id: p.bankId,
    person_category: p.personCategory,
    specialty_code: p.specialtyCode,
    specialty_codes: p.specialtyCodes,
    scope_mode: p.scopeMode,
    qtype: p.qtype,
    qtypes: p.qtypes,
    count: p.count,
    order: p.order,
    source: p.source,
    tags: p.tags,
    source_file_ids: p.sourceFileIds,
  });
  return listOf(data, "questions");
}

export const fetchPracticeFilters = (params) => get("/api/practice/filters", params);

/** 服务端判分并写入练习记录 / 错题本。 */
export const submitPractice = ({ bankId, stableId, userAnswer }) =>
  post("/api/practice/submit", { bank_id: bankId, stable_id: stableId, user_answer: userAnswer });
