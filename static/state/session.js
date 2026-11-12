// 会话状态：当前登录用户与其有效授权（模块私有，只读访问）
import * as authApi from "../api/auth.js";

let currentUser = null;

export const getUser = () => currentUser;
export const isAdmin = () => !!currentUser && currentUser.role === "admin";

/** 当前有效授权；人员类别只能来自这里，前端不提供修改入口。 */
export const getAuthorization = () => (currentUser && currentUser.authorization) || null;

export async function loadSession() {
  currentUser = await authApi.me();
  return currentUser;
}

export function clearSession() {
  currentUser = null;
}
