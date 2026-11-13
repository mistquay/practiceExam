// 登录页：用户名 + 密码
import * as authApi from "../api/auth.js";
import { h, mount } from "../components/dom.js";

export function render(view, { query, navigate, onLogin }) {
  const employeeNo = h("input", { class: "input", name: "employee_no", autocomplete: "username", required: true });
  const password = h("input", {
    class: "input",
    type: "password",
    name: "password",
    autocomplete: "current-password",
    required: true,
  });
  employeeNo.value = query.get("username") || "";
  const message = h("div", { class: "form-message", role: "alert" });
  const submitBtn = h("button", { type: "submit", class: "btn btn-primary btn-block" }, "登录");

  async function onSubmit(event) {
    event.preventDefault();
    message.textContent = "";
    submitBtn.disabled = true;
    try {
      await authApi.login(employeeNo.value.trim(), password.value);
      if (!(await onLogin())) throw new Error("登录成功但读取当前用户失败，请稍后重试");
      const redirect = query.get("redirect");
      navigate(redirect && !redirect.startsWith("/login") ? redirect : "/");
    } catch (err) {
      message.textContent = err.status === 401 ? "用户名或密码错误" : err.message;
      password.value = "";
    } finally {
      submitBtn.disabled = false;
    }
  }

  mount(
    view,
    h(
      "section",
      { class: "card login-card" },
      h("h1", { class: "card-title" }, "登录"),
      query.get("registered") === "1" ? h("p", { class: "alert alert-success", role: "status" }, "注册成功，请使用新账号登录。") : null,
      h(
        "form",
        { class: "form", onsubmit: onSubmit },
        h("label", { class: "field" }, h("span", null, "用户名"), employeeNo),
        h("label", { class: "field" }, h("span", null, "密码"), password),
        message,
        submitBtn
      ),
      h("p", { class: "hint" }, "没有账号？", h("a", { href: "#/register" }, "注册账号")),
      h("p", { class: "hint" }, "忘记密码请联系管理员重置。")
    )
  );
  employeeNo.focus();
}
