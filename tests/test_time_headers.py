"""真实 Uvicorn HTTP 头与前端 ESM 对时回归；不读取或写入业务数据库。"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def uvicorn_responses(tmp_path_factory):
    # 使用独立临时端口和数据库路径；关闭 lifespan，完全不执行 bootstrap。
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert port != 8792
    env = os.environ.copy()
    env["EXAM_DB_PATH"] = str(tmp_path_factory.mktemp("time-headers") / "unused.db")
    script = """
import sys
import uvicorn
from starlette.responses import JSONResponse
from starlette.routing import Route
from app.main import app

async def normal(request):
    return JSONResponse({"server_now": "2026-11-12T06:44:51+00:00"})

async def blocked(request):
    return JSONResponse({"answer": "must not be sent"})

app.router.routes.insert(0, Route("/api/exams/time-regression", normal))
app.router.routes.insert(0, Route("/api/exams/guard-regression", blocked))
uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), lifespan="off",
            log_level="critical", access_log=False)
"""
    process = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-c", script, str(port)],
        cwd=REPO_ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    def fetch(path):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return {
                "status": response.status,
                "headers": response.getheaders(),
                "body": response.read().decode("utf-8"),
            }
        finally:
            conn.close()

    try:
        deadline = time.monotonic() + 15
        while True:
            assert process.poll() is None, "测试 Uvicorn 启动失败"
            try:
                homepage = fetch("/")
                break
            except OSError:
                if time.monotonic() >= deadline:
                    pytest.fail("测试 Uvicorn 启动超时")
                time.sleep(0.05)
        yield {
            "static": homepage,
            "exam": fetch("/api/exams/time-regression"),
            "blocked": fetch("/api/exams/guard-regression"),
        }
        assert not Path(env["EXAM_DB_PATH"]).exists()
    finally:
        # 只终止本测试创建的进程，绝不操作已有服务。
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)


@pytest.mark.parametrize("kind,status", [("static", 200), ("exam", 200), ("blocked", 500)])
def test_real_uvicorn_has_single_date(uvicorn_responses, kind, status):
    response = uvicorn_responses[kind]
    assert response["status"] == status
    dates = [value for key, value in response["headers"] if key.lower() == "date"]
    assert len(dates) == 1, dates
    # 同时防止一条头内已被合并为两个日期（标准 HTTP 日期只含一个逗号）。
    assert dates[0].count(",") == 1, dates
    assert parsedate_to_datetime(dates[0]).tzinfo is not None
    server_now = [value for key, value in response["headers"] if key.lower() == "server-now"]
    assert len(server_now) == 1
    assert datetime.fromisoformat(server_now[0]).tzinfo is not None
    if kind == "blocked":
        assert json.loads(response["body"]) == {"detail": "考试接口响应含判分依据，已阻断下发"}


def test_frontend_server_time_fallbacks(uvicorn_responses):
    node = shutil.which("node")
    assert node, "日期回归必须实际运行 Node ESM 测试，不能跳过"
    script = r"""
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
const moduleUrl = (source) => `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const httpUrl = moduleUrl(readFileSync("static/api/http.js", "utf8"));
const { requestWithMeta, ApiError } = await import(httpUrl);
const examSource = readFileSync("static/api/exam.js", "utf8").replace('"./http.js"', JSON.stringify(httpUrl));
const { startExam, getAttempt } = await import(moduleUrl(examSource));
const { createCountdown } = await import(moduleUrl(readFileSync("static/components/countdown.js", "utf8")));
const live = JSON.parse(process.env.TIME_REGRESSION_RESPONSE);
const bodyNow = "2026-11-12T06:44:51+00:00";
const headerNow = "2026-11-12T06:45:51+00:00";
const date = "Thu, 12 Nov 2026 06:46:51 GMT";
const duplicate = "Thu, 12 Nov 2026 06:44:51 GMT, Thu, 12 Nov 2026 06:44:51 GMT";
const iso = (value) => new Date(value).toISOString();
let calls = 0;
function mock(data, headers, status = 200) {
  globalThis.fetch = async () => {
    calls++;
    return new Response(JSON.stringify(data), {
      status, headers: { "Content-Type": "application/json", ...headers },
    });
  };
}
const cases = [
  [bodyNow, headerNow, date, bodyNow],
  [bodyNow, headerNow, duplicate, bodyNow],
  [bodyNow, null, "bad date", bodyNow],
  [bodyNow, null, null, bodyNow],
  ["bad body", headerNow, duplicate, headerNow],
  [null, headerNow, "bad date", headerNow],
  [null, headerNow, null, headerNow],
  ["", headerNow, date, headerNow],
  ["bad body", "bad header", date, date],
  [null, null, date, date],
  [123, null, date, date],
  [{}, null, date, date],
];
for (const [body, header, dateHeader, expected] of cases) {
  const headers = {};
  if (header !== null) headers["Server-Now"] = header;
  if (dateHeader !== null) headers.Date = dateHeader;
  mock({ server_now: body }, headers);
  const result = await requestWithMeta("GET", "/test");
  assert.equal(result.serverDate, iso(expected));
  // 开考和重连都必须使用已校验的时间，不能再被坏 body 值覆盖。
  assert.equal((await startExam({ batchId: 1, templateId: 1 })).server_now, iso(expected));
  assert.equal((await getAttempt(1)).server_now, iso(expected));
}
for (const badHeaders of [{}, { Date: "bad date" }, { Date: duplicate },
                           { Date: duplicate, "Server-Now": "bad header" }]) {
  mock({ server_now: "invalid body" }, badHeaders);
  for (const run of [() => requestWithMeta("GET", "/test"),
                     () => startExam({}), () => getAttempt(1)]) {
    await assert.rejects(run, (error) => error instanceof ApiError
      && !(error instanceof RangeError) && /服务端未返回有效时间/.test(error.message));
  }
}
// 真实 Uvicorn 响应进入实际前端函数；再模拟旧服务/代理合并重复 Date。
for (const repeated of [false, true]) {
  const headers = Object.fromEntries(live.headers);
  if (repeated) headers.date += `, ${headers.date}`;
  mock(JSON.parse(live.body), headers);
  const result = await requestWithMeta("GET", "/api/exams/time-regression");
  assert.equal(result.serverDate, iso(bodyNow));
  const countdown = createCountdown({ onTick() {}, onExpire() {} });
  try {
    countdown.sync("2026-11-12T07:44:51+00:00", result.serverDate);
    assert.ok(countdown.remainingMs() > 3590000);
  } finally {
    countdown.stop();
  }
}
// 原有 HTTP 错误优先，不被时间校验掩盖。
mock({ detail: "Not authenticated" }, { Date: duplicate }, 401);
await assert.rejects(() => requestWithMeta("GET", "/api/exams/1"),
  (error) => error instanceof ApiError && error.status === 401);
console.log(`ESM server time regression passed (${calls} fetch calls)`);
"""
    env = os.environ.copy()
    env["TIME_REGRESSION_RESPONSE"] = json.dumps(uvicorn_responses["exam"])
    result = subprocess.run(
        [node, "--input-type=module", "-"], input=script, text=True, encoding="utf-8",
        capture_output=True, cwd=REPO_ROOT, env=env, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ESM server time regression passed (51 fetch calls)" in result.stdout
