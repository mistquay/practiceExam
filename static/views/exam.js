// 考试视图：整卷作答 → 交卷 → 成绩。
// 约束：只使用考试 DTO（题干 / 选项 / 分值），不持有、不渲染任何判分依据；
// 计时只认服务端 deadline_at + server_now，断线重连不重置剩余时间；判分完全在服务端。
import { renderSelfReview } from "./self-test-review.js";
import { getAttempt, submitExam } from "../api/exam.js";
import { loadDraft, saveDraft, clearDraft } from "../state/exam-draft.js";
import { h, mount, loading, errorBox, empty, scrollToEl } from "../components/dom.js";
import { renderQuestionCard, readChoice, lockCard, markCard } from "../components/question-card.js";
import { renderNav } from "../components/question-nav.js";
import { createCountdown, fmtRemaining } from "../components/countdown.js";
import { toast, toastError } from "../components/toast.js";

const PAPER_TYPE_NAMES = { mock: "模拟考试", formal: "正式考试", makeup: "补考" };

export async function render(view, { params }) {
  const attemptId = params.id;
  mount(view, loading("正在加载试卷…"));
  let attempt;
  try {
    attempt = await getAttempt(attemptId);
  } catch (err) {
    mount(view, errorBox(err), h("a", { href: "#/history", class: "btn btn-outline" }, "返回考试记录"));
    return;
  }
  if (attempt.status !== "in_progress") {
    renderResult(view, attempt, attempt.questions || [], null);
    return;
  }
  return runPaper(view, attemptId, attempt);
}

const sortedQuestions = (list) =>
  (list || []).slice().sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));

