// 登录即可练习与自主模拟；管理场次保留兼容。
import { renderSelfTestPicker } from "./self-test-picker.js";
import { renderPracticePicker } from "./practice-picker.js";
import { listBanks, getDimension } from "../api/banks.js";
import { listAvailable, startExam, listMyExams } from "../api/exam.js";
import { getSelection, saveSelection } from "../state/selection.js";
import { h, mount, loading, errorBox, empty, fmtDate, fmtDateTime } from "../components/dom.js";
import { toastError } from "../components/toast.js";

export async function render(view, { navigate }) {
  const sel = getSelection();
  const resumeBox = h("div");
  const bankBox = h("div");
  const stepsBox = h("div");
  mount(view, resumeBox, bankBox, stepsBox);

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
        h("section", { class: "card" }, h("h2", { class: "card-title" }, "自主模拟考试"), examBox));
      renderPracticePicker(practiceBox, { bankId: id, dim, navigate });
      renderSteps(examBox, { bankId: id, dim, navigate });
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

function renderSteps(box, options) {
  const picker = h("div");
  const batches = h("div");
  const details = h("details", { class: "disclosure" }, h("summary", null, "可参加的考试场次（受场次规则限制）"), batches);
  details.addEventListener("toggle", () => {
    if (details.open) renderExamOptions(batches, options);
  });
  mount(box, picker, details);
  renderSelfTestPicker(picker, options);
}

async function renderExamOptions(box, { bankId, specialty, paperType, personCategory, navigate }) {
  mount(box, loading("正在查询可参加的考试…"));
  let items;
  try {
    items = await listAvailable({ bankId, specialtyCode: specialty, paperType, personCategory });
  } catch (err) {
    mount(box, errorBox(err));
    return;
  }
  if (!items.length) {
    mount(box, empty("当前没有可参加的场次（不在时间窗内、未列入名单或次数已用完）"));
    return;
  }

  async function begin(item, btn) {
    const minutes = item.duration_min ? `限时 ${item.duration_min} 分钟，` : "";
    if (!window.confirm(`确定开始「${item.name}」？${minutes}开始后计时不可暂停。`)) return;
    btn.disabled = true;
    try {
      const attempt = await startExam({ batchId: item.batch_id, templateId: item.template_id, specialtyCode: specialty || "pd_live", personCategory });
      navigate(`/exam/${attempt.attempt_id ?? attempt.id}`);
    } catch (err) {
      toastError(err);
      btn.disabled = false;
    }
  }

  mount(
    box,
    h(
      "div",
      { class: "list" },
      items.map((item) => {
        const btn = h("button", { class: "btn btn-primary btn-sm" }, "开始考试");
        btn.disabled = item.attempts_left === 0;
        btn.addEventListener("click", () => begin(item, btn));
        return h(
          "div",
          { class: "list-item" },
          h(
            "div",
            null,
            h("strong", null, item.name || item.code || `场次 ${item.batch_id}`),
            h(
              "div",
              { class: "hint" },
              [
                item.duration_min ? `时长 ${item.duration_min} 分钟` : null,
                item.total_score != null ? `总分 ${item.total_score}` : null,
                item.pass_score != null ? `及格 ${item.pass_score}` : null,
                item.start_at || item.end_at ? `开放 ${fmtDateTime(item.start_at)} ~ ${fmtDateTime(item.end_at)}` : null,
                item.attempts_left != null ? `剩余次数 ${item.attempts_left}` : null,
              ]
                .filter(Boolean)
                .join(" · ")
            )
          ),
          btn
        );
      })
    )
  );
}
