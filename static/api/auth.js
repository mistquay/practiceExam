// 认证接口
import { get, post } from "./http.js";

export const login = (employeeNo, password) =>
  post("/api/auth/login", { employee_no: employeeNo, password });

export const logout = () => post("/api/auth/logout");

/** 返回当前登录账号及角色；人员类别由用户在练习/考试时自主选择。 */
export const me = () => get("/api/auth/me");