function runPaper(view, attemptId, attempt) {
  const questions = sortedQuestions(attempt.questions);
  const choices = loadDraft(attemptId); // { stable_id: choice }
  let submitting = false;
  let finished = false;

  const title = attempt.name || PAPER_TYPE_NAMES[attempt.paper_type] || "考试";
  const timerEl = h("span", { class: "timer" }, "--:--:--");
  const countEl = h("span", { class: "hint" });
  const submitBtn = h("button", { class: "btn btn-primary", onclick: () => submit(false) }, "交卷");
  const navEl = h("div", { class: "question-nav" });
  const statusEl = h("div");
  const paperEl = h("div");

  const cards = questions.map((q, i) =>
    renderQuestionCard(q, i, {
      choice: choices[q.stable_id] || "",
      onChange: () => onChoice(i),
    })
  );

  mount(
    view,
    h(
      "div",
      { class: "exam-bar" },
      h("strong", null, title),
      h("span", { role: "timer" }, "剩余 ", timerEl),
      countEl,
      submitBtn
    ),
    statusEl,
    h("section", { class: "card" }, navEl, paperEl)
  );
  mount(paperEl, cards);

  function onChoice(i) {
    choices[questions[i].stable_id] = readChoice(cards[i]);
    saveDraft(attemptId, choices);
    refreshNav();
  }

  function filledCount() {
    return questions.filter((q) => choices[q.stable_id]).length;
  }

  function refreshNav() {
    countEl.textContent = `已作答 ${filledCount()} / ${questions.length}`;
    renderNav(navEl, questions.length, {
      statusOf: (i) => (choices[questions[i].stable_id] ? "done" : ""),
      onPick: (i) => {
        scrollToEl(cards[i]);
        cards[i].querySelector("input, textarea")?.focus({ preventScroll: true });
      },
    });
  }

  const countdown = createCountdown({
    onTick: (ms) => {
      timerEl.textContent = fmtRemaining(ms);
      timerEl.classList.toggle("timer-warning", ms <= 5 * 60 * 1000);
    },
    onExpire: () => {
      toast("考试时间到，正在自动交卷…", "warning");
      submit(true);
    },
  });

  async function resync() {
    if (finished || document.visibilityState === "hidden") return;
    try {
      const latest = await getAttempt(attemptId);
      if (latest.status !== "in_progress") {
        // 服务端已结束本场（例如到时自动交卷）
        finish(latest);
        return;
      }
      countdown.sync(latest.deadline_at, latest.server_now);
    } catch (err) {
      toastError(err);
    }
  }

  async function submit(auto) {
    if (submitting || finished) return;
    if (!auto) {
      const left = questions.length - filledCount();
      const tip = left > 0 ? `还有 ${left} 题未作答，` : "";
      if (!window.confirm(`${tip}确定交卷吗？交卷后不能修改。`)) return;
    }
    submitting = true;
    submitBtn.disabled = true;
    cards.forEach(lockCard);
    const responses = questions.map((q) => ({ stable_id: q.stable_id, choice: choices[q.stable_id] || "" }));
    try {
      const result = await submitExam(attemptId, responses);
      finish(result);
    } catch (err) {
      if (err.status === 409) {
        toast("本场考试已交卷，不能重复提交；以下为已提交的成绩。", "warning", 6000);
        try {
          finish(await getAttempt(attemptId));
        } catch (e) {
          mount(statusEl, errorBox(e));
        }
        return;
      }
      submitting = false;
      // 网络或服务端错误：保留作答草稿，允许重试（剩余时间仍以服务端为准）
      mount(
        statusEl,
        h(
          "div",
          { class: "alert alert-danger row-between" },
          h("span", null, `交卷失败：${err.message}。作答已保存在本机，可重试。`),
          h("button", { class: "btn btn-primary btn-sm", onclick: () => submit(true) }, "重试交卷")
        )
      );
      if (!auto) {
        submitBtn.disabled = false;
        cards.forEach((card) => card.querySelectorAll("input, textarea").forEach((el) => (el.disabled = false)));
      }
    }
  }

  function finish(result) {
    finished = true;
    countdown.stop();
    clearDraft(attemptId);
    detach();
    renderResult(view, result, questions, choices);
  }

  const onVisible = () => resync();
  const onBeforeUnload = (e) => {
    if (!finished) e.preventDefault();
  };
  function detach() {
    document.removeEventListener("visibilitychange", onVisible);
    window.removeEventListener("online", onVisible);
    window.removeEventListener("beforeunload", onBeforeUnload);
  }
  document.addEventListener("visibilitychange", onVisible);
  window.addEventListener("online", onVisible);
  window.addEventListener("beforeunload", onBeforeUnload);

  refreshNav();
  try {
    countdown.sync(attempt.deadline_at, attempt.server_now);
  } catch (err) {
    mount(statusEl, errorBox(err));
  }

  return () => {
    countdown.stop();
    detach();
  };
}

