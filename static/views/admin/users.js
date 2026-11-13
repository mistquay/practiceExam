// 用户管理：CSV 批量导入、查询、重置密码、停用/启用
import { importUsers, listUsers, resetPassword, setUserActive } from "../../api/admin.js";
import { h, mount, loading, errorBox } from "../../components/dom.js";
import { dataTable } from "../../components/table.js";
import { toast, toastError } from "../../components/toast.js";

export async function render(view) {
  const file = h("input", { type: "file", accept: ".csv,text/csv", class: "input" });
  const importBtn = h("button", { class: "btn btn-primary" }, "导入");
  const importResult = h("div");
  const keyword = h("input", { class: "input", placeholder: "用户名 / 姓名" });
  const listBox = h("div");

  importBtn.addEventListener("click", async () => {
    if (!file.files.length) {
      toast("请先选择 CSV 文件", "warning");
      return;
    }
    importBtn.disabled = true;
    mount(importResult, loading("正在导入…"));
    try {
      const r = await importUsers(file.files[0]);
      const errors = r.errors || [];
      mount(
        importResult,
        h("div", { class: `alert ${errors.length ? "alert-warning" : "alert-success"}` }, `新增 ${r.created ?? 0} 人，更新 ${r.updated ?? 0} 人，失败 ${errors.length} 行`),
        errors.length
          ? dataTable(
              [
                { title: "行号", key: "line" },
                { title: "原因", key: "message" },
              ],
              errors
            )
          : null
      );
      search();
    } catch (err) {
      mount(importResult, errorBox(err));
    } finally {
      importBtn.disabled = false;
    }
  });

  async function onReset(user) {
    if (!window.confirm(`确定重置 ${user.name}（${user.employee_no}）的密码？`)) return;
    try {
      const r = await resetPassword(user.id);
      window.alert(r && r.temp_password ? `新的临时密码：${r.temp_password}\n请线下告知本人并要求尽快修改。` : "密码已重置");
    } catch (err) {
      toastError(err);
    }
  }

  async function onToggle(user) {
    const next = !user.active;
    if (!window.confirm(`确定${next ? "启用" : "停用"} ${user.name}（${user.employee_no}）？`)) return;
    try {
      await setUserActive(user.id, next);
      toast(next ? "已启用" : "已停用", "success");
      search();
    } catch (err) {
      toastError(err);
    }
  }

  async function search() {
    mount(listBox, loading());
    try {
      const rows = await listUsers(keyword.value.trim());
      mount(
        listBox,
        dataTable(
          [
            { title: "用户名", key: "employee_no" },
            { title: "姓名", key: "name" },
            { title: "单位/班组", key: "team" },
            { title: "角色", render: (u) => (u.role === "admin" ? "管理员" : "考生") },
            { title: "状态", render: (u) => (u.active === false ? h("span", { class: "badge badge-bad" }, "停用") : "正常") },
            {
              title: "操作",
              render: (u) =>
                h(
                  "div",
                  { class: "actions-inline" },
                  h("button", { class: "btn btn-ghost btn-sm", onclick: () => onReset(u) }, "重置密码"),
                  h("button", { class: "btn btn-ghost btn-sm", onclick: () => onToggle(u) }, u.active === false ? "启用" : "停用")
                ),
            },
          ],
          rows,
          "没有匹配的用户"
        )
      );
    } catch (err) {
      mount(listBox, errorBox(err));
    }
  }

  mount(
    view,
    h(
      "section",
      { class: "card" },
      h("h2", { class: "card-title" }, "批量导入用户（CSV）"),
      h(
        "p",
        { class: "hint" },
        "UTF-8 编码，首行为表头：employee_no,name,team,role,initial_password。用户名唯一；已存在的用户名按更新处理；role 取 admin / examinee。"
      ),
      h("div", { class: "row-inline" }, file, importBtn),
      importResult
    ),
    h(
      "section",
      { class: "card" },
      h("h2", { class: "card-title" }, "用户列表"),
      h(
        "form",
        { class: "row-inline", onsubmit: (e) => (e.preventDefault(), search()) },
        keyword,
        h("button", { type: "submit", class: "btn btn-outline" }, "查询")
      ),
      listBox
    )
  );
  search();
}
