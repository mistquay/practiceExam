// 考试接口（考试 DTO：服务端不下发任何判分依据，前端也只上送考生作答 choice）
import { get, post, listOf, requestWithMeta } from "./http.js";

/** 当前用户可参加的场次 / 模板。 */
export async function listAvailable({ bankId, specialtyCode, paperType }) {
  const data = await get("/api/exams/available", {
    bank_id: bankId,
    specialty_code: specialtyCode,
    paper_type: paperType,
  });
  return listOf(data, "batches");
}

function withServerNow({ data, serverDate }) {
  return { ...data, server_now: serverDate };
}

/** 开考：返回 attempt_id、deadline_at、server_now 与题目。 */
export async function startExam({ batchId, templateId, specialtyCode }) {
  return withServerNow(
    await requestWithMeta("POST", "/api/exams/start", {
      body: { batch_id: batchId, template_id: templateId, specialty_code: specialtyCode },
    })
  );
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
