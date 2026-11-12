# 题库练习与考试系统（practiceExam）

一句话：面向**内网离线环境**的安规题库练习与考试系统——登录即可练习全部已发布题库；首页无需人员授权即可自选人员类型与专业进行自主模拟；管理场次保留模板、名单、时间与次数规则，考试接口不下发答案、服务端判分、锁卷留痕，成绩与错题按用户持久化。

> 本文给 dong 看：只讲**启动 / 导入 / 发版迁移 / 备份 / 线上部署**五件事、数据事实、待拍板事项与验收状态。运行库 `data/exam.db` 现只保留 `2026-11` 版题库；历史资料与含 legacy 的原库快照已移至 `archive/`，不进公开仓库。

---

## 1. 架构

| 层 | 实现 |
|---|---|
| 后端 | **FastAPI** + Uvicorn，`app/` 分层（routers / services / rules / db）；考试接口不下发答案，判分在服务端 |
| 数据库 | **SQLite + WAL**（`PRAGMA journal_mode=WAL` / `synchronous=NORMAL` / `busy_timeout`），单库 `data/exam.db`；显式 SQL 迁移 `migrations/*.sql` 由 `app.db.migrate` 执行，**禁用 `to_sql(replace)` 与删表迁移** |
| 前端 | **原生 ESM**（`static/` 下 `api/ state/ views/ components/` + 本地 `style.css`），无 npm 构建链、无 CDN 外链，**断外网可用** |
| ETL | 离线 CLI `etl/`：按文件头嗅探 `.xls`（147 OOXML / 102 OLE2）→ 规范化 → 去重 → 报告 → 入库；pandas / openpyxl / xlrd **只在 ETL 环境**，不进生产镜像 |
| 部署 | 单容器 Compose，镜像只装 `requirements.txt`，挂载 `./data:/app/data`；容器端口 **8000**，宿主机 **8888**；数据库路径由 `EXAM_DB_PATH` 指定 |

宿主机上的一切 Python 操作**只用 mamba 的 ame 环境**；容器内用镜像自带的 Python 3.14（`python:3.14-slim`），二者互不替代。资料目录 `2026-11-12/` 只读，导入报告、备份、数据库都在 `data/`。

---

## 2. 环境（硬性要求）

- 解释器：**`/d/services/mamba_envs/ame/python.exe`**（Windows 写法 `D:\services\mamba_envs\ame\python.exe`），**Python 3.14.4**。
- **不要用系统默认 `python`，也不要在仓库外建 venv**。下文所有命令都用这个解释器的绝对路径 + `-m` 形式。
- 安装依赖（一次装齐运行时 + 离线 ETL / 测试依赖）：

```bash
/d/services/mamba_envs/ame/python.exe -m pip install --only-binary=:all: -r requirements.txt -r requirements-etl.txt
```

Windows `cmd` / PowerShell 下等价写法：

```bat
D:\services\mamba_envs\ame\python.exe -m pip install --only-binary=:all: -r requirements.txt -r requirements-etl.txt
```

> Python 3.14 请保留 `--only-binary=:all:`（只取预编译 wheel，避免源码编译）。完全离线的机器：先在联网机用同一个 ame 解释器 `-m pip download --only-binary=:all: -r requirements.txt -r requirements-etl.txt -d wheels/`，把 `wheels/` 拷进内网后 `-m pip install --no-index --find-links wheels/ -r requirements.txt -r requirements-etl.txt`；也可直接分发已构建好的整包镜像（见 `Dockerfile` 注释）。生产镜像**只装 `requirements.txt`**。

---

## 3. 四件事（全部用 ame 解释器）

### 3.1 启动

