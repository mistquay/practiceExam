// 练习接口（练习专用 DTO：可含参考答案与解析，与考试接口完全隔离）
import { get, post, listOf } from "./http.js";

/**
 * @param {object} p bankId / personCategory / specialtyCode / qtype / count / order / source
 */
export async function fetchPracticeQuestions(p) {
  const data = await get("/api/practice/questions", {
    bank_id: p.bankId,
    person_category: p.personCategory,
    specialty_code: p.specialtyCode,
    qtype: p.qtype,
    count: p.count,
    order: p.order,
    source: p.source,
  });
  return listOf(data, "questions");
}

/** 服务端判分并写入练习记录 / 错题本。 */
export const submitPractice = ({ bankId, stableId, userAnswer }) =>
  post("/api/practice/submit", { bank_id: bankId, stable_id: stableId, user_answer: userAnswer });
