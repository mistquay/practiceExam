// 练习视图：逐题作答，服务端判分后即时反馈；答对自动下一题；可看解析。
// 使用练习专用 DTO（/api/practice/*），与考试视图 / 考试 DTO 完全分离。
import { fetchPracticeQuestions, submitPractice } from "../api/practice.js";
import { get } from "../api/http.js";
import { loadPracticeDraft, savePracticeDraft, clearPracticeDraft } from "../state/practice-draft.js";
import { getUser } from "../state/session.js";
import { h, mount, loading, errorBox, empty } from "../components/dom.js";
import { renderQuestionCard, readChoice, lockCard, markCard, kindOf } from "../components/question-card.js";
import { renderNav } from "../components/question-nav.js";
import { toastError } from "../components/toast.js";

const AUTO_NEXT_MS = 600;

export async function render(view, { query, navigate }) {
  const saved = query.has("resume") ? loadPracticeDraft() : null;
  const bankId = saved?.bankId || query.get("bank");
  if (!bankId) {
    mount(view, errorBox("缺少选题参数，请从选题页进入"), h("a", { href: "#/", class: "btn btn-outline" }, "返回选题"));
    return;
  }

  mount(view, loading("正在抽题…"));
  let questions;
  try {
    if (saved) {
      const data = await get("/api/practice/questions", { bank_id: bankId, stable_ids: saved.ids, count: 200 });
      const byId = new Map(data.questions.map(q => [q.stable_id, q]));
      questions = saved.ids.map(id => byId.get(id)).filter(Boolean);
    } else questions = await fetchPracticeQuestions({
      bankId,
      personCategory: query.get("person"),
      specialtyCode: query.get("specialty"),
      specialtyCodes: query.getAll("specialty_codes"),
      scopeMode: query.get("scope_mode"),
      qtype: query.get("qtype"),
      count: query.get("count"),
      order: query.get("order"),
      source: query.get("source"),
      tags: query.getAll("tags"),
      sourceFileIds: query.getAll("source_file_ids"),
    });
  } catch (err) {
    mount(view, errorBox(err), h("a", { href: "#/", class: "btn btn-outline" }, "返回选题"));
    return;
  }
  if (!questions.length) {
    if (saved) clearPracticeDraft();
    mount(view, empty("没有符合条件的题目（可能已全部练习过，或错题本为空）"), h("a", { href: "#/", class: "btn btn-outline" }, "返回选题"));
    return;
  }

  return createSession(view, { bankId, questions, navigate, saved });
}

