// 简单工具函数

const WRONG_BOOK_KEY = "practiceExamWrongBook";
const PRACTICED_BOOK_KEY = "practiceExamPracticedBook";

function loadWrongBookMap() {
  try {
    const raw = localStorage.getItem(WRONG_BOOK_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object") {
      return parsed;
    }
  } catch (e) {
    console.error("加载错题本失败:", e);
  }
  return {};
}

function saveWrongBookMap(map) {
  try {
    localStorage.setItem(WRONG_BOOK_KEY, JSON.stringify(map));
  } catch (e) {
    console.error("保存错题本失败:", e);
  }
}

function addToWrongBook(question) {
  if (!question || question.id == null) return;
  const id = String(question.id);
  const map = loadWrongBookMap();
  map[id] = question;
  saveWrongBookMap(map);
}

function removeFromWrongBook(questionId) {
  if (questionId == null) return;
  const id = String(questionId);
  const map = loadWrongBookMap();
  if (Object.prototype.hasOwnProperty.call(map, id)) {
    delete map[id];
    saveWrongBookMap(map);
  }
}

function getWrongBookQuestions() {
  const map = loadWrongBookMap();
  return Object.values(map);
}

function loadPracticedMap() {
  try {
    const raw = localStorage.getItem(PRACTICED_BOOK_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object") {
      return parsed;
    }
  } catch (e) {
    console.error("加载练习记录失败:", e);
  }
  return {};
}

function savePracticedMap(map) {
  try {
    localStorage.setItem(PRACTICED_BOOK_KEY, JSON.stringify(map));
  } catch (e) {
    console.error("保存练习记录失败:", e);
  }
}

function addToPracticedBook(questionId) {
  if (questionId == null) return;
  const id = String(questionId);
  const map = loadPracticedMap();
  if (!Object.prototype.hasOwnProperty.call(map, id)) {
    map[id] = true;
    savePracticedMap(map);
  }
}

function getPracticedMap() {
  return loadPracticedMap();
}

function normalizeAnswer(str) {
  if (!str) return "";
  return str.toString().toUpperCase().replace(/\s+/g, "");
}

function detectQuestionKind(question) {
  const typeStr = (question.type || "").trim();
  const options = question.options || {};
  const hasOptions = Object.keys(options).length > 0;

  if (typeStr.includes("判断")) return "truefalse";
  if (typeStr.includes("多选") || typeStr.includes("多项")) return "multiple";
  if (typeStr.includes("简答") || typeStr.includes("问答")) return "short";
  if (!hasOptions) return "short";
  if (typeStr.includes("单选")) return "single";
  // 默认按单选处理
  return "single";
}

function normalizeTrueFalseCorrect(raw) {
  const s = normalizeAnswer(raw);
  if (!s) return null;

  const truePatterns = ["1", "T", "Y", "YES", "TRUE", "对", "正确", "是", "√", "✔"];
  const falsePatterns = ["0", "F", "N", "NO", "FALSE", "错", "错误", "否", "×", "✘"];

  if (truePatterns.some((p) => s.includes(p))) return "A"; // 默认 A=正确
  if (falsePatterns.some((p) => s.includes(p))) return "B"; // 默认 B=错误

  const letters = s.replace(/[^A-Z]/g, "");
  if (letters === "A" || letters === "B") return letters;

  return null;
}

