"""自由随机组卷与本人交卷后自评；不改变管理场次阅卷权限。"""
from __future__ import annotations

import json
import secrets
from decimal import Decimal, InvalidOperation

from app.models import QTYPES, QTYPE_SHORT
from app.rules.paper import PaperDraft, read_snapshot, store_paper
from app.services import audit, exam
from app.services.db import write_tx
from app.services.errors import bad_request, conflict, not_found
from app.services.practice import active_bank_id
from app.services.practice_selection import selection, source_facets, tag_facets, specialty_facets
from app.services.util import add_minutes, json_dumps, now_iso, sha256_text, utcnow


def scope(conn, user, body):
    bank = active_bank_id(conn, body.bank_id)
    sql, params = selection(conn, user, bank=bank, person_category=body.person_category,
                            tags=body.tags, source_file_ids=body.source_file_ids,
                            specialty_codes=body.specialty_codes or ([body.specialty_code] if body.specialty_code else []),
                            scope_mode=body.scope_mode)
    return bank, sql, params


def specialties(conn, bank):
    return specialty_facets(conn, bank)


def inventory(conn, user, body):
    bank, sql, params = scope(conn, user, body)
    counts = {q: 0 for q in QTYPES}
    counts.update({r['qtype']: r['n'] for r in conn.execute(
        'SELECT qv.qtype, COUNT(*) AS n' + sql + ' GROUP BY qv.qtype', params)})
    return {'counts': counts, 'total': sum(counts.values()), 'tags': tag_facets(conn, bank),
            'sources': source_facets(conn, bank), 'specialties': specialties(conn, bank)}


def start(conn, user, body):
    counts = body.counts
    if set(counts) - set(QTYPES):
        raise bad_request('无效题型')
    n = sum(counts.values())
    if not 1 <= n <= 200:
        raise bad_request('总题数需为 1~200 的整数')
    moment = utcnow()
    at = moment.isoformat(timespec='seconds')
    seed = 'free:' + secrets.token_hex(16)
    with write_tx(conn):
        bank, sql, params = scope(conn, user, body)
        pool = [dict(r) for r in conn.execute('SELECT qv.*' + sql, params)]
        rng = secrets.SystemRandom()
        chosen = []
        shortages = []
        for qtype in QTYPES:
            need = counts.get(qtype, 0)
            candidates = [q for q in pool if q['qtype'] == qtype]
            if len(candidates) < need:
                shortages.append(f'{qtype}：需要 {need}，可用 {len(candidates)}，缺 {need-len(candidates)}')
            else:
                chosen.extend(rng.sample(candidates, need))
        if shortages:
            raise bad_request('题量不足；' + '；'.join(shortages))
        rng.shuffle(chosen)
        # 仅满足历史 NOT NULL 外键的系统兼容层，不提供规则、不限制类别、不对 UI 开放。
        code = f'__system_free_{bank}'
        conn.execute(
            "INSERT INTO paper_templates (code, version, name, paper_type, bank_version_id, "
            "question_count, total_score, pass_score, duration_min, status, created_at, specialty_rule, "
            "required_question_ids, exclude_question_ids, type_ratio, random_policy, short_answer_policy) "
            "SELECT ?, 1, '自由随机组卷', 'mock', ?, 0, 100, 60, 60, 'disabled', ?, '{}', "
            "'[]', '[]', '{}', 'system_random', 'self_review' "
            "WHERE NOT EXISTS (SELECT 1 FROM paper_templates WHERE code=?)", (code, bank, at, code))
        tid = conn.execute('SELECT id FROM paper_templates WHERE code=?', (code,)).fetchone()[0]
        config = {'bank_id': bank, 'person_category': body.person_category,
                  'specialty_code': body.specialty_code, 'tags': body.tags,
                  'specialty_codes': body.specialty_codes, 'scope_mode': body.scope_mode,
                  'source_file_ids': body.source_file_ids, 'counts': {q: counts.get(q, 0) for q in QTYPES},
                  'duration_min': body.duration_min, 'total_score': 100,
                  'score_rule': 'equal_cents_remainder_by_sequence', 'grading': 'objective_then_self_review'}
        specialty_snapshot = ';'.join(body.specialty_codes) or body.specialty_code
        draft = PaperDraft(tid, code, 1, bank, seed, body.person_category, specialty_snapshot)
        base, remainder = divmod(10000, n)
        for i, q in enumerate(chosen):
            draft.items.append({
                'seq': i+1, 'stable_id': q['stable_id'], 'question_version_id': q['id'],
                'qtype': q['qtype'], 'stem': q['stem'], 'options': json.loads(q['options'] or '{}'),
                'answer': q['answer'], 'answer_raw': q['answer_raw'], 'basis': q['basis'],
                'analysis': q['analysis'], 'score': (base + int(i < remainder)) / 100,
                'is_required': 0,
            })
        draft.question_count, draft.total_score = n, 100
        draft.snapshot_hash = sha256_text(json_dumps({'selection': config, 'items': draft.items, 'seed': seed}))
        paper_id, _ = store_paper(conn, draft, at=at)
        conn.execute('UPDATE papers SET selection_json=? WHERE id=?', (json_dumps(config), paper_id))
        cur = conn.execute(
            "INSERT INTO attempts (user_id, paper_id, person_category_snapshot, specialty_snapshot, "
            "started_at, deadline_at, exam_mode, pass_score_snapshot, duration_min_snapshot) "
            "VALUES (?, ?, ?, ?, ?, ?, 'self_service', 60, ?)",
            (user['id'], paper_id, body.person_category, specialty_snapshot, at,
             add_minutes(moment, body.duration_min), body.duration_min))
        aid = cur.lastrowid
        audit.log(conn, user['employee_no'], 'self_test.start', f'attempts:{aid}', at=at, after=config)
    return exam.view(conn, user, aid)


