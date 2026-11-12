"""单进程基础防刷：有界、带锁、一次性验证码；无外部服务或图像依赖。"""
from __future__ import annotations

import asyncio
import hmac
import math
import re
import secrets
import sqlite3
import struct
import threading
import time
import zlib
from collections import deque

from fastapi import HTTPException

from app.services import audit
from app.services.auth import hash_password
from app.services.db import write_tx
from app.services.errors import bad_request, conflict
from app.services.util import now_iso

# 5×7 像素字模，只输出光栅，无文本元数据。
DIGITS = (
    ('01110','11011','11011','11011','11011','11011','01110'),
    ('00100','01100','00100','00100','00100','00100','01110'),
    ('01110','10001','00001','00010','00100','01000','11111'),
    ('11110','00001','00001','01110','00001','00001','11110'),
    ('00010','00110','01010','10010','11111','00010','00010'),
    ('11111','10000','10000','11110','00001','00001','11110'),
    ('01110','10000','10000','11110','10001','10001','01110'),
    ('11111','00001','00010','00100','01000','01000','01000'),
    ('01110','10001','10001','01110','10001','10001','01110'),
    ('01110','10001','10001','01111','00001','00001','01110'),
)


def new_answer():
    return ''.join(secrets.choice('0123456789') for _ in range(4))


def png_image(answer: str) -> bytes:
    width, height = 152, 52
    pixels = bytearray([245, 247, 250] * width * height)

    def dot(x, y, color):
        if 0 <= x < width and 0 <= y < height:
            at = (y * width + x) * 3
            pixels[at:at + 3] = bytes(color)

    for _ in range(5):
        y, slope = secrets.randbelow(height), secrets.randbelow(7) - 3
        for x in range(width):
            dot(x, (y + x * slope // 12) % height, (175, 186, 198))
    for i, char in enumerate(answer):
        left, top = 12 + i * 34, 9 + secrets.randbelow(6)
        for y, row in enumerate(DIGITS[int(char)]):
            for x, bit in enumerate(row):
                if bit == '1':
                    for dy in range(4):
                        for dx in range(4):
                            dot(left + x * 4 + dx, top + y * 4 + dy, (35, 52, 72))

    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))

    raw = b''.join(b'\0' + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


class RegistrationGuard:
    TTL = 180
    MAX_CHALLENGES = 2048
    MAX_CLIENTS = 4096
    WINDOW = 60
    LIMITS = {'captcha': 30, 'register': 10}

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.key = secrets.token_bytes(32)
        self.challenges = {}
        self.clients = {}

    def _digest(self, challenge_id, answer):
        return hmac.digest(self.key, (challenge_id + ':' + answer).encode('utf-8'), 'sha256')

    def _clean(self, now):
        for key, (_, expires, _) in list(self.challenges.items()):
            if expires <= now:
                del self.challenges[key]
        for key, events in list(self.clients.items()):
            while events and events[0] <= now - self.WINDOW:
                events.popleft()
            if not events:
                del self.clients[key]

    def cleanup(self):
        with self.lock:
            self._clean(self.clock())

    def _limited(self, retry=60):
        raise HTTPException(429, '操作较频繁，请稍后重试', headers={
            'Retry-After': str(max(1, math.ceil(retry))), 'Cache-Control': 'no-store'})

    def rate(self, ip, action):
        with self.lock:
            now = self.clock()
            self._clean(now)
            key = (ip, action)
            if key not in self.clients:
                if len(self.clients) >= self.MAX_CLIENTS:
                    self._limited()
                self.clients[key] = deque()
            events = self.clients[key]
            if len(events) >= self.LIMITS[action]:
                self._limited(events[0] + self.WINDOW - now)
            events.append(now)

    def issue(self, ip, replace=None):
        self.rate(ip, 'captcha')
        answer = new_answer()
        challenge_id = secrets.token_urlsafe(24)
        image = png_image(answer)
        with self.lock:
            now = self.clock()
            self._clean(now)
            if replace in self.challenges and self.challenges[replace][2] == ip:
                del self.challenges[replace]
            if len(self.challenges) >= self.MAX_CHALLENGES:
                self._limited()
            self.challenges[challenge_id] = (self._digest(challenge_id, answer), now + self.TTL, ip)
        return challenge_id, image

    def consume(self, ip, challenge_id, answer):
        with self.lock:
            now = self.clock()
            self._clean(now)
            item = self.challenges.pop(challenge_id, None)
            valid = item is not None and item[2] == ip and hmac.compare_digest(
                item[0], self._digest(challenge_id, answer))
        if not valid:
            raise bad_request('验证码错误、已使用或已过期，请换一张后重试')


guard = RegistrationGuard()


async def cleanup_loop():
    """随应用生命周期运行；空闲时也每分钟清除过期挑战和限流桶。"""
    while True:
        await asyncio.sleep(60)
        guard.cleanup()


def register(conn: sqlite3.Connection, ip: str, data):
    if not isinstance(data, dict):
        raise bad_request('请提交注册表单')
    # 先消费挑战，即使其他字段无效也不能再次尝试同一张图。
    cid, answer = data.get('captcha_id'), data.get('captcha')
    if not isinstance(cid, str) or len(cid) > 128:
        raise bad_request('请填写图形验证码，并在失败后换一张')
    supplied = answer.strip() if isinstance(answer, str) and len(answer) <= 32 else ''
    guard.consume(ip, cid, supplied if re.fullmatch(r'[0-9]{4}', supplied) else '')
    if set(data) - {'employee_no', 'password', 'captcha_id', 'captcha'}:
        raise bad_request('注册仅接受用户名、密码和验证码，不可设置角色等属性')
    username, password = data.get('employee_no'), data.get('password')
    if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{2,31}', username):
        raise bad_request('用户名须为3–32位字母、数字、下划线或短横线，以字母或数字开头，不能含空格')
    username = username.lower()
    try:
        password_bytes = password.encode('utf-8') if isinstance(password, str) else b''
    except UnicodeEncodeError:
        password_bytes = b''
    if (not isinstance(password, str) or len(password) < 8 or not password_bytes or len(password_bytes) > 72
            or not re.search(r'[A-Za-z]', password) or not re.search(r'[0-9]', password)
            or any(c.isspace() for c in password)):
        raise bad_request('密码须至少8位，包含字母和数字，不含空白，UTF-8编码不超过72字节')
    hashed = hash_password(password)
    with write_tx(conn):
        # BEGIN IMMEDIATE 序列化检查与插入，保留所有导入账号（含大小写变体）。
        if conn.execute('SELECT 1 FROM users WHERE employee_no = ? COLLATE NOCASE', (username,)).fetchone():
            raise conflict('用户名已存在，请换一个用户名')
        cur = conn.execute(
            "INSERT INTO users (employee_no,name,password_hash,role,active,must_change_password,created_at) "
            "VALUES (?,?,?,'examinee',1,0,?)", (username, username, hashed, now_iso()))
        audit.log(conn, username, 'user.register', f'users:{cur.lastrowid}',
                  after={'employee_no': username, 'role': 'examinee', 'active': True})
    return {'ok': True, 'employee_no': username, 'message': '注册成功，请使用新账号登录'}
