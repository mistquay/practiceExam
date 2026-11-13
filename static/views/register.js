// 自助注册：验证码仅以服务器生成的 PNG 展示，答案不在客户端保存。
import { post } from "../api/http.js";
import { h, mount } from "../components/dom.js";

export function render(view, { navigate }) {
  let challengeId = "";
  let imageUrl = "";
  let disposed = false;
  let busy = false;
  const username = h("input", { class: "input", name: "employee_no", autocomplete: "username", required: true,
    minlength: 3, maxlength: 32, pattern: "[A-Za-z0-9](?:[A-Za-z0-9_]|-){2,31}", "aria-describedby": "username-help" });
  const password = h("input", { class: "input", type: "password", name: "password", autocomplete: "new-password",
    required: true, minlength: 8, maxlength: 72, "aria-describedby": "password-help" });
  const confirm = h("input", { class: "input", type: "password", name: "confirm_password", autocomplete: "new-password", required: true });
  const code = h("input", { class: "input", name: "captcha", inputmode: "numeric", autocomplete: "off",
    required: true, pattern: "[0-9]{4}", maxlength: 4, "aria-describedby": "captcha-help" });
  const image = h("img", { alt: "四位数字验证码", width: 152, height: 52 });
  const refresh = h("button", { type: "button", class: "btn btn-ghost captcha-refresh", onclick: () => refreshCode() },
    image, h("span", null, "换一张"));
  const message = h("div", { class: "form-message", role: "alert" });
  const captchaMessage = h("p", { class: "hint", role: "status" });
  const submit = h("button", { type: "submit", class: "btn btn-primary btn-block", disabled: true }, "注册账号");

  async function refreshCode() {
    if (busy || disposed) return;
    busy = true;
    refresh.disabled = true;
    submit.disabled = true;
    const oldId = challengeId;
    challengeId = "";
    code.value = "";
    captchaMessage.textContent = "正在加载验证码…";
    try {
      const response = await fetch(`/api/auth/captcha${oldId ? `?replace=${encodeURIComponent(oldId)}` : ""}`, {
        cache: "no-store", credentials: "same-origin",
      });
      if (!response.ok) {
        const retry = response.headers.get("Retry-After");
        throw new Error(response.status === 429 ? `换码太频繁，请${retry || "稍后"}秒后重试。` : "验证码加载失败，请点击换一张重试。");
      }
      const blob = await response.blob();
      if (disposed) return;
      if (imageUrl) URL.revokeObjectURL(imageUrl);
      imageUrl = URL.createObjectURL(blob);
      image.src = imageUrl;
      challengeId = response.headers.get("X-Captcha-ID") || "";
      captchaMessage.textContent = "";
    } catch (err) {
      if (!disposed) captchaMessage.textContent = err.message;
    } finally {
      busy = false;
      refresh.disabled = false;
      submit.disabled = !challengeId;
    }
  }

  async function onSubmit(event) {
    event.preventDefault();
    if (!challengeId || busy) return;
    message.textContent = "";
    if (password.value !== confirm.value) {
      message.textContent = "两次输入的密码不一致，请重新确认。";
      confirm.focus();
      return;
    }
    busy = true;
    submit.disabled = true;
    refresh.disabled = true;
    submit.textContent = "正在注册…";
    try {
      const result = await post("/api/auth/register", {
        employee_no: username.value, password: password.value, captcha_id: challengeId, captcha: code.value,
      });
      password.value = confirm.value = "";
      if (!disposed) navigate(`/login?registered=1&username=${encodeURIComponent(result.employee_no)}`);
    } catch (err) {
      if (!disposed) message.textContent = `${err.message}。请填写新的验证码后重试。`;
      busy = false;
      await refreshCode();
    } finally {
      busy = false;
      submit.textContent = "注册账号";
      submit.disabled = !challengeId;
      refresh.disabled = false;
    }
  }

  mount(view, h("section", { class: "card login-card" },
    h("h1", { class: "card-title" }, "注册账号"),
    h("form", { class: "form", onsubmit: onSubmit },
      h("label", { class: "field" }, h("span", null, "用户名"), username),
      h("p", { class: "hint", id: "username-help" }, "3–32位字母、数字、下划线或短横线，以字母或数字开头。"),
      h("label", { class: "field" }, h("span", null, "密码"), password),
      h("p", { class: "hint", id: "password-help" }, "至少8位，包含字母和数字，不含空格。"),
      h("label", { class: "field" }, h("span", null, "确认密码"), confirm),
      h("label", { class: "field" }, h("span", null, "图形验证码"), code),
      refresh, h("div", { id: "captcha-help" }, captchaMessage), message, submit),
    h("p", { class: "hint" }, "已有账号？", h("a", { href: "#/login" }, "返回登录"))));
  refreshCode();
  username.focus();
  return () => {
    disposed = true;
    if (imageUrl) URL.revokeObjectURL(imageUrl);
    password.value = confirm.value = "";
  };
}