def _submitted(conn, user, attempt_id):
    row = exam._attempt(conn, user, attempt_id)
    if row['exam_mode'] != 'self_service':
        raise not_found('自主测试不存在')
    if row['status'] == 'in_progress' or not row['submitted_at']:
        raise conflict('交卷后才可查看参考答案或自评')
    return row


def review(conn, user, attempt_id):
    row = _submitted(conn, user, attempt_id)
    answers = {r['stable_id']: r for r in conn.execute(
        'SELECT * FROM attempt_answers WHERE attempt_id=?', (attempt_id,))}
    items = []
    for q in read_snapshot(conn, row['paper_id']):
        if q['qtype'] != QTYPE_SHORT:
            continue
        a = answers.get(q['stable_id'])
        items.append({'stable_id': q['stable_id'], 'seq': q['seq'], 'stem': q['stem'],
                      'reference_answer': q.get('answer_raw') or q.get('answer') or '题库未提供参考答案，请自行核对资料。',
                      'own_answer': a['user_answer'] if a else '', 'max_score': q['score'],
                      'earned': a['score'] if a else 0, 'pending': bool(a and a['graded_result'] == 'pending')})
    return {'items': items}


def rate(conn, user, attempt_id, stable_id, value):
    try:
        score = Decimal(str(value))
    except InvalidOperation:
        raise bad_request('自评分必须为有效数字')
    if not score.is_finite() or score != score.quantize(Decimal('0.01')):
        raise bad_request('自评分最多两位小数')
    with write_tx(conn):
        row = _submitted(conn, user, attempt_id)
        snap = next((q for q in read_snapshot(conn, row['paper_id'])
                     if q['stable_id'] == stable_id and q['qtype'] == QTYPE_SHORT), None)
        if not snap:
            raise not_found('本卷简答题不存在')
        if not 0 <= score <= Decimal(str(snap['score'])):
            raise bad_request('自评分必须在 0 与该题满分之间')
        a = conn.execute('SELECT * FROM attempt_answers WHERE attempt_id=? AND stable_id=?',
                         (attempt_id, stable_id)).fetchone()
        if not a or a['graded_result'] != 'pending':
            if a and a['graded_by'] == 'self' and Decimal(str(a['score'])) == score:
                return  # 相同请求幂等，已确认的评分不能覆盖
            raise conflict('本题已确认评分，不能重复修改')
        conn.execute("UPDATE attempt_answers SET score=?, graded_result=?, graded_by='self', graded_at=? "
                     'WHERE attempt_id=? AND stable_id=?',
                     (float(score), 'right' if score == Decimal(str(snap['score'])) else 'wrong', now_iso(), attempt_id, stable_id))
        exam.recompute_score(conn, attempt_id)
        audit.log(conn, user['employee_no'], 'self_test.rate', f'attempts:{attempt_id}',
                  after={'stable_id': stable_id, 'score': float(score)})
