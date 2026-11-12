// 极简 DOM 构造工具：所有文本走 textContent，杜绝拼接 innerHTML 带来的 XSS。

/**
 * h("button", { class: "btn", onclick: fn, disabled: true }, "文本", childNode, [more])
 */
export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  Object.entries(props || {}).forEach(([key, value]) => {
    if (value === undefined || value === null || value === false) return;
    if (key === "class") el.className = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2), value);
    } else if (key in el && typeof value !== "string") el[key] = value;
    else el.setAttribute(key, value === true ? "" : String(value));
  });
  append(el, children);
  return el;
}

function append(el, children) {
  children.flat(Infinity).forEach((child) => {
    if (child === undefined || child === null || child === false) return;
    el.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  });
}

export function mount(container, ...children) {
  container.replaceChildren();
  append(container, children);
  return container;
}

export function fmtDateTime(value) {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function fmtDate(value) {
  return value ? String(value).slice(0, 10) : "—";
}

/** 滚动到元素：尊重系统「减少动态效果」设置（CSS 的 scroll-behavior 管不到 JS 指定的 smooth） */
export function scrollToEl(el) {
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
}

/** 加载中 / 空状态 / 错误占位 */
export const loading = (text = "加载中…") => h("div", { class: "placeholder" }, text);
export const empty = (text = "暂无数据") => h("div", { class: "placeholder" }, text);
export const errorBox = (err) =>
  h("div", { class: "alert alert-danger" }, err && err.message ? err.message : String(err));
