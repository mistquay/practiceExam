# 浏览器补充验收（2026-11-12）

## 范围与环境

- 使用 `D:\services\mamba_envs\ame\python.exe`（Python 3.14.4）启动真实 Uvicorn，监听临时验收地址 `http://127.0.0.1:8792`。
- 数据库由 `sqlite3.backup()` 安全复制到项目内 `data/browser-check/`；测试账号、授权、考试记录均为隔离数据，不作为正式人员或成绩。
- 先在 Alma 侧栏内置浏览器实际操作登录、练习和记录；它的原生确认框自动处理卡住后，切换到 PinchTab 驱动的独立 Chrome，完成考试、管理员页面和移动端验收。没有跳过或伪造确认结果。
- 测试账号密码随机生成，未写入本报告；验收结束清理凭据、数据库副本、浏览器工具与临时配置。

## 浏览器发现并修复的三个问题

| 问题 | 浏览器证据 | 修复 | 回归 |
|---|---|---|---|
| 首页只有标题，CSS/JS 未加载 | `/` 为 200；`/static/style.css` 和 `/static/app.js` 均为 404 | `app/main.py` 在根挂载前明确挂载 `/static` | `tests/test_static.py` 验证首页引用资源与 ESM 子模块的 200、MIME 和 API 路由优先级 |
| 点击开考后 `Invalid time value` | 真实 Uvicorn 将标准 Date 头与应用自写 Date 拼成重复日期，前端 `toISOString()` 抛错 | 应用不重复设置 Date，保留 Server-Now；前端优先有效 body.server_now、Server-Now，最后安全解析 Date，不使用客户端时间兜底 | `tests/test_time_headers.py` 含真实 Uvicorn 与 Node ESM 回归 |
| 重载后 API 随机 500 | Uvicorn 日志明确报 SQLite 连接在依赖与路由线程间切换触发 `ProgrammingError` | 请求级连接设 `check_same_thread=False`，仍每请求单独连接并关闭，不引入共享全局连接；保留 WAL/写事务锁/有限重试 | `tests/test_uvicorn.py`，12 客户端并发，52 轮登录及查询，共 312 请求，无 500 |

## 实际交互结果

### 考生

1. 用浏览器工号/密码表单登录，显示当前有效的「重点人员」授权、专业、生效期和依据；人员类别不提供考生修改入口。
2. 选择配电带电、练习，进入 20 题顺序练习。
3. 在第一题实际选择 B，页面即时显示错误、参考答案 A 与依据。
4. 点击练习记录，显示新增 1 条；点击错题本，同一题错误次数为 1。不是 API 返回 200 就算通过，以上均读取了实际渲染 DOM。
5. 从页面进入正式考试并接受确认，实际渲染 100 张题卡与服务端倒计时。
6. 作答两题后刷新：刷新前 `00:54:45`、2 个已选项；刷新后 `00:54:44`、仍为 2 个已选项，没有重置计时或丢失本机草稿。
7. 实际交卷后显示 **2/100、未通过、错题 98 道**，所有题目输入已锁定；考试历史显示同一条已交卷记录。
8. 修复后再从首页全新开考（非仅续考），显示 **100 题、01:00:00**，再次交卷并能在历史中查看。

### 浏览器网络与脚本证据

修复后独立 Chrome 对该考试实例实际响应核验：

```json
{
  "http": 200,
  "questionCount": 100,
  "required": 19,
  "forbiddenFields": [],
  "date": "Thu, 12 Nov 2026 07:00:45 GMT",
  "serverNow": "2026-11-12T07:00:45+00:00"
}
```

`forbiddenFields` 扫描 `answer/rawAnswer/answer_key/is_correct/正确答案`，均零命中。该页面资源 `externalResources=[]`、`failedResources=[]`，Chrome uncaught errors 为 **No errors**。这验证的是当前真实浏览器路径，并不等于所有页面、所有浏览器都没有错误。

### 管理员

- 从考生退出后，通过真实登录表单切换管理员。
- 实际打开用户列表、授权、题库版本、试卷模板、场次与成绩导出、人工阅卷六个管理子页面，无白屏。
- 授权页按工号查询考生，选择类别/专业、填写生效日、依据与审批人，确认提交；页面显示「授权已保存」，历史中保留旧授权并截断到前一天。
- 题库版本页显示新库 **11,450** 与 legacy **1,295**；模板页显示两类人员模板；场次页显示隔离验收场次；简答题队列为空且显示明确提示。
- 实际点击「导出全部成绩 CSV」：请求 `/api/reports/export?format=csv` 返回 **200**，浏览器收到 **560 字节**、`text/csv; charset=utf-8` 的非空 Blob。

### 移动端与布局

- 独立 Chrome 视口 **390×844**，管理场次页 `documentElement.scrollWidth=390`、`innerWidth=390`，没有整页水平溢出；宽表格在本身容器内滚动（容器宽 337、内容宽 620）。
- 同一视口全新开考仍显示 100 张题卡，页面宽度为 390。
- 另检查桌面 **1280×800** 考试页面。证据图片：
  - [桌面考试页](exam-desktop.png)
  - [移动端考试页](exam-mobile.png)
  - [移动端管理员场次页](admin-mobile.png)

## 全套自动化回归

最终全套测试 **79 passed，2 warnings，77.97 秒**，退出码 0。警告为 anyio/pytest 与 Starlette TestClient 的依赖弃用提示，无测试失败。包含原有 ETL、API、100 个 seed 的必答题/锁卷、50 路并发交卷与迁移测试，以及新增静态资源、服务端时间头和真实 Uvicorn 多客户端并发回归。原始输出保存在 [test-results.txt](test-results.txt)。

命令：

```bash
PYTHONDONTWRITEBYTECODE=1 /d/services/mamba_envs/ame/python.exe -X utf8 -m pytest tests/ -q -p no:cacheprovider --basetemp data/browser-check/pytest
```

## 边界与仍需确认

- 本次补齐真实浏览器验收，不将先前 API 冒烟冒充页面验收。
- 管理员 CSV 批量导入、密码重置、发布切换、人工阅卷非空队列、正式内网部署与 Docker 构建没有全部再做手工点击；对应已存在的自动测试不能替代目标部署环境验收。
- 模板题量、分值、及格线、时长和「网络信息安全除外」等仍是 README 第 5 节列出的待确认口径；本次能运行不代表已获批准组织正式考试。
- 浏览器验收是本次实跑，不宣称已经建立可无人值守重放的完整浏览器测试套件。新增可重复回归包括静态资源、日期与真实 Uvicorn 并发测试。
