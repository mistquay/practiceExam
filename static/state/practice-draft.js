import { getUser } from "./session.js";

const key = () => `practice-draft:${getUser()?.id}`;
export function loadPracticeDraft() {
  try {
    const value = JSON.parse(localStorage.getItem(key()) || "null");
    return value && Array.isArray(value.ids) && value.ids.length ? value : null;
  } catch { return null; }
}
export function savePracticeDraft(value) {
  try { localStorage.setItem(key(), JSON.stringify(value)); } catch { /* 仍可正常练习 */ }
}
export function clearPracticeDraft() {
  try { localStorage.removeItem(key()); } catch { /* 存储不可用 */ }
}
