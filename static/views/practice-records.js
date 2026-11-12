// 练习记录（仅本人，分页）
import { listPracticeRecords } from "../api/records.js";
import { listOf } from "../api/http.js";
import { h, mount, loading, errorBox, fmtDateTime } from "../components/dom.js";
import { dataTable } from "../components/table.js";

const PAGE = 50;

export async function render(view) {
  let offset = 0;
  const body = h("div");
  const pager = h("div", { class: "actions" });
  mount(view, h("section", { class: "card" }, h("h1", { class: "card-title" }, "练习记录"), body, pager));

  async function load() {
    mount(body, loading());
    mount(pager);
    try {
      const data = await listPracticeRecords({ limit: PAGE, offset });
      const rows = listOf(data, "records");
      mount(
        body,
        dataTable(
          [
            { title: "时间", render: (r) => fmtDateTime(r.answered_at) },
            { title: "题干", render: (r) => h("div", { class: "cell-stem" }, r.stem || r.stable_id) },
            { title: "题型", key: "qtype" },
            { title: "我的作答", render: (r) => r.user_answer || "—" },
            {
              title: "结果",
              render: (r) =>
                r.is_correct === true
                  ? h("span", { class: "badge badge-ok" }, "对")
                  : r.is_correct === false
                    ? h("span", { class: "badge badge-bad" }, "错")
                    : "—",
            },
          ],
          rows,
          "暂无练习记录"
        )
      );
      const total = data && typeof data.total === "number" ? data.total : null;
      const hasMore = total !== null ? offset + rows.length < total : rows.length === PAGE;
      mount(
        pager,
        h("button", { class: "btn btn-outline btn-sm", disabled: offset === 0, onclick: () => turn(-PAGE) }, "上一页"),
        h("span", { class: "hint" }, total !== null ? `共 ${total} 条` : `第 ${offset / PAGE + 1} 页`),
        h("button", { class: "btn btn-outline btn-sm", disabled: !hasMore, onclick: () => turn(PAGE) }, "下一页")
      );
    } catch (err) {
      mount(body, errorBox(err));
    }
  }

  function turn(delta) {
    offset = Math.max(0, offset + delta);
    load();
  }
  load();
}
