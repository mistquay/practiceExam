// 入口：hash 路由 + 会话守卫。视图按需动态加载（原生 ESM，无构建链）。
import { onUnauthorized } from "./api/http.js";
import * as authApi from "./api/auth.js";
import { loadSession, clearSession, getUser, isAdmin } from "./state/session.js";
import { resetSelection } from "./state/selection.js";
import { clearAllDrafts } from "./state/exam-draft.js";
import { h, mount, errorBox, loading } from "./components/dom.js";
import { toastError } from "./components/toast.js";

const routes = [
  { pattern: "/login", load: () => import("./views/login.js"), public: true },
  { pattern: "/", load: () => import("./views/home.js") },
  { pattern: "/practice", load: () => import("./views/practice.js") },
  { pattern: "/exam/:id", load: () => import("./views/exam.js") },
  { pattern: "/wrong", load: () => import("./views/wrong-book.js") },
  { pattern: "/records", load: () => import("./views/practice-records.js") },
  { pattern: "/history", load: () => import("./views/exam-history.js") },
  { pattern: "/admin", load: () => import("./views/admin/index.js"), admin: true },
  { pattern: "/admin/:tab", load: () => import("./views/admin/index.js"), admin: true },
];

const root = document.getElementById("app");
const navEl = document.getElementById("main-nav");
const userEl = document.getElementById("user-info");

let cleanup = null;
let sessionReady = false;

export function navigate(path) {
  if (location.hash === `#${path}`) render();
  else location.hash = path;
}

function parseHash() {
  const raw = location.hash.replace(/^#/, "") || "/";
  const [path, qs] = raw.split("?");
  return { path: path || "/", query: new URLSearchParams(qs || "") };
}

function match(path) {
  const parts = path.split("/").filter(Boolean);
  for (const route of routes) {
    const segs = route.pattern.split("/").filter(Boolean);
    if (segs.length !== parts.length) continue;
    const params = {};
    const ok = segs.every((seg, i) => {
      if (seg.startsWith(":")) {
        params[seg.slice(1)] = decodeURIComponent(parts[i]);
        return true;
      }
      return seg === parts[i];
    });
    if (ok) return { route, params };
  }
  return null;
}

async function ensureSession() {
  if (sessionReady) return true;
  try {
    await loadSession();
    sessionReady = true;
  } catch (err) {
    if (err.status !== 401) toastError(err);
    return false;
  }
  return true;
}

function renderChrome() {
  const user = getUser();
  if (!user) {
    mount(navEl);
    mount(userEl);
    return;
  }
  const { path } = parseHash();
  const links = [
    ["/", "选题"],
    ["/wrong", "错题本"],
    ["/records", "练习记录"],
    ["/history", "考试记录"],
  ];
  if (isAdmin()) links.push(["/admin", "管理"]);
  mount(
    navEl,
    links.map(([href, text]) =>
      h(
        "a",
        { href: `#${href}`, class: (href === "/" ? path === "/" : path.startsWith(href)) ? "active" : null },
        text
      )
    )
  );
  mount(
    userEl,
    h("span", null, `${user.name || ""}（${user.employee_no || ""}）`),
    h("button", { type: "button", class: "btn btn-ghost btn-sm", onclick: logout }, "退出")
  );
}

async function logout() {
  try {
    await authApi.logout();
  } catch (err) {
    if (err.status !== 401) toastError(err);
  }
  resetLocalState();
  navigate("/login");
}

function resetLocalState() {
  clearSession();
  resetSelection();
  clearAllDrafts();
  sessionReady = false;
}

async function render() {
  if (typeof cleanup === "function") cleanup();
  cleanup = null;

  const { path, query } = parseHash();
  const found = match(path);
  if (!found) {
    mount(root, errorBox(`页面不存在：${path}`));
    return;
  }
  const { route, params } = found;

  if (!route.public && !(await ensureSession())) {
    const redirect = encodeURIComponent(location.hash.replace(/^#/, ""));
    location.hash = `/login?redirect=${redirect}`;
    return;
  }
  renderChrome();

  if (route.admin && !isAdmin()) {
    mount(root, errorBox("无权限：仅管理员可访问"));
    return;
  }

  // 每次渲染用新容器：上一个视图未完成的异步回调写入的是已脱离文档的旧容器，互不干扰
  const view = h("div", { class: "view" });
  mount(root, view);
  view.appendChild(loading());
  try {
    const mod = await route.load();
    view.replaceChildren();
    cleanup = await mod.render(view, { params, query, navigate, onLogin: handleLogin });
  } catch (err) {
    mount(view, errorBox(err));
  }
}

async function handleLogin() {
  sessionReady = false;
  return ensureSession();
}

onUnauthorized(() => {
  if (!sessionReady && !getUser()) return;
  resetLocalState();
  renderChrome();
  if (!parseHash().path.startsWith("/login")) navigate("/login");
});

window.addEventListener("hashchange", render);
render();
