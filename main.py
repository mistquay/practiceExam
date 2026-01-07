from pathlib import Path
import json
import random
import sqlite3
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "questions.db"
STATIC_DIR = BASE_DIR / "static"


app = FastAPI(title="Practice Exam App")


def get_db_connection() -> sqlite3.Connection:
    """
    获取 SQLite 连接。每次请求使用独立连接，避免并发问题。
    """
    if not DB_PATH.exists():
        raise HTTPException(status_code=500, detail="题库数据库不存在，请先生成 questions.db")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def normalize_answer(answer: Optional[str]) -> str:
    """
    统一答案格式：转大写并去掉所有空白字符，便于前端对比。
    """
    if answer is None:
        return ""
    # 去掉所有空格和换行
    return "".join(str(answer).upper().split())


def parse_score(raw_score: Any, type_str: Optional[str]) -> float:
    """
    按规则解析每题分值：
    - 若 score 有效数字，则使用该分值；
    - 若为简答/问答题且分值缺失或无效，则按 10 分处理；
    - 其他题型若分值缺失或无效，则按 1 分处理。
    """
    base_score: Optional[float] = None

    if raw_score is not None:
        s = str(raw_score).strip()
        if s:
            try:
                base_score = float(s)
            except ValueError:
                base_score = None

    type_str = (type_str or "").strip()
    is_short_answer = ("简答" in type_str) or ("问答" in type_str)

    if is_short_answer:
        # 简答题默认 10 分，除非题库中已有明确分值
        if base_score is None or base_score <= 0:
            return 10.0
        return base_score

    # 非简答题：没有设置分值时按 1 分计
    if base_score is None or base_score <= 0:
        return 1.0
    return base_score


def row_to_question(row: sqlite3.Row) -> Dict[str, Any]:
    """
    将数据库行转换为前端所需的题目结构。
    """
    # 解析选项 JSON
    options_raw = row["options"] if "options" in row.keys() else None
    try:
        options = json.loads(options_raw) if options_raw else {}
    except json.JSONDecodeError:
        options = {}

    q_type = row["type"] if "type" in row.keys() else ""
    score = parse_score(row["score"] if "score" in row.keys() else None, q_type)

    return {
        "id": row["id"],
        "category": row["category"],
        "source_file": row["source_file"] if "source_file" in row.keys() else None,
        "type": q_type,
        "question": row["question"],
        "options": options,
        "answer": normalize_answer(row["answer"]),
        # 原始答案文本也一并返回，便于展示
        "rawAnswer": row["answer"],
        "explanation": row["explanation"],
        "analysis": row["analysis"],
        "score": score,
        "codeId": row["codeId"],
    }


def detect_question_kind(q: Dict[str, Any]) -> str:
    """
    按前端相同规则识别题目类型：
    - 判断题
    - 多选题
    - 简答/问答题
    - 单选题（默认）
    """
    type_str = str(q.get("type") or "").strip()
    options = q.get("options") or {}
    has_options = bool(options)

    if "判断" in type_str:
        return "truefalse"
    if ("多选" in type_str) or ("多项" in type_str):
        return "multiple"
    if ("简答" in type_str) or ("问答" in type_str):
        return "short"
    if not has_options:
        return "short"
    if "单选" in type_str:
        return "single"
    return "single"


