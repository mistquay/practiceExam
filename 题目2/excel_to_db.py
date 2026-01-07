import pandas as pd
import json
import re
import sqlite3
import glob
import os
import string

def normalize_question_type(qtype: str) -> str:
    """
    把各种题型名称归一化（可按你的数据库/练习工具需要调整）
    """
    if qtype is None:
        return ""
    t = str(qtype).strip()

    # 兼容常见写法
    mapping = {
        "单选": "单选题",
        "单选题": "单选题",
        "单项选择题": "单选题",
        "多选": "多选题",
        "多选题": "多选题",
        "多项选择题": "多选题",
        "判断": "判断题",
        "判断题": "判断题",
        "是非题": "判断题",
        "填空": "填空题",
        "填空题": "填空题",
        "简答": "简答题",
        "简答题": "简答题",
    }
    return mapping.get(t, t)

def parse_options(option_str, question_type):
    """
    将各种选项格式转换为 JSON 字符串：
    - 旧格式：A-内容|B-内容 / A.内容|B.内容 / A、内容|B、内容
    - 新格式：0.2$;$0.5$;$1.0$;$1.5。   （没有A/B，按顺序自动生成）
    """
    qtype = normalize_question_type(question_type)

    # 1) 处理空值：判断题为空则自动补全
    if pd.isna(option_str) or str(option_str).strip() == "":
        if qtype == "判断题":
            return json.dumps({"A": "正确", "B": "错误"}, ensure_ascii=False)
        return json.dumps({}, ensure_ascii=False)

    s = str(option_str).strip()

    # 2) 先统一换行
    s = s.replace("\n", " ").replace("\r", " ")

    # 3) 判断它是不是 “$;$” 新格式
    #    注意：有些文件可能会混用，所以只要包含 "$;$" 就按新格式优先解析
    if "$;$" in s:
        parts = [p.strip() for p in s.split("$;$") if p.strip()]
        # 去掉末尾常见全角句号/分号（很多导出会在最后一个选项带“。”）
        cleaned = []
        for p in parts:
            p = re.sub(r"[。；;]+$", "", p).strip()
            cleaned.append(p)

        options = {}
        letters = list(string.ascii_uppercase)
        for i, text in enumerate(cleaned):
            if i < len(letters):
                options[letters[i]] = text
            else:
                # 极端情况：选项超过26个
                options[f"OPT{i+1}"] = text

        return json.dumps(options, ensure_ascii=False)

    # 4) 否则按旧格式解析（兼容 | 分隔 或者 Excel导出用换行）
    #    兼容有些文件用 “；” 或 “;” 分隔，但注意别误伤题干中有分号的情况
    #    这里仍以 | 为主；如果没有 | 而有明显 A. / B.，也能解析
    s2 = s.replace('\n', '|')
    items = re.split(r'\|', s2)

    options = {}
    for item in items:
        item = item.strip()
        if not item:
            continue

        # 支持 A-xxx, A.xxx, A、xxx
        match = re.match(r'^([A-Z])\s*[-.、]\s*(.*)', item)
        if match:
            key = match.group(1)
            val = match.group(2).strip()
            val = re.sub(r"[。；;]+$", "", val).strip()
            options[key] = val
        else:
            # 容错：A内容
            if len(item) > 1 and item[0].isalpha() and item[0].isupper():
                val = item[1:].strip(" .、-")
                val = re.sub(r"[。；;]+$", "", val).strip()
                options[item[0]] = val

    return json.dumps(options, ensure_ascii=False)

def pick_category(df: pd.DataFrame, file_name: str) -> pd.Series:
    """
    分类字段统一输出为 category：
    - 优先使用旧格式的 '一级纲要'
    - 其次尝试新格式的 '依据 出处'
    - 都没有就用文件名
    """
    if "一级纲要" in df.columns:
        cat = df["一级纲要"].fillna("").astype(str).str.strip()
        # 如果某些行是空的，用文件名补齐
        cat = cat.replace("", os.path.splitext(file_name)[0])
        return cat

    # 新模板列名里有空格：'依据 出处'
    for c in ["依据 出处", "依据", "出处"]:
        if c in df.columns:
            cat = df[c].fillna("").astype(str).str.strip()
            cat = cat.replace("", os.path.splitext(file_name)[0])
            return cat

    return pd.Series([os.path.splitext(file_name)[0]] * len(df))