function gradeQuestion(question, kind, userAnswer) {
  const totalScore = typeof question.score === "number" ? question.score : 0;
  const rawAnswer = question.rawAnswer || question.answer || "";

  // 简答题：仅给出参考答案，不进行自动评分
  if (kind === "short") {
    return {
      kind,
      isCorrect: null,
      earnedScore: 0,
      totalScore,
      correctDisplay: rawAnswer,
    };
  }

  if (!userAnswer) {
    return {
      kind,
      isCorrect: false,
      earnedScore: 0,
      totalScore,
      correctDisplay: rawAnswer,
    };
  }

  const correctNormalized = normalizeAnswer(rawAnswer);

  if (kind === "truefalse") {
    const correctKey = normalizeTrueFalseCorrect(correctNormalized);
    const isCorrect = !!correctKey && normalizeAnswer(userAnswer) === correctKey;
    return {
      kind,
      isCorrect,
      earnedScore: isCorrect ? totalScore : 0,
      totalScore,
      correctDisplay: rawAnswer || (correctKey === "A" ? "正确(A)" : "错误(B)"),
    };
  }

  // 单选 / 多选：按选项字母集合比较
  const correctLetters = (correctNormalized.match(/[A-Z]/g) || []).sort();
  const userLetters = (normalizeAnswer(userAnswer).match(/[A-Z]/g) || []).sort();

  if (!correctLetters.length) {
    // 若题库答案没有包含选项字母，则不进行判分
    return {
      kind,
      isCorrect: null,
      earnedScore: 0,
      totalScore,
      correctDisplay: rawAnswer,
    };
  }

  if (correctLetters.length !== userLetters.length) {
    return {
      kind,
      isCorrect: false,
      earnedScore: 0,
      totalScore,
      correctDisplay: correctLetters.join(""),
    };
  }

  const isCorrect = correctLetters.every((ch, idx) => ch === userLetters[idx]);

  return {
    kind,
    isCorrect,
    earnedScore: isCorrect ? totalScore : 0,
    totalScore,
    correctDisplay: correctLetters.join(""),
  };
}

// 渲染题目
function createQuestionElement(question, index, options = {}) {
  const existingAnswer =
    typeof options.existingAnswer === "string" ? options.existingAnswer : "";
  const readOnly = !!options.readOnly;

  const kind = detectQuestionKind(question);
  const card = document.createElement("div");
  card.className = "question-card card";
  card.dataset.questionIndex = String(index);

  const header = document.createElement("div");
  header.className =
    "question-header d-flex justify-content-between align-items-center mb-2";
  const titleSpan = document.createElement("span");
  titleSpan.textContent = `第 ${index + 1} 题`;

  const metaSpan = document.createElement("span");
  metaSpan.className = "meta";
  const metaParts = [];
  if (question.type) metaParts.push(question.type);
  if (question.category) metaParts.push(question.category);
  if (question.source_file) metaParts.push(question.source_file);
  if (typeof question.score === "number") metaParts.push(`${question.score} 分`);
  metaSpan.textContent = metaParts.join(" | ");

  header.appendChild(titleSpan);
  header.appendChild(metaSpan);

  const textDiv = document.createElement("div");
  textDiv.className = "question-text card-text";
  textDiv.textContent = question.question || "";

  const body = document.createElement("div");
  body.className = "question-body";

  if (kind === "short") {
    const wrapper = document.createElement("div");
    wrapper.className = "question-short-answer";
    const label = document.createElement("label");
    label.textContent = "请在此输入你的答案：";
      const textarea = document.createElement("textarea");
      textarea.id = `q-${index}-short`;
      textarea.className = "form-control form-control-sm";
    if (existingAnswer) {
      textarea.value = existingAnswer;
    }
    if (readOnly) {
      textarea.disabled = true;
    }
    wrapper.appendChild(label);
    wrapper.appendChild(textarea);
    body.appendChild(wrapper);
  } else {
    const optionsDiv = document.createElement("div");
    optionsDiv.className = "question-options";
    const options = question.options || {};
    const keys = Object.keys(options).sort();
    const inputType = kind === "multiple" ? "checkbox" : "radio";

    keys.forEach((key) => {
      const optionLabel = document.createElement("label");
      optionLabel.className = "form-check";

      const input = document.createElement("input");
      input.type = inputType;
      input.name = `q-${index}`;
      input.value = key;
      input.id = `q-${index}-${key}`;
      input.className = "form-check-input";
      if (existingAnswer) {
        if (inputType === "checkbox" && existingAnswer.includes(key)) {
          input.checked = true;
        } else if (inputType === "radio" && existingAnswer === key) {
          input.checked = true;
        }
      }
      if (readOnly) {
        input.disabled = true;
      }

      const textSpan = document.createElement("span");
      textSpan.className = "form-check-label";
      textSpan.textContent = `${key}. ${options[key] ?? ""}`;

      optionLabel.appendChild(input);
      optionLabel.appendChild(textSpan);
      optionsDiv.appendChild(optionLabel);
    });

    body.appendChild(optionsDiv);
  }

  const feedbackDiv = document.createElement("div");
  feedbackDiv.className = "question-feedback mt-2";

  card.appendChild(header);
  card.appendChild(textDiv);
  card.appendChild(body);
  card.appendChild(feedbackDiv);

  return card;
}

