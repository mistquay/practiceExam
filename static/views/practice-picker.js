// 练习直接可用，不依赖人员类别、专业或考试授权。
import { tagPicker } from "../components/tag-picker.js";
import { fetchPracticeFilters } from "../api/practice.js";
import { h, mount, loading, errorBox } from "../components/dom.js";
import { toastError } from "../components/toast.js";

export async function renderPracticePicker(box, { bankId, dim, navigate }) {
  mount(box, loading("正在加载练习筛选…"));
  let facets;
  try {
    facets = await fetchPracticeFilters({ bank_id: bankId });
  } catch (err) {
    const retry = h("button", { class: "btn btn-outline", type: "button",
      onclick: () => renderPracticePicker(box, { bankId, dim, navigate }) }, "重试");
    mount(box, errorBox(err), retry);
    return;
  }
  const select = (items) => h("select", { class: "input", onchange: refresh },
    items.map(([value, text]) => h("option", { value }, text)));
  const person = select([["", "全部人员类型"], ...(dim.person_categories || []).map((p) => [p.code, p.name])]);
  const order = select([["sequence", "顺序练习"], ["random", "随机练习"]]);
  const source = select([["all", "全部题目"], ["unpracticed", "仅未练习"], ["wrong", "仅错题"]]);
  const qtype = select([["", "全部题型"], ...(dim.qtypes || []).map((q) => [q.code, q.name])]);
  const file = select([["", "全部来源"], ...facets.sources.map((s) =>
    [String(s.id), `${s.rel_path}${s.sheet ? ` · 工作表 ${s.sheet}` : ""}（${s.question_count}题）`])]);
  const sourceDescription = h("span", { id: "practice-source-description", class: "hint practice-source-description" }, "包含每道题的全部来源，重复题只出一次。");
  file.setAttribute("aria-describedby", "practice-source-description");
  const count = h("input", { class: "input", type: "number", min: 1, max: 200, value: "20", required: true });
  const tags = tagPicker(facets.tags, refresh);
  const status = h("p", { class: "hint", role: "status", "aria-live": "polite", "aria-atomic": "true" });
  const start = h("button", { class: "btn btn-primary", type: "submit", disabled: true }, "开始练习");
  let request = 0;
  function params() {
    return { bank_id: bankId, person_category: person.value, qtype: qtype.value, source: source.value, order: order.value,
      tags: tags.value,
      source_file_ids: file.value ? [file.value] : [] };
  }
  async function refresh() {
    const current = ++request;
    sourceDescription.textContent = file.value ? file.selectedOptions[0].textContent : "包含每道题的全部来源，重复题只出一次。";
    start.disabled = true;
    status.textContent = "正在统计匹配题目…";
    try {
      const result = await fetchPracticeFilters(params());
      if (current !== request) return;
      status.textContent = result.total ? `匹配 ${result.total} 道题；每次最多练习 200 道。` : "没有符合条件的题目，请减少标签或更换来源、题型。";
      start.disabled = result.total === 0;
    } catch (err) {
      if (current === request) status.textContent = `统计失败：${err.message}。请调整筛选重试。`;
    }
  }
  function begin(event) {
    event.preventDefault();
    if (start.disabled) return;
    const n = Number(count.value);
    if (!Number.isInteger(n) || n < 1 || n > 200) {
      toastError("题目数量需为 1~200 的整数");
      return;
    }
    const p = params();
    const query = new URLSearchParams({ bank: bankId, person: p.person_category, qtype: p.qtype, source: p.source, order: p.order, count: String(n) });
    p.tags.forEach((tag) => query.append("tags", tag));
    p.source_file_ids.forEach((id) => query.append("source_file_ids", id));
    navigate(`/practice?${query}`);
  }
  const field = (name, input) => h("label", { class: "field" }, h("span", null, name), input);
  mount(box, h("form", { class: "form", onsubmit: begin },
    h("p", { class: "hint" }, "登录即可练习全部已发布题库，无需人员或专业授权。人员类型默认全部，可自由选择；标签、来源和题型可组合筛选。"),
    h("div", { class: "field" }, h("span", null, "题目标签（多选取交集，不选为全部）"), tags),
    h("div", { class: "form-grid" },
      h("label", { class: "field field-wide" }, h("span", null, "题目来源（完整路径 / 工作表）"), file, sourceDescription),
      field("人员类型", person), field("题型", qtype), field("题目范围", source), field("练习顺序", order), field("题目数量", count)),
    status, h("div", { class: "actions" }, start,
      h("button", { class: "btn btn-outline", type: "button", onclick: () => {
        tags.value = [];
        person.value = "";
        file.value = "";
        qtype.value = "";
        source.value = "all";
        refresh();
      } }, "清空筛选"))));
  refresh();
}
