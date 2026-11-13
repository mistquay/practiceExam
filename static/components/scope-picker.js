import { h } from "./dom.js";
import { tagPicker } from "./tag-picker.js";
import { getScope, saveScope } from "../state/selection.js";

export function scopePicker({ bankId, kind, dim, facets, onChange }) {
  const saved = getScope(bankId, kind);
  const mode = h("select", { class: "input", "aria-label": "选题方式", onchange: changed },
    h("option", { value: "custom" }, "自由选题"), h("option", { value: "notice" }, "按通知选题"));
  const person = h("select", { class: "input", "aria-label": "人员题库", onchange: changed },
    h("option", { value: "" }, "全部人员题库"),
    (dim.person_categories || []).filter(p => p.question_count > 0).map(p => h("option", { value: p.code }, p.name)));
  const specialties = tagPicker(facets.specialties || [], changed, { title: "专业", empty: "全部专业（点击多选）" });
  const files = tagPicker(facets.sources.map(s => ({ code: String(s.id), name: `${s.rel_path}${s.sheet ? ` · ${s.sheet}` : ""}（${s.question_count}题）` })), changed,
    { title: "题目来源", empty: "全部来源（点击多选）", search: true, summaryLimit: 2 });
  const tags = tagPicker(facets.tags, changed);
  const hint = h("p", { class: "hint", hidden: true }, "人员基础题库 + 所选专业题库");
  const field = (title, control) => h("div", { class: "field" }, h("span", null, title), control);
  const element = h("div", { class: "form" },
    h("div", { class: "form-grid" }, field("选题方式", mode), field("人员题库", person), field("专业", specialties)), hint,
    field("题目来源", files), field("题目标签", tags));
  mode.value = saved.scope_mode || "custom";
  person.value = saved.person_category || "";
  specialties.value = saved.specialty_codes || [];
  files.value = (saved.source_file_ids || []).map(String);
  tags.value = saved.tags || [];
  let previousMode = mode.value;
  function adjust() {
    const notice = mode.value === "notice";
    Array.from(person.options).forEach(option => {
      option.hidden = notice && option.value && !["重点人员", "一线人员"].includes(option.value);
    });
    person.options[0].textContent = notice ? "请选择人员题库" : "全部人员题库";
    specialties.empty = notice ? "选择专业（可多选）" : "全部专业（点击多选）";
    specialties.updateSummary();
    if (notice && !["重点人员", "一线人员"].includes(person.value)) person.value = "";
    hint.hidden = !notice;
  }
  function value() {
    return { scope_mode: mode.value, person_category: person.value || null, specialty_codes: specialties.value,
      source_file_ids: files.value.map(Number), tags: tags.value };
  }
  function changed() {
    if (mode.value === "notice" && previousMode !== "notice") {
      specialties.value = ["pd_general", "pd_live"];
      files.value = []; tags.value = [];
    }
    previousMode = mode.value;
    adjust();
    saveScope(bankId, kind, value());
    onChange();
  }
  adjust();
  return { element, value, valid: () => mode.value !== "notice" || !!person.value,
    reset() { mode.value = "custom"; person.value = ""; specialties.value = []; files.value = []; tags.value = []; changed(); } };
}
