// 通用表格：columns = [{ title, render(row) | key }]
import { h, empty } from "./dom.js";

export function dataTable(columns, rows, emptyText) {
  if (!rows.length) return empty(emptyText);
  // 宽表格在窄屏内部横向滚动：滚动容器可聚焦，键盘用户才能用方向键滚动（WCAG 2.1.1）
  return h(
    "div",
    { class: "table-wrap", tabindex: "0", role: "region", "aria-label": "数据表格（可横向滚动）" },
    h(
      "table",
      { class: "table" },
      h("thead", null, h("tr", null, columns.map((c) => h("th", { scope: "col" }, c.title)))),
      h(
        "tbody",
        null,
        rows.map((row) =>
          h(
            "tr",
            null,
            columns.map((c) => h("td", null, c.render ? c.render(row) : row[c.key] ?? "—"))
          )
        )
      )
    )
  );
}