function getUserAnswer(question, index) {
  const kind = detectQuestionKind(question);

  if (kind === "short") {
    const textarea = document.getElementById(`q-${index}-short`);
    return textarea ? textarea.value : "";
  }

  const name = `q-${index}`;

  if (kind === "multiple") {
    const inputs = Array.from(
      document.querySelectorAll(`input[name="${name}"]:checked`)
    );
    const letters = inputs.map((el) => el.value.toString().toUpperCase()).sort();
    return letters.join("");
  }

  const checked = document.querySelector(
    `input[name="${name}"]:checked`
  );
  return checked ? checked.value.toString().toUpperCase() : "";
}

function setQuestionFeedback(question, index, grade, userAnswer) {
  const card = document.querySelector(
    `.question-card[data-question-index="${index}"]`
  );
  if (!card) return;

  const feedbackDiv = card.querySelector(".question-feedback");
  if (!feedbackDiv) return;

  const explanationText = question.explanation || "";
  const analysisText = question.analysis || "";
  const extraHtml = `
    ${
      explanationText
        ? `<div><strong>依据：</strong>${explanationText}</div>`
        : ""
    }
    ${
      analysisText
        ? `<div><strong>解析：</strong>${analysisText}</div>`
        : ""
    }
  `;

  if (grade.kind === "short") {
    card.classList.remove("correct", "incorrect");
    feedbackDiv.innerHTML = `
      <div><strong>参考答案：</strong>${grade.correctDisplay || "（题库中无答案）"}</div>
      ${extraHtml}
    `;
    return;
  }

  if (grade.isCorrect === null) {
    card.classList.remove("correct", "incorrect");
    feedbackDiv.innerHTML = `
      <div>本题答案格式不规范，未自动判分。</div>
      <div><strong>参考答案：</strong>${grade.correctDisplay || "（题库中无答案）"}</div>
      ${extraHtml}
    `;
    return;
  }

  if (grade.isCorrect) {
    card.classList.add("correct");
    card.classList.remove("incorrect");
    feedbackDiv.innerHTML = `
      <div><strong>回答正确！</strong>本题得分：${grade.earnedScore} / ${
      grade.totalScore
    }</div>
      ${extraHtml}
    `;
  } else {
    card.classList.add("incorrect");
    card.classList.remove("correct");
    const uaDisplay = userAnswer || "未作答";
    feedbackDiv.innerHTML = `
      <div><strong>回答错误。</strong>你的答案：${uaDisplay}，正确答案：${
      grade.correctDisplay || "（题库中无答案）"
    }。</div>
      ${extraHtml}
    `;
  }
}

// 练习模式状态
let practiceQuestions = [];
let practiceIndex = 0;
// 每道题的作答与判分结果：{ userAnswer: string, grade: Grade | null }
let practiceStates = [];

// 考试模式状态
let examQuestions = [];

