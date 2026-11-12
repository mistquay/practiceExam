"""自由组卷闭环、安全与精度（真实题库的仓库内隔离副本）。"""
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from test_self_service import scenario
from app.models import PERSON_CATEGORIES, QTYPES
from app.services.dto import FORBIDDEN_TOKENS
from app.db import migrate


def begin(user, bank, counts, **kw):
    r = user.post('/api/self-tests/start', json={'bank_id': bank, 'counts': counts, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def snapshots(conn, aid):
    return [json.loads(r[0]) for r in conn.execute(
        'SELECT snapshot_json FROM paper_questions WHERE paper_id=(SELECT paper_id FROM attempts WHERE id=?) ORDER BY seq', (aid,))]


@pytest.mark.parametrize('person', [None, *PERSON_CATEGORIES])
def test_all_people_without_templates(scenario, person):
    _, _, user, conn, bank = scenario
    conn.execute("UPDATE paper_templates SET status='disabled'")
    conn.commit()
    inventory = user.post('/api/self-tests/inventory', json={'bank_id': bank, 'person_category': person}).json()
    counts = {q: int(inventory['counts'][q] > 0) for q in QTYPES}
    a = begin(user, bank, counts, person_category=person, duration_min=17, score=999, total_score=999)
    qs = snapshots(conn, a['id'])
    assert Counter(q['qtype'] for q in qs) == Counter({q: n for q,n in counts.items() if n})
    assert a['total_score'] == 100 and a['duration_min'] == 17
    assert len(set(q['stable_id'] for q in qs)) == len(qs)
    assert sum(Decimal(str(q['score'])) for q in qs) == 100
    config = json.loads(conn.execute('SELECT selection_json FROM papers WHERE id=(SELECT paper_id FROM attempts WHERE id=?)', (a['id'],)).fetchone()[0])
    assert config['person_category'] == person and config['total_score'] == 100 and config['counts'] == counts
    for token in FORBIDDEN_TOKENS:
        assert token.lower() not in json.dumps(a).encode().lower()
    if person:
        assert all(conn.execute("SELECT 1 FROM question_tags WHERE bank_version_id=? AND stable_id=? AND tag_type='person_scope' AND tag_value=?", (bank,q['stable_id'],person)).fetchone() for q in qs)


def test_example_precision_and_objective(scenario):
    _, _, user, conn, bank = scenario
    a = begin(user, bank, {'单选题':80, '判断题':10, '简答题':10})
    assert Counter(q['qtype'] for q in a['questions']) == {'单选题':80, '判断题':10, '简答题':10}
    a = begin(user, bank, {'单选题':3})
    qs = snapshots(conn, a['id'])
    assert [q['score'] for q in qs] == [33.34,33.33,33.33]
    conn.execute('UPDATE question_versions SET answer=? WHERE id=?', ('forged', qs[0]['question_version_id']))
    conn.commit()
    r = user.post(f"/api/exams/{a['id']}/submit", json={'responses':[{'stable_id':q['stable_id'],'choice':q['answer']} for q in qs], 'score':999})
    assert r.status_code == 200, r.text
    assert r.json()['score'] == 100 and r.json()['passed'] is True
    assert user.get(f"/api/exams/{a['id']}").json()['score'] == 100


@pytest.mark.parametrize('counts', [{'简答题':1}, {'简答题':3}, {'单选题':1,'多选题':1,'判断题':1,'简答题':2}])
def test_self_review_lifecycle_and_permissions(scenario, counts):
    _, admin, user, conn, bank = scenario
    a = begin(user, bank, counts)
    aid = a['id']
    qs = snapshots(conn, aid)
    short = [q for q in qs if q['qtype']=='简答题']
    path = f'/api/self-tests/{aid}'
    rating = {'stable_id':short[0]['stable_id'], 'score':short[0]['score']}
    assert user.get(path+'/review').status_code == 409
    assert user.post(path+'/self-rating', json=rating).status_code == 409
    assert admin.get(path+'/review').status_code == 404
    assert admin.post(path+'/self-rating', json=rating).status_code == 404
    result = user.post(f'/api/exams/{aid}/submit', json={'responses':[{'stable_id':q['stable_id'], 'choice':'我的简答中包含参考答案、正确答案和 answer 字样，但这不是评分依据。' if q['qtype']=='简答题' else q['answer']} for q in qs]})
    assert result.status_code == 200, result.text
    assert result.json()['score'] is None and result.json()['passed'] is None
    assert result.json()['result']['pending_count'] == len(short)
    for token in FORBIDDEN_TOKENS:
        assert token.lower() not in result.content.lower()
    assert user.get(path+'/review').json()['items'][0]['reference_answer']
    history = user.get('/api/exams').json()['items'][0]
    assert history['score'] is None and history['status']=='pending_review'
    export = user.get('/api/reports/export', params={'format':'json'}).json()
    assert export['rows'][0]['score']=='' and export['rows'][0]['status']=='待自评'
    assert export['graded_count']==0
    for value in [-1, short[0]['score']+0.01, 0.001, 'NaN', 'Infinity']:
        assert user.post(path+'/self-rating', json={**rating,'score':value}).status_code in (400,422)
    assert user.post(path+'/self-rating', json={**rating,'stable_id':'not-on-paper'}).status_code==404
    assert user.post(f'/api/exams/{aid}/submit', json={'responses':[]}).status_code==409
    for q in short:
        rating = {'stable_id':q['stable_id'], 'score':q['score']}
        assert user.post(path+'/self-rating', json=rating).status_code==200
        assert user.post(path+'/self-rating', json=rating).status_code==200
        assert user.post(path+'/self-rating', json={**rating,'score':0}).status_code==409
    final = user.get(f'/api/exams/{aid}').json()
    assert final['score']==100 and final['status']=='graded' and '自评' in final['notice']
    assert user.get('/api/exams').json()['items'][0]['score']==100
    export = user.get('/api/reports/export', params={'format':'json'}).json()
    assert export['graded_count']==0 and export['pass_rate'] is None and export['rows'][0]['status']=='自评/非正式'


def test_expiration_and_blank(scenario):
    _, _, user, conn, bank = scenario
    for blank in ('', '   '):
        a = begin(user, bank, {'简答题':1})
        r = user.post(f"/api/exams/{a['id']}/submit", json={'responses':[{'stable_id':a['questions'][0]['stable_id'],'choice':blank}]})
        assert r.json()['score']==0 and r.json()['result']['pending_count']==0
    a = begin(user, bank, {'简答题':1})
    conn.execute('UPDATE attempts SET deadline_at=? WHERE id=?', ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),a['id']))
    conn.commit()
    r = user.get(f"/api/exams/{a['id']}")
    assert r.json()['status']=='expired' and r.json()['score']==0
    assert user.get(f"/api/self-tests/{a['id']}/review").status_code==200
    assert user.post(f"/api/exams/{a['id']}/submit", json={'responses':[]}).status_code==409


