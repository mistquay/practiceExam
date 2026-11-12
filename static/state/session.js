// 会话状态：当前登录账号与角色（模块私有，只读访问）
import * as authApi from "../api/auth.js";

let currentUser = null;

export const getUser = () => currentUser;
export const isAdmin = () => !!currentUser && currentUser.role === "admin";

let revision = 0;

export async function loadSession() {
  const requestRevision = ++revision;
  currentUser = null;
  const user = await authApi.me();
  // 退出、切换账号或更新的请求均使旧响应失效。
  if (requestRevision !== revision) return null;
  currentUser = user;
  return currentUser;
}

export function clearSession() {
  revision++;
  currentUser = null;
}
