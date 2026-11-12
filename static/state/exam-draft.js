// 考试作答草稿：只保存考生自己的选择，用于断线 / 刷新后恢复。
// 键按 attempt 隔离，交卷或退出登录即清除；剩余时间不在本地保存，始终以服务端 deadline 为准。
const PREFIX = "exam-draft:";

export function loadDraft(attemptId) {
  try {
    const raw = localStorage.getItem(PREFIX + attemptId);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

export function saveDraft(attemptId, choices) {
  try {
    localStorage.setItem(PREFIX + attemptId, JSON.stringify(choices));
  } catch {
    // 存储不可用时仅失去本地恢复能力，不影响作答
  }
}

export function clearDraft(attemptId) {
  localStorage.removeItem(PREFIX + attemptId);
}

export function clearAllDrafts() {
  Object.keys(localStorage)
    .filter((k) => k.startsWith(PREFIX))
    .forEach((k) => localStorage.removeItem(k));
}
