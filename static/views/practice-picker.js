// 练习直接可用，不依赖人员类别、专业或考试授权。
import { scopePicker } from "../components/scope-picker.js";
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
  const order = select([["sequence", "顺序练习"], ["random", "随机练习"]]);
  const source = select([["unpracticed", "未练习的题目"], ["all", "全部题目"], ["wrong", "错题"]]);
  const qtypes = tagPicker(dim.qtypes || [], refresh, { title: "题型", empty: "全部题型（点击多选）" });
  const count = h("input", { class: "input", type: "number", min: 1, max: 200, value: "20", required: true });
  const scope = scopePicker({ bankId, kind: "practice", dim, facets, onChange: refresh });
  const status = h("p", { class: "hint", role: "status", "aria-live": "polite", "aria-atomic": "true" });
  const start = h("button", { class: "btn btn-primary", type: "submit", disabled: true }, "开始练习");
  let request = 0;
  function params() {
    return { bank_id: bankId, ...scope.value(), qtypes: qtypes.value, source: source.value, order: order.value };
  }
  async function refresh() {
    const current = ++request;
    start.disabled = true;
    if (!scope.valid()) { status.textContent = "请选择重点人员或一线人员题库。"; return; }
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
    const query = new URLSearchParams({ bank: bankId, person: p.person_category || "", scope_mode: p.scope_mode, source: p.source, order: p.order, count: String(n) });
    p.qtypes.forEach((type) => query.append("qtypes", type));
    p.specialty_codes.forEach((code) => query.append("specialty_codes", code));
    p.tags.forEach((tag) => query.append("tags", tag));
    p.source_file_ids.forEach((id) => query.append("source_file_ids", id));
    navigate(`/practice?${query}`);
  }
  const field = (name, input, tag = "label") => h(tag, { class: "field" }, h("span", null, name), input);
  mount(box, h("form", { class: "form", onsubmit: begin },
    scope.element,
    h("div", { class: "form-grid" },
      field("题型（可多选）", qtypes, "div"), field("题目范围", source), field("练习顺序", order), field("题目数量", count)),
    status, h("div", { class: "actions" }, start,
      h("button", { class: "btn btn-outline", type: "button", onclick: () => {
        qtypes.value = [];
        source.value = "all";
        scope.reset();
      } }, "清空筛选"))));
  refresh();
}
