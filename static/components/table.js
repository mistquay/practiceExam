// 通用表格：columns = [{ title, render(row) | key }]
import { h, empty } from "./dom.js";

export function dataTable(columns, rows, emptyText) {
  if (!rows.length) return empty(emptyText);
  return h(
    "div",
    { class: "table-wrap" },
    h(
      "table",
      { class: "table" },
      h("thead", null, h("tr", null, columns.map((c) => h("th", null, c.title)))),
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
