// 错题本（服务端 /api/records/wrong，仅本人）
import { listWrong, removeWrong, clearWrong } from "../api/records.js";
import { listOf } from "../api/http.js";
import { getSelection } from "../state/selection.js";
import { getAuthorization } from "../state/session.js";
import { h, mount, loading, errorBox, fmtDateTime } from "../components/dom.js";
import { dataTable } from "../components/table.js";
import { toast, toastError } from "../components/toast.js";

export async function render(view, { navigate }) {
  const { bankId, specialtyCode } = getSelection();
  const body = h("div");
  const practiceBtn = h("button", { class: "btn btn-primary btn-sm" }, "练习错题");
  const clearBtn = h("button", { class: "btn btn-danger btn-sm" }, "清空错题本");

  practiceBtn.addEventListener("click", () => {
    if (!bankId || !specialtyCode) {
      toast("请先在选题页选择题库与专业", "warning");
      navigate("/");
      return;
    }
    const auth = getAuthorization();
    const q = new URLSearchParams({
      bank: bankId,
      person: (auth && auth.person_category) || "",
      specialty: specialtyCode,
      order: "random",
      source: "wrong",
      count: "50",
    });
    navigate(`/practice?${q.toString()}`);
  });

  clearBtn.addEventListener("click", async () => {
    if (!window.confirm("确定清空全部错题记录吗？此操作不可恢复。")) return;
    try {
      await clearWrong();
      toast("错题本已清空", "success");
      load();
    } catch (err) {
      toastError(err);
    }
  });

  mount(
    view,
    h(
      "section",
      { class: "card" },
      h("div", { class: "row-between" }, h("h1", { class: "card-title" }, "错题本"), h("div", { class: "actions" }, practiceBtn, clearBtn)),
      body
    )
  );

  async function remove(row) {
    try {
      await removeWrong(row.stable_id);
      load();
    } catch (err) {
      toastError(err);
    }
  }

  async function load() {
    mount(body, loading());
    try {
      const rows = listOf(await listWrong(bankId));
      mount(
        body,
        dataTable(
          [
            { title: "题干", render: (r) => h("div", { class: "cell-stem" }, r.stem || r.stable_id) },
            { title: "题型", key: "qtype" },
            { title: "错误次数", key: "wrong_count" },
            { title: "最近答错", render: (r) => fmtDateTime(r.last_wrong_at) },
            { title: "已掌握", render: (r) => (r.mastered ? "是" : "否") },
            { title: "操作", render: (r) => h("button", { class: "btn btn-ghost btn-sm", onclick: () => remove(r) }, "移除") },
          ],
          rows,
          "错题本为空"
        )
      );
    } catch (err) {
      mount(body, errorBox(err));
    }
  }
  load();
}
