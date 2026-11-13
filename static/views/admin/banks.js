// 题库版本发布
import { listBankVersions, publishBank, previewBank, confirmBankImport, exportBank, bankImportOptions } from "../../api/admin.js";
import { h, mount, loading, errorBox, fmtDateTime } from "../../components/dom.js";
import { dataTable } from "../../components/table.js";
import { toast, toastError } from "../../components/toast.js";

const STATUS = { draft: "草稿", active: "已发布", archived: "已归档" };

export async function render(view) {
  const body = h("div");
  const importBox = h("div");
  mount(view, h("section", { class: "card" }, h("h2", { class: "card-title" }, "导入题库"), importBox),
    h("section", { class: "card" }, h("h2", { class: "card-title" }, "题库版本"), body));
  renderImport(importBox, load);

  async function onPublish(bank) {
    if (!window.confirm(`发布「${bank.name || bank.code}」？当前题库将归档，之后练习使用此版本。`)) return;
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
              render: (r) => h("div", { class: "actions-inline" },
                r.status !== "active" ? h("button", { class: "btn btn-primary btn-sm", onclick: () => onPublish(r) }, r.status === "archived" ? "重新发布" : "发布") : null,
                ...[["xlsx", "导出Excel"], ["zip", "导出题库包"]].map(([format, label]) => {
                  const button = h("button", { type: "button", class: "btn btn-outline btn-sm", onclick: async () => {
                    button.disabled = true;
                    try { await exportBank(r.id, format); } catch (err) { toastError(err); }
                    finally { button.disabled = false; }
                  } }, label);
                  return button;
                })),
            },
          ],
          rows,
          "暂无题库，请先导入"
        )
      );
    } catch (err) {
      mount(body, errorBox(err));
    }
  }
  load();
}

async function renderImport(box, onImported) {
  mount(box, loading());
  let options;
  try { options = await bankImportOptions(); }
  catch (err) { mount(box, errorBox(err), h("button", { class: "btn btn-outline", onclick: () => renderImport(box, onImported) }, "重试")); return; }
  const code = h("input", { class: "input", required: true, maxlength: 64, pattern: "[A-Za-z0-9](?:[A-Za-z0-9_.]|-){0,63}", placeholder: "如 2026-12" });
  const name = h("input", { class: "input", required: true, maxlength: 100, placeholder: "如 2026年12月安规题库" });
  const files = h("input", { class: "input", type: "file", multiple: true, accept: ".xls,.xlsx,.zip,.json" });
  const folder = h("input", { class: "input", type: "file", webkitdirectory: true, multiple: true });
  files.addEventListener("change", () => { folder.value = ""; });
  folder.addEventListener("change", () => { files.value = ""; });
  const person = h("select", { class: "input" }, h("option", { value: "" }, "按目录自动识别"),
    options.person_categories.map(p => h("option", { value: p }, p)));
  const specialty = h("select", { class: "input" }, h("option", { value: "" }, "按文件名自动识别"),
    options.specialties.map(s => h("option", { value: s.code }, s.name)));
  const preview = h("div", { "aria-live": "polite" });
  const message = h("div", { role: "alert" });
  const submit = h("button", { type: "submit", class: "btn btn-primary" }, "预览导入");
  const field = (title, input) => h("label", { class: "field" }, h("span", null, title), input);
  const controls = h("fieldset", { class: "import-fields" },
    h("div", { class: "form-grid" }, field("新版本号", code), field("题库名称", name)),
    field("选择文件（Excel / ZIP / 完整题库包）", files), field("或选择题库目录", folder),
    h("p", { class: "hint" }, "合计不超过50MB。整批导入请保留人员目录；原始Excel可指定归属，本系统导出的文件保留原归属。"),
    h("div", { class: "form-grid" }, field("人员题库", person), field("专业", specialty)), submit);
  const form = h("form", { class: "form", onsubmit: async event => {
    event.preventDefault();
    const selected = Array.from(files.files.length ? files.files : folder.files)
      .filter(file => /\.(xls|xlsx|zip|json)$/i.test(file.name));
    if (!selected.length) { mount(message, errorBox("请选择题库文件")); return; }
    controls.disabled = true;
    submit.textContent = "正在解析…";
    mount(message); mount(preview);
    try {
      const result = await previewBank({ files: selected, code: code.value, name: name.value, person: person.value, specialty: specialty.value });
      const confirm = h("button", { type: "button", class: "btn btn-primary", onclick: async () => {
        confirm.disabled = true; controls.disabled = true;
        try {
          await confirmBankImport(result.token);
          toast("题库已保存为草稿，可在版本列表中发布", "success");
          form.reset(); mount(preview); await onImported();
        } catch (err) { mount(message, errorBox(err)); confirm.disabled = false; }
        finally { controls.disabled = false; }
      } }, "确认保存为草稿");
      const counts = Object.entries(result.counts).map(([type, count]) => ({ type, count }));
      mount(preview, h("h3", null, `${result.name} · ${result.code}`),
        h("p", null, `共 ${result.total} 道题，${result.sources} 个来源，保命题 ${result.protected} 道。`),
        result.raw_rows != null ? h("p", { class: "hint" }, `原始 ${result.raw_rows} 行，合并重复 ${result.duplicates} 行，未识别 ${result.invalid_rows} 行，跳过辅助工作表 ${result.skipped_sheets} 个。`) : null,
        dataTable([{ title: "题型", key: "type" }, { title: "题量", key: "count" }], counts),
        h("p", null, "人员题库：", Object.entries(result.personnel).map(([p, n]) => `${p} ${n}题`).join("、") || "未分配"),
        h("p", null, "专业：", Object.entries(result.specialty_counts).map(([code, n]) => `${options.specialties.find(s => s.code === code)?.name || code} ${n}题`).join("、") || "未分配"),
        result.conflicts ? h("p", { class: "alert alert-warning" }, `${result.conflicts} 道题存在答案冲突，已按来源多数答案合并，请核对原始资料。`) : null,
        result.warning_count ? h("div", { class: "alert alert-warning" }, h("p", null, `${result.warning_count} 道题的答案需要核对：`), result.warnings.map(text => h("p", null, text))) : null,
        result.invalid_samples?.length ? h("details", null, h("summary", null, "查看未识别行"), result.invalid_samples.map(row => h("p", null, `${row.rel_path} · ${row.sheet} · 第${row.row}行`))) : null,
        confirm);
    } catch (err) { mount(message, errorBox(err)); }
    finally { controls.disabled = false; submit.textContent = "预览导入"; }
  } }, controls, message, preview);
  controls.addEventListener("input", () => { mount(preview); mount(message); });
  controls.addEventListener("change", () => { mount(preview); mount(message); });
  mount(box, form);
}
