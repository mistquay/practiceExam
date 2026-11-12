// 专用本机隔离库 + 独立 Chrome；沿用 browser_free_self_tests.mjs 的环境变量。
// EXAM_BROWSER_ADMIN / EXAM_BROWSER_ADMIN_PASSWORD 可覆盖隔离管理员凭据。
import assert from 'node:assert/strict';
import { command, close } from './browser_cdp.mjs';
import { checkAdminUi } from './browser_admin_ui.mjs';
const origin = process.env.EXAM_BROWSER_URL;
assert(origin && ['127.0.0.1', 'localhost'].includes(new URL(origin).hostname));
const evaluate = async js => JSON.parse(await command('eval', `(async()=>JSON.stringify((await (${js})) ?? null))()`));
async function wait(js) {
  for (let i = 0; i < 100; i++) {
    if (await evaluate(js)) return;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert.fail(`等待超时：${js}`);
}
async function route(path, ready) {
  await evaluate(`import('/static/app.js').then(m => m.navigate(${JSON.stringify(path)}))`);
  await wait(ready);
}
async function login(user, password) {
  await evaluate(`fetch('/api/auth/logout',{method:'POST'})`);
  await command('goto', origin + '/#/login');
  await command('reload');
  await wait(`!!document.querySelector('input[name=employee_no]')`);
  await evaluate(`(()=>{document.querySelector('[name=employee_no]').value=${JSON.stringify(user)};
    document.querySelector('[name=password]').value=${JSON.stringify(password)};
    document.querySelector('form').requestSubmit();return true})()`);
  await wait(`!!document.querySelector('.self-test-form')`);
}
async function snapshot() {
  return evaluate(`(()=>{const controls=[...document.querySelectorAll('a,button,[role=button]')];return {
    hash:location.hash, width:innerWidth, scroll:document.documentElement.scrollWidth,
    adminLinks:controls.filter(e=>(e.getAttribute('href')||'').includes('/admin')).map(e=>e.outerHTML),
    adminActions:controls.filter(e=>/^(管理|发布|人工阅卷|成绩管理|用户管理|授权|重置密码|停用|启用|创建场次|导出成绩)$/.test(e.textContent.trim())).map(e=>e.textContent),
    resources:performance.getEntriesByType('resource').filter(e=>/\\/static\\/(views\\/admin\\/|api\\/admin\\.js)|\\/api\\/admin\\//.test(e.name)).map(e=>e.name)}})()`);
}
const measurements = [];
try {
  await command('goto', origin + '/#/login');
  await wait(`!!document.querySelector('form')`);
  await login(process.env.EXAM_BROWSER_USER, process.env.EXAM_BROWSER_PASSWORD);
  const bank = await evaluate(`(async()=> (await (await fetch('/api/banks')).json()).banks?.[0]?.id)()`);
  // 兼容列表 DTO，以真实首页选择状态为准。
  const bankId = bank || await evaluate(`import('/static/state/selection.js').then(m=>m.getSelection().bankId)`);
  assert(bankId, '首页未选择题库');
  const attempt = await evaluate(`(async()=>{const r=await fetch('/api/self-tests/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bank_id:Number(${JSON.stringify(bankId)}),counts:{'简答题':1}})});if(!r.ok)throw Error(await r.text());return r.json()})()`);
  const aid = attempt.id;
  const checks = [
    ['/', `!!document.querySelector('.self-test-form')`],
    ['/wrong', `!!document.querySelector('h1') && document.querySelector('h1').textContent.includes('错题')`],
    ['/records', `!!document.querySelector('table') || document.body.innerText.includes('暂无练习记录')`],
    ['/history', `!!document.querySelector('table')`],
    [`/practice?bank=${bankId}&count=1`, `!!document.querySelector('.question-card')`],
    [`/exam/${aid}`, `!!document.querySelector('textarea')`],
  ];
  for (const width of [1280, 320, 390]) {
    await command('viewport', String(width), '900', ...(width < 500 ? ['--mobile'] : []));
    for (const [path, ready] of checks) {
      await route(path, ready);
      const s = await snapshot();
      assert.deepEqual(s.adminLinks, [], JSON.stringify(s));
      assert.deepEqual(s.adminActions, [], JSON.stringify(s));
      assert.deepEqual(s.resources, [], JSON.stringify(s));
      assert(s.scroll <= width, JSON.stringify(s));
      measurements.push(s);
    }
  }
  // 自评/结果页面不是管理员页，不应被隐藏。
  await evaluate(`fetch('/api/exams/${aid}/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({responses:[{stable_id:${JSON.stringify(attempt.questions[0].stable_id)},choice:'本人简答'}]})})`);
  await route(`/exam/${aid}`, `document.body.innerText.includes('参考答案：')`);
  assert.equal(await evaluate(`!!document.querySelector('input[step="0.01"]')`), true);
  for (const width of [320, 390]) {
    await command('viewport', String(width), '900', '--mobile');
    const resultSnapshot = await snapshot();
    assert.deepEqual(resultSnapshot.adminLinks, []);
    assert.deepEqual(resultSnapshot.adminActions, []);
    assert.deepEqual(resultSnapshot.resources, []);
    assert(resultSnapshot.scroll <= width);
    measurements.push(resultSnapshot);
  }
  await evaluate(`(()=>{document.querySelector('input[step="0.01"]').value='0';document.querySelector('form').requestSubmit();return true})()`);
  await wait(`document.body.innerText.includes('已确认 0')`);
  assert.deepEqual((await snapshot()).adminLinks, []);
  // 名称澄清后仍保留普通账号可参加场次的入口/API。
  await route('/', `!!document.querySelector('.self-test-form')`);
  await evaluate(`(()=>{document.querySelector('details').open=true;return true})()`);
  await wait(`performance.getEntriesByType('resource').some(e=>e.name.includes('/api/exams/available'))`);
  assert.equal(await evaluate(`document.querySelector('summary').textContent`), '可参加的考试场次（受场次规则限制）');
  assert.equal(await evaluate(`fetch('/api/exams/available').then(r=>r.status)`), 200);
  for (const tab of ['', '/users', '/banks', '/templates', '/batches', '/grading', '/auth', '/auth?user=1', '/unknown']) {
    await route('/admin' + tab, `document.body.innerText.includes('无权限：仅管理员可访问')`);
    const s = await snapshot();
    assert.deepEqual(s.adminLinks, []);
    assert.deepEqual(s.resources, [], '普通用户直接 hash 不得加载管理模块/API');
    assert.equal(await evaluate(`!!document.querySelector('a[href="#/"]')`), true);
  }
  const forbidden = await evaluate(`(async()=>{const cases=[['GET','users'],['GET','banks'],['GET','templates'],['GET','batches'],['GET','grading/pending'],['GET','audit'],['POST','banks/1/publish'],['PATCH','templates/1',{status:'active'}],['POST','users/1/reset-password'],['GET','users/1/authorizations']];const out=[];for(const [method,path,body] of cases){out.push([method,path,(await fetch('/api/admin/'+path,{method,headers:{'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})})).status]);}return out})()`);
  assert(forbidden.every(row => row[2] === 403), JSON.stringify(forbidden));
  // 清资源计时后检查延迟 me 期间无管理员菜单，迟到响应不能覆盖新路由。
  await login(process.env.EXAM_BROWSER_ADMIN || 'admin', process.env.EXAM_BROWSER_ADMIN_PASSWORD || 'Admin@12345');
  assert.equal((await evaluate(`(${checkAdminUi.toString()})()`)).result, 'PASS');
  for (const tab of ['banks', 'templates', 'batches', 'grading']) {
    await route('/admin/' + tab, `document.querySelector('.tabs .active')?.getAttribute('href') === '#/admin/${tab}' && !document.querySelector('.loading')`);
    assert.equal(await evaluate(`!!document.querySelector('#main-nav a[href="#/admin"]')`), true);
  }
  // 不刷新页面，在服务端切换为普通账号；旧缓存角色不能继续显示管理 UI。
  await evaluate(`(()=>{localStorage.setItem('exam-draft:role-check','{}');return true})()`);
  await evaluate(`fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({employee_no:${JSON.stringify(process.env.EXAM_BROWSER_USER)},password:${JSON.stringify(process.env.EXAM_BROWSER_PASSWORD)}})})`);
  await evaluate(`(()=>{performance.clearResourceTimings();return true})()`);
  await route('/admin/users', `document.body.innerText.includes('无权限：仅管理员可访问')`);
  const switched = await snapshot();
  assert.equal(await evaluate(`localStorage.getItem('exam-draft:role-check')`), null, '账号/角色变化未清理草稿');
  assert.deepEqual(switched.adminLinks, []);
  assert.deepEqual(switched.resources, [], '切换普通账号后仍调用管理 API');
  await login(process.env.EXAM_BROWSER_ADMIN || 'admin', process.env.EXAM_BROWSER_ADMIN_PASSWORD || 'Admin@12345');
  await route('/admin/users', `!!document.querySelector('.tabs')`);
  await evaluate(`(()=>{window.realFetch=window.fetch;window.fetch=async(...args)=>{if(args[0]==='/api/auth/me') {await new Promise(r=>setTimeout(r,600));return new Response(JSON.stringify({role:'examinee',name:'降权用户'}),{headers:{'Content-Type':'application/json'}});}return realFetch(...args)};return true})()`);
  await evaluate(`(()=>{window.adminFlashes=0;window.chromeObserver=new MutationObserver(()=>{if(document.querySelector('#main-nav a[href="#/admin"],.tabs'))window.adminFlashes++;});chromeObserver.observe(document.body,{childList:true,subtree:true});return true})()`);
  await evaluate(`import('/static/app.js').then(m=>m.navigate('/admin/users'))`);
  assert.equal(await evaluate(`!!document.querySelector('#main-nav a[href="#/admin"],.tabs')`), false, 'me 未 ready 残留旧管理 UI');
  await wait(`document.body.innerText.includes('无权限：仅管理员可访问')`);
  assert.deepEqual((await snapshot()).adminLinks, []);
  assert.equal(await evaluate(`adminFlashes`), 0, '会话加载期间闪现管理 UI');
  await evaluate(`(()=>{chromeObserver.disconnect();return true})()`);
  // 失败必须清空旧管理员会话，不得在公开登录页继续显示管理导航。
  await evaluate(`(()=>{window.fetch=async(...args)=>args[0]==='/api/auth/me'?new Response('{}',{status:503,headers:{'Content-Type':'application/json'}}):realFetch(...args);return true})()`);
  await route('/admin/templates', `!!document.querySelector('input[name=employee_no]')`);
  assert.deepEqual((await snapshot()).adminLinks, []);
  assert.equal(await evaluate(`import('/static/state/session.js').then(m=>m.getUser())`), null);
  // 退出后的迟到管理员 me 响应不得复活会话。
  assert.equal(await evaluate(`(async()=>{const session=await import('/static/state/session.js');let release;window.fetch=()=>new Promise(resolve=>{release=resolve});const pending=session.loadSession();session.clearSession();release(new Response(JSON.stringify({role:'admin'}),{headers:{'Content-Type':'application/json'}}));await pending;window.fetch=realFetch;return session.getUser()===null})()`), true);
  // 登录页 / 注册页公开可见，PNG 验证码真正加载；不改变验证码安全实现。
  await route('/register', `!!document.querySelector('.captcha-refresh img')?.naturalWidth && !document.querySelector('button[type=submit]').disabled`);
  assert.equal(await evaluate(`!!document.querySelector('a[href="#/login"]')`), true);
  assert.deepEqual((await snapshot()).adminLinks, []);
  // 仅当隔离服务通过测试注入固定验证码时，走完整真实注册表单。
  if (process.env.EXAM_BROWSER_CAPTCHA_ANSWER) {
    const username = `registered_${Date.now()}`;
    await evaluate(`(()=>{for(const [key,value] of Object.entries({employee_no:${JSON.stringify(username)},password:'Practice@123',confirm_password:'Practice@123',captcha:${JSON.stringify(process.env.EXAM_BROWSER_CAPTCHA_ANSWER)}}))document.querySelector('[name='+key+']').value=value;document.querySelector('form').requestSubmit();return true})()`);
    await wait(`document.body.innerText.includes('注册成功')`);
    await evaluate(`(()=>{document.querySelector('[name=password]').value='Practice@123';document.querySelector('form').requestSubmit();return true})()`);
    await wait(`!!document.querySelector('.self-test-form')`);
    assert.equal(await evaluate(`import('/static/state/session.js').then(m=>m.getUser().role)`), 'examinee');
    assert.deepEqual((await snapshot()).adminLinks, []);
    assert.equal(await evaluate(`fetch('/api/admin/users').then(r=>r.status)`), 403);
  }
  await route('/login', `!!document.querySelector('a[href="#/register"]')`);
  await login(process.env.EXAM_BROWSER_USER, process.env.EXAM_BROWSER_PASSWORD);
  await evaluate(`document.querySelector('#user-info button').click()`);
  assert.equal(await evaluate(`document.getElementById('main-nav').children.length`), 0);
  await wait(`!!document.querySelector('input[name=employee_no]')`);
  console.log(JSON.stringify({ result: 'PASS', measurements, forbidden, checks: '普通页面/直接hash/管理员菜单/降权/未ready/会话失败/公开注册验证码/退出' }, null, 2));
} finally { close(); }
