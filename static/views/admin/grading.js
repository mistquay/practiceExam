// 简答题人工阅卷队列（管理员 / 阅卷人；评分留痕由服务端 grading_reviews + audit_log 记录）
import { listPendingGrading, gradeItem } from "../../api/admin.js";
import { h, mount, loading, errorBox, empty, fmtDateTime } from "../../components/dom.js";
import { toast, toastError } from "../../components/toast.js";

export async function render(view) {
  const body = h("div");
  mount(
    view,
    h(
      "section",
      { class: "card" },
      h(
        "div",
        { class: "row-between" },
        h("h2", { class: "card-title" }, "待人工阅卷"),
        h("button", { class: "btn btn-outline btn-sm", onclick: () => load() }, "刷新")
      ),
      body
    )
  );

  function itemCard(item) {
    const max = Number(item.max_score ?? item.score ?? 0);
    const score = h("input", { class: "input input-narrow", type: "number", min: 0, max, step: 0.5, required: true });
    const comment = h("input", { class: "input", placeholder: "评语（可选）" });

    async function onSubmit(e) {
      e.preventDefault();
      const value = Number(score.value);
      if (!(value >= 0 && value <= max)) {
        toast(`分数需在 0 ~ ${max} 之间`, "warning");
        return;
      }
      try {
        await gradeItem({ attempt_id: item.attempt_id, stable_id: item.stable_id, score: value, comment: comment.value.trim() });
        toast("评分已保存", "success");
        load();
      } catch (err) {
        toastError(err);
      }
    }

    return h(
      "article",
      { class: "question-card" },
      h(
        "header",
        { class: "q-header" },
        h("span", { class: "q-no" }, `${item.name || ""}（${item.employee_no || ""}）`),
        h("span", { class: "q-meta" }, `满分 ${max} · 交卷 ${fmtDateTime(item.submitted_at)}`)
      ),
      h("div", { class: "q-stem" }, item.stem || item.stable_id),
      h("div", { class: "grading-block" }, h("strong", null, "考生作答："), h("div", { class: "pre" }, item.user_text || "（未作答）")),
      item.reference
        ? h("div", { class: "grading-block muted" }, h("strong", null, "评分要点："), h("div", { class: "pre" }, item.reference))
        : null,
      h(
        "form",
        { class: "row-inline", onsubmit: onSubmit },
        h("label", { class: "field-inline" }, "得分", score),
        comment,
        h("button", { type: "submit", class: "btn btn-primary btn-sm" }, "提交评分")
      )
    );
  }

  async function load() {
    mount(body, loading());
    try {
      const items = await listPendingGrading();
      mount(body, items.length ? items.map(itemCard) : empty("没有待阅卷的简答题"));
    } catch (err) {
      mount(body, errorBox(err));
    }
  }
  load();
}