@pytest.mark.parametrize('change', [
    {'counts':{}}, {'counts':{'单选题':201}}, {'counts':{'单选题':-1}},
    {'counts':{'单选题':1.5}}, {'counts':{'单选题':True}}, {'counts':{'fake':1}},
    {'person_category':'invalid'}, {'specialty_code':'invalid'}, {'tags':['invalid']},
    {'source_file_ids':[-1]}, {'duration_min':0}, {'duration_min':241},
])
def test_invalid_input(scenario, change):
    _, _, user, _, bank = scenario
    assert user.post('/api/self-tests/start', json={'bank_id':bank,'counts':{'单选题':1},**change}).status_code in (400,422)


def test_inventory_shortage_and_active_retired(scenario):
    _, _, user, conn, bank = scenario
    q = conn.execute("SELECT stable_id FROM question_versions WHERE bank_version_id=? AND qtype='简答题' AND retired=0 LIMIT 1", (bank,)).fetchone()[0]
    conn.execute("UPDATE question_versions SET retired=1 WHERE bank_version_id=? AND stable_id!=? AND qtype='简答题'", (bank,q))
    conn.commit()
    r = user.post('/api/self-tests/start', json={'bank_id':bank,'counts':{'简答题':2}})
    assert r.status_code==400 and '需要 2，可用 1，缺 1' in r.json()['detail']
    a = begin(user,bank,{'简答题':1})
    assert a['questions'][0]['stable_id']==q
    conn.execute("UPDATE bank_versions SET status='archived' WHERE id=?", (bank,))
    conn.commit()
    assert user.post('/api/self-tests/start',json={'bank_id':bank,'counts':{'简答题':1}}).status_code==404


