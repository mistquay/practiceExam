// 题目卡片（练习、考试共用的纯展示组件）。
// 只负责渲染题干 / 选项 / 作答控件并读取考生的选择（choice），不接触任何判分依据；
// 判分反馈由各自视图追加到 .question-feedback 区域。
import { h } from "./dom.js";

const JUDGE_DEFAULT = [
  { key: "A", text: "正确" },
  { key: "B", text: "错误" },
];

/** 题型 → 交互方式：single / multiple / judge / text */
export function kindOf(question) {
  const t = String(question.qtype || question.type || "");
  if (t.includes("判断")) return "judge";
  if (t.includes("多选") || t.includes("多项")) return "multiple";
  if (t.includes("简答") || t.includes("问答")) return "text";
  return normalizeOptions(question.options).length ? "single" : "text";
}

/** 选项兼容：[{key,text}] / {A:"..."} / ["A-xxx", ...] → [{key,text}] */
export function normalizeOptions(options) {
  if (!options) return [];
  if (Array.isArray(options)) {
    return options.map((opt, i) => {
      if (opt && typeof opt === "object") return { key: String(opt.key ?? opt.label), text: opt.text ?? opt.value ?? "" };
      const m = /^\s*([A-Z])\s*[-.、．:：]\s*(.*)$/s.exec(String(opt));
      return m ? { key: m[1], text: m[2] } : { key: String.fromCharCode(65 + i), text: String(opt) };
    });
  }
  return Object.keys(options)
    .sort()
    .map((key) => ({ key, text: options[key] ?? "" }));
}

function optionsOf(question, kind) {
  const opts = normalizeOptions(question.options);
  return kind === "judge" && !opts.length ? JUDGE_DEFAULT : opts;
}

/**
 * @param {object} question  {stable_id, qtype, stem, options, score}
 * @param {number} index     从 0 开始的题序
 * @param {object} opt       { choice, disabled, onChange, meta }
 */
export function renderQuestionCard(question, index, { choice = "", disabled = false, onChange, meta } = {}) {
  const kind = kindOf(question);
  const name = `q-${index}`;
  const titleId = `question-${index}-title`;
  const stemId = `question-${index}-stem`;
  const labelledBy = `${titleId} ${stemId}`;
  const metaParts = [question.qtype || question.type, meta, question.score != null ? `${question.score} 分` : null]
    .filter(Boolean)
    .join(" · ");

  let body;
  if (kind === "text") {
    body = h("textarea", {
      class: "input q-textarea",
      name,
      rows: 4,
      placeholder: "请输入作答内容",
      "aria-labelledby": labelledBy,
      disabled,
      oninput: onChange,
    });
    body.value = choice || "";
  } else {
    const inputType = kind === "multiple" ? "checkbox" : "radio";
    body = h(
      "div",
      { class: "q-options", role: kind === "multiple" ? "group" : "radiogroup", "aria-labelledby": labelledBy },
      optionsOf(question, kind).map((o) =>
        h(
          "label",
          { class: "q-option" },
          h("input", {
            type: inputType,
            name,
            value: o.key,
            checked: kind === "multiple" ? String(choice).includes(o.key) : choice === o.key,
            disabled,
            onchange: onChange,
          }),
          h("span", { class: "q-option-key" }, o.key),
          h("span", { class: "q-option-text" }, o.text)
        )
      )
    );
  }

  return h(
    "article",
    { class: "question-card", id: `question-${index}`, dataset: { index: String(index), kind } },
    h(
      "header",
      { class: "q-header" },
      h("span", { class: "q-no", id: titleId }, `第 ${index + 1} 题`),
      h("span", { class: "q-meta" }, metaParts)
    ),
    h("div", { class: "q-stem", id: stemId }, question.stem || question.question || ""),
    body,
    h("div", { class: "question-feedback" })
  );
}

/** 读取卡片上考生的选择：单选/判断 "A"，多选 "AC"（字母序），简答为文本。 */
export function readChoice(card) {
  const textarea = card.querySelector("textarea");
  if (textarea) return textarea.value.trim();
  return Array.from(card.querySelectorAll("input:checked"))
    .map((el) => el.value)
    .sort()
    .join("");
}

export function lockCard(card) {
  card.querySelectorAll("input, textarea").forEach((el) => {
    el.disabled = true;
  });
}

/** 标记对错底色：state = "right" | "wrong" | "pending" | null */
export function markCard(card, state) {
  card.classList.remove("is-right", "is-wrong", "is-pending");
  if (state) card.classList.add(`is-${state}`);
}
