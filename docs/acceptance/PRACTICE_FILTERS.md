# 自由练考、人员筛选、标签下拉与来源筛选验收

## 本轮：自由随机组卷与简答诚实自评

首页已移除自主模板选择，支持「按数量 / 按比例」两种自由组卷模式。按数量保留默认单选40、多选30、判断30、简答0，可编辑，总计1~200题。按比例填写总题量1~200整数及四型0~100整数百分比，比例和须精确100%；最大余数法换算整数题数，并列固定按单选、多选、判断、简答顺序，零比例始终0题，实时显示实际题数及精确总量。提供80单选/10判断/10简答的一键示例，两模式均可使用，不把“选择80”固定成业务规则。

切换不自动开考，不重置人员/专业/来源/标签或时长。首次进入比例模式、或数量已调整时，以当前有效数量的总量及最大余数法整数百分比初始化；比例有效时切回数量带回实际counts，可直接调整；无效比例不覆盖上次数量。数量未改时再次进入比例保留上次比例草稿。整数百分比是近似表达，极小题量转换成比例后再换算时，以页面显示的实际题数为准。开考仍仅提交既有`POST /api/self-tests/start`的counts与范围、时长，不提交比例或客户端分值。库存不足继续逐型禁用、明确提示，不跨型补题；固定100分及简答评分完全沿用服务端逻辑。

### 比例 UI 增补验证（本次，小范围前端）

- `node --test tests/test_self_test_counts.mjs`：**5 passed，0 failed**。包括100题80/0/10/10→80/0/10/10；3题25/25/25/25→1/1/1/0；比例和不为100、零/负/超界/小数总量、负/超界/小数/空比例拒绝；数量校验及转比例；遍历全部176,851种整数百分比组合，在1/3/100/200题下验证总数守恒、上下取整及零比例0题。
- 新增`tests/browser_self_test_modes.mjs`并接入原`tests/browser_free_self_tests.mjs`的三宽度循环；旧脚本数字输入选择器排除禁用的隐藏模式字段。可按下方既有隔离服务/CDP方式复跑完整浏览器流程。
- 本次实际浏览器使用Alma Preview，加载真实生产组件及CSS、独立临时静态服务，库存和开考API为隔离fixture，不连接运行数据库。320px、390px均实际执行上述浏览器模式用例并PASS，scrollWidth分别严格320/390，两模式控件无横向溢出；原生选择控件切换、整数实时显示、非法值禁用、切回数量直接编辑均通过。检查真实PNG确认比例控件/实际题数/库存提示可读。反复切换后开考请求数为0；用户点击开考并接受原生确认框后，捕获载荷仅含范围、counts、duration_min，无ratio或分值；三题实际提交1/1/1/0。
- 本次`tests/test_static.py`：**3 passed，2 warnings**（anyio/Starlette既有弃用警告）；前端及测试模块`node --check`、`git diff --check`通过，界面机械检测`[]`。后端无修改，未重跑上轮120项全量慢测试，也未把fixture验证表述为真实后端端到端开考/评分回归。
- 本次未转交、未另开会话、未commit/push，不修改两份运行库或原始资料；临时验证服务/页面、pytest副本及截图在验证后清理。

本次额外实测比例80/0/10/10、库存单选仅79题时显示“不足1题”并禁用开考；两份运行库`data/exam.db`与`deploy/data/exam.db`测试前后SHA-256一致。

### 沿用的范围与服务端规则

