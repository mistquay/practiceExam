# 题库练习与考试系统（practiceExam）

一句话：面向**内网离线环境**的安规题库练习与考试系统——题库一次导入成带版本的库，按「人员类别 × 专业」模板组卷，答案不下发、服务端判分、锁卷留痕，成绩与错题按用户持久化。

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

以下都是资料里没有、目前按推荐默认填的**占位/口径**，拍板前不能当正式规则用；改动会牵动 `fixtures/paper_templates.yaml`、组卷算法与验收断言。

1. **必答范围**：暂定必答 = **19 道标黄题**（配电安规·通用部分），其余只作 `is_protected` 标签。**119 道保命题汇总里的其余题是否也必考？**（方案 §12-5 推荐：仅 19 道必答，其余可加权抽题。）
2. **专业开放集合**：暂定只开放「**配电安规通用 + 配电带电**」2 个专业，其余 **20 个停用**（`enabled=false`，数据保留）。请确认「一般是」是否就等于只开这两个。
3. **「网络信息安全除外」**：暂按**口径 B = 剔除题目**，落成模板级可枚举的 566 题 `exclude_question_ids` 集合；**A（该专业人员另考）/ C（该类人员不参加）未采纳**。A 与 B 互斥，必须拍板单一口径并签字。
4. **第一版试卷的全部数字都是占位**：**100 题 / 总分 100 / 及格 60 / 时长 60 分钟 / 单选 40% 多选 30% 判断 30%**。资料里**没有任何试卷定义文件**，这些数字需发文方给出（方案 §12-10，P3 硬前提）。
5. **卷面分值规则**：题库原始分值为单选 1 / 多选 2 / 判断 0.5，按 40/30/30 抽 100 题合计 **115 分**，与总分 100 冲突；现统一**每题 1 分占位**（模板 `type_score` 三种题型均 1.0，题库 `score` 只留档、不参与组卷判分），见 `app/rules/scoring.py`。请确认最终采用原始分值还是每题 1 分，以及总分口径。
6. **领导人员 / 专业管理人员**：暂**不纳入**规则与模板（数据先保留，领导目录无专业自选子目录，需专属模板）。请确认是否纳入、对应哪张卷。
7. **简答题**：暂**排除**出自动判分卷（652 行 / 191 题只标 `qtype=简答`）。**是否纳入人工阅卷**？若纳入，需人工阅卷队列 + 复核 + 成绩更正审计，二选一写死。
8. **人员类别判定**：判定依据（岗位清单 / 三种现场角色名单、文号）、审批人、生效期与申诉更正留痕，暂定**由管理员录入授权记录**（必填 `basis_doc` + `approver` + `effective_from`），不是考生自选字段。请确认责任归属与审批人。

另：旧 localStorage 错题因旧 `id` 不稳定不做自动迁移，需一次性告知用户导出/清零；练考边界、成绩单口径等详见 `REFACTOR_PLAN.md` §12。

---

## 6. 验收命令与已知状态

```bash
/d/services/mamba_envs/ame/python.exe -m pytest tests/ -q
```

（若 `%TEMP%\pytest-of-<user>` 无权限，追加 `-p no:cacheprovider --basetemp <临时目录>`；导入相关的 dry-run 断言还可用 `-m etl import --source 2026-11-12 --version 2026-11 --dry-run` 连跑两次比对报告。）

已知状态：

- **已补齐真实浏览器交互验收**：使用内置浏览器与独立 Chrome、真实 Uvicorn 和隔离数据库，实际跑通登录、人员/专业/试卷选择、练习反馈、错题/记录同步、100 题开考、刷新保留作答与倒计时、交卷、成绩历史，以及管理员授权保存和 CSV 导出；桌面与 390×844 移动端布局已检查。浏览器发现的静态资源 404、重复 Date 头、SQLite 跨线程 500 均已修复并补回归。见 [浏览器验收报告与截图](docs/acceptance/BROWSER_ACCEPTANCE.md)。这不等于已建立完整无人值守浏览器套件，目标内网与 Docker 部署仍需环境验收。
- 模板状态为 `draft`，第 5 节 8 项未拍板前**不应开正式考试**。
- **GUI 线程与 Telegram 通道是两个不同的目标/会话**：本次文件只落在当前 GUI 线程的工作目录 `D:\project\practiceExam`，不会自动出现在 Telegram 通道里，需要在对应目标上另行同步或查看。
- 本次收尾只改了 `README.md`、`requirements.txt`、`requirements-etl.txt`（仅顶部注释）、`Dockerfile`、`docker-compose.yml`、`.gitignore`（仅追加），**未运行任何写库事务、未导入、未迁移、未升级、未回滚、未删除任何文件、未 git commit**。
