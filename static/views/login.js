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
  const message = h("div", { class: "form-message" });
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
      h(
        "form",
        { class: "form", onsubmit: onSubmit },
        h("label", { class: "field" }, h("span", null, "用户名"), employeeNo),
        h("label", { class: "field" }, h("span", null, "密码"), password),
        message,
        submitBtn
      ),
      h("p", { class: "hint" }, "账号由管理员统一导入，如忘记密码请联系管理员重置。")
    )
  );
  employeeNo.focus();
}
