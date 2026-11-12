// 统一 fetch 封装：相对路径、Cookie 会话、统一错误语义（401/403/404/409/422/5xx/网络）。
// 所有 API 模块只通过这里发请求，错误统一抛 ApiError，由视图决定如何提示。

export class ApiError extends Error {
  constructor(status, message, detail) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

let unauthorizedHandler = null;

/** 注册 401 回调（由 app.js 注入：清会话并跳登录页）。 */
export function onUnauthorized(fn) {
  unauthorizedHandler = fn;
}

function buildUrl(path, query) {
  if (!query) return path;
  const params = new URLSearchParams();
  Object.entries(query).forEach(([k, v]) => {
    if (Array.isArray(v)) v.forEach((item) => params.append(k, String(item)));
    else if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  });
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

async function parseBody(resp) {
  if (resp.status === 204) return null;
  const type = resp.headers.get("Content-Type") || "";
  if (type.includes("application/json")) {
    try {
      return await resp.json();
    } catch {
      return null;
    }
  }
  return null;
}

function detailText(data) {
  const detail = data && data.detail;
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI 422：[{loc, msg, type}]
    return detail
      .map((d) => (d && d.msg ? `${(d.loc || []).slice(1).join(".")} ${d.msg}`.trim() : String(d)))
      .join("；");
  }
  if (typeof detail === "object" && detail.message) return detail.message;
  return JSON.stringify(detail);
}

function messageFor(status, data, method, path) {
  const detail = detailText(data);
  switch (status) {
    case 401:
      return detail && detail !== "Not authenticated" ? detail : "登录已失效，请重新登录";
    case 403:
      return detail ? `无权限：${detail}` : "无权限执行该操作";
    case 404:
      return !detail || detail === "Not Found"
        ? `接口未就绪或资源不存在：${method} ${path}`
        : detail;
    case 405:
    case 501:
      return `接口尚未实现：${method} ${path}`;
    case 409:
      return detail || "操作冲突（可能已提交过）";
    case 422:
      return `参数校验失败：${detail || "请检查输入"}`;
    default:
      if (status >= 500) return `服务器错误（${status}）${detail ? "：" + detail : ""}`;
      return detail || `请求失败（${status}）`;
  }
}

async function send(method, path, { query, body, form } = {}) {
  const url = buildUrl(path, query);
  const init = {
    method,
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  };
  if (form) {
    init.body = form; // FormData：浏览器自动带 boundary
  } else if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }

  let resp;
  try {
    resp = await fetch(url, init);
  } catch {
    throw new ApiError(0, "网络连接失败，请检查网络或后端服务是否启动");
  }
  return resp;
}

async function handle(resp, method, path) {
  const data = await parseBody(resp);
  if (!resp.ok) {
    if (resp.status === 401 && unauthorizedHandler && !path.startsWith("/api/auth/login")) {
      unauthorizedHandler();
    }
    throw new ApiError(resp.status, messageFor(resp.status, data, method, path), data);
  }
  return data;
}

/** 普通 JSON 请求，返回响应体。 */
export async function request(method, path, options) {
  const resp = await send(method, path, options);
  return handle(resp, method, path);
}

function validServerDate(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? new Date(timestamp).toISOString() : null;
}

/** 同 request，另返回有效服务端时间；不使用本地时钟兜底。 */
export async function requestWithMeta(method, path, options) {
  const resp = await send(method, path, options);
  const data = await handle(resp, method, path);
  const serverDate = validServerDate(data && data.server_now)
    || validServerDate(resp.headers.get("Server-Now"))
    || validServerDate(resp.headers.get("Date"));
  if (!serverDate) {
    throw new ApiError(resp.status, "服务端未返回有效时间，无法对时", data);
  }
  return { data, serverDate };
}

/** 下载文件（导出）。失败时按 JSON 错误体解析并抛 ApiError。 */
export async function download(path, query, fallbackName) {
  const resp = await send("GET", path, { query });
  if (!resp.ok) await handle(resp, "GET", path);
  const blob = await resp.blob();
  const disposition = resp.headers.get("Content-Disposition") || "";
  const match = /filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/i.exec(disposition);
  const name = match ? decodeURIComponent(match[1] || match[2]) : fallbackName;
  const href = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
}

/** 列表接口兼容：{items:[...]} / 直接数组 / {<key>:[...]}。 */
export function listOf(data, key) {
  if (Array.isArray(data)) return data;
  if (!data) return [];
  if (Array.isArray(data.items)) return data.items;
  if (key && Array.isArray(data[key])) return data[key];
  return [];
}

export const get = (path, query) => request("GET", path, { query });
export const post = (path, body) => request("POST", path, { body });
export const patch = (path, body) => request("PATCH", path, { body });
export const del = (path) => request("DELETE", path);
