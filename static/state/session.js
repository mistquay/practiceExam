// 会话状态：当前登录用户与其有效授权（模块私有，只读访问）
import * as authApi from "../api/auth.js";

let currentUser = null;

export const getUser = () => currentUser;
export const isAdmin = () => !!currentUser && currentUser.role === "admin";

/** 管理记录中的当前有效授权；不限制练习/考试，考试人员类别由用户另行选择。 */
export const getAuthorization = () => (currentUser && currentUser.authorization) || null;

export async function loadSession() {
  currentUser = await authApi.me();
  return currentUser;
}

export function clearSession() {
  currentUser = null;
}
