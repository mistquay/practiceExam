import { getUser } from "./session.js";

// 选题偏好按账号、题库和用途隔离，不承载权限判断。
let last = { bankId: "", specialtyCode: "", paperType: "" };

export const getSelection = () => ({ ...last });

export function saveSelection(patch) {
  last = { ...last, ...patch };
}

export function resetSelection() {
  last = { bankId: "", specialtyCode: "", paperType: "" };
}

const scopeKey = (bank, kind) => `practice-scope:${getUser()?.id}:${bank}:${kind}`;
export function getScope(bank, kind) {
  try { return JSON.parse(localStorage.getItem(scopeKey(bank, kind)) || "{}"); }
  catch { return {}; }
}
export function saveScope(bank, kind, value) {
  try { localStorage.setItem(scopeKey(bank, kind), JSON.stringify(value)); } catch { /* 无存储时仍可选题 */ }
}
