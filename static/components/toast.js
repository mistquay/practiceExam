// 全局轻提示
import { h } from "./dom.js";

function region() {
  let el = document.getElementById("toast-region");
  if (!el) {
    el = h("div", { id: "toast-region", class: "toast-region", role: "status", "aria-live": "polite" });
    document.body.appendChild(el);
  }
  return el;
}

export function toast(message, type = "info", ms = 3500) {
  const item = h("div", { class: `toast toast-${type}` }, message);
  region().appendChild(item);
  setTimeout(() => item.remove(), ms);
}

export function toastError(err) {
  toast(err && err.message ? err.message : String(err), "danger", 5000);
}
