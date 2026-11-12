// 考试历史（仅本人）
import { listMyExams } from "../api/exam.js";
import { h, mount, loading, errorBox, fmtDateTime } from "../components/dom.js";
import { dataTable } from "../components/table.js";

const STATUS = { in_progress: "作答中", submitted: "已交卷", graded: "已出分", pending_review: "待阅卷", expired: "已超时" };
const TYPES = { mock: "模拟", formal: "正式", makeup: "补考" };

export async function render(view) {
  const body = h("div");
  mount(view, h("section", { class: "card" }, h("h1", { class: "card-title" }, "考试记录"), body));
  mount(body, loading());
  try {
    const rows = await listMyExams();
    mount(
      body,
      dataTable(
        [
          { title: "考试", render: (r) => r.name || r.template_name || `#${r.id}` },
          { title: "类型", render: (r) => TYPES[r.paper_type] || r.paper_type || "—" },
          { title: "开始", render: (r) => fmtDateTime(r.started_at) },
          { title: "交卷", render: (r) => fmtDateTime(r.submitted_at) },
          { title: "状态", render: (r) => STATUS[r.status] || r.status || "—" },
          { title: "得分", render: (r) => (r.score != null ? `${r.score} / ${r.total_score ?? "—"}` : "—") },
          {
            title: "结论",
            render: (r) =>
              r.passed === true
                ? h("span", { class: "badge badge-ok" }, "通过")
                : r.passed === false
                  ? h("span", { class: "badge badge-bad" }, "未通过")
                  : "—",
          },
          {
            title: "",
            render: (r) =>
              h("a", { href: `#/exam/${r.id}`, class: "btn btn-ghost btn-sm" }, r.status === "in_progress" ? "继续作答" : "查看"),
          },
        ],
        rows,
        "暂无考试记录"
      )
    );
  } catch (err) {
    mount(body, errorBox(err));
  }
}
