// 题库版本与维度（前端下拉只从这里取值）
import { get, listOf } from "./http.js";

export async function listBanks() {
  return listOf(await get("/api/banks"), "banks");
}

export const getDimension = (bankId) =>
  get(`/api/banks/${encodeURIComponent(bankId)}/dimension`);