- 全部/任一有题人员类别均可开考，无需模板、授权、场次；与真实专业标签、练习同源的标签交集/来源并集筛选组合。专业不受specialties.enabled限制。库存按实际组合逐型实时显示，开考事务内重查；不足逐型明确报出，不换型补题。仅active版本、retired=0题，stable_id不重复，简答不受auto_gradable=0排除。
- 时长默认60分钟，允许5~240整数分钟；服务端截止时间、刷新、到期和重复交卷沿用现有保护。到期不接受迟到提交的作答；本机草稿不等同服务端已保存答案，刷新到期后按服务器已接收数据（当前无自动保存接口则为空）结束。
- 固定总分100。以整数10000个0.01分单位除以实际题数，余数依锁卷顺序给前几题各补0.01；3题为33.34/33.33/33.33，不使用题库原分。累加使用Decimal，客户端分值/总分/参考依据不参与评分。规则、范围、数量、人员/专业、时长冻结于papers.selection_json和题目快照。0004仅新增一个JSON列。
- 简答非空不自动判分，交卷后客观题先锁分，score/passed为null，显示“待自评，客观题X/该部分分值”。本人已交卷后逐题对照参考答案和自己的答案确认0~本题满分，最多两位小数。相同评分重试幂等；已确认不同分值409。空白未答明确0分无需自评。全部完成后才产生100分基准最终成绩，仍标本人自评/非正式，不是关键词自动评分或可信正式成绩。
- 自主review不能访问进行中、他人或managed卷；管理员不能借自主接口看他人的卷。原管理阅卷权限不放宽，自主待自评也不进入管理员待阅卷队列。历史和导出区分待自评/自评完成；导出保留旧“得分”列名，待自评留空，新增客观题得分/该部分满分/简答本人自评分。自主成绩始终不参与管理场次及格率/通过人数。
- 全局/api/exams/* forbidden_tokens保护原样保留。简答原文可能包含“参考答案”或answer字样，因此该原文和参考依据只走受控review，不回传到考试结果responses以触发全局阻断。旧模板自主API与管理场次仍兼容；内部每库一条disabled系统外键兼容记录，不按人员造永久模板、不作为管理模板开放。

### 本轮接口

- POST /api/self-tests/inventory：bank_id、person_category、specialty_code、tags数组、source_file_ids数组；返回counts、真实specialties/tags/sources，不含参考依据。
- POST /api/self-tests/start：上述范围加counts（题型中文全名到非负整数）、duration_min，不需要template_id。详情和交卷仍使用/api/exams/{id}及/submit。
- GET /api/self-tests/{id}/review：本人已交卷后返回简答参考答案/自己的答案/待评标记/满分。
- POST /api/self-tests/{id}/self-rating：stable_id、score；服务端验证归属、已交卷、卷内简答、分值边界和重复确认。

### 本轮实际验证

测试库、迁移备份、浏览器profile、日志和截图均位于仓库.tmp-free-tests/，源库通过只读连接和SQLite backup复制。仅使用/d/services/mamba_envs/ame/python.exe。

自动测试：最终命令 `PYTHONDONTWRITEBYTECODE=1 /d/services/mamba_envs/ame/python.exe -m pytest tests/ -q -p no:cacheprovider --basetemp=.tmp-free-tests/final-pass`，结果 **120 passed，2 warnings，149.26秒**，无失败/跳过；警告为anyio pytest CallSpec2与Starlette httpx弃用。已覆盖无模板全部人员类别、真实80/10/10分布、纯简答/混合/纯客观、三题分值和Decimal总和、自评零分/部分分/满分、非法数量/筛选/时长、库存不足/retired/active、快照、跨用户、进行中参考保护、重复交卷/评分、到期、导出与正式阅卷隔离。既有迁移全回滚/归档数据回查保留并更新到0004，另测0004降到0003再升级。

真实浏览器：Preview已测三宽度DOM，但截图和原生confirm处理超时；完整流程改用独立headless Chrome原生CDP，不使用个人profile、不stub confirm。新增tests/browser_free_self_tests.mjs复用原browser_cdp.mjs，实际PASS：1280px scrollWidth1270，320px/390px scrollWidth分别320/390，所有表单控件无横向溢出；领导/管理类别选择、80/10/10填充、库存不足禁用、标签菜单展开关闭；混合三题→刷新草稿→交卷→待自评→刷新→自评12.34→最终12.34/100→历史“自评完成（非正式）”。已捕获并检查三宽度真实PNG。旧tests/browser_self_service.mjs只更新模板选择段为新表单，手机标签菜单勾选/取消/清空/完成/Esc/外点/ArrowDown/Space/Tab焦点与44px触控覆盖全部保留并PASS，100题纯客观开考→交卷0分→非正式历史也PASS。

复跑浏览器使用下方旧示例的环境变量，将URL换为隔离服务实际端口（本轮8028），运行node tests/browser_free_self_tests.mjs和node tests/browser_self_service.mjs。必须先创建隔离库和独立Chrome；不可连运行库。

本轮收尾：标准 `git diff --check` 返回0；改动前端模块及浏览器脚本node语法检查通过。已停止本轮自建Uvicorn/Chrome，进程与8028监听复核为空；删除.tmp-free-tests/及仅本轮新增模块的pyc中间物，既有文件未删除。两份数据库开工/结束SHA256均为 `12bb102e0a366f441bea91928d5301131081a810b1b85c066ced5b07cc54c3e7`。保留此前未提交改动，未commit/push，未改原资料，未构建/部署镜像；Docker及目标内网部署未在本轮验证。


## 沿用的练习及管理行为

- 登录即可浏览/练习所有 active 题库的未退役题目，包含保命题、必答题、考试池和简答题；不依赖人员或专业授权。
- 练习人员类型默认「全部」，可自由选择任一类别；与真实标签交集、来源并集、题型、个人错题/未练习、顺序/随机组合。首页来源单选，API/练习链接可传多个来源。
- 标签使用真正下拉多选，而非常驻复选框或 details 替代。勾选保持展开；清空立即清除，取消恢复本次打开前的选择，完成关闭并返回触发器焦点；Esc 关闭并返回焦点，外部指针点击和 Tab 离开关闭但不抢回焦点。ArrowDown 从触发器进入首个选项，Space 切换原生复选框。
- 首页自主测试不要求人员授权，人员类型不限本人身份，题型数量与范围由用户直接填写、反复随机开考；不依赖模板。旧模板自主API只作兼容，不能当作正式规则。
- 领导人员/专业管理人员已可按所选类别真实题池自由开考，不再有模板门槛。
- 管理场次也不再查人员授权；仍要求 active 模板、active 题库、已启用且模板允许的专业，并保留场次开放状态、名单、时间窗、补考次数规则。
- 考试继续使用服务端截止时间、锁卷答案和逐题分值判分。人员类别、专业、时长和及格线随本次考试冻结；自主模式固定标为 mock/self_service，详情、历史和导出均标明非正式成绩。管理场次及格率、已判分数和通过人数排除自主模拟，但导出仍保留模拟明细。
- 账号可见文案保持「用户名」；API、数据库和导入 CSV 的 employee_no 保持兼容。练习可返回参考答案，不写 attempts；考试接口仍经过独立 DTO 白名单与答案保护。

## 接口

- `GET /api/practice/filters`：tags、sources、组合筛选 total；来源包含 id、rel_path、sheet、question_count。
- `GET /api/practice/questions`：questions、全部匹配 total、实际 returned；count 为 1..200。
- 两接口的 `person_category` 为空/不传表示全部，非空必须是有效人员类别。`specialty_code` 兼容接收但不限制练习。
- 重复 tags 取交集：protected / required / facets 返回的真实 color_group:值、data_issue:值。重复 source_file_ids 取来源并集，再与人员、标签、题型和个人范围相交；使用 EXISTS 避免多来源重复出题。
- `GET /api/exams/self-service/templates?bank_id=1&person_category=一线人员`：返回可用模拟模板、专业集合和草案提示。
- `POST /api/exams/start`：自主模拟请求包含 `self_service:true`、template_id、person_category、specialty_code；不能同时绑定 batch_id。客户端指定的 seed 不用于自主模拟重放。
- 未登录 401；非法人员类别、标签、来源、题型、顺序、范围和业务组合返回 400；参数类型/数量越界 422。未发布题库练习 404、考试 403；其他账号访问考试 404；重复交卷 409。
- `0003_self_service_exams.sql` 新增 exam_mode、pass_score_snapshot、duration_min_snapshot；旧记录默认为 managed。两份交付数据库不在开发验证中迁移。

## 上一轮模板自主模式验证（历史记录）

仅使用 `/d/services/mamba_envs/ame/python.exe`；SQLite backup 从只读源连接生成仓库内隔离副本。

```bash
PYTHONDONTWRITEBYTECODE=1 /d/services/mamba_envs/ame/python.exe -m pytest tests/ -q -p no:cacheprovider --basetemp=.tmp-self-service-check/final-tests
```

结果：**94 passed，2 warnings，112.38 秒，无失败或跳过**。警告来自 anyio 的 pytest CallSpec2 和 Starlette TestClient 的 httpx 弃用，不是业务失败。

覆盖包括：

1. 无授权登录、全部/各人员类别练习，人员 + 保命标签 + 来源交集与独立 SQL 结果一致；非法人员类别和注入字符串拒绝。
2. 无授权、无场次、draft 模板自主开考；服务端落库人员类别快照与卷内题目 person_scope 一致。重点/一线 × 通用/带电四组合开考及超时计分均有参数化回归。
3. 后续授权修改不限制自由选择或改变旧快照；模板阈值/时长修改不影响旧考试。全对按锁卷快照满分、全空零分；伪造 score、elapsed、deadline 不改变服务端判分，到期全对提交仍为 expired/0 分。
4. 考试详情、提交结果防答案泄漏；跨账号读取/提交 404，重复提交 409，历史按本人及所选类别持久化。
5. 自主模拟不受管理场次名单/时间/次数限制，但非法专业、人员与模板不匹配、混绑场次、无法组卷及归档题库均拒绝。
6. 自主全对 + 管理场次全错的混合导出保留两条明细，管理口径 graded_count=1、passed_count=0、pass_rate=0；只有自主成绩时管理口径为空/0。
7. 既有场次/时间/次数、权限隔离、快照、100 个 seed 的组卷、ETL 来源关系、并发和备份测试继续通过。迁移正向/回滚/正向精确断言更新到 0003，并继续检查回滚前数据归档。

原有来源索引与迁移成果保留，本轮没有重新生成索引或重导资料。源关系对账仍覆盖 30,989 原始行、30,969 个来源关系及 251 个来源工作表。

## 上一轮浏览器回归（历史记录）

Preview 已实测移动端控件，但原生确认框处理出现阻塞；最终完整验收改用**独立 headless Chrome + 原生 CDP WebSocket**，不连接个人浏览器资料。测试只连接 `127.0.0.1:8017` 隔离库服务及无人员授权账号。未用 stub 替代 confirm：CDP 捕获真实 Page.javascriptDialogOpening 后接受。

可复跑测试为 `tests/browser_self_service.mjs`，适配器 `tests/browser_cdp.mjs` 使用 Node >=22 内置 WebSocket，无需安装包。示例（先用只读源库 backup 创建仓库内隔离库、bootstrap、导入无授权验收账号，再启动隔离 Uvicorn）：

```bash
# 这里的目录全部是验证中间物，验收结束应停止进程并清理。
EXAM_DB_PATH="$PWD/.tmp-self-service-check/browser/exam.db" PYTHONDONTWRITEBYTECODE=1 \
  /d/services/mamba_envs/ame/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8017
# 在另一终端启动独立 Chrome（Windows/Git Bash 示例，不能使用个人 profile）：
'/c/Program Files/Google/Chrome/Application/chrome.exe' --headless=new \
  --remote-debugging-port=0 --user-data-dir="$PWD/.tmp-self-service-check/chrome" \
  --no-first-run --no-default-browser-check about:blank
# 从 profile/DevToolsActivePort 取端口，GET /json/list 获取 type=page 的 WebSocket URL。
EXAM_BROWSER_CDP='ws://127.0.0.1:<端口>/devtools/page/<id>' \
EXAM_BROWSER_URL=http://127.0.0.1:8017 EXAM_BROWSER_USER=free_browser \
EXAM_BROWSER_PASSWORD='<隔离账号密码>' node tests/browser_self_service.mjs
```

最终真实输出：

- **PASS 320px**：innerWidth=320、documentElement.scrollWidth=320；触发器高 44px，全部选项高 44px、宽 244px，三个操作按钮高 44px、宽 76px，无横向溢出。
- **PASS 390px**：innerWidth=390、scrollWidth=390；全部选项高 44px，操作按钮高 44px、宽约 99px，无横向溢出。
- 两个宽度均通过真实触摸事件勾选保持展开、取消恢复、清空/完成、Esc、外部点击、ArrowDown/Space、Tab 离开及焦点检查；所有真实颜色/数据标签全选后的长摘要也不溢出。精确断言 CSS 视口等于目标宽度，避免仿真失效后误测桌面宽度。
- **PASS 无授权首页选择一线人员/配电带电 → 原生确认 → 100 题开考 → 服务端交卷 0 分 → 非正式成绩 → 考试历史**。人员/标签/来源组合统计及空状态也已实测。

本轮真实点击暴露并修复：label 空白处点击时 focusout 微任务提前关闭菜单导致勾选丢失；以及后续 `.input` 样式覆盖触发器 44px 的问题。测试使用 CDP 指针/键盘事件，不以直接调用 onchange 分支替代这些交互。

## 上一轮交付边界（历史记录）

最终 `git diff --check`、全部改动前端模块和新增浏览器脚本的 `node --check` 通过；界面机械规则检测结果为 `[]`。两份数据库开始/结束 SHA-256 均为 `12bb102e0a366f441bea91928d5301131081a810b1b85c066ced5b07cc54c3e7`。已停止自建 Uvicorn/Chrome 进程并删除 `.tmp-self-service-check/` 与上轮残留 `.tmp-self-service-tests/`。

未 commit/push，未构建或发布镜像，未修改两个数据库及原始题目资料。开发代码完成不表示现网已部署；发版应先备份并在副本验证迁移。临时浏览器 profile、测试库、备份、日志和服务属于验证中间物，验收后清理；测试代码与本说明为保留交付。