function computePracticeStats() {
  const total = practiceQuestions.length;
  let answered = 0;
  let correct = 0;
  let autoTotalScore = 0;
  let autoEarnedScore = 0;

  practiceStates.forEach((state, idx) => {
    if (!state || !state.grade) return;
    const grade = state.grade;
    const isShort = grade.kind === "short";

    if (isShort) {
      // 简答题视为已作答，但不计入自动判分统计
      answered += 1;
      return;
    }

    if (grade.isCorrect !== null) {
      answered += 1;
      if (grade.isCorrect) correct += 1;
      autoTotalScore += grade.totalScore;
      autoEarnedScore += grade.earnedScore;
    }
  });

  return { total, answered, correct, autoTotalScore, autoEarnedScore };
}

function updatePracticeProgress() {
  const progress = document.getElementById("practiceProgress");
  if (!progress || !practiceQuestions.length) return;
  const stats = computePracticeStats();
  const current = Math.min(practiceIndex + 1, stats.total || 0);
  progress.textContent = `当前第 ${current} / ${
    stats.total
  } 题，已答 ${stats.answered} 题，其中自动判对 ${
    stats.correct
  } 题，自动得分 ${
    stats.autoEarnedScore
  } / ${stats.autoTotalScore} 分。`;
}

function renderPracticeNav() {
  const nav = document.getElementById("practiceNav");
  if (!nav) return;
  nav.innerHTML = "";

  practiceQuestions.forEach((q, idx) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-sm btn-outline-secondary nav-item";
    btn.textContent = String(idx + 1);
    if (idx === practiceIndex) {
      btn.classList.add("current");
    }
    const state = practiceStates[idx];
    if (state && state.grade) {
      const grade = state.grade;
      if (grade.kind === "short") {
        btn.classList.add("answered");
      } else if (grade.isCorrect === true) {
        btn.classList.add("correct");
      } else if (grade.isCorrect === false) {
        btn.classList.add("incorrect");
      }
    }
    btn.addEventListener("click", () => {
      practiceIndex = idx;
      renderCurrentPracticeQuestion();
    });
    nav.appendChild(btn);
  });
}

function renderExamNav() {
  const nav = document.getElementById("examNav");
  if (!nav) return;
  nav.innerHTML = "";

  examQuestions.forEach((q, idx) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-sm btn-outline-secondary nav-item";
    btn.textContent = String(idx + 1);
    btn.addEventListener("click", () => {
      scrollToExamQuestion(idx);
    });
    nav.appendChild(btn);
  });

  updateExamNavStatus();
}

function updateExamNavStatus() {
  const nav = document.getElementById("examNav");
  if (!nav || !examQuestions.length) return;
  const buttons = nav.querySelectorAll("button.nav-item");

  buttons.forEach((btn, idx) => {
    btn.classList.remove("answered", "correct", "incorrect");
    const q = examQuestions[idx];
    const answer = getUserAnswer(q, idx);
    if (answer) {
      btn.classList.add("answered");
    }
    const card = document.querySelector(
      `.question-card[data-question-index="${idx}"]`
    );
    if (card) {
      if (card.classList.contains("correct")) {
        btn.classList.add("correct");
      } else if (card.classList.contains("incorrect")) {
        btn.classList.add("incorrect");
      }
    }
  });
}

