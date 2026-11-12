// 题号导航：statusOf(i) 返回附加的 class（如 "current"、"done"、"is-right"、"is-wrong"）
import { h, mount } from "./dom.js";

export function renderNav(container, count, { statusOf, onPick }) {
  const buttons = Array.from({ length: count }, (_, i) =>
    h(
      "button",
      { type: "button", class: `nav-item ${statusOf(i) || ""}`.trim(), onclick: () => onPick(i) },
      String(i + 1)
    )
  );
  mount(container, buttons);
}
