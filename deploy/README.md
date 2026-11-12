# 线上部署（直接拉镜像）

整个 `deploy/` 目录是自包含的部署包，拷到服务器就能用。

```bash
cd deploy
cp .env.example .env
# 编辑 .env，把 EXAM_ADMIN_PASSWORD 换成自己的强密码
docker compose up -d
```

浏览器打开 `http://服务器IP:8888/`，用 `.env` 里的管理员账号登录。

## 目录里有什么

| 文件 | 作用 |
|---|---|
| `docker-compose.yml` | 直接拉 `cxsz16888/practiceexam:latest`，不本地构建 |
| `.env.example` | 管理员账号与会话配置模板 |
| `data/exam.db` | **题库本体**，唯一的题目来源；`data/` 目录不要漏带 |

镜像里只有程序和静态资源，**一道题都没有**。空 `data/` 启动会得到 0 道题的站点，首页会提示「当前没有已发布的题库版本」。也不要指望在容器里跑 ETL —— 镜像没装 pandas / openpyxl / xlrd，`etl/` 也没打进去。

## 当前题库

`2026-11` 版（active，本次导入的那一批），去重后 **11,450 道**：单选 4,591 / 判断 3,812 / 多选 2,856 / 简答 191。可自动判分 11,259 道，标黄必答 19 道。只开放「配电安规通用」「配电带电」2 个专业，其余 20 个停用。历史 `legacy` 题库已从运行库移除，原库快照在 `archive/`。

## 上线前注意

- 两张试卷模板状态仍是 `draft`，**能练习、不能开正式考试**；管理员需在「管理」里激活模板并建场次。
- `./data` 会持续写入 `-wal` / `-shm`、成绩、导入报告和备份，别删、别只备份单个 `.db` 文件。按主 README 的 `VACUUM INTO` 做一致性备份。
- 要固定版本，把 compose 里 `image` 的 tag 换成已发布的 commit sha（见文件顶部注释）。
- 完全无外网的内网机器：在联网机 `docker pull` 后 `docker save` / `docker load` 分发，改用项目根的 `docker-compose.yml`（本地 build）或给这个 compose 加 `image: practice-exam:本地tag`。