function createSession(view, { bankId, questions, navigate, saved }) {
  const owner = getUser()?.id;
  let disposed = false;
  // 本次练习的全部状态都在这个闭包里
  const compatible = q => saved?.versions?.[q.stable_id] === q.version_hash;
  const results = questions.map(q => compatible(q) ? saved.results?.[q.stable_id] || null : null);
  const choices = Object.fromEntries(questions.map(q => [q.stable_id, compatible(q) ? saved.choices?.[q.stable_id] || "" : ""]));
  const revealed = questions.map(() => false);
  let index = Math.max(0, questions.findIndex(q => q.stable_id === saved?.currentId));
  let busy = false;
  let autoNextTimer = null;
  // 单选 / 判断「点选即提交」只对指针（鼠标、触屏）生效：键盘方向键在单选组内移动时
  // 同样会触发 change，若也自动提交，键盘用户无法越过中间选项。键盘作答改用「提交本题」。
  let pointerAt = -Infinity;

  const progressEl = h("div", { class: "progress-text" });
  const navEl = h("div", { class: "question-nav" });
  const cardBox = h("div");
  const prevBtn = h("button", { class: "btn btn-outline", onclick: () => go(index - 1) }, "上一题");
  const submitBtn = h("button", { class: "btn btn-primary", onclick: () => submit() }, "提交本题");
  const revealBtn = h("button", { class: "btn btn-outline", onclick: () => reveal() }, "查看解析");
  const nextBtn = h("button", { class: "btn btn-outline", onclick: () => go(index + 1) }, "下一题");

  function persist() {
    if (disposed || getUser()?.id !== owner) return;
    savePracticeDraft({ bankId, ids: questions.map(q => q.stable_id), currentId: questions[index].stable_id,
      versions: Object.fromEntries(questions.map(q => [q.stable_id, q.version_hash])), choices,
      results: Object.fromEntries(questions.map((q, i) => [q.stable_id, results[i]])) });
  }

  mount(
    view,
    h(
      "section",
      { class: "card" },
      h(
        "div",
        { class: "row-between" },
        h("h1", { class: "card-title" }, "练习"),
        h("a", { href: "#/", class: "btn btn-ghost btn-sm" }, "结束练习")
      ),
      progressEl,
      navEl,
      cardBox,
      h("div", { class: "actions" }, prevBtn, submitBtn, revealBtn, nextBtn)
    )
  );

  function stats() {
    const done = results.filter(Boolean);
    const graded = done.filter((r) => typeof r.outcome.is_correct === "boolean");
    return { done: done.length, right: graded.filter((r) => r.outcome.is_correct).length, graded: graded.length };
  }

  function navStatus(i) {
    const r = results[i];
    const cls = [i === index ? "current" : ""];
    if (r) cls.push(r.outcome.is_correct === true ? "is-right" : r.outcome.is_correct === false ? "is-wrong" : "done");
    return cls.join(" ");
  }

  function feedbackNodes(question, outcome, choice) {
    const reference = outcome ? outcome.correct_answer ?? outcome.answer : question.answer;
    const analysis = (outcome && outcome.analysis) || question.analysis;
    const basis = (outcome && outcome.basis) || question.basis;
    const nodes = [];
    if (outcome) {
      if (outcome.is_correct === true) nodes.push(h("div", { class: "fb-title ok" }, "回答正确"));
      else if (outcome.is_correct === false)
        nodes.push(h("div", { class: "fb-title bad" }, `回答错误。你的选择：${choice || "未作答"}`));
      else nodes.push(h("div", { class: "fb-title" }, "本题不自动判分，请对照参考答案自评"));
    }
    nodes.push(h("div", null, h("strong", null, "参考答案："), reference || (outcome ? "（题库未提供）" : "提交后显示")));
    if (analysis) nodes.push(h("div", null, h("strong", null, "解析："), analysis));
    if (basis) nodes.push(h("div", null, h("strong", null, "依据："), basis));
    return nodes;
  }

  function draw() {
    const question = questions[index];
    const r = results[index];
    const kind = kindOf(question);
    const instant = kind === "single" || kind === "judge";
    const card = renderQuestionCard(question, index, {
      choice: r ? r.choice : choices[question.stable_id],
      disabled: !!r,
      meta: question.specialty_name || question.outline_l2,
      onChange: () => {
        submitBtn.disabled = busy || !readChoice(card);
        if (instant && performance.now() - pointerAt < 1000) submit();
      },
    });
    card.addEventListener("pointerdown", () => {
      pointerAt = performance.now();
    });
    card.addEventListener("input", () => { choices[question.stable_id] = readChoice(card); persist(); });
    card.addEventListener("change", () => { choices[question.stable_id] = readChoice(card); persist(); });
    if (r) {
      markCard(card, r.outcome.is_correct === true ? "right" : r.outcome.is_correct === false ? "wrong" : null);
    }
    const feedback = card.querySelector(".question-feedback");
    feedback.tabIndex = -1;
    if (r || revealed[index]) mount(feedback, feedbackNodes(question, r && r.outcome, r && r.choice));
    // 作答区被整体重绘时保住焦点：已判分则聚焦反馈（读屏立即朗读对错），否则聚焦新题首个控件
    const shown = !!(r || revealed[index]);
    const active = document.activeElement;
    const focusWasInCard = cardBox.contains(active) || (shown && (active === submitBtn || active === revealBtn));
    mount(cardBox, card);
    if (focusWasInCard) (shown ? feedback : card.querySelector("input, textarea"))?.focus({ preventScroll: true });

    const s = stats();
    progressEl.textContent = `第 ${index + 1} / ${questions.length} 题 · 已答 ${s.done} · 答对 ${s.right} / ${s.graded}`;
    renderNav(navEl, questions.length, { statusOf: navStatus, onPick: go });

    prevBtn.disabled = index === 0;
    nextBtn.textContent = index === questions.length - 1 ? "完成" : "下一题";
    submitBtn.disabled = !!r || busy || (kind !== "text" && !readChoice(card));
    revealBtn.disabled = !!r || revealed[index];
    persist();
  }

  function go(i) {
    if (busy || disposed) return;
    clearTimeout(autoNextTimer);
    if (i >= questions.length) {
      finish();
      return;
    }
    if (i < 0) return;
    index = i;
    draw();
  }

  function reveal() {
    revealed[index] = true;
    draw();
  }

  async function submit() {
    if (busy || results[index]) return;
    const question = questions[index];
    const card = cardBox.querySelector(".question-card");
    const choice = readChoice(card);
    if (!choice && kindOf(question) !== "text") {
      toastError("请先作答再提交");
      return;
    }
    busy = true;
    lockCard(card);
    const at = index;
    try {
      const outcome = await submitPractice({ bankId, stableId: question.stable_id, userAnswer: choice });
      results[at] = { choice, outcome };
      persist();
      if (!disposed && outcome.is_correct === true && at < questions.length - 1) {
        autoNextTimer = setTimeout(() => {
          if (index === at) go(at + 1);
        }, AUTO_NEXT_MS);
      }
    } catch (err) {
      toastError(err);
    } finally {
      busy = false;
      if (!disposed && index === at) draw();
    }
  }

  function finish() {
    disposed = true;
    clearTimeout(autoNextTimer);
    clearPracticeDraft();
    const s = stats();
    mount(
      view,
      h(
        "section",
        { class: "card" },
        h("h1", { class: "card-title" }, "本次练习完成"),
        h("p", null, `共 ${questions.length} 题，已答 ${s.done} 题，答对 ${s.right} / ${s.graded} 题。`),
        h(
          "div",
          { class: "actions" },
          h("button", { class: "btn btn-primary", onclick: () => navigate("/") }, "返回选题"),
          h("button", { class: "btn btn-outline", onclick: () => navigate("/wrong") }, "查看错题本")
        )
      )
    );
  }

  draw();
  return () => { disposed = true; clearTimeout(autoNextTimer); };
}