```bash
/d/services/mamba_envs/ame/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- 本机直连：<http://127.0.0.1:8000/>。
- Compose 启动时**容器内仍是 8000，宿主机映射到 8888**：<http://127.0.0.1:8888/>。
- 默认库为 `data/exam.db`（`EXAM_DB_PATH` 可覆盖，容器里设为 `/app/data/exam.db`）。空库启动会创建初始管理员，生产环境务必用 `EXAM_ADMIN_NO` / `EXAM_ADMIN_PASSWORD` / `EXAM_ADMIN_NAME` 覆盖。

### 3.1.1 自助注册与基础验证码

- 登录页「注册账号」可自助创建普通 `examinee`，默认激活，无需管理员审批或人员授权即可练习、自由组卷；注册成功后引导登录。管理员 CSV 批量导入仍保留，接口/表头 `employee_no` 不变。
- 新用户名为 3–32 位 ASCII 字母/数字/下划线/短横线，以字母或数字开头，不接受空白，统一存为小写。自助账号登录忽略大小写；既有导入账号仍优先精确匹配。注册以不区分 ASCII 大小写的方式检查全部已有账号（含管理员和停用账号），重名返回 409，绝不覆盖。密码至少 8 位、含字母和数字、不含空白，UTF-8 合计最多 72 字节，沿用 bcrypt 哈希；注册不接受角色、激活状态或授权字段，并在事务内写审计。
- `GET /api/auth/captcha` 直接返回本地标准库生成的光栅 PNG，响应头 `X-Captcha-ID` 仅是随机挑战标识；`Cache-Control: no-store`。换码传 `?replace=<旧挑战ID>`，使同客户端旧挑战失效。`POST /api/auth/register` 接收 `employee_no/password/captcha_id/captcha`，成功 201；验证码错误、过期、重复使用均须换一张。无公开解答/调试接口，无新增依赖或外部服务，amd64/arm64 无图像库 wheel 要求。
- 验证码为四位数字与轻量干扰线，约 3 分钟有效；服务端只保存带进程私钥的 HMAC 哈希，带锁原子消费，提交成功或失败均消费。内存最多 2048 个挑战、4096 个限流桶，应用每分钟定期清理，申请/消费/限流时也清理过期项，达到上限返回 429。每客户端每 60 秒最多申请 30 张、提交 10 次，429 提供 `Retry-After`，不改变已有登录接口或登录限流语义。同一 NAT 下共享额度。
- **这是基础防自动注册，不防 OCR 或分布式攻击。当前仅适配单 worker**；重启使挑战和限流失效，多 worker/多副本需共享存储与原子消费。更强防刷应配网关。客户端地址使用 ASGI 对端，不直接读取 `X-Forwarded-For`；直连建议 Uvicorn `--no-proxy-headers`，反向代理部署只在 `--forwarded-allow-ips` 中配置真实受信代理地址，禁止通配信任，否则可绕过 IP 限流。

### 3.2 导入题库

只解析、出报告（**不写库**；报告落在 `data/import_report_2026-11.md` / `.json`，两次运行应逐字节一致）：

```bash
/d/services/mamba_envs/ame/python.exe -m etl import --source 2026-11-12 --version 2026-11 --dry-run
```

确认报告后正式入库（`--load` 会写 `data/exam.db`，与 `--dry-run` 互斥）：

```bash
/d/services/mamba_envs/ame/python.exe -m etl import --source 2026-11-12 --version 2026-11 --load
```

历史旧库已归档到 `archive/questions.db`，运行库 `data/exam.db` 中**已移除 `legacy` 版本**，现在只有 `2026-11` 一个题库版本。若需重新导入旧库做对账（一般不需要，会重新写入 legacy）：

```bash
/d/services/mamba_envs/ame/python.exe -m etl import --version legacy --source archive/questions.db --dry-run
/d/services/mamba_envs/ame/python.exe -m etl import --version legacy --source archive/questions.db --load
```

可选参数：`--verify-rar`（解压 RAR 到临时目录比对 MD5，仅校验、不作数据源）、`--db <路径>`、`--report-dir <目录>`。ETL 需要 `requirements-etl.txt` 里的 openpyxl / xlrd，生产镜像不带。

### 3.3 发版 / 迁移

```bash
/d/services/mamba_envs/ame/python.exe -m app.db.migrate status    --db data/exam.db
/d/services/mamba_envs/ame/python.exe -m app.db.migrate upgrade   --db data/exam.db
/d/services/mamba_envs/ame/python.exe -m app.db.migrate downgrade --db data/exam.db
```

即 `status | upgrade | downgrade --db data/exam.db`，可选 `--to 0001` / `--to 0000`。

> **上线硬规矩：先在副本上「正向 → 回滚 → 正向」各跑一遍、确认 0 次 `DROP` / `to_sql(replace)`，再对 `data/exam.db` 执行。** 副本用下面 3.4 的 `VACUUM INTO` 生成，绝不用复制 `.db` 文件的方式。`upgrade` 会在每个迁移前用 `VACUUM INTO` 留一致性快照，`downgrade` 用 `sqlite3` backup API 整页回写，回滚后旧数据仍可查。演练命令（对副本，不碰正式库）：

```bash
/d/services/mamba_envs/ame/python.exe -m app.db.migrate upgrade   --db data/backups/exam.rehearsal.db
/d/services/mamba_envs/ame/python.exe -m app.db.migrate downgrade --db data/backups/exam.rehearsal.db
/d/services/mamba_envs/ame/python.exe -m app.db.migrate upgrade   --db data/backups/exam.rehearsal.db
```

### 3.4 备份（WAL 安全）

**不要直接复制 `.db` 文件**：WAL 模式下 `-wal` 里可能还有未 checkpoint 的数据，只拷主库文件会丢成绩。两种安全做法（目标文件不存在时用 `VACUUM INTO`，可覆写用 `backup()`）：

```bash
/d/services/mamba_envs/ame/python.exe -c "import sqlite3; c=sqlite3.connect('data/exam.db'); c.execute('VACUUM INTO ?', ('data/backups/exam-backup.db',)); c.close()"
```

```bash
/d/services/mamba_envs/ame/python.exe -c "import sqlite3; s=sqlite3.connect('data/exam.db'); d=sqlite3.connect('data/backups/exam-backup.db'); s.backup(d); d.close(); s.close()"
```

`VACUUM INTO` 的目标文件必须事先不存在；`backup()` 会覆写目标。备份须含 `audit_logs` 与卷面快照，建议每日备份 + 每月恢复演练，升级/回滚前先备份。

### 3.5 线上部署（直接拉镜像）

`deploy/` 是自包含部署包：`docker-compose.yml` 直接拉 Docker Hub 的 `cxsz16888/practiceexam:latest`，不本地构建、不跑 ETL；`deploy/data/exam.db` 是题库本体（镜像里不含任何题目）。

```bash
cd deploy
cp .env.example .env      # 改掉 EXAM_ADMIN_PASSWORD
docker compose up -d      # 宿主机 8888 → 容器 8000
```

把整个 `deploy/` 目录拷到服务器即可，别漏 `data/`。详见 [deploy/README.md](deploy/README.md)。归档目录 `archive/` 与资料目录 `2026-11-12/` 均不进公开仓库。

---

## 练习与考试权限、筛选接口（当前行为）

- **练习 / 浏览题库：只需登录**，可选全部 `active` 题库及其中所有未退役题目，包括保命题、必答题、简答题及考试池题目。不检查 `person_authorizations` 或专业是否启用。`person_category` 默认全部，可自由选任一人员类别，并与标签、来源、题型等取交集；旧 `specialty_code` 参数兼容接收但不限制练习。
- **考试无需人员授权**：首页自主模拟可自由选择人员类型（不限本人授权类别）与题库真实专业、标签、来源范围，直接填写单选/多选/判断/简答数量（总计 1~200），不依赖管理员模板、不绑定管理场次，不受名单、时间窗或次数审批限制。自主测试总分固定 100，以分为单位均分并依卷序分配余数；时长默认 60 分钟，可填 5~240。简答交卷后对照参考答案由本人自评，待自评期间不展示最终成绩，空白未答计零；不是可信自动评分或正式考试。旧模板自主 API 保持兼容。管理场次仍要求 active 模板、开放题库、已启用专业，并保留场次名单、时间窗和重考次数限制，但不查人员授权。服务端计时、判分、锁卷快照、答案保护及按用户隔离继续有效。练习可返回参考答案，但不写 `attempts`。
- 首页上方直接筛选练习；下方「自主模拟考试」无需授权即可自选开考，管理场次另行折叠展示。标签为真正下拉多选：勾选保持展开，支持清空、取消恢复打开前选择、完成、Esc、外部点击及键盘焦点流转。账号文案统一为「用户名」，登录 API 和 CSV 导入表头继续使用 `employee_no`，无需改列名或迁移账号。
- `GET /api/practice/filters?bank_id=1` 返回 `tags`（`code/name`）、`sources`（`id/rel_path/sheet/question_count`）和当前组合的 `total`。来源按完整路径及工作表区分，不只显示文件名；来源题数按 stable_id 去重。此接口不返回题干或答案。
- `GET /api/practice/questions` 返回 `{questions, total, returned}`：`total` 是**全部匹配题数**，`returned` 是实际返回题数（受 `count=1..200` 限制）。前端展示匹配数及空状态；空结果为 `questions=[] / total=0 / returned=0`。
- 两接口共享筛选参数：可重复 `tags=protected`（`is_protected=1`）、`tags=required`（`is_required=1`）；颜色分组 / 数据标记使用 facets 返回的真实 `color_group:值` / `data_issue:值`。多个标签取交集；重复 `source_file_ids=<id>` 取来源并集，再与 `person_category`、标签、`qtype`、`source=all|unpracticed|wrong` 取交集。`order=sequence|random` 控制出题顺序；同一道题来自多个文件仍只出一次。首页提供单一来源下拉，API 及练习链接支持多来源。
- 非法标签、跨题库或不存在的来源、非法题型 / 顺序 / 范围返回 400（类型错误返回 422），未登录返回 401，非 active 题库返回 404。所有筛选值使用 SQL 参数；无逐题查询。

例如：`/api/practice/questions?bank_id=1&tags=protected&tags=required&source_file_ids=10&qtype=判断题&order=random&count=20`。来源 ID 应从 facets 获取，不要硬编码。

### 已部署旧库升级与来源完整性

`0002_question_sources.sql` 仅新增多对多来源关系及索引，先补已有版本的代表来源，**不改题目或正式组卷规则**。启动引导从 `fixtures/question_sources_2026-11.json` 幂等补全旧 2026-11 库的全部来源关系，并记录审计；仅在题库 code、源路径、sheet、文件 MD5 与现库匹配且 stable_id 已存在时写入。索引含 **30,969 个来源关系 / 251 个来源工作表**，不含题干、答案或解析，来自原资料 30,989 行的只读扫描。生产不需要安装 ETL 依赖，也不需要带原资料或重导题库；镜像需包含新增 migration、来源索引及新代码。后续 ETL 每次按所有原始记录同步关系，不再只保存代表行。

`0003_self_service_exams.sql` 为 attempts 增加自主/管理模式、及格线和时长快照；历史记录默认为 managed。自主模拟的详情、历史与导出明确标为非正式成绩，并排除在管理场次及格率与通过人数统计之外。`0004_free_self_tests.sql` 新增 papers.selection_json，冻结本次过滤、题型数量、人员/专业、时长、固定 100 分与分配规则。首页改走 `/api/self-tests/inventory`、`/start`，不再依赖模板，领导及管理人员有题即可开考。系统内部仅保留一个不限制类别的外键兼容记录（每库一条、disabled、不在模板 UI 展示）。简答参考答案只允许在 `/api/self-tests/{id}/review` 本人已交卷后查看，`/self-rating` 确认逐题自评分，相同评分重试幂等、已确认不可更改。`/api/exams/*` 全局答案保护不变。

**本轮开发不修改运行库或部署库**；升级迁移与补全已在隔离副本验证。部署新代码后启动才会对部署环境自己的库执行迁移，按 3.3 先备份并演练。未知历史题库只能保留现存版本可追溯的来源；需用新版 ETL 重导才可补全它们，不会凭文件名推测关系。

---

## 4. 数据事实（`bank_version=2026-11`，来源 `2026-11-12/`）

| 项 | 数量 / 口径 |
|---|---|
| 源文件 | **251** 个（249 个 `.xls` + 1 PDF + 1 RAR；`.xls` 按文件头分流 147 OOXML / 102 OLE2，PDF 与 RAR 0 题入库） |
| 原始题目行 | **30,989** 行（含副本重复；995 空行、5 个辅助 sheet 均 0 题入库） |
| 唯一题 | **11,450** 道：`stable_id = sha1(规范化题干 + 规范化选项)`，**不含答案与解析**（答案勘误不换 ID，历史成绩不断链） |
| 与方案数字对账 | 方案「题干 + 选项原文 + 答案」口径为 **11,456**，比本导入多 **6** 道：5 个同题同选项仅答案不同的少数派答案变体 + 1 个选项文本不同但解析后相同的合并；逐条原因见 `data/import_report_2026-11.md` |
| 标黄必考 | **19** 道（`is_required=1`，全属配电安规·通用部分；「标黄必」汇总文件 119 行 / 去重 108 题） |
| 保命题 | **1,578** 行（130 文件，去重 596 题），是标签而非试卷类型；另 11 道备注为空的编辑遗留黄题不进必答 |
| 简答题 | 原始 **652** 行 → 去重 **191** 题，**不入自动判分卷**（`auto_gradable=0`；答案长文本、多为 `\` 占位分值） |
| 网络信息安全 | 口径 B 的排除集合为 **566** 题（一级纲要 ∈ 通信安规 / 信息安规，及标黄必汇总末尾 29 行不着色保命题） |
| legacy（已移除） | 旧 `questions.db` 1,325 行 → 1,295 题，原为 `archived`；现已从运行库清除，原库快照 `archive/exam-full-2026-11-12.db`，源文件 `archive/questions.db` |

去重后按题型：单选 4,591 / 多选 2,856 / 判断 3,812 / 简答 191；可自动判分 11,259 题。以上去重分项**不是**原始行数（12,331 / 10,112 / 7,894 / 652）。

---

## 5. 待 dong 确认（共 8 项）

以下仅针对管理场次正式规则，不再约束自由组卷自主测试（自主测试全人员、真实专业范围、100 分等权、简答本人自评）。这些是资料里没有、目前按推荐默认填的**占位/口径**，拍板前不能当正式规则用；改动会牵动 `fixtures/paper_templates.yaml`、组卷算法与验收断言。

1. **必答范围**：暂定必答 = **19 道标黄题**（配电安规·通用部分），其余只作 `is_protected` 标签。**119 道保命题汇总里的其余题是否也必考？**（方案 §12-5 推荐：仅 19 道必答，其余可加权抽题。）
2. **专业开放集合**：暂定只开放「**配电安规通用 + 配电带电**」2 个专业，其余 **20 个停用**（`enabled=false`，数据保留）。请确认「一般是」是否就等于只开这两个。
3. **「网络信息安全除外」**：暂按**口径 B = 剔除题目**，落成模板级可枚举的 566 题 `exclude_question_ids` 集合；**A（该专业人员另考）/ C（该类人员不参加）未采纳**。A 与 B 互斥，必须拍板单一口径并签字。
4. **第一版试卷的全部数字都是占位**：**100 题 / 总分 100 / 及格 60 / 时长 60 分钟 / 单选 40% 多选 30% 判断 30%**。资料里**没有任何试卷定义文件**，这些数字需发文方给出（方案 §12-10，P3 硬前提）。
5. **卷面分值规则**：题库原始分值为单选 1 / 多选 2 / 判断 0.5，按 40/30/30 抽 100 题合计 **115 分**，与总分 100 冲突；现统一**每题 1 分占位**（模板 `type_score` 三种题型均 1.0，题库 `score` 只留档、不参与组卷判分），见 `app/rules/scoring.py`。请确认最终采用原始分值还是每题 1 分，以及总分口径。
6. **领导人员 / 专业管理人员**：暂**不纳入**规则与模板（数据先保留，领导目录无专业自选子目录，需专属模板）。请确认是否纳入、对应哪张卷。
7. **简答题**：暂**排除**出自动判分卷（652 行 / 191 题只标 `qtype=简答`）。**是否纳入人工阅卷**？若纳入，需人工阅卷队列 + 复核 + 成绩更正审计，二选一写死。
8. **人员类别维度**：仅作为每次练习筛选或考试时用户自主选择的维度，不是账号的固定身份或权限。人员授权管理 UI（标签、用户行按钮、表单、固定人员类别列）已移除，旧 `#/admin/auth` 正常回落至用户管理，不再加载旧页面。`person_authorizations` 历史数据、审计与考试快照不删除，旧 API 与 `authorization` 响应字段仅作 deprecated 历史兼容，无新界面依赖；不新增数据库迁移，不改变迁移回滚。账号登录、启停用及管理员角色鉴权保持不变。

另：旧 localStorage 错题因旧 `id` 不稳定不做自动迁移，需一次性告知用户导出/清零；练考边界、成绩单口径等详见 `REFACTOR_PLAN.md` §12。

---

## 6. 验收命令与已知状态

```bash
/d/services/mamba_envs/ame/python.exe -m pytest tests/ -q
```

（若 `%TEMP%\pytest-of-<user>` 无权限，追加 `-p no:cacheprovider --basetemp <临时目录>`；导入相关的 dry-run 断言还可用 `-m etl import --source 2026-11-12 --version 2026-11 --dry-run` 连跑两次比对报告。）

本轮自助注册验收（工作区实现，未提交/推送/构建/发布）：

- 最终执行 `/d/services/mamba_envs/ame/python.exe -m pytest tests/ -q --basetemp=.tmp-registration-final`：**156 passed**（约 201 秒，含新增 36 项；仅有 anyio/Starlette 两条既有依赖弃用警告）。`node --test tests/test_self_test_counts.mjs`：**5 passed**；修改的三个前端 JS 均通过 `node --check`，`git diff --check` 通过。测试临时库、脚本、日志和截图已清理，隔离测试服务已停止。
- 新增 `tests/test_registration.py`，以进程内可控随机源与单调时钟 fixture 测试 PNG 签名/CRC/光栅数据与无解答泄漏、注册/登录/无授权练习及自由组卷、导入与管理员重名保护、非法字段/弱密码/72 字节边界、敏感属性注入、错误/过期/刷新/重复挑战、429 与 `Retry-After`、伪造转发头、容量上限/周期清理和并发。没有修改或删除旧测试断言。
- 内置真实浏览器 + 仅绑定 `127.0.0.1` 的隔离 Uvicorn，数据库经 SQLite backup API 复制，答案只在该测试进程内替换生成函数：实际跑通错误验证码→自动换码、点击换码、确认密码不一致、注册成功→预填用户名的登录页→登录→首页；确认普通角色且授权为 null。亮暗色、320/390px 手机与1280px桌面检查通过，控件无横向溢出；原生用户名 pattern 亦实际校验。一次侧栏导航等待超时，后续实际表单操作和首页 DOM/API 检查均成功，不以导航返回值冒充完成。
- 两个数据库 `data/exam.db`、`deploy/data/exam.db` 的开始 SHA-256 均为 `12bb102e0a366f441bea91928d5301131081a810b1b85c066ced5b07cc54c3e7`；结束复核均仍为 `12bb102e0a366f441bea91928d5301131081a810b1b85c066ced5b07cc54c3e7`。未改原资料、运行库、部署库、迁移或依赖；没有验证/构建新的多架构镜像。

本轮人员授权 UI 清理验收（仅本地工作区，未提交/推送/发镜像）：

- 全套 ame Python 测试 **158 passed**（201 秒，含原注册/PNG 验证码、无授权练习/自由组卷、legacy API、角色权限和迁移回滚测试；新增 2 项静态发布资源/deprecated API 回归）；Node 数量分配测试 **5 passed**。全部静态 JS 与新增浏览器测试通过 `node --check`，`git diff --check` 通过。
- `tests/browser_admin_ui.mjs` 导出真实浏览器检查函数 `checkAdminUi()`，在已登录管理员的本机隔离服务页面执行。1280px 桌面和 390px 手机实际验证用户页、旧 `#/admin/auth`（含 `?user=1`）与未知 tab 均正常显示用户管理：授权入口/按钮/表单及旧导入提示为零，无固定人员类别列，不加载旧授权模块或 API。错题练习不再使用历史授权类别。普通账号访问管理页被拒绝，管理 API 返回 403；未登录访问旧 hash 转登录。
- 测试均使用工作区内临时数据库副本，运行库、部署库及历史数据库/备份的前后 SHA-256 清单一致；运行库与部署库仍均为 `12bb102e0a366f441bea91928d5301131081a810b1b85c066ced5b07cc54c3e7`。无数据删除、迁移或部署改动；既有注册与 PNG 验证码未提交改动保留。

已知状态：

- **已补齐真实浏览器交互验收**：使用内置浏览器与独立 Chrome、真实 Uvicorn 和隔离数据库，实际跑通登录、人员/专业/试卷选择、练习反馈、错题/记录同步、100 题开考、刷新保留作答与倒计时、交卷、成绩历史，以及当时的管理员授权保存和 CSV 导出（人员授权管理 UI 现已移除，该项仅为历史验收记录）；桌面与 390×844 移动端布局已检查。浏览器发现的静态资源 404、重复 Date 头、SQLite 跨线程 500 均已修复并补回归。见 [浏览器验收报告与截图](docs/acceptance/BROWSER_ACCEPTANCE.md)。这不等于已建立完整无人值守浏览器套件，目标内网与 Docker 部署仍需环境验收。
- 模板状态为 `draft`，第 5 节 8 项未拍板前**不应开正式考试**。
- **GUI 线程与 Telegram 通道是两个不同的目标/会话**：本次文件只落在当前 GUI 线程的工作目录 `D:\project\practiceExam`，不会自动出现在 Telegram 通道里，需要在对应目标上另行同步或查看。
- 历史部署包整理记录（非本轮练习功能修改）：当时只改了 `README.md`、`requirements.txt`、`requirements-etl.txt`（仅顶部注释）、`Dockerfile`、`docker-compose.yml`、`.gitignore`（仅追加），未改运行库。本轮练习开放与来源筛选的实现及隔离副本验证见 [回归验收记录](docs/acceptance/PRACTICE_FILTERS.md)。
