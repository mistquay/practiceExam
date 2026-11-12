// 认证接口
import { get, post } from "./http.js";

export const login = (employeeNo, password) =>
  post("/api/auth/login", { employee_no: employeeNo, password });

export const logout = () => post("/api/auth/logout");

/** 返回当前用户及其「当前有效授权」（人员类别 / 专业 / 生效期）。 */
export const me = () => get("/api/auth/me");
