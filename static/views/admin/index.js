// 管理员页：顶部标签 + 按需加载子页面
import { h, mount } from "../../components/dom.js";

const TABS = [
  { key: "users", title: "用户", load: () => import("./users.js") },
  { key: "banks", title: "题库管理", load: () => import("./banks.js") },
  { key: "templates", title: "试卷模板", load: () => import("./templates.js") },
  { key: "batches", title: "场次与成绩导出", load: () => import("./batches.js") },
  { key: "grading", title: "人工阅卷", load: () => import("./grading.js") },
];

export async function render(view, ctx) {
  const active = TABS.find((t) => t.key === ctx.params.tab) || TABS[0];
  const body = h("div");
  mount(
    view,
    h(
      "nav",
      { class: "tabs", "aria-label": "管理功能" },
      TABS.map((t) =>
        h("a", { href: `#/admin/${t.key}`, class: t === active ? "active" : null, "aria-current": t === active ? "page" : null }, t.title)
      )
    ),
    body
  );
  // 窄屏下标签横向滚动，确保当前标签可见
  view.querySelector(".tabs a.active")?.scrollIntoView({ block: "nearest", inline: "nearest" });
  const mod = await active.load();
  return mod.render(body, ctx);
}
