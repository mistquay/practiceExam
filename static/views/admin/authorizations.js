// 人员类别授权：带生效期、依据文号与审批人；变更留痕由服务端 audit_log 记录
import { listUsers, listAuthorizations, createAuthorization, revokeAuthorization } from "../../api/admin.js";
import { listBanks, getDimension } from "../../api/banks.js";
import { h, mount, loading, errorBox, empty, fmtDate, fmtDateTime } from "../../components/dom.js";
import { dataTable } from "../../components/table.js";
import { toast, toastError } from "../../components/toast.js";

const toOption = (x) =>
  typeof x === "string" ? { code: x, name: x } : { code: x.code ?? x.value, name: x.name ?? x.label ?? x.code };

async function loadDictionaries() {
  const banks = await listBanks();
  const bank = banks.find((b) => !b.status || b.status === "active") || banks[0];
  if (!bank) return { persons: [], specialties: [] };
  const dim = await getDimension(bank.id);
  return {
    persons: (dim.person_categories || []).map(toOption),
    specialties: (dim.specialties || []).map(toOption),
  };
}

export async function render(view, { query, navigate }) {
  const keyword = h("input", { class: "input", placeholder: "输入用户名或姓名查找" });
  const pickBox = h("div");
  const detailBox = h("div");

  mount(
    view,
    h(
      "section",
      { class: "card" },
      h("h2", { class: "card-title" }, "选择人员"),
      h(
        "form",
        { class: "row-inline", onsubmit: (e) => (e.preventDefault(), find()) },
        keyword,
        h("button", { type: "submit", class: "btn btn-outline" }, "查找")
      ),
      pickBox
    ),
    detailBox
  );

  async function find() {
    mount(pickBox, loading());
    try {
      const users = await listUsers(keyword.value.trim());
      mount(
        pickBox,
        users.length
          ? h(
              "div",
              { class: "list" },
              users.slice(0, 20).map((u) =>
                h(
                  "button",
                  { type: "button", class: "list-item list-button", onclick: () => navigate(`/admin/auth?user=${u.id}`) },
                  h("span", null, `${u.name}（${u.employee_no}）`),
                  h("span", { class: "hint" }, u.team || "")
                )
              )
            )
          : empty("没有匹配的用户")
      );
    } catch (err) {
      mount(pickBox, errorBox(err));
    }
  }

  const userId = query.get("user");
  if (userId) showUser(detailBox, userId);
}

async function showUser(box, userId) {
  mount(box, loading());
  let dict;
  try {
    dict = await loadDictionaries();
  } catch (err) {
    mount(box, errorBox(err));
    return;
  }

  const historyBox = h("div");
  const person = h(
    "select",
    { class: "input", required: true },
    h("option", { value: "" }, "请选择"),
    dict.persons.map((p) => h("option", { value: p.code }, p.name))
  );
  const specialty = h(
    "select",
    { class: "input" },
    h("option", { value: "" }, "不限定"),
    dict.specialties.map((s) => h("option", { value: s.code }, s.name))
  );
  const from = h("input", { class: "input", type: "date", required: true });
  const to = h("input", { class: "input", type: "date" });
  const basis = h("input", { class: "input", required: true, placeholder: "文号 / 岗位清单名称" });
  const approver = h("input", { class: "input", required: true, placeholder: "审批人" });

  async function onCreate(e) {
    e.preventDefault();
    if (to.value && to.value < from.value) {
      toast("失效日期不能早于生效日期", "warning");
      return;
    }
    if (!window.confirm("确定提交该授权？与现有授权的衔接以服务端规则为准。")) return;
    try {
      await createAuthorization({
        user_id: Number(userId) || userId,
        person_category: person.value,
        specialty_code: specialty.value || null,
        effective_from: from.value,
        effective_to: to.value || null,
        basis_doc: basis.value.trim(),
        approver: approver.value.trim(),
      });
      toast("授权已保存", "success");
      e.target.reset();
      loadHistory();
    } catch (err) {
      toastError(err);
    }
  }

  async function onRevoke(auth) {
    if (!window.confirm(`确定撤销「${auth.person_category}」授权？`)) return;
    try {
      await revokeAuthorization(auth.id);
      toast("已撤销", "success");
      loadHistory();
    } catch (err) {
      toastError(err);
    }
  }

  async function loadHistory() {
    mount(historyBox, loading());
    try {
      const rows = await listAuthorizations(userId);
      mount(
        historyBox,
        dataTable(
          [
            { title: "人员类别", key: "person_category" },
            { title: "专业", render: (r) => r.specialty_name || r.specialty_code || "—" },
            { title: "生效", render: (r) => fmtDate(r.effective_from) },
            { title: "失效", render: (r) => (r.effective_to ? fmtDate(r.effective_to) : "长期") },
            { title: "依据", key: "basis_doc" },
            { title: "审批人", key: "approver" },
            { title: "录入时间", render: (r) => fmtDateTime(r.decided_at) },
            {
              title: "状态",
              render: (r) =>
                r.revoked_at
                  ? `已撤销 ${fmtDateTime(r.revoked_at)}`
                  : h("button", { class: "btn btn-ghost btn-sm", onclick: () => onRevoke(r) }, "撤销"),
            },
          ],
          rows,
          "暂无授权记录"
        )
      );
    } catch (err) {
      mount(historyBox, errorBox(err));
    }
  }

  mount(
    box,
    h("section", { class: "card" }, h("h2", { class: "card-title" }, `授权历史（用户 #${userId}）`), historyBox),
    h(
      "section",
      { class: "card" },
      h("h2", { class: "card-title" }, "新增授权"),
      h(
        "form",
        { class: "form-grid", onsubmit: onCreate },
        h("label", { class: "field" }, h("span", null, "人员类别 *"), person),
        h("label", { class: "field" }, h("span", null, "专业"), specialty),
        h("label", { class: "field" }, h("span", null, "生效日期 *"), from),
        h("label", { class: "field" }, h("span", null, "失效日期"), to),
        h("label", { class: "field" }, h("span", null, "依据 *"), basis),
        h("label", { class: "field" }, h("span", null, "审批人 *"), approver),
        h("div", { class: "field field-end" }, h("button", { type: "submit", class: "btn btn-primary" }, "保存授权"))
      )
    )
  );
  loadHistory();
}
