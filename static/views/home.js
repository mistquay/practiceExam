// 首页：题库练习、模拟考试与继续作答。
import { renderSelfTestPicker } from "./self-test-picker.js";
import { renderPracticePicker } from "./practice-picker.js";
import { listBanks, getDimension } from "../api/banks.js";
import { listMyExams } from "../api/exam.js";
import { getSelection, saveSelection } from "../state/selection.js";
import { h, mount, loading, errorBox, fmtDateTime } from "../components/dom.js";
import { loadPracticeDraft, clearPracticeDraft } from "../state/practice-draft.js";

export async function render(view, { navigate }) {
  const sel = getSelection();
  const resumeBox = h("div");
  const bankBox = h("div");
  const stepsBox = h("div");
  const practiceResume = h("div");
  mount(view, practiceResume, resumeBox, bankBox, stepsBox);
  const draft = loadPracticeDraft();
  if (draft) mount(practiceResume, h("section", { class: "card row-between" },
    h("h2", { class: "card-title" }, `上次练习 · 第 ${Math.max(0, draft.ids.indexOf(draft.currentId)) + 1} / ${draft.ids.length} 题`),
    h("div", { class: "actions" }, h("button", { class: "btn btn-primary", onclick: () => navigate("/practice?resume=1") }, "继续练习"),
      h("button", { class: "btn btn-ghost", onclick: () => { clearPracticeDraft(); mount(practiceResume); } }, "结束上次练习"))));

  showResumeBanner(resumeBox, navigate);

  let banks;
  try {
    banks = (await listBanks()).filter((b) => !b.status || b.status === "active");
  } catch (err) {
    mount(stepsBox, errorBox(err));
    return;
  }
  if (!banks.length) {
    mount(stepsBox, errorBox("当前没有已发布的题库版本，请联系管理员"));
    return;
  }

  let bankId = banks.some((b) => String(b.id) === String(sel.bankId)) ? sel.bankId : String(banks[0].id);
  if (banks.length > 1) {
    const select = h(
      "select",
      { class: "input", onchange: () => loadBank(select.value) },
      banks.map((b) => h("option", { value: String(b.id), selected: String(b.id) === bankId }, b.name || b.code))
    );
    mount(bankBox, h("label", { class: "field inline-field" }, h("span", null, "题库版本"), select));
  } else {
    mount(bankBox, h("p", { class: "hint" }, `题库版本：${banks[0].name || banks[0].code}`));
  }

  async function loadBank(id) {
    bankId = id;
    saveSelection({ bankId: id });
    mount(stepsBox, loading());
    try {
      const dim = await getDimension(id);
      const practiceBox = h("div");
      const examBox = h("div");
      mount(stepsBox,
        h("section", { class: "card" }, h("h1", { class: "card-title" }, "题库练习"), practiceBox),
        h("details", { class: "card disclosure" }, h("summary", null, "模拟考试"), examBox));
      renderPracticePicker(practiceBox, { bankId: id, dim, navigate });
      renderSelfTestPicker(examBox, { bankId: id, dim, navigate });
    } catch (err) {
      mount(stepsBox, errorBox(err));
    }
  }
  loadBank(bankId);
}

async function showResumeBanner(box, navigate) {
  try {
    const ongoing = (await listMyExams()).filter((e) => e.status === "in_progress");
    if (!ongoing.length) return;
    mount(
      box,
      ongoing.map((e) =>
        h(
          "div",
          { class: "alert alert-warning row-between" },
          h("span", null, `你有未交卷的考试「${e.name || e.template_name || e.id}」，截止 ${fmtDateTime(e.deadline_at)}`),
          h("button", { class: "btn btn-primary btn-sm", onclick: () => navigate(`/exam/${e.id}`) }, "继续作答")
        )
      )
    );
  } catch {
    // 横幅是辅助信息，失败不打扰主流程
  }
}
