// 错题本（服务端 /api/records/wrong，仅本人）
import { listWrong, removeWrong, clearWrong } from "../api/records.js";
import { listBanks } from "../api/banks.js";
import { listOf } from "../api/http.js";
import { getSelection } from "../state/selection.js";
import { h, mount, loading, errorBox, fmtDateTime } from "../components/dom.js";
import { dataTable } from "../components/table.js";
import { toast, toastError } from "../components/toast.js";

export async function render(view, { navigate }) {
  const { bankId, specialtyCode } = getSelection();
  const body = h("div");
  const practiceBtn = h("button", { class: "btn btn-primary btn-sm" }, "练习错题");
  const clearBtn = h("button", { class: "btn btn-danger btn-sm" }, "清空错题本");

  function practiceWrong(bank) {
    const q = new URLSearchParams({ bank, person: "", order: "random", source: "wrong", count: "50" });
    if (specialtyCode) q.set("specialty", specialtyCode);
    navigate(`/practice?${q.toString()}`);
  }

  // 练习已不要求专业；未在选题页选过题库时，默认用第一个已发布题库
  practiceBtn.addEventListener("click", async () => {
    if (bankId) {
      practiceWrong(bankId);
      return;
    }
    practiceBtn.disabled = true;
    try {
      const banks = (await listBanks()).filter((b) => !b.status || b.status === "active");
      if (banks.length) practiceWrong(String(banks[0].id));
      else toast("当前没有已发布的题库版本", "warning");
    } catch (err) {
      toastError(err);
    } finally {
      practiceBtn.disabled = false;
    }
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
