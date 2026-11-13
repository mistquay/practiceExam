// 考试接口（考试 DTO：服务端不下发任何判分依据，前端也只上送考生作答 choice）
import { get, post, listOf, requestWithMeta } from "./http.js";

export async function listSelfServiceTemplates({ bankId, personCategory }) {
  return listOf(await get("/api/exams/self-service/templates", {
    bank_id: bankId, person_category: personCategory,
  }), "items");
}

function withServerNow({ data, serverDate }) {
  return { ...data, server_now: serverDate };
}

/** 读取考试实例（断线重连 / 查看成绩），deadline 以服务端为准。 */
export async function getAttempt(attemptId) {
  return withServerNow(
    await requestWithMeta("GET", `/api/exams/${encodeURIComponent(attemptId)}`)
  );
}

/** 交卷：responses = [{stable_id, choice}]；重复提交由服务端返回 409。 */
export const submitExam = (attemptId, responses) =>
  post(`/api/exams/${encodeURIComponent(attemptId)}/submit`, { responses });

/** 本人考试历史。 */
export async function listMyExams() {
  return listOf(await get("/api/exams"), "exams");
}