function scrollToExamQuestion(index) {
  const card = document.querySelector(
    `.question-card[data-question-index="${index}"]`
  );
  if (card) {
    card.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function initControls() {
  const startButton = document.getElementById("startButton");
  const submitPracticeButton = document.getElementById(
    "submitPracticeButton"
  );
  const nextPracticeButton = document.getElementById("nextPracticeButton");
  const submitExamButton = document.getElementById("submitExamButton");
  const clearWrongBookButton = document.getElementById(
    "clearWrongBookButton"
  );
  const clearPracticedButton = document.getElementById(
    "clearPracticedButton"
  );

  startButton.addEventListener("click", handleStart);
  submitPracticeButton.addEventListener("click", handlePracticeSubmit);
  nextPracticeButton.addEventListener("click", handlePracticeNext);
  submitExamButton.addEventListener("click", handleExamSubmit);
  if (clearWrongBookButton) {
    clearWrongBookButton.addEventListener("click", handleClearWrongBook);
  }
  if (clearPracticedButton) {
    clearPracticedButton.addEventListener("click", handleClearPracticedBook);
  }

  loadCategories();
  loadSourceFiles();
  loadTypes();
}

function handleClearWrongBook() {
  const confirmed = window.confirm("确定要清空当前浏览器里的全部错题记录吗？");
  if (!confirmed) return;

  try {
    localStorage.removeItem(WRONG_BOOK_KEY);
    const status = document.getElementById("statusMessage");
    if (status) {
      status.textContent = "错题本已清空。";
    }
  } catch (e) {
    console.error("清空错题本失败:", e);
  }
}

function handleClearPracticedBook() {
  const confirmed = window.confirm("确定要清空当前浏览器里的练习记录吗？顺序练习会从头开始。");
  if (!confirmed) return;

  try {
    localStorage.removeItem(PRACTICED_BOOK_KEY);
    const status = document.getElementById("statusMessage");
    if (status) {
      status.textContent = "练习记录已清空。";
    }
  } catch (e) {
    console.error("清空练习记录失败:", e);
  }
}

async function loadCategories() {
  const select = document.getElementById("categorySelect");
  const status = document.getElementById("statusMessage");

  try {
    status.textContent = "正在加载类别...";
    const resp = await fetch("/api/categories");
    if (!resp.ok) throw new Error("加载类别失败");
    const data = await resp.json();
    const categories = data.categories || [];

    // 清除“全部类别”以外的选项
    while (select.options.length > 1) {
      select.remove(1);
    }

    categories.forEach((c) => {
      const opt = document.createElement("option");
      opt.value = c;
      opt.textContent = c;
      select.appendChild(opt);
    });

    status.textContent = `类别加载完成，共 ${categories.length} 个。`;
  } catch (e) {
    status.textContent = "加载类别失败，请检查后台服务。";
    // 控制台保留详细错误信息，方便调试
    console.error(e);
  }
}

async function loadSourceFiles() {
  const select = document.getElementById("sourceFileSelect");
  const status = document.getElementById("statusMessage");

  if (!select) return;

  try {
    status.textContent = "正在加载来源文件...";
    const resp = await fetch("/api/source_files");
    if (!resp.ok) throw new Error("加载来源文件失败");
    const data = await resp.json();
    const sourceFiles = data.source_files || [];

    while (select.options.length > 1) {
      select.remove(1);
    }

    sourceFiles.forEach((s) => {
      const opt = document.createElement("option");
      opt.value = s;
      opt.textContent = s;
      select.appendChild(opt);
    });

    status.textContent = `来源文件加载完成，共 ${sourceFiles.length} 个。`;
  } catch (e) {
    status.textContent = "加载来源文件失败，请检查后台服务。";
    console.error(e);
  }
}

async function loadTypes() {
  const select = document.getElementById("typeSelect");
  const status = document.getElementById("statusMessage");

  if (!select) return;

  try {
    status.textContent = "正在加载题型...";
    const resp = await fetch("/api/types");
    if (!resp.ok) throw new Error("加载题型失败");
    const data = await resp.json();
    const types = data.types || [];

    while (select.options.length > 1) {
      select.remove(1);
    }

    types.forEach((t) => {
      const opt = document.createElement("option");
      opt.value = t;
      opt.textContent = t;
      select.appendChild(opt);
    });

    status.textContent = `题型加载完成，共 ${types.length} 个。`;
  } catch (e) {
    status.textContent = "加载题型失败，请检查后台服务。";
    console.error(e);
  }
}

async function handleStart() {
  const mode = document.getElementById("modeSelect").value;
  const category = document.getElementById("categorySelect").value;
  const sourceFileSelect = document.getElementById("sourceFileSelect");
  const sourceFile = sourceFileSelect ? sourceFileSelect.value : "";
  const qtype = document.getElementById("typeSelect")
    ? document.getElementById("typeSelect").value
    : "";
  const countInput = document.getElementById("countInput");
  const status = document.getElementById("statusMessage");

  const count = parseInt(countInput.value, 10);
  if (!Number.isFinite(count) || count <= 0) {
    status.textContent = "题目数量必须为正整数。";
    return;
  }

  // 错题练习模式：直接从本地错题本抽题，不请求后端
  if (mode === "wrong") {
    const allWrong = getWrongBookQuestions();
    if (!allWrong.length) {
      status.textContent = "错题本为空，目前没有记录错题。";
      return;
    }

    const pool = allWrong.slice();
    // 打乱顺序
    for (let i = pool.length - 1; i > 0; i -= 1) {
      const j = Math.floor(Math.random() * (i + 1));
      const tmp = pool[i];
      pool[i] = pool[j];
      pool[j] = tmp;
    }

    const selected = pool.slice(0, Math.min(count, pool.length));
    status.textContent = `从错题本抽取 ${selected.length} 题进行练习。`;
    startPracticeMode(selected);
    return;
  }

  status.textContent = "正在抽题...";

  const params = new URLSearchParams();
  const backendMode = mode === "sequence" ? "sequence" : mode;
  params.set("mode", backendMode);
  // 顺序练习模式：从后端取较多题目（按题号排序），前端再根据练习记录筛选
  if (mode === "sequence") {
    params.set("count", String(Math.max(count * 5, 500)));
  } else {
    params.set("count", String(count));
  }
  if (category) params.set("category", category);
  if (sourceFile) params.set("source_file", sourceFile);
  if (qtype) params.set("qtype", qtype);

  try {
    const resp = await fetch(`/api/questions?${params.toString()}`);
    if (!resp.ok) throw new Error("抽题失败");
    const data = await resp.json();
    let questions = data.questions || [];

    if (!questions.length) {
      status.textContent = "没有抽到题目，请尝试调整类别或数量。";
      return;
    }

    if (mode === "sequence") {
      const practicedMap = getPracticedMap();
      const unpracticed = questions.filter(
        (q) => !Object.prototype.hasOwnProperty.call(practicedMap, String(q.id))
      );
      const selected = unpracticed.slice(0, count);
      if (!selected.length) {
        status.textContent = "当前条件下的题目已全部练习过。";
        return;
      }
      status.textContent = `顺序练习模式，本次从未练习题中抽取 ${selected.length} 题。`;
      startPracticeMode(selected);
    } else {
      status.textContent = `本次共抽取 ${questions.length} 题。`;
      if (mode === "practice") {
        startPracticeMode(questions);
      } else {
        startExamMode(questions);
      }
    }
  } catch (e) {
    status.textContent = "抽题失败，请检查后台服务。";
    console.error(e);
  }
}

function startPracticeMode(questions) {
  practiceQuestions = questions;
  practiceIndex = 0;
  practiceStates = questions.map(() => ({
    userAnswer: "",
    grade: null,
  }));

  document.getElementById("practicePanel").classList.remove("hidden");
  document.getElementById("examPanel").classList.add("hidden");

  renderCurrentPracticeQuestion();
}

function renderCurrentPracticeQuestion() {
  const container = document.getElementById(
    "practiceQuestionContainer"
  );
  const feedback = document.getElementById("practiceFeedback");
  const progress = document.getElementById("practiceProgress");
  const submitBtn = document.getElementById("submitPracticeButton");
  const nextBtn = document.getElementById("nextPracticeButton");

  container.innerHTML = "";
  feedback.innerHTML = "";

  if (!practiceQuestions.length) {
    progress.textContent = "";
    submitBtn.disabled = true;
    nextBtn.disabled = true;
    return;
  }

  const question = practiceQuestions[practiceIndex];
  const state = practiceStates[practiceIndex];
  const hasGrade = state && state.grade;
  const card = createQuestionElement(question, practiceIndex, {
    existingAnswer: state ? state.userAnswer : "",
    readOnly: !!hasGrade,
  });
  container.appendChild(card);

  const kind = detectQuestionKind(question);

  // 下一题按钮在练习模式中始终可用，用于跳过未作答题目
  nextBtn.disabled = false;

  // 根据题型控制“提交本题”按钮显示与自动提交逻辑
  if (kind === "single" || kind === "truefalse") {
    // 单选/判断：点击选项自动提交
    submitBtn.style.display = "none";
    submitBtn.disabled = true;

    if (!hasGrade) {
      const radios = card.querySelectorAll('input[type="radio"]');
      radios.forEach((input) => {
        input.addEventListener("change", () => {
          handlePracticeSubmit();
        });
      });
    }
  } else {
    // 多选与简答：保留“提交本题”按钮
    submitBtn.style.display = "";
    submitBtn.disabled = !!hasGrade;
  }

  // 若本题已经判分过，恢复反馈信息
  if (hasGrade) {
    setQuestionFeedback(
      question,
      practiceIndex,
      state.grade,
      state.userAnswer
    );
  }

  renderPracticeNav();
  updatePracticeProgress();
}

function handlePracticeSubmit() {
  if (
    !practiceQuestions.length ||
    practiceIndex >= practiceQuestions.length
  ) {
    return;
  }

  const question = practiceQuestions[practiceIndex];
  const kind = detectQuestionKind(question);
  const userAnswer = getUserAnswer(question, practiceIndex);

  if (!userAnswer && kind !== "short") {
    const feedback = document.getElementById("practiceFeedback");
    feedback.textContent = "请先作答再提交。";
    return;
  }

  const grade = gradeQuestion(question, kind, userAnswer);
  practiceStates[practiceIndex] = {
    userAnswer,
    grade,
  };

  // 记录为已练习题目（无论对错）
  addToPracticedBook(question.id);

  // 错题本记录：客观题答错加入，答对则从错题本移除
  if (grade.kind !== "short" && grade.isCorrect === false) {
    addToWrongBook(question);
  } else if (grade.kind !== "short" && grade.isCorrect === true) {
    removeFromWrongBook(question.id);
  }

  // 单题反馈（练习模式即时展示）
  setQuestionFeedback(question, practiceIndex, grade, userAnswer);

  const feedback = document.getElementById("practiceFeedback");
  if (grade.kind === "short") {
    feedback.textContent = "简答题仅提供参考答案，不进行自动判分。";
  } else if (grade.isCorrect === null) {
    feedback.textContent = "本题答案格式不规范，未自动判分。";
  } else if (grade.isCorrect) {
    feedback.textContent = "回答正确！";
  } else {
    feedback.textContent = "回答错误，请查看正确答案和解析。";
  }

  // 提交后，本题不再允许修改
  const card = document.querySelector(
    `.question-card[data-question-index="${practiceIndex}"]`
  );
  if (card) {
    const inputs = card.querySelectorAll("input, textarea");
    inputs.forEach((el) => {
      el.disabled = true;
    });
  }

  // 更新导航与统计
  renderPracticeNav();
  updatePracticeProgress();

  const submitBtn = document.getElementById("submitPracticeButton");
  submitBtn.disabled = true;

  // 做对题目时自动跳转到下一题；做错则停留当前题
  const nextBtn = document.getElementById("nextPracticeButton");
  const shouldAutoNext = grade.kind !== "short" && grade.isCorrect === true;
  if (shouldAutoNext && nextBtn) {
    nextBtn.disabled = true;
    // 略停留片刻，让用户看到“正确”的反馈再跳转
    setTimeout(() => {
      handlePracticeNext();
    }, 500);
  }
}

function showPracticeSummary() {
  const submitBtn = document.getElementById("submitPracticeButton");
  const nextBtn = document.getElementById("nextPracticeButton");
  const progress = document.getElementById("practiceProgress");

  const stats = computePracticeStats();
  progress.textContent = `本次练习已完成，共 ${stats.total} 题，其中自动判对 ${
    stats.correct
  } 题，自动得分 ${
    stats.autoEarnedScore
  } / ${stats.autoTotalScore} 分。`;
  submitBtn.disabled = true;
  nextBtn.disabled = true;
}

function handlePracticeNext() {
  if (practiceIndex < practiceQuestions.length - 1) {
    practiceIndex += 1;
    renderCurrentPracticeQuestion();
  } else {
    showPracticeSummary();
  }
}

function startExamMode(questions) {
  examQuestions = questions;

  document.getElementById("examPanel").classList.remove("hidden");
  document.getElementById("practicePanel").classList.add("hidden");

  const modeSelect = document.getElementById("modeSelect");
  const mode = modeSelect ? modeSelect.value : "exam";
  const titleEl = document.querySelector("#examPanel h2");
  if (titleEl) {
    titleEl.textContent = mode === "mock" ? "模拟考试模式" : "考试模式";
  }

  const container = document.getElementById("examQuestionsContainer");
  container.innerHTML = "";

  examQuestions.forEach((q, idx) => {
    const card = createQuestionElement(q, idx);
    container.appendChild(card);
  });

  document.getElementById("examSummary").innerHTML = "";

  renderExamNav();

  // 监听答题变化，用于更新题号导航的“已作答”状态
  container.addEventListener("change", () => {
    updateExamNavStatus();
  });
  container.addEventListener("input", () => {
    updateExamNavStatus();
  });
}

function handleExamSubmit() {
  if (!examQuestions.length) return;

  let autoTotalScore = 0;
  let autoEarnedScore = 0;
  let correctCount = 0;
  let autoQuestionCount = 0;
  let shortQuestionCount = 0;

  examQuestions.forEach((q, idx) => {
    const kind = detectQuestionKind(q);
    const userAnswer = getUserAnswer(q, idx);
    const grade = gradeQuestion(q, kind, userAnswer);

    if (kind === "short") {
      shortQuestionCount += 1;
    } else if (grade.isCorrect !== null) {
      autoQuestionCount += 1;
      autoTotalScore += grade.totalScore;
      autoEarnedScore += grade.earnedScore;
      if (grade.isCorrect) correctCount += 1;

      // 错题本记录：客观题答错加入，答对则移除
      if (grade.isCorrect === false) {
        addToWrongBook(q);
      } else if (grade.isCorrect === true) {
        removeFromWrongBook(q.id);
      }
    }

    setQuestionFeedback(q, idx, grade, userAnswer);
  });

  const summary = document.getElementById("examSummary");
  const totalQuestions = examQuestions.length;
  const accuracy =
    autoQuestionCount > 0
      ? ((correctCount / autoQuestionCount) * 100).toFixed(1)
      : "0.0";

  summary.innerHTML = `
    <div>本次共 ${totalQuestions} 题，其中自动判分题 ${autoQuestionCount} 题，简答题 ${shortQuestionCount} 题。</div>
    <div>自动判分总分：${autoEarnedScore} / ${autoTotalScore} 分，正确 ${correctCount} 题，正确率 ${accuracy}%。</div>
    ${
      shortQuestionCount > 0
        ? "<div>简答题请根据参考答案自行核分（默认每题 10 分，或以题库中分值为准）。</div>"
        : ""
    }
  `;

  // 交卷后更新题号导航的对错标记
  updateExamNavStatus();
}

document.addEventListener("DOMContentLoaded", initControls);
