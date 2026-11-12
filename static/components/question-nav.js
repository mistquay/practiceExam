// 题号导航：statusOf(i) 返回附加的 class（如 "current"、"done"、"is-right"、"is-wrong"）
// 状态同时写入可读名称，读屏用户不依赖颜色即可知道每题进度。
import { h, mount } from "./dom.js";

const STATUS_TEXT = { "is-right": "答对", "is-wrong": "答错", done: "已作答" };

export function renderNav(container, count, { statusOf, onPick }) {
  container.setAttribute("role", "navigation");
  container.setAttribute("aria-label", "题号导航");
  const buttons = Array.from({ length: count }, (_, i) => {
    const classes = (statusOf(i) || "").split(/\s+/).filter(Boolean);
    const state = classes.map((c) => STATUS_TEXT[c]).find(Boolean);
    return h(
      "button",
      {
        type: "button",
        class: ["nav-item", ...classes].join(" "),
        "aria-label": `第 ${i + 1} 题${state ? `，${state}` : "，未作答"}`,
        "aria-current": classes.includes("current") ? "step" : null,
        onclick: () => onPick(i),
      },
      String(i + 1)
    );
  });
  // 重绘会替换按钮；若焦点原在导航内，交还给同一题号，键盘用户不丢位置
  const focused = Array.prototype.indexOf.call(container.children, document.activeElement);
  mount(container, buttons);
  if (focused >= 0) buttons[focused]?.focus({ preventScroll: true });
}
