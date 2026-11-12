// 三步选题：人员类别（取自当前有效授权，只读）→ 专业 → 试卷类型
import { listBanks, getDimension } from "../api/banks.js";
import { listAvailable, startExam, listMyExams } from "../api/exam.js";
import { getAuthorization } from "../state/session.js";
import { getSelection, saveSelection } from "../state/selection.js";
import { h, mount, loading, errorBox, empty, fmtDate, fmtDateTime } from "../components/dom.js";
import { toastError } from "../components/toast.js";

// 试卷类型枚举（方案 §12-11）；后端 dimension.paper_types 有值时以后端为准
const DEFAULT_PAPER_TYPES = [
  { code: "practice", name: "练习", desc: "逐题即时反馈，可看解析" },
  { code: "mock", name: "模拟考试", desc: "整卷计时，交卷后出分" },
  { code: "formal", name: "正式考试", desc: "按场次参加，计入成绩" },
  { code: "makeup", name: "补考", desc: "按场次补考" },
];

const asOption = (item) =>
  typeof item === "string" ? { code: item, name: item } : { ...item, code: item.code ?? item.value, name: item.name ?? item.label ?? item.code };

export async function render(view, { navigate }) {
  const auth = getAuthorization();
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
      renderSteps(stepsBox, { bankId: id, dim, auth, navigate });
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

function renderSteps(box, { bankId, dim, auth, navigate }) {
  const sel = getSelection();
  const specialties = (dim.specialties || []).map(asOption).filter((s) => s.enabled !== false);
  const paperTypes = dim.paper_types && dim.paper_types.length ? dim.paper_types.map(asOption) : DEFAULT_PAPER_TYPES;
  const personCategories = (dim.person_categories || []).map((p) => asOption(p).name);

  let specialty = specialties.some((s) => s.code === sel.specialtyCode)
    ? sel.specialtyCode
    : auth && specialties.some((s) => s.code === auth.specialty_code)
      ? auth.specialty_code
      : "";
  let paperType = paperTypes.some((p) => p.code === sel.paperType) ? sel.paperType : "";

  const step2 = h("div");
  const step3 = h("div");
  const step4 = h("div");

  // 步骤 1：人员类别（只读，来自授权）
  const step1 = auth && auth.person_category
    ? h(
        "div",
        { class: "locked-value" },
        h("strong", null, auth.person_category),
        h(
          "span",
          { class: "hint" },
          `生效期 ${fmtDate(auth.effective_from)} ~ ${auth.effective_to ? fmtDate(auth.effective_to) : "长期"}`,
          auth.basis_doc ? `，依据：${auth.basis_doc}` : ""
        ),
        personCategories.length && !personCategories.includes(auth.person_category)
          ? h("div", { class: "alert alert-warning" }, "该人员类别不在当前题库版本的可选范围内，请联系管理员")
          : null
      )
    : h("div", { class: "alert alert-danger" }, "未找到当前有效的人员类别授权，无法选题。请联系管理员录入授权（需注明依据与生效期）。");

  function choiceGrid(items, current, onPick) {
    return h(
      "div",
      { class: "choice-grid" },
      items.map((it) =>
        h(
          "button",
          { type: "button", class: `choice-card ${it.code === current ? "selected" : ""}`, onclick: () => onPick(it.code) },
          h("strong", null, it.name),
          it.desc ? h("span", { class: "hint" }, it.desc) : null
        )
      )
    );
  }

  function refresh() {
    saveSelection({ specialtyCode: specialty, paperType });
    mount(
      step2,
      specialties.length
        ? choiceGrid(specialties, specialty, (code) => {
            specialty = code;
            refresh();
          })
        : empty("当前题库版本没有开放的专业")
    );
    mount(
      step3,
      choiceGrid(paperTypes, paperType, (code) => {
        paperType = code;
        refresh();
      })
    );
    if (!auth || !specialty || !paperType) {
      mount(step4, h("p", { class: "hint" }, "请依次完成上面的选择"));
    } else {
      // 每次用新槽位：快速切换时，旧的异步结果只会写进已脱离文档的旧槽位
      const slot = mount(step4, h("div")).firstChild;
      if (paperType === "practice") renderPracticeOptions(slot, { bankId, dim, auth, specialty, navigate });
      else renderExamOptions(slot, { bankId, specialty, paperType, navigate });
    }
  }

  const section = (no, title, content) =>
    h("section", { class: "card step" }, h("h2", { class: "card-title" }, h("span", { class: "step-no" }, no), title), content);

  mount(
    box,
    section("1", "人员类别", step1),
    auth ? section("2", "专业", step2) : null,
    auth ? section("3", "试卷类型", step3) : null,
    auth ? section("4", "开始", step4) : null
  );
  refresh();
}

function renderPracticeOptions(box, { bankId, dim, auth, specialty, navigate }) {
  const qtypes = (dim.qtypes || []).map((q) => asOption(q));
  const order = h(
    "select",
    { class: "input" },
    h("option", { value: "sequence" }, "顺序练习"),
    h("option", { value: "random" }, "随机练习")
  );
  const source = h(
    "select",
    { class: "input" },
    h("option", { value: "all" }, "全部题目"),
    h("option", { value: "unpracticed" }, "仅未练习"),
    h("option", { value: "wrong" }, "仅错题")
  );
  const qtype = h(
    "select",
    { class: "input" },
    h("option", { value: "" }, "全部题型"),
    qtypes.map((q) => h("option", { value: q.code }, q.name))
  );
  const count = h("input", { class: "input", type: "number", min: 1, max: 200, value: "20" });

  function start() {
    const n = Number.parseInt(count.value, 10);
    if (!Number.isInteger(n) || n < 1 || n > 200) {
      toastError("题目数量需为 1~200 的整数");
      return;
    }
    const q = new URLSearchParams({
      bank: bankId,
      person: auth.person_category,
      specialty,
      order: order.value,
      source: source.value,
      count: String(n),
    });
    if (qtype.value) q.set("qtype", qtype.value);
    navigate(`/practice?${q.toString()}`);
  }

  mount(
    box,
    h(
      "div",
      { class: "form-grid" },
      h("label", { class: "field" }, h("span", null, "练习方式"), order),
      h("label", { class: "field" }, h("span", null, "题目范围"), source),
      h("label", { class: "field" }, h("span", null, "题型"), qtype),
      h("label", { class: "field" }, h("span", null, "题目数量"), count)
    ),
    h("button", { class: "btn btn-primary", onclick: start }, "开始练习")
  );
}

async function renderExamOptions(box, { bankId, specialty, paperType, navigate }) {
  mount(box, loading("正在查询可参加的考试…"));
  let items;
  try {
    items = await listAvailable({ bankId, specialtyCode: specialty, paperType });
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
      const attempt = await startExam({ batchId: item.batch_id, templateId: item.template_id, specialtyCode: specialty });
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
