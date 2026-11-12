// 场次管理（名单 / 时间窗 / 次数上限 / 补考）与成绩导出
import { listBatches, createBatch, setBatchStatus, listTemplates, listBankVersions, exportScores } from "../../api/admin.js";
import { h, mount, loading, errorBox, fmtDateTime } from "../../components/dom.js";
import { dataTable } from "../../components/table.js";
import { toast, toastError } from "../../components/toast.js";

const STATUS = { draft: "未开放", open: "开放中", closed: "已关闭", cancelled: "已取消" };

/** datetime-local 值 → 带本地时区偏移的 ISO 字符串，避免服务端误判时区 */
function withOffset(local) {
  if (!local) return null;
  const min = -new Date(local).getTimezoneOffset();
  const sign = min >= 0 ? "+" : "-";
  const p = (n) => String(Math.floor(Math.abs(n))).padStart(2, "0");
  return `${local.length === 16 ? local + ":00" : local}${sign}${p(min / 60)}:${p(min % 60)}`;
}

async function doExport(batchId) {
  try {
    await exportScores({ batchId });
  } catch (err) {
    toastError(err);
  }
}

export async function render(view) {
  const listBox = h("div");
  const formBox = h("div");
  mount(
    view,
    h(
      "section",
      { class: "card" },
      h(
        "div",
        { class: "row-between" },
        h("h2", { class: "card-title" }, "场次"),
        h("button", { class: "btn btn-outline btn-sm", onclick: () => doExport() }, "导出全部成绩 CSV")
      ),
      listBox
    ),
    h("section", { class: "card" }, h("h2", { class: "card-title" }, "新建场次"), formBox)
  );

  async function onStatus(batch, status) {
    if (!window.confirm(`确定将场次「${batch.code}」设为${STATUS[status]}？`)) return;
    try {
      await setBatchStatus(batch.id, status);
      toast("已更新", "success");
      loadList();
    } catch (err) {
      toastError(err);
    }
  }

  async function loadList() {
    mount(listBox, loading());
    try {
      const rows = await listBatches();
      mount(
        listBox,
        dataTable(
          [
            { title: "编码", key: "code" },
            { title: "模板", render: (b) => b.template_name || b.template_id },
            { title: "题库版本", render: (b) => b.bank_version_code || b.bank_version_id },
            { title: "时间窗", render: (b) => `${fmtDateTime(b.start_at)} ~ ${fmtDateTime(b.end_at)}` },
            { title: "次数上限", key: "max_attempts" },
            { title: "补考", render: (b) => (b.allow_retake ? "允许" : "否") },
            { title: "名单人数", render: (b) => b.examinee_count ?? (b.examinee_list || []).length },
            { title: "状态", render: (b) => STATUS[b.status] || b.status },
            {
              title: "操作",
              render: (b) =>
                h(
                  "div",
                  { class: "actions-inline" },
                  b.status !== "open"
                    ? h("button", { class: "btn btn-ghost btn-sm", onclick: () => onStatus(b, "open") }, "开放")
                    : h("button", { class: "btn btn-ghost btn-sm", onclick: () => onStatus(b, "closed") }, "关闭"),
                  h("button", { class: "btn btn-ghost btn-sm", onclick: () => doExport(b.id) }, "导出成绩")
                ),
            },
          ],
          rows,
          "暂无场次"
        )
      );
    } catch (err) {
      mount(listBox, errorBox(err));
    }
  }

  async function loadForm() {
    mount(formBox, loading());
    let templates;
    let banks;
    try {
      [templates, banks] = await Promise.all([listTemplates(), listBankVersions()]);
    } catch (err) {
      mount(formBox, errorBox(err));
      return;
    }
    const code = h("input", { class: "input", required: true, placeholder: "如 2026-11-配电-第1场" });
    const template = h(
      "select",
      { class: "input", required: true },
      h("option", { value: "" }, "请选择"),
      templates.filter((t) => t.status === "active").map((t) => h("option", { value: String(t.id) }, t.name || t.code))
    );
    const bank = h(
      "select",
      { class: "input", required: true },
      h("option", { value: "" }, "请选择"),
      banks.filter((b) => b.status === "active").map((b) => h("option", { value: String(b.id) }, b.name || b.code))
    );
    const startAt = h("input", { class: "input", type: "datetime-local", required: true });
    const endAt = h("input", { class: "input", type: "datetime-local", required: true });
    const maxAttempts = h("input", { class: "input", type: "number", min: 1, value: "1", required: true });
    const allowRetake = h("input", { type: "checkbox" });
    const examinees = h("textarea", { class: "input", rows: 4, placeholder: "考生用户名，每行一个或以逗号分隔；留空表示不限名单（以服务端规则为准）" });

    async function onCreate(e) {
      e.preventDefault();
      if (endAt.value <= startAt.value) {
        toast("结束时间必须晚于开始时间", "warning");
        return;
      }
      const list = examinees.value
        .split(/[\s,，;；]+/)
        .map((s) => s.trim())
        .filter(Boolean);
      try {
        await createBatch({
          code: code.value.trim(),
          template_id: Number(template.value),
          bank_version_id: Number(bank.value),
          start_at: withOffset(startAt.value),
          end_at: withOffset(endAt.value),
          max_attempts: Number(maxAttempts.value),
          allow_retake: allowRetake.checked,
          examinee_list: list,
        });
        toast("场次已创建", "success");
        e.target.reset();
        loadList();
      } catch (err) {
        toastError(err);
      }
    }

    mount(
      formBox,
      h(
        "form",
        { class: "form-grid", onsubmit: onCreate },
        h("label", { class: "field" }, h("span", null, "场次编码 *"), code),
        h("label", { class: "field" }, h("span", null, "试卷模板 *"), template),
        h("label", { class: "field" }, h("span", null, "题库版本 *"), bank),
        h("label", { class: "field" }, h("span", null, "开始时间 *"), startAt),
        h("label", { class: "field" }, h("span", null, "结束时间 *"), endAt),
        h("label", { class: "field" }, h("span", null, "次数上限 *"), maxAttempts),
        h("label", { class: "field field-check" }, allowRetake, h("span", null, "允许补考")),
        h("label", { class: "field field-wide" }, h("span", null, "考生名单"), examinees),
        h("div", { class: "field field-end" }, h("button", { type: "submit", class: "btn btn-primary" }, "创建场次"))
      )
    );
  }

  loadList();
  loadForm();
}