def read_excel_with_auto_header(path: str, preview_rows: int = 10) -> pd.DataFrame:
    """
    自动识别表头行：
    - 新模板：第1行是标题，第2行才是表头
    - 旧模板：通常第1行就是表头
    """
    tmp = pd.read_excel(path, header=None, dtype=str, nrows=preview_rows)

    # 用这些字段作为“表头特征”，命中一个就很可能是表头行
    header_signals = {
        "试题正文", "试题选项", "试题答案", "试题编号", "题型",
        "题干", "选项", "答案", "试题编码"
    }

    header_row = None
    for i in range(len(tmp)):
        row_values = tmp.iloc[i].astype(str).fillna("").str.strip().tolist()
        row_set = set(v for v in row_values if v and v != "nan")
        if row_set & header_signals:
            header_row = i
            break

    # 如果没找到，就退回默认 header=0
    if header_row is None:
        header_row = 0

    df = pd.read_excel(path, header=header_row, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def process_excel_files():
    files = [f for f in (glob.glob("*.xlsx") + glob.glob("*.xls")) if not os.path.basename(f).startswith('~$')]
    all_data = []

    print(f"找到 {len(files)} 个Excel文件...")

    for f in files:
        try:
            print(f"正在读取: {f}")
            df = read_excel_with_auto_header(f)
            df.columns = [str(c).strip() for c in df.columns]

            # 给每个df计算 category（兼容两套格式）
            df["category"] = pick_category(df, f)

            all_data.append(df)
        except Exception as e:
            print(f"读取失败 {f}: {e}")

    if not all_data:
        print("未找到Excel文件，请检查路径。")
        return

    full_df = pd.concat(all_data, ignore_index=True)

    # 兼容两套表头的字段映射：
    # 旧格式字段：题干/选项/答案/题目依据/试题编码...
    # 新格式字段：试题正文/试题选项/试题答案/答案解析/试题编号...
    rename_map = {
        # 旧格式
        "题型": "type",
        "题干": "question",
        "选项": "raw_options",
        "答案": "answer",
        "题目依据": "explanation",
        "试题分数": "score",
        "试题编码": "codeId",
        "备注": "remarks",
        "说明": "Description",
        "判断题解析": "analysis",

        # 新格式
        "题型": "type",          # 新格式也有题型
        "试题正文": "question",
        "试题选项": "raw_options",
        "试题答案": "answer",
        "答案解析": "analysis",
        "答题分数": "score",
        "试题编号": "codeId",
        "备注": "remarks",

        # 新格式其他可选字段（如果你想存更多信息，也可以映射进去）
        "难度": "difficulty",
        "适用等级": "level",
        "答题时间": "timeLimit",
        "出题人": "author",
    }

    full_df = full_df.rename(columns=rename_map)

    # 题型归一化
    if "type" in full_df.columns:
        full_df["type"] = full_df["type"].fillna("").astype(str).apply(normalize_question_type)

    # 丢掉空题干
    if "question" in full_df.columns:
        full_df = full_df.dropna(subset=["question"])
        full_df["question"] = full_df["question"].fillna("").astype(str).str.strip()
        full_df = full_df[full_df["question"] != ""]

    # 答案清洗：转大写、去空格
    if "answer" in full_df.columns:
        full_df["answer"] = full_df["answer"].fillna("").astype(str).str.upper().str.strip()
        # 一些导出可能是 “B。” 或 “B,”，顺手去掉常见结尾标点
        full_df["answer"] = full_df["answer"].str.replace(r"[。；;，,]+$", "", regex=True).str.strip()

    # 选项解析
    if "raw_options" not in full_df.columns:
        full_df["raw_options"] = ""

    full_df["options"] = full_df.apply(
        lambda row: parse_options(row.get("raw_options"), row.get("type")),
        axis=1
    )

    # explanation 兼容：如果旧字段 explanation 不存在，但新字段里你想用 “依据 出处” 也可补进去
    if "explanation" not in full_df.columns:
        full_df["explanation"] = ""

    # 最终需要的列（保持你的练习工具复用）
    final_cols = [
        "category", "type", "question", "options", "answer",
        "explanation", "analysis", "score", "codeId", "remarks", "Description"
    ]

    # 如果 Description 不存在就补空，确保列齐全（避免你的工具依赖列名时出错）
    for c in final_cols:
        if c not in full_df.columns:
            full_df[c] = ""

    result_df = full_df[final_cols].copy()

    # 自增 id（只用于导入后定位，不当 SQLite 主键）
    result_df.insert(0, "id", range(1, len(result_df) + 1))

    # 输出检查文件
    result_df.to_csv("cleaned_questions.csv", index=False, encoding="utf-8-sig")

    # 写入 SQLite
    db_path = "questions.db"
    conn = sqlite3.connect(db_path)
    result_df.to_sql("questions", conn, if_exists="replace", index=False)
    conn.close()

    print(f"处理完成！\n共导入 {len(result_df)} 道题目。\n数据库已生成: {db_path}")

if __name__ == "__main__":
    process_excel_files()
