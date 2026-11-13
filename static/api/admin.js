// 管理员接口
import { request, get, post, patch, listOf, download } from "./http.js";

// 用户
export function importUsers(file) {
  const form = new FormData();
  form.append("file", file);
  return request("POST", "/api/admin/users/import", { form });
}
export async function listUsers(q) {
  return listOf(await get("/api/admin/users", { q }), "users");
}
export const resetPassword = (userId) => post(`/api/admin/users/${userId}/reset-password`);
export const setUserActive = (userId, active) => patch(`/api/admin/users/${userId}`, { active });

// 题库版本
export async function listBankVersions() {
  return listOf(await get("/api/admin/banks"), "banks");
}
export const publishBank = (bankId) => post(`/api/admin/banks/${bankId}/publish`);
export function previewBank({ files, code, name, person, specialty }) {
  const form = new FormData();
  files.forEach(file => form.append("files", file, file.webkitRelativePath || file.name));
  form.append("code", code); form.append("name", name);
  if (person) form.append("person", person);
  if (specialty) form.append("specialty", specialty);
  return request("POST", "/api/admin/banks/import/preview", { form });
}
export const confirmBankImport = (token) => post("/api/admin/banks/import/confirm", { token });
export const exportBank = (bankId, format) => download(`/api/admin/banks/${bankId}/export`, { format }, `题库.${format}`);
export const bankImportOptions = () => get("/api/admin/banks/import/options");

// 试卷模板
export async function listTemplates() {
  return listOf(await get("/api/admin/templates"), "templates");
}
export const setTemplateStatus = (templateId, status) =>
  patch(`/api/admin/templates/${templateId}`, { status });

// 场次
export async function listBatches() {
  return listOf(await get("/api/admin/batches"), "batches");
}
export const createBatch = (payload) => post("/api/admin/batches", payload);
export const setBatchStatus = (batchId, status) => patch(`/api/admin/batches/${batchId}`, { status });

// 成绩导出
export const exportScores = ({ batchId, format = "csv" }) =>
  download("/api/reports/export", { batch_id: batchId, format }, `成绩导出.${format}`);

// 简答题人工阅卷
export async function listPendingGrading() {
  return listOf(await get("/api/admin/grading/pending"), "items");
}
export const gradeItem = (payload) => post("/api/admin/grading", payload);
