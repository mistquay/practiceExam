"""自助注册与 PNG 验证码；随机/时钟仅通过进程内 fixture 控制。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import struct
import zlib

import pytest
from fastapi import HTTPException

from test_self_service import scenario
from app.services import registration as R
from app.services import testkit as T
from app.services.errors import ApiError


@pytest.fixture
def protection(monkeypatch):
    now = [1000.0]
    guard = R.RegistrationGuard(clock=lambda: now[0])
    monkeypatch.setattr(R, 'guard', guard)
    monkeypatch.setattr(R, 'new_answer', lambda: '4826')
    return guard, now


def challenge(client, **kwargs):
    response = client.get('/api/auth/captcha', **kwargs)
    assert response.status_code == 200, response.text
    return response.headers['X-Captcha-ID']


def payload(cid, **kwargs):
    return {'employee_no': 'new_user', 'password': 'SafePass123', 'captcha_id': cid, 'captcha': '4826', **kwargs}


def test_png_and_no_answer_leak(scenario, protection):
    _, _, user, _, _ = scenario
    response = user.get('/api/auth/captcha')
    assert response.headers['content-type'] == 'image/png'
    assert response.headers['cache-control'] == 'no-store'
    assert response.content[:8] == b'\x89PNG\r\n\x1a\n'
    assert b'4826' not in response.content
    assert '4826' not in str(dict(response.headers))
    data, offset, kinds, compressed = response.content, 8, [], b''
    while offset < len(data):
        size = struct.unpack('!I', data[offset:offset+4])[0]
        kind, body = data[offset+4:offset+8], data[offset+8:offset+8+size]
        assert zlib.crc32(kind + body) == struct.unpack('!I', data[offset+8+size:offset+12+size])[0]
        kinds.append(kind)
        if kind == b'IHDR':
            assert struct.unpack('!2I5B', body) == (152, 52, 8, 2, 0, 0, 0)
        if kind == b'IDAT':
            compressed += body
        offset += size + 12
    assert kinds == [b'IHDR', b'IDAT', b'IEND']
    raster = zlib.decompress(compressed)
    assert len(raster) == 52 * (152 * 3 + 1)
    assert len(set(raster)) > 4
    guard, _ = protection
    assert all(isinstance(item[0], bytes) and len(item[0]) == 32 for item in guard.challenges.values())
    assert '4826' not in repr(guard.challenges)
    for path in ('/static/views/register.js', '/static/views/login.js', '/'):
        assert '4826' not in user.get(path).text


def test_register_login_practice_and_free_exam(scenario, protection):
    _, _, imported, conn, bank = scenario
    user = T.client()
    cid = challenge(user)
    result = user.post('/api/auth/register', json=payload(cid, employee_no='New_User'))
    assert result.status_code == 201, result.text
    assert result.json()['employee_no'] == 'new_user'
    assert 'SafePass123' not in result.text and '4826' not in result.text
    row = conn.execute("SELECT * FROM users WHERE employee_no='new_user'").fetchone()
    assert row['role'] == 'examinee' and row['active'] == 1 and row['must_change_password'] == 0
    assert row['password_hash'].startswith('$2') and row['password_hash'] != 'SafePass123'
    logs = conn.execute("SELECT * FROM audit_logs WHERE action='user.register'").fetchall()
    assert len(logs) == 1 and logs[0]['actor'] == 'new_user'
    assert 'SafePass123' not in str(tuple(logs[0])) and '4826' not in str(tuple(logs[0]))
    login = user.post('/api/auth/login', json={'employee_no': 'NEW_USER', 'password': 'SafePass123'})
    assert login.status_code == 200, login.text
    assert 'HttpOnly' in login.headers['set-cookie']
    assert login.json()['authorization'] is None
    assert user.get('/api/practice/questions', params={'bank_id': bank}).status_code == 200
    result = user.post('/api/self-tests/start', json={'bank_id': bank, 'counts': {'单选题': 1}})
    assert result.status_code == 200, result.text
    assert result.json()['exam_mode'] == 'self_service'
    assert user.get('/api/admin/users').status_code == 403
    assert imported.get('/api/auth/me').json()['employee_no'] == 'free_user'
    assert user.post('/api/auth/register', json=payload(cid)).status_code == 400


@pytest.mark.parametrize('username', ['free_user', 'FREE_USER', 'admin', 'ADMIN'])
def test_duplicate_imported_and_admin(scenario, protection, username):
    _, _, user, conn, _ = scenario
    before = [tuple(r) for r in conn.execute('SELECT * FROM users ORDER BY id')]
    result = user.post('/api/auth/register', json=payload(challenge(user), employee_no=username))
    assert result.status_code == 409, result.text
    assert before == [tuple(r) for r in conn.execute('SELECT * FROM users ORDER BY id')]


@pytest.mark.parametrize('fields', [
    {'employee_no': 'ab'}, {'employee_no': 'a'*33}, {'employee_no': ' user'},
    {'employee_no': 'user '}, {'employee_no': 'us er'}, {'employee_no': '_user'},
    {'employee_no': "x' OR 1=1--"}, {'employee_no': '用户名称'}, {'employee_no': 123},
    {'password': 'short1'}, {'password': '12345678'}, {'password': 'abcdefgh'},
    {'password': 'Safe Pass123'}, {'password': 'a1' + 'x'*71},
    {'password': 'a1' + '中'*24}, {'password': None},
    {'role': 'admin'}, {'active': False}, {'must_change_password': False},
    {'authorization': {'role': 'admin'}},
])
def test_invalid_fields_consumed(scenario, protection, fields):
    _, _, user, conn, _ = scenario
    cid = challenge(user)
    response = user.post('/api/auth/register', json=payload(cid, **fields))
    assert response.status_code == 400, response.text
    assert user.post('/api/auth/register', json=payload(cid)).status_code == 400
    assert not conn.execute("SELECT 1 FROM users WHERE employee_no='new_user'").fetchone()


def test_bcrypt_boundary_and_duplicate_new_case(scenario, protection):
    _, _, user, _, _ = scenario
    password = 'a1' + '中'*23 + 'x'
    assert len(password.encode()) == 72
    assert user.post('/api/auth/register', json=payload(challenge(user), password=password)).status_code == 201
    assert user.post('/api/auth/login', json={'employee_no': 'new_user', 'password': password}).status_code == 200
    assert user.post('/api/auth/register', json=payload(challenge(user), employee_no='NEW_USER')).status_code == 409


def test_wrong_expired_refreshed_and_foreign_ip(scenario, protection):
    _, _, user, _, _ = scenario
    guard, now = protection
    cid = challenge(user)
    assert user.post('/api/auth/register', json=payload(cid, captcha='0000')).status_code == 400
    assert user.post('/api/auth/register', json=payload(cid)).status_code == 400
    cid = challenge(user)
    now[0] += 180
    assert user.post('/api/auth/register', json=payload(cid)).status_code == 400
    cid = challenge(user)
    new_cid = challenge(user, params={'replace': cid})
    assert new_cid != cid
    assert user.post('/api/auth/register', json=payload(cid)).status_code == 400
    assert user.post('/api/auth/register', json=payload(new_cid)).status_code == 201
    cid, _ = guard.issue('other-ip')
    assert user.post('/api/auth/register', json=payload(cid, employee_no='different')).status_code == 400


def test_http_rate_limits_and_untrusted_forwarded_header(scenario, protection):
    _, _, user, _, _ = scenario
    guard, now = protection
    for i in range(guard.LIMITS['captcha']):
        assert user.get('/api/auth/captcha', headers={'X-Forwarded-For': f'10.0.0.{i}'}).status_code == 200
    response = user.get('/api/auth/captcha')
    assert response.status_code == 429 and response.headers['Retry-After'] == '60'
    assert response.headers['cache-control'] == 'no-store'
    for i in range(guard.LIMITS['register']):
        assert user.post('/api/auth/register', content='bad', headers={'X-Forwarded-For': str(i)}).status_code == 400
    response = user.post('/api/auth/register', json={})
    assert response.status_code == 429 and int(response.headers['Retry-After']) > 0
    # 不触碰既有登录语义。
    assert user.post('/api/auth/login', json={'employee_no': 'free_user', 'password': 'Practice@123'}).status_code == 200
    now[0] += 61
    assert user.get('/api/auth/captcha').status_code == 200
    assert user.post('/api/auth/register', content='bad').status_code == 400
    assert user.post('/api/auth/register', content='x'*4097).status_code == 413


def test_store_bounds_pruning_and_atomic_consume(protection):
    guard, now = protection
    guard.MAX_CHALLENGES = 3
    for _ in range(3):
        guard.issue('ip')
    with pytest.raises(HTTPException) as exc:
        guard.issue('ip')
    assert exc.value.status_code == 429 and len(guard.challenges) == 3
    now[0] += 180
    cid, _ = guard.issue('ip')
    assert len(guard.challenges) == 1

    def consume(_):
        try:
            guard.consume('ip', cid, '4826')
            return True
        except ApiError:
            return False

    with ThreadPoolExecutor(max_workers=16) as pool:
        assert sum(pool.map(consume, range(32))) == 1
    guard.MAX_CLIENTS = 2
    guard.rate('one', 'register')
    with pytest.raises(HTTPException):
        guard.rate('two', 'register')
    assert len(guard.clients) == 2
    now[0] += 61
    guard.rate('two', 'register')
    assert len(guard.clients) == 1


@pytest.mark.parametrize('fields', [{'password': 'Safe1234\ud800'}, {'captcha': '\ud800'}, {'captcha': 4826}, {'captcha': '0'*33}])
def test_malformed_unicode_and_code_consumed(scenario, protection, fields):
    _, _, user, _, _ = scenario
    cid = challenge(user)
    # 转义 Unicode，真实走 HTTP JSON 解析，不让客户端提前编码失败。
    response = user.post('/api/auth/register', content=json.dumps(payload(cid, **fields)),
                         headers={'Content-Type': 'application/json'})
    assert response.status_code == 400 and 'Safe1234' not in response.text
    assert user.post('/api/auth/register', json=payload(cid)).status_code == 400


def test_periodic_cleanup(protection, monkeypatch):
    guard, now = protection
    guard.issue('ip')
    calls = []

    async def tick(seconds):
        assert seconds == 60
        calls.append(seconds)
        if len(calls) == 2:
            raise asyncio.CancelledError
        now[0] += 180

    monkeypatch.setattr(R.asyncio, 'sleep', tick)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(R.cleanup_loop())
    assert not guard.challenges and not guard.clients


def test_concurrent_registration_same_name_and_challenge(scenario, protection):
    _, _, user, conn, _ = scenario
    ids = [challenge(user) for _ in range(2)]

    def attempt(cid):
        return T.client().post('/api/auth/register', json=payload(cid)).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, ids)) == [201, 409]
    cid = challenge(user)
    with ThreadPoolExecutor(max_workers=2) as pool:
        # 同一挑战并发最多消费一次；其中一次会遇到已存在用户名。
        assert sorted(pool.map(attempt, [cid, cid])) == [400, 409]
    assert conn.execute("SELECT count(*) FROM users WHERE employee_no='new_user'").fetchone()[0] == 1