def build_mock_exam(
    questions: List[Dict[str, Any]],
    total_score: int = 100,
    short_count: int = 2,
) -> List[Dict[str, Any]]:
    """
    构造一套模拟考试试卷，力求整套总分严格等于 total_score：
    - 题库范围：所有题目（不按类别或题型筛选）
    - 简答题：固定从题库中选出 short_count 道
    - 其余题型（单选、多选、判断）：从剩余题目中按分值组合，使总分恰好为 total_score
    - 题目顺序：单选 -> 多选 -> 判断 -> 简答

    若题库分值配置无法组合出总分为 total_score 的试卷，将抛出 ValueError。
    """
    if not questions:
        raise ValueError("题库为空，无法生成模拟试卷。")

    # 预处理题目：识别类型并将分值离散化为整数，保证与前端计分规则一致
    enriched: List[Dict[str, Any]] = []
    for q in questions:
        q_copy = dict(q)
        kind = detect_question_kind(q_copy)
        score_val = q_copy.get("score") or 0
        score_int = int(round(float(score_val)))
        if score_int <= 0:
            score_int = 10 if kind == "short" else 1
        q_copy["_kind"] = kind
        q_copy["_score_int"] = score_int
        enriched.append(q_copy)

    shorts = [q for q in enriched if q["_kind"] == "short"]
    others = [q for q in enriched if q["_kind"] != "short"]

    if len(shorts) < short_count:
        raise ValueError("题库中简答题数量不足，无法构造包含固定 2 道简答题的模拟试卷。")

    random.shuffle(shorts)
    random.shuffle(others)

    # 对非简答题构建一次子集和 DP：dp[sum] = 使用 others 中若干题目凑出 sum 的索引列表
    max_sum = total_score
    dp: Dict[int, List[int]] = {0: []}
    for idx, q in enumerate(others):
        v = q["_score_int"]
        # 逆序遍历当前已有和，避免覆盖
        current_items = list(dp.items())
        for s, subset in current_items:
            new_sum = s + v
            if new_sum > max_sum:
                continue
            if new_sum not in dp:
                dp[new_sum] = subset + [idx]

    # 尝试所有简答题两两组合，寻找可以与非简答题一起凑满 total_score 的方案
    n_short = len(shorts)
    for i in range(n_short):
        for j in range(i + 1, n_short):
            short_score = shorts[i]["_score_int"] + shorts[j]["_score_int"]
            if short_score > total_score:
                continue
            rest = total_score - short_score
            if rest not in dp:
                continue

            # 找到一组满足总分的试卷
            other_indices = dp[rest]
            selected_others = [others[k] for k in other_indices]

            singles = [q for q in selected_others if q["_kind"] == "single"]
            multiples = [q for q in selected_others if q["_kind"] == "multiple"]
            truefalses = [q for q in selected_others if q["_kind"] == "truefalse"]

            ordered_questions: List[Dict[str, Any]] = []
            ordered_questions.extend(singles)
            ordered_questions.extend(multiples)
            ordered_questions.extend(truefalses)
            ordered_questions.append(shorts[i])
            ordered_questions.append(shorts[j])

            # 返回时去掉内部字段
            for q in ordered_questions:
                q.pop("_kind", None)
                q.pop("_score_int", None)
            return ordered_questions

    # 若无法找到任意组合恰好凑满 total_score，则抛出异常，由上层决定提示方式
    raise ValueError("当前题库中题目分值组合无法凑满 100 分，请检查题库分数配置。")


@app.get("/", response_class=FileResponse)
def index() -> FileResponse:
    """
    返回前端首页。
    """
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=500, detail="前端页面不存在，请检查 static/index.html 是否存在")
    return FileResponse(index_file)


@app.get("/api/categories")
def get_categories() -> Dict[str, List[str]]:
    """
    获取题库中的所有类别（一级纲要），用于前端选择。
    """
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            "SELECT DISTINCT category FROM questions "
            "WHERE category IS NOT NULL AND TRIM(category) != '' "
            "ORDER BY category"
        )
        categories = [row["category"] for row in cursor.fetchall()]
    finally:
        conn.close()

    return {"categories": categories}


@app.get("/api/source_files")
def get_source_files() -> Dict[str, List[str]]:
    """
    获取题库中的所有来源文件（source_file 字段），用于前端筛选。
    """
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            "SELECT DISTINCT source_file FROM questions "
            "WHERE source_file IS NOT NULL AND TRIM(source_file) != '' "
            "ORDER BY source_file"
        )
        source_files = [row["source_file"] for row in cursor.fetchall()]
    finally:
        conn.close()

    return {"source_files": source_files}


