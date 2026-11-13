import { get, post } from "../api/http.js";
import { h, mount, loading, errorBox } from "../components/dom.js";
import { toastError } from "../components/toast.js";

export async function renderSelfReview(box, attemptId, onRated) {
  mount(box, loading("正在读取交卷后的简答参考答案…"));
  try {
    const data = await get(`/api/self-tests/${encodeURIComponent(attemptId)}/review`);
    if (!data.items.length) { mount(box); return; }
    mount(box, h("h2", { class: "card-title" }, "简答题评分"),
      h("p", { class: "hint" }, "对照答案评分，确认后不能修改。"),
      data.items.map(q => {
        const score = h("input", { type: "number", class: "input", min: 0, max: q.max_score, step: "0.01", required: true, placeholder: `0~${q.max_score}` });
        const confirm = h("button", { type: "submit", class: "btn btn-primary" }, "确认本题自评分");
        const form = h("form", { class: "form self-review-form", onsubmit: async event => {
          event.preventDefault();
          if (!form.reportValidity() || confirm.disabled) return;
          confirm.disabled = true;
          try {
            await post(`/api/self-tests/${encodeURIComponent(attemptId)}/self-rating`, { stable_id: q.stable_id, score: Number(score.value) });
            await onRated();
          } catch (err) { toastError(err); confirm.disabled = false; }
        } }, h("label", { class: "field" }, h("span", null, `第 ${q.seq} 题自评分（满分 ${q.max_score} 分）`), score), confirm);
        return h("section", { class: "question-card" }, h("h3", null, `第 ${q.seq} 题 · ${q.stem}`),
          h("p", { class: "question-analysis" }, `自己的答案：${q.own_answer || "（空白未答）"}`),
          h("p", { class: "question-analysis" }, `参考答案：${q.reference_answer}`),
          q.pending ? form : h("p", null, `得分 ${q.earned} / ${q.max_score}`));
      }));
  } catch (err) { mount(box, errorBox(err)); }
}