def test_partial_self_scores_and_managed_review_isolation(scenario):
    _, admin, user, conn, bank = scenario
    from app.services import admin as admin_service
    a = begin(user, bank, {'简答题':3})
    qs = snapshots(conn, a['id'])
    user.post(f"/api/exams/{a['id']}/submit", json={'responses':[
        {'stable_id':q['stable_id'],'choice':'非空作答'} for q in qs]})
    assert not any(r['attempt_id']==a['id'] for r in admin_service.pending_grading(conn))
    assert admin.post('/api/admin/grading', json={'attempt_id':a['id'],'stable_id':qs[0]['stable_id'],'score':1}).status_code==409
    for q, score in zip(qs, [0,12.34,33.33]):
        assert user.post(f"/api/self-tests/{a['id']}/self-rating", json={'stable_id':q['stable_id'],'score':score}).status_code==200
    final = user.get(f"/api/exams/{a['id']}").json()
    assert final['score']==45.67 and final['passed'] is False
    report = user.get('/api/reports/export',params={'format':'json'}).json()['rows'][0]
    assert report['self_score']==45.67 and report['objective_total']==0
    conn.execute("UPDATE attempts SET exam_mode='managed' WHERE id=?",(a['id'],))
    conn.commit()
    assert user.get(f"/api/self-tests/{a['id']}/review").status_code==404
    assert user.post(f"/api/self-tests/{a['id']}/self-rating",json={'stable_id':qs[0]['stable_id'],'score':0}).status_code==404


def test_real_source_tags_and_disabled_specialty(scenario):
    _, _, user, conn, bank = scenario
    facets=user.post('/api/self-tests/inventory',json={'bank_id':bank}).json()
    # 不以 specialties.enabled 为开考门槛。
    specialty=conn.execute("SELECT s.code FROM specialties s JOIN question_tags t ON t.tag_type='specialty' AND t.tag_value=s.code JOIN question_versions q ON q.bank_version_id=t.bank_version_id AND q.stable_id=t.stable_id AND q.retired=0 WHERE t.bank_version_id=? AND s.enabled=0 LIMIT 1",(bank,)).fetchone()[0]
    source=conn.execute("SELECT qs.source_file_id FROM question_sources qs JOIN question_tags t ON t.bank_version_id=qs.bank_version_id AND t.stable_id=qs.stable_id WHERE qs.bank_version_id=? AND t.tag_type='specialty' AND t.tag_value=? LIMIT 1",(bank,specialty)).fetchone()[0]
    scope={'bank_id':bank,'specialty_code':specialty,'source_file_ids':[source]}
    inv=user.post('/api/self-tests/inventory',json=scope).json()
    assert inv['total']>0 and specialty in {s['code'] for s in facets['specialties']}
    counts={q: min(2,inv['counts'][q]) for q in QTYPES}
    a=begin(user,bank,counts,specialty_code=specialty,source_file_ids=[source])
    for q in snapshots(conn,a['id']):
        assert conn.execute('SELECT 1 FROM question_sources WHERE bank_version_id=? AND stable_id=? AND source_file_id=?',(bank,q['stable_id'],source)).fetchone()
        assert conn.execute("SELECT 1 FROM question_tags WHERE bank_version_id=? AND stable_id=? AND tag_type='specialty' AND tag_value=?",(bank,q['stable_id'],specialty)).fetchone()
    inv=user.post('/api/self-tests/inventory',json={'bank_id':bank,'tags':['protected']}).json()
    counts={q:min(2,inv['counts'][q]) for q in QTYPES}
    a=begin(user,bank,counts,tags=['protected'])
    assert all(conn.execute('SELECT is_protected FROM question_versions WHERE id=?',(q['question_version_id'],)).fetchone()[0] for q in snapshots(conn,a['id']))


def test_migration_roundtrip(scenario):
    db, _, _, conn, _ = scenario
    conn.close()
    assert migrate.downgrade(db, '0003')==['0004']
    assert migrate.upgrade(db)==['0004']
    assert all(r['checksum_ok'] for r in migrate.status(db))