/** 成绩页：只展示服务端给出的得分 / 对错结论 / 解析，不展示判分依据。 */
function renderResult(view, data, questionsIn, localChoices) {
  const res = data.result || data;
  const questions = sortedQuestions(questionsIn.length ? questionsIn : data.questions);
  const items = res.items || [];
  const itemOf = new Map(items.map((it) => [it.stable_id, it]));
  const choices = localChoices || {};
  (data.responses || res.responses || []).forEach((r) => {
    choices[r.stable_id] = r.choice;
  });

  // 服务端逐题结论：right / pending 之外（答错、未作答）一律按失分展示
  const isLost = (r) => !!r && r !== "right" && r !== "pending";
  const resultOf = (q) => (itemOf.get(q.stable_id) || {}).result;
  const pending = res.pending_count ?? items.filter((it) => it.result === "pending").length;
  const wrongCount = items.filter((it) => isLost(it.result)).length;
  let onlyWrong = false;

  const passedText = res.passed === true ? "通过" : res.passed === false ? "未通过" : "待定";
  const summary = h(
    "section",
    { class: "card result-summary" },
    h("h1", { class: "card-title" }, data.name || "考试成绩"),
    h(
      "div",
      { class: "score-line" },
      res.score != null
        ? [h("span", { class: "score" }, res.score), h("span", { class: "hint" }, ` / ${res.total_score ?? "—"} 分`)]
        : h("span", { class: "score score-pending" }, "未出总分"),
      h("span", { class: `badge ${res.passed === true ? "badge-ok" : res.passed === false ? "badge-bad" : ""}` }, passedText)
    ),
    h(
      "p",
      { class: "hint" },
      [res.pass_score != null ? `及格线 ${res.pass_score} 分` : null, items.length ? `错题 ${wrongCount} 道` : null]
        .filter(Boolean)
        .join(" · ")
    ),
    pending > 0 ? h("div", { class: "alert alert-warning" }, data.exam_mode === "self_service" ? `待评分：${pending} 道简答题；客观题 ${res.objective_score} / ${res.objective_total} 分。` : `${pending} 道简答题待阅卷。`) : null
  );

  const reviewBox = h("section", { class: "card" });
  const navEl = h("div", { class: "question-nav" });
  const listEl = h("div");
  const filterBtn = h("button", { class: "btn btn-outline btn-sm" }, "只看错题");
  filterBtn.addEventListener("click", () => {
    onlyWrong = !onlyWrong;
    filterBtn.textContent = onlyWrong ? "显示全部" : "只看错题";
    drawList();
  });

  function cardFor(q, i) {
    const it = itemOf.get(q.stable_id) || {};
    const card = renderQuestionCard(q, i, { choice: choices[q.stable_id] || "", disabled: true });
    markCard(card, isLost(it.result) ? "wrong" : it.result === "right" || it.result === "pending" ? it.result : null);
    const selfShort = data.exam_mode === "self_service" && q.qtype === "简答题";
    const label = selfShort ? (it.result === "pending" ? "待评分" : it.result === "blank" ? "未作答" : "得分")
      : { right: "得分", wrong: "答错", pending: "待人工阅卷" }[it.result] || (it.result ? "未作答" : "未评分");
    mount(
      card.querySelector(".question-feedback"),
      h("div", null, h("strong", null, `${label}：`), `${it.earned ?? 0} / ${it.score ?? q.score ?? "—"} 分`),
      it.analysis ? h("div", null, h("strong", null, "解析："), it.analysis) : null
    );
    return card;
  }

  function drawList() {
    if (!questions.length) {
      // 服务端未返回卷面时，只列出结果条目
      mount(
        listEl,
        items.length
          ? items
              .filter((it) => !onlyWrong || isLost(it.result))
              .map((it) =>
                h(
                  "div",
                  { class: `list-item ${isLost(it.result) ? "is-wrong" : `is-${it.result}`}` },
                  h("span", null, `第 ${it.seq ?? "?"} 题 ${it.stem || ""}`),
                  h("span", null, `${it.earned ?? 0} / ${it.score ?? "—"}`)
                )
              )
          : empty("服务端未返回逐题结果")
      );
      return;
    }
    const cards = questions.map(cardFor);
    const visible = cards.filter((_, i) => !onlyWrong || isLost(resultOf(questions[i])));
    mount(listEl, visible.length ? visible : empty("没有错题"));
    renderNav(navEl, questions.length, {
      statusOf: (i) => {
        const r = resultOf(questions[i]);
        return r === "right" ? "is-right" : isLost(r) ? "is-wrong" : r === "pending" ? "done" : "";
      },
      onPick: (i) => {
        const el = cards[i];
        if (el.isConnected) scrollToEl(el);
      },
    });
  }

  mount(
    view,
    summary,
    data.exam_mode === "self_service" ? reviewBox : null,
    h(
      "section",
      { class: "card" },
      h("div", { class: "row-between" }, h("h2", { class: "card-title" }, "逐题结果"), filterBtn),
      navEl,
      listEl,
      h(
        "div",
        { class: "actions" },
        h("a", { href: "#/history", class: "btn btn-outline" }, "考试记录"),
        h("a", { href: "#/", class: "btn btn-primary" }, "返回选题")
      )
    )
  );
  drawList();
  if (data.exam_mode === "self_service") renderSelfReview(reviewBox, data.attempt_id || data.id, async () => {
    const latest = await getAttempt(data.attempt_id || data.id);
    renderResult(view, latest, latest.questions || [], null);
  });
}
