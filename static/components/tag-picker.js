// 原生复选框组成的下拉多选；移除组件时自动解绑文档监听。
import { h, mount } from "./dom.js";

let nextId = 0;
class TagPicker extends HTMLElement {
  connectedCallback() {
    this.outside = (event) => {
      if (!this.contains(event.target)) this.close(false);
    };
    this.reposition = () => this.positionPanel();
    this.onScroll = (event) => {
      if (!this.panel.contains(event.target)) this.positionPanel();
    };
    document.addEventListener("pointerdown", this.outside);
  }
  disconnectedCallback() {
    this.close(false);
    document.removeEventListener("pointerdown", this.outside);
  }
  setup(items, onChange, { title = "题目标签", empty = "全部标签（点击多选）", search = false, summaryLimit = Infinity } = {}) {
    this.className = "tag-picker";
    const id = `tag-picker-${++nextId}`;
    this.inputs = items.map((item) => ({ ...item,
      input: h("input", { type: "checkbox", value: item.code, onchange: () => this.changed() }) }));
    this.onChange = onChange;
    this.trigger = h("button", { type: "button", class: "input tag-picker-trigger",
      "aria-expanded": "false", "aria-controls": id,
      onclick: () => this.panel.hidden ? this.open() : this.close(false) });
    this.empty = empty;
    this.summaryLimit = summaryLimit;
    this.trigger.setAttribute("aria-label", title);
    const rows = this.inputs.map((item) => h("label", { class: "tag-picker-option" }, item.input, h("span", null, item.name)));
    const searchBox = search ? h("input", { class: "input", type: "search", placeholder: `搜索${title}`, "aria-label": `搜索${title}`,
      onkeydown: (event) => { if (event.key === "Enter") event.preventDefault(); },
      oninput: (event) => {
        const term = event.target.value.trim().toLowerCase();
        rows.forEach((row, i) => { row.hidden = !this.inputs[i].name.toLowerCase().includes(term); });
        this.positionPanel();
      } }) : null;
    this.panel = h("div", { id, class: "tag-picker-panel", hidden: true }, searchBox,
      h("div", { class: "tag-picker-options", role: "group", "aria-label": title }, rows),
      h("div", { class: "tag-picker-actions" },
        h("button", { type: "button", class: "btn btn-outline", onclick: () => { this.value = []; this.changed(); } }, "清空"),
        h("button", { type: "button", class: "btn btn-outline", onclick: () => { this.value = this.before; this.changed(); this.close(true); } }, "取消"),
        h("button", { type: "button", class: "btn btn-primary", onclick: () => this.close(true) }, "完成")));
    // 原生 Popover 进入顶层，避免被卡片、动画或折叠容器裁切；旧浏览器保留固定定位。
    this.usePopover = typeof this.panel.showPopover === "function";
    if (this.usePopover) this.panel.setAttribute("popover", "manual");
    this.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !this.panel.hidden) {
        event.preventDefault(); event.stopPropagation(); this.close(true);
      }
      if (event.key === "ArrowDown" && event.target === this.trigger) {
        event.preventDefault(); this.open();
        (searchBox || this.inputs.find((_, i) => !rows[i].hidden)?.input)?.focus();
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
      ? `已选 ${selected.length} 项：${selected.slice(0, this.summaryLimit).map((item) => item.name).join("、")}${selected.length > this.summaryLimit ? "…" : ""}`
      : this.empty;
    this.trigger.title = this.trigger.textContent;
    this.positionPanel();
  }
  changed() { this.updateSummary(); this.onChange(); }
  open() {
    if (!this.panel.hidden) return;
    this.before = this.value;
    this.panel.hidden = false;
    if (this.usePopover) this.panel.showPopover();
    this.trigger.setAttribute("aria-expanded", "true");
    this.positionPanel();
    if (this.panel.hidden) return;
    document.addEventListener("scroll", this.onScroll, true);
    window.addEventListener("resize", this.reposition);
    window.visualViewport?.addEventListener("resize", this.reposition);
    window.visualViewport?.addEventListener("scroll", this.reposition);
    this.sizeObserver = new ResizeObserver(this.reposition);
    this.sizeObserver.observe(this.trigger);
  }
  positionPanel() {
    if (!this.panel || this.panel.hidden) return;
    const rect = this.trigger.getBoundingClientRect();
    const viewport = window.visualViewport;
    const margin = 8, gap = 6;
    const leftEdge = (viewport?.offsetLeft || 0) + margin;
    const topEdge = (viewport?.offsetTop || 0) + margin;
    const rightEdge = leftEdge + (viewport?.width || document.documentElement.clientWidth) - 2 * margin;
    const bottomEdge = topEdge + (viewport?.height || window.innerHeight) - 2 * margin;
    if (!rect.width || rect.bottom <= topEdge || rect.top >= bottomEdge) {
      this.close(false);
      return;
    }
    const width = Math.min(rect.width, Math.max(0, rightEdge - leftEdge));
    this.panel.style.width = `${width}px`;
    this.panel.style.left = `${Math.max(leftEdge, Math.min(rect.left, rightEdge - width))}px`;
    this.panel.style.maxHeight = `${Math.max(0, Math.min(420, bottomEdge - topEdge))}px`;
    const desired = this.panel.getBoundingClientRect().height;
    const below = Math.max(0, bottomEdge - rect.bottom - gap);
    const above = Math.max(0, rect.top - topEdge - gap);
    const upwards = below < desired && above > below;
    this.panel.style.maxHeight = `${Math.min(420, upwards ? above : below)}px`;
    const height = this.panel.getBoundingClientRect().height;
    const top = upwards ? rect.top - gap - height : rect.bottom + gap;
    this.panel.style.top = `${Math.max(topEdge, Math.min(top, bottomEdge - height))}px`;
  }
  close(focus) {
    if (!this.panel || this.panel.hidden) return;
    if (this.usePopover && this.panel.matches(":popover-open")) this.panel.hidePopover();
    this.panel.hidden = true;
    this.trigger.setAttribute("aria-expanded", "false");
    document.removeEventListener("scroll", this.onScroll, true);
    window.removeEventListener("resize", this.reposition);
    window.visualViewport?.removeEventListener("resize", this.reposition);
    window.visualViewport?.removeEventListener("scroll", this.reposition);
    this.sizeObserver?.disconnect();
    if (focus) this.trigger.focus();
  }
}
customElements.define("exam-tag-picker", TagPicker);
export const tagPicker = (items, onChange, options) => document.createElement("exam-tag-picker").setup(items, onChange, options);
