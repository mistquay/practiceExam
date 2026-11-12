// 题库版本发布
import { listBankVersions, publishBank } from "../../api/admin.js";
import { h, mount, loading, errorBox, fmtDateTime } from "../../components/dom.js";
import { dataTable } from "../../components/table.js";
import { toast, toastError } from "../../components/toast.js";

const STATUS = { draft: "草稿", active: "已发布", archived: "已归档" };

export async function render(view) {
  const body = h("div");
  mount(view, h("section", { class: "card" }, h("h2", { class: "card-title" }, "题库版本"), body));

  async function onPublish(bank) {
    if (!window.confirm(`确定发布题库版本「${bank.name || bank.code}」？发布后考生即可按该版本练习与考试。`)) return;
    try {
      await publishBank(bank.id);
      toast("已发布", "success");
      load();
    } catch (err) {
      toastError(err);
    }
  }

  async function load() {
    mount(body, loading());
    try {
      const rows = await listBankVersions();
      mount(
        body,
        dataTable(
          [
            { title: "版本号", key: "code" },
            { title: "名称", key: "name" },
            { title: "生效日期", key: "effective_date" },
            { title: "导入时间", render: (r) => fmtDateTime(r.imported_at) },
            { title: "题量", render: (r) => r.question_count ?? "—" },
            { title: "状态", render: (r) => STATUS[r.status] || r.status },
            { title: "发布人", render: (r) => r.published_by || "—" },
            {
              title: "操作",
              render: (r) =>
                r.status === "draft"
                  ? h("button", { class: "btn btn-primary btn-sm", onclick: () => onPublish(r) }, "发布")
                  : "—",
            },
          ],
          rows,
          "暂无题库版本（请先用导入 CLI 导入）"
        )
      );
    } catch (err) {
      mount(body, errorBox(err));
    }
  }
  load();
}
