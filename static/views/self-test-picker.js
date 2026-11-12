// 按数量或比例换算题型数量；库存与开考仍使用服务端数量接口。
import { post, requestWithMeta } from "../api/http.js";
import { tagPicker } from "../components/tag-picker.js";
import { h, mount, loading, errorBox } from "../components/dom.js";
import { toastError } from "../components/toast.js";

import { TYPES, validateCounts, ratioCounts, countsToPercentages } from "../components/self-test-counts.js";
export async function renderSelfTestPicker(box, { bankId, dim, navigate }) {
  mount(box, loading("正在加载自由组卷范围…"));
  let facets;
  try { facets = await post("/api/self-tests/inventory", { bank_id: Number(bankId) }); }
  catch (err) { mount(box, errorBox(err)); return; }
  const field = (title, input) => h("label", { class: "field" }, h("span", null, title), input);
  const select = (options) => h("select", { class: "input", onchange: refresh },
    options.map(([value, title]) => h("option", { value }, title)));
  const person = select([["", "全部人员类型"], ...(dim.person_categories || []).filter(p => p.question_count > 0).map(p => [p.code, p.name])]);
  const specialty = select([["", "全部专业"], ...facets.specialties.map(s => [s.code, s.name])]);
  const file = select([["", "全部来源"], ...facets.sources.map(s => [String(s.id), `${s.rel_path}${s.sheet ? ` · ${s.sheet}` : ""}`])]);
  const tags = tagPicker(facets.tags, refresh);
  const stock = Object.fromEntries(TYPES.map(q => [q, h("span", { class: "hint" })]));
  const inputs = Object.fromEntries(TYPES.map((q, i) => [q, h("input", { class: "input", type: "number", min: 0, max: 200, step: 1, value: [40,30,30,0][i], required: true, oninput: summarize })]));
  const ratios = TYPES.map((q, i) => h("input", { class: "input", type: "number", min: 0, max: 100, step: 1, value: [40,30,30,0][i], required: true, disabled: true, oninput: summarize, "aria-label": `${q}百分比` }));
  const total = h("input", { class: "input", type: "number", min: 1, max: 200, step: 1, value: 100, required: true, disabled: true, oninput: summarize });
  const actual = TYPES.map(() => h("span", { class: "hint" }));
  const quantityPanel = h("div", null, h("div", { class: "form-grid" }, TYPES.map(q => field(`${q}数量`, inputs[q]))));
  const ratioPanel = h("div", { hidden: true }, field("总题量（1~200 题）", total),
    h("div", { class: "form-grid" }, TYPES.map((q, i) => h("label", { class: "field" }, h("span", null, `${q}比例（%）`), ratios[i], actual[i]))),
    h("p", { class: "hint" }, "比例须为整数且合计 100%；按最大余数法换算，余数并列依单选、多选、判断、简答顺序补题。0% 不分配题目。"));
  let mode = "quantity", lastQuantity = "";
  const modeSelect = h("select", { class: "input", "aria-label": "组卷模式", onchange: () => {
    const values = TYPES.map(q => inputs[q].value);
    if (modeSelect.value === "ratio") {
      if (validateCounts(values) && JSON.stringify(values) !== lastQuantity) {
        total.value = values.reduce((sum, v) => sum + Number(v), 0);
        countsToPercentages(values).forEach((v, i) => { ratios[i].value = v; });
      }
    } else {
      try { ratioCounts(total.value, ratios.map(input => input.value)).forEach((v, i) => { inputs[TYPES[i]].value = v; }); }
      catch { /* 无效比例不覆盖上次有效的数量，可切回直接修正。 */ }
      lastQuantity = JSON.stringify(TYPES.map(q => inputs[q].value));
    }
    mode = modeSelect.value;
    quantityPanel.hidden = mode !== "quantity";
    ratioPanel.hidden = mode !== "ratio";
    TYPES.forEach(q => { inputs[q].disabled = mode !== "quantity"; });
    [...ratios, total].forEach(input => { input.disabled = mode !== "ratio"; });
    summarize();
  } }, h("option", { value: "quantity" }, "按数量"), h("option", { value: "ratio" }, "按比例"));
  const duration = h("input", { class: "input", type: "number", min: 5, max: 240, step: 1, value: 60, required: true });
  const summary = h("p", { role: "status", "aria-live": "polite" });
  const status = h("p", { class: "hint", role: "status" });
  const start = h("button", { type: "submit", class: "btn btn-primary", disabled: true }, "随机组卷并开考");
  let inventory = null, request = 0, busy = false;
  function scope() {
    return { bank_id: Number(bankId), person_category: person.value || null, specialty_code: specialty.value || null,
      tags: tags.value, source_file_ids: file.value ? [Number(file.value)] : [] };
  }
  function counts() {
    const values = mode === "ratio" ? ratioCounts(total.value, ratios.map(input => input.value)) : TYPES.map(q => Number(inputs[q].value));
    return Object.fromEntries(TYPES.map((q, i) => [q, values[i]]));
  }
  function summarize() {
    let c = {}, message = "";
    try {
      c = counts();
      if (mode === "quantity" && !validateCounts(TYPES.map(q => inputs[q].value))) message = "请填写总计 1~200 道题的非负整数数量。";
    } catch (err) { message = err.message; }
    const valid = !message, n = Object.values(c).reduce((a,b) => a+b, 0);
    actual.forEach((el, i) => { el.textContent = valid ? `实际 ${c[TYPES[i]]} 题` : "实际题数：待有效比例"; });
    summary.textContent = valid ? `总题数 ${n} / 200 · 固定总分 100 分` : `${message} · 固定总分 100 分`;
    let shortage = false;
    TYPES.forEach(q => {
      const available = inventory?.[q];
      const missing = available != null ? Math.max(0, (c[q] || 0)-available) : 0;
      stock[q].textContent = available == null ? "库存统计中…" : `匹配库存 ${available} 题${missing ? `；不足 ${missing} 题` : ""}`;
      shortage ||= missing > 0;
    });
    start.disabled = busy || !inventory || !valid || shortage;
    if (inventory) status.textContent = !valid ? message : shortage ? "部分题型不足，请减少该型数量或扩大筛选范围；不会用其他题型补足。" : "库存充足，可以开始。";
  }
  async function refresh() {
    const id = ++request;
    inventory = null;
    status.textContent = "正在统计所选范围…";
    summarize();
    try {
      const data = await post("/api/self-tests/inventory", scope());
      if (id !== request) return;
      inventory = data.counts;
      summarize();
    } catch (err) { if (id === request) status.textContent = `库存查询失败：${err.message}，请调整范围重试。`; }
  }
  async function begin(event) {
    event.preventDefault();
    summarize();
    if (start.disabled || !event.target.reportValidity()) return;
    if (!window.confirm(`开始非正式自主测试？限时 ${duration.value} 分钟，开始后不能暂停；简答需交卷后本人自评。`)) return;
    busy = true; summarize();
    try {
      const { data } = await requestWithMeta("POST", "/api/self-tests/start", { body: { ...scope(), counts: counts(), duration_min: Number(duration.value) } });
      navigate(`/exam/${data.attempt_id}`);
    } catch (err) { toastError(err); busy = false; summarize(); }
  }
  mount(box, h("form", { class: "form self-test-form", onsubmit: begin },
    h("p", { class: "alert alert-warning" }, "非正式自主测试：无需管理员模板、场次或人员授权，所有人员类别均可自由选择。客观题服务端评分；简答题交卷后对照参考答案由本人自评，不是自动评分。"),
    h("div", { class: "form-grid" }, field("人员类型", person), field("专业（真实题库标签，不限启用状态）", specialty),
      h("label", { class: "field field-wide" }, h("span", null, "题目来源 / 工作表"), file)),
    h("div", { class: "field" }, h("span", null, "题目标签（多选取交集）"), tags),
    h("h3", null, "题型分配"),
    field("组卷模式", modeSelect), quantityPanel, ratioPanel,
    h("div", { class: "form-grid" }, TYPES.map(q => h("div", { class: "field" }, h("span", null, q), stock[q])), field("时长（5~240 分钟）", duration)),
    h("button", { type: "button", class: "btn btn-outline", onclick: () => {
      if (mode === "ratio") { total.value = 100; [80,0,10,10].forEach((v, i) => { ratios[i].value = v; }); }
      else [80,0,10,10].forEach((v,i) => { inputs[TYPES[i]].value = v; });
      summarize();
    } }, "填入示例：80 单选 + 10 判断 + 10 简答"),
    summary,
    h("p", { class: "hint" }, "100 分按实际题数平均分配，以 0.01 分为最小单位；除不尽的余数依锁卷顺序给前几题各加 0.01 分。例如 3 题为 33.34 / 33.33 / 33.33 分，合计严格 100 分。"),
    status, start));
  refresh();
}
