// 试卷模板启停（模板内容由配置 / 后端维护，这里只做启用与停用）
import { listTemplates, setTemplateStatus } from "../../api/admin.js";
import { h, mount, loading, errorBox } from "../../components/dom.js";
import { dataTable } from "../../components/table.js";
import { toast, toastError } from "../../components/toast.js";

const TYPES = { practice: "练习", mock: "模拟", formal: "正式", makeup: "补考" };

export async function render(view) {
  const body = h("div");
  mount(
    view,
    h(
      "section",
      { class: "card" },
      h("h2", { class: "card-title" }, "试卷模板"),
      h("p", { class: "hint" }, "停用只影响新开考；已开考的试卷按锁卷快照继续。"),
      body
    )
  );

  async function onToggle(t) {
    const next = t.status === "active" ? "disabled" : "active";
    if (!window.confirm(`确定${next === "active" ? "启用" : "停用"}模板「${t.name || t.code}」？`)) return;
    try {
      await setTemplateStatus(t.id, next);
      toast("已更新", "success");
      load();
    } catch (err) {
      toastError(err);
    }
  }

  async function load() {
    mount(body, loading());
    try {
      const rows = await listTemplates();
      mount(
        body,
        dataTable(
          [
            { title: "编码", key: "code" },
            { title: "名称", key: "name" },
            { title: "版本", key: "version" },
            { title: "类型", render: (t) => TYPES[t.paper_type] || t.paper_type || "—" },
            { title: "人员类别", key: "person_category" },
            { title: "总分 / 及格", render: (t) => `${t.total_score ?? "—"} / ${t.pass_score ?? "—"}` },
            { title: "时长(分)", key: "duration_min" },
            {
              title: "状态",
              render: (t) =>
                t.status === "active"
                  ? h("span", { class: "badge badge-ok" }, "启用")
                  : h("span", { class: "badge" }, "停用"),
            },
            {
              title: "操作",
              render: (t) =>
                h(
                  "button",
                  { class: "btn btn-ghost btn-sm", onclick: () => onToggle(t) },
                  t.status === "active" ? "停用" : "启用"
                ),
            },
          ],
          rows,
          "暂无模板"
        )
      );
    } catch (err) {
      mount(body, errorBox(err));
    }
  }
  load();
}
