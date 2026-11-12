// 入口：hash 路由 + 会话守卫。视图按需动态加载（原生 ESM，无构建链）。
import { onUnauthorized } from "./api/http.js";
import * as authApi from "./api/auth.js";
import { loadSession, clearSession, getUser } from "./state/session.js";
import { canAccess } from "./route-access.js";
import { resetSelection } from "./state/selection.js";
import { clearAllDrafts } from "./state/exam-draft.js";
import { h, mount, errorBox, loading } from "./components/dom.js";
import { toastError } from "./components/toast.js";

const routes = [
  { pattern: "/login", load: () => import("./views/login.js"), public: true },
  { pattern: "/register", load: () => import("./views/register.js"), public: true },
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
let renderRevision = 0;

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
  const previousUser = getUser();
  sessionReady = false;
  try {
    const user = await loadSession();
    if (!user) return false;
    if (previousUser && (previousUser.id !== user.id || previousUser.role !== user.role)) {
      resetSelection();
      clearAllDrafts();
    }
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
  links.push(["/admin", "管理"]);
  mount(
    navEl,
    links.filter(([href]) => canAccess(match(href)?.route, user)).map(([href, text]) =>
      {
        const active = href === "/" ? path === "/" : path.startsWith(href);
        return h("a", { href: `#${href}`, class: active ? "active" : null, "aria-current": active ? "page" : null }, text);
      }
    )
  );
  mount(
    userEl,
    h("span", null, `${user.name || ""}（${user.employee_no || ""}）`),
    h("button", { type: "button", class: "btn btn-ghost btn-sm", onclick: logout }, "退出")
  );
}

async function logout() {
  resetLocalState();
  try {
    await authApi.logout();
  } catch (err) {
    if (err.status !== 401) toastError(err);
  }
  resetLocalState();
  navigate("/login");
}

function resetLocalState() {
  renderRevision++;
  clearSession();
  mount(root);
  mount(navEl);
  mount(userEl);
  resetSelection();
  clearAllDrafts();
  sessionReady = false;
}

async function render() {
  const revision = ++renderRevision;
  // 会话未确认时立即移除旧页面/导航，尤其不能保留上一账号的管理界面。
  mount(root, loading());
  mount(navEl);
  mount(userEl);
  if (typeof cleanup === "function") cleanup();
  cleanup = null;

  const { path, query } = parseHash();
  const found = match(path);
  if (!found) {
    mount(root, errorBox(`页面不存在：${path}`));
    return;
  }
  const { route, params } = found;

  if (route.public) {
    clearSession();
    resetSelection();
    clearAllDrafts();
    sessionReady = false;
  } else {
    const ready = await ensureSession();
    if (revision !== renderRevision) return;
    if (!ready) {
      resetLocalState();
      const redirect = encodeURIComponent(location.hash.replace(/^#/, ""));
      location.hash = `/login?redirect=${redirect}`;
      return;
    }
  }
  renderChrome();

  if (!canAccess(route, getUser())) {
    mount(root, errorBox("无权限：仅管理员可访问"), h("a", { href: "#/", class: "btn btn-primary" }, "返回首页"));
    return;
  }

  // 每次渲染用新容器：上一个视图未完成的异步回调写入的是已脱离文档的旧容器，互不干扰
  const view = h("div", { class: "view" });
  mount(root, view);
  view.appendChild(loading());
  try {
    const mod = await route.load();
    if (revision !== renderRevision) return;
    view.replaceChildren();
    const dispose = await mod.render(view, { params, query, navigate, onLogin: handleLogin });
    if (revision === renderRevision) {
      cleanup = dispose;
      announceView(view);
    } else if (typeof dispose === "function") dispose();
  } catch (err) {
    mount(view, errorBox(err));
  }
}

// 单页切换后同步标题；视图自身没有接管焦点时（焦点落在 body 或已被移除的旧节点上），
// 把焦点移到主区域，读屏软件从新页面开头朗读，键盘用户不必再从顶栏 Tab 过来。
const APP_TITLE = document.title;
let firstRender = true;
function announceView(view) {
  const heading = view.querySelector("h1, h2");
  document.title = heading ? `${heading.textContent} · ${APP_TITLE}` : APP_TITLE;
  const active = document.activeElement;
  if (!firstRender && (!active || active === document.body || !active.isConnected)) focusMain({ preventScroll: true });
  firstRender = false;
}

// main 只在被程序聚焦时临时可聚焦：常驻 tabindex 会让点击空白处把焦点吸到 main，
// 进而触发组件（如标签下拉）的 focusout 关闭逻辑。
function focusMain(options) {
  root.setAttribute("tabindex", "-1");
  root.addEventListener("blur", () => root.removeAttribute("tabindex"), { once: true });
  root.focus(options);
}

document.querySelector(".skip-link")?.addEventListener("click", (event) => {
  event.preventDefault(); // href="#app" 只为语义，不能改动 hash 路由
  focusMain();
});

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
