// 按数量或比例换算题型数量；库存与开考仍使用服务端数量接口。
import { post, requestWithMeta } from "../api/http.js";
import { scopePicker } from "../components/scope-picker.js";
import { h, mount, loading, errorBox } from "../components/dom.js";
import { toastError } from "../components/toast.js";

import { TYPES, validateCounts, ratioCounts, countsToPercentages } from "../components/self-test-counts.js";
export async function renderSelfTestPicker(box, { bankId, dim, navigate }) {
  mount(box, loading("正在加载自由组卷范围…"));
  let facets;
  try { facets = await post("/api/self-tests/inventory", { bank_id: Number(bankId) }); }
  catch (err) { mount(box, errorBox(err)); return; }
  const field = (title, input) => h("label", { class: "field" }, h("span", null, title), input);
  const scopeControl = scopePicker({ bankId, kind: "exam", dim, facets, onChange: refresh });
  const stock = Object.fromEntries(TYPES.map(q => [q, h("span", { class: "hint" })]));
  const inputs = Object.fromEntries(TYPES.map((q, i) => [q, h("input", { class: "input", type: "number", min: 0, max: 200, step: 1, value: [40,30,30,0][i], required: true, oninput: summarize })]));
  const ratios = TYPES.map((q, i) => h("input", { class: "input", type: "number", min: 0, max: 100, step: 1, value: [40,30,30,0][i], required: true, disabled: true, oninput: summarize, "aria-label": `${q}百分比` }));
  const total = h("input", { class: "input", type: "number", min: 1, max: 200, step: 1, value: 100, required: true, disabled: true, oninput: summarize });
  const actual = TYPES.map(() => h("span", { class: "hint" }));
  const quantityPanel = h("div", null, h("div", { class: "form-grid" }, TYPES.map(q => field(`${q}数量`, inputs[q]))));
  const ratioPanel = h("div", { hidden: true }, field("总题量（1~200 题）", total),
    h("div", { class: "form-grid" }, TYPES.map((q, i) => h("label", { class: "field" }, h("span", null, `${q}比例（%）`), ratios[i], actual[i]))),
    h("p", { class: "hint" }, "各题型比例合计需为 100%。"));
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
    return { bank_id: Number(bankId), ...scopeControl.value() };
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
      stock[q].textContent = available == null ? "题量统计中…" : `可选 ${available} 题${missing ? `；还缺 ${missing} 题` : ""}`;
      shortage ||= missing > 0;
    });
    start.disabled = busy || !inventory || !valid || shortage;
    if (inventory) status.textContent = !valid ? message : shortage ? "题量不足，请减少数量或扩大选题范围。" : "";
  }
  async function refresh() {
    const id = ++request;
    inventory = null;
    if (!scopeControl.valid()) { summarize(); status.textContent = "请选择重点人员或一线人员题库。"; return; }
    status.textContent = "正在统计所选范围…";
    summarize();
    try {
      const data = await post("/api/self-tests/inventory", scope());
      if (id !== request) return;
      inventory = data.counts;
      summarize();
    } catch (err) { if (id === request) status.textContent = `题量查询失败：${err.message}，请调整范围重试。`; }
  }
  async function begin(event) {
    event.preventDefault();
    summarize();
    if (start.disabled || !event.target.reportValidity()) return;
    if (!window.confirm(`开始模拟考试？限时 ${duration.value} 分钟。`)) return;
    busy = true; summarize();
    try {
      const { data } = await requestWithMeta("POST", "/api/self-tests/start", { body: { ...scope(), counts: counts(), duration_min: Number(duration.value) } });
      navigate(`/exam/${data.attempt_id}`);
    } catch (err) { toastError(err); busy = false; summarize(); }
  }
  mount(box, h("form", { class: "form self-test-form", onsubmit: begin },
    scopeControl.element,
    h("h3", null, "题型分配"),
    field("组卷模式", modeSelect), quantityPanel, ratioPanel,
    h("div", { class: "form-grid" }, TYPES.map(q => h("div", { class: "field" }, h("span", null, q), stock[q])), field("时长（5~240 分钟）", duration)),
    h("button", { type: "button", class: "btn btn-outline", onclick: () => {
      if (mode === "ratio") { total.value = 100; [80,0,10,10].forEach((v, i) => { ratios[i].value = v; }); }
      else [80,0,10,10].forEach((v,i) => { inputs[TYPES[i]].value = v; });
      summarize();
    } }, "填入示例：80 单选 + 10 判断 + 10 简答"),
    summary,
    status, start));
  refresh();
}
