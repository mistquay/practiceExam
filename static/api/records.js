// 个人记录：错题本、练习记录（仅本人，服务端持久化）
import { get, del } from "./http.js";

export const listWrong = (bankId) => get("/api/records/wrong", { bank_id: bankId });

export const removeWrong = (stableId) =>
  del(`/api/records/wrong/${encodeURIComponent(stableId)}`);

export const clearWrong = () => del("/api/records/wrong");

export const listPracticeRecords = ({ limit, offset }) =>
  get("/api/records/practice", { limit, offset });