@app.get("/api/types")
def get_types() -> Dict[str, List[str]]:
    """
    获取题库中的所有题型（type 字段），用于前端筛选。
    """
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            "SELECT DISTINCT type FROM questions "
            "WHERE type IS NOT NULL AND TRIM(type) != '' "
            "ORDER BY type"
        )
        types = [row["type"] for row in cursor.fetchall()]
    finally:
        conn.close()

    return {"types": types}


@app.get("/api/questions")
def get_questions(
    mode: str = Query("practice", pattern="^(practice|exam|mock|sequence)$"),
    category: Optional[str] = Query(default=None, description="题目类别，多个类别用逗号分隔"),
    source_file: Optional[str] = Query(default=None, description="题目来源文件，多个来源用逗号分隔"),
    qtype: Optional[str] = Query(default=None, description="题目类型（type 字段），多个类型用逗号分隔"),
    count: int = Query(20, ge=1, le=5000, description="本次抽取的题目数量"),
) -> Dict[str, Any]:
    """
    获取题目：
    - mode:
      - practice：练习模式，按条件随机抽取若干题目
      - exam：普通考试模式，按条件随机抽取若干题目
      - mock：模拟考试模式，基于全库生成一套约 total_score 的试卷
      - sequence：顺序练习模式，按题号顺序返回题目
    - category: 类别名称（可用逗号分隔多个，mock 模式下忽略）
    - source_file: 来源文件（可用逗号分隔多个，mock 模式下忽略）
    - qtype: 题型（type 字段，可用逗号分隔多个，mock 模式下忽略）
    - count: 抽题数量（mock 模式下忽略）
    """
    conn = get_db_connection()
    try:
        if mode == "mock":
            # 模拟考试：基于整个题库构造一套约 100 分的试卷
            cursor = conn.execute(
                "SELECT id, category, source_file, type, question, options, answer, "
                "explanation, analysis, score, codeId "
                "FROM questions"
            )
            rows = cursor.fetchall()
            all_questions = [row_to_question(row) for row in rows]
            try:
                questions = build_mock_exam(all_questions, total_score=100, short_count=2)
            except ValueError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
        else:
            where_clauses: List[str] = []
            params: List[Any] = []

            if category:
                # 支持逗号分隔的多个类别
                cats = [c.strip() for c in category.split(",") if c.strip()]
                if cats:
                    placeholders = ",".join("?" for _ in cats)
                    where_clauses.append(f"category IN ({placeholders})")
                    params.extend(cats)

            if source_file:
                # 支持逗号分隔的多个来源文件
                source_files = [s.strip() for s in source_file.split(",") if s.strip()]
                if source_files:
                    placeholders = ",".join("?" for _ in source_files)
                    where_clauses.append(f"source_file IN ({placeholders})")
                    params.extend(source_files)

            if qtype:
                # 支持逗号分隔的多个题型
                types = [t.strip() for t in qtype.split(",") if t.strip()]
                if types:
                    placeholders = ",".join("?" for _ in types)
                    where_clauses.append(f"type IN ({placeholders})")
                    params.extend(types)

            base_sql = (
                "SELECT id, category, source_file, type, question, options, answer, "
                "explanation, analysis, score, codeId "
                "FROM questions"
            )

            if where_clauses:
                base_sql += " WHERE " + " AND ".join(where_clauses)

            # 按模式决定排序方式
            if mode == "sequence":
                sql = f"{base_sql} ORDER BY id ASC LIMIT ?"
            else:
                sql = f"{base_sql} ORDER BY RANDOM() LIMIT ?"
            params.append(count)

            cursor = conn.execute(sql, params)
            rows = cursor.fetchall()
            questions = [row_to_question(row) for row in rows]
    finally:
        conn.close()

    return {
        "mode": mode,
        "count": len(questions),
        "questions": questions,
    }


# 提供静态文件（前端资源）
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


if __name__ == "__main__":
    # 本地运行示例：python main.py
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
