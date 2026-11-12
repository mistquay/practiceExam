// 原生复选框组成的下拉多选；移除组件时自动解绑文档监听。
import { h, mount } from "./dom.js";

let nextId = 0;
class TagPicker extends HTMLElement {
  connectedCallback() {
    this.outside = (event) => {
      if (!this.contains(event.target)) this.close(false);
    };
    document.addEventListener("pointerdown", this.outside);
  }
  disconnectedCallback() {
    document.removeEventListener("pointerdown", this.outside);
  }
  setup(items, onChange) {
    this.className = "tag-picker";
    const id = `tag-picker-${++nextId}`;
    this.inputs = items.map((item) => ({ ...item,
      input: h("input", { type: "checkbox", value: item.code, onchange: () => this.changed() }) }));
    this.onChange = onChange;
    this.trigger = h("button", { type: "button", class: "input tag-picker-trigger",
      "aria-expanded": "false", "aria-controls": id,
      onclick: () => this.panel.hidden ? this.open() : this.close(false) });
    this.panel = h("div", { id, class: "tag-picker-panel", hidden: true },
      h("div", { class: "tag-picker-options", role: "group", "aria-label": "题目标签，多选取交集" },
        this.inputs.map((item) => h("label", { class: "tag-picker-option" }, item.input, h("span", null, item.name)))),
      h("div", { class: "tag-picker-actions" },
        h("button", { type: "button", class: "btn btn-outline", onclick: () => { this.value = []; this.changed(); } }, "清空"),
        h("button", { type: "button", class: "btn btn-outline", onclick: () => { this.value = this.before; this.changed(); this.close(true); } }, "取消"),
        h("button", { type: "button", class: "btn btn-primary", onclick: () => this.close(true) }, "完成")));
    this.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !this.panel.hidden) {
        event.preventDefault(); event.stopPropagation(); this.close(true);
      }
      if (event.key === "ArrowDown" && event.target === this.trigger) {
        event.preventDefault(); this.open(); this.inputs[0]?.input.focus();
      }
    });
    this.addEventListener("focusout", (event) => {
      // 点击 label 空白处会先失焦，再由浏览器将焦点交给 checkbox。
      // 不在这一瞬间折叠面板，否则真实指针点击会丢失；外部点击由 pointerdown 处理。
      if (event.relatedTarget && !this.contains(event.relatedTarget)) this.close(false);
    });
    mount(this, this.trigger, this.panel);
    this.updateSummary();
    return this;
  }
  get value() { return this.inputs.filter((item) => item.input.checked).map((item) => item.code); }
  set value(values) {
    this.inputs.forEach((item) => { item.input.checked = values.includes(item.code); });
    this.updateSummary();
  }
  updateSummary() {
    const selected = this.inputs.filter((item) => item.input.checked);
    this.trigger.textContent = selected.length
      ? `已选 ${selected.length} 项：${selected.map((item) => item.name).join("、")}`
      : "全部标签（点击多选）";
  }
  changed() { this.updateSummary(); this.onChange(); }
  open() {
    if (!this.panel.hidden) return;
    this.before = this.value;
    this.panel.hidden = false;
    this.trigger.setAttribute("aria-expanded", "true");
  }
  close(focus) {
    if (!this.panel || this.panel.hidden) return;
    this.panel.hidden = true;
    this.trigger.setAttribute("aria-expanded", "false");
    if (focus) this.trigger.focus();
  }
}
customElements.define("exam-tag-picker", TagPicker);
export const tagPicker = (items, onChange) => document.createElement("exam-tag-picker").setup(items, onChange);
