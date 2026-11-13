// Node >=22，真实独立 Chrome CDP 浏览器验收；不得连接运行库或个人浏览器。
// 需 EXAM_BROWSER_CDP=ws://127.0.0.1:port/devtools/page/id、EXAM_BROWSER_URL、
// EXAM_BROWSER_USER、EXAM_BROWSER_PASSWORD；详见 docs/acceptance/PRACTICE_FILTERS.md。
import assert from 'node:assert/strict';
import { command, close } from './browser_cdp.mjs';

const origin = process.env.EXAM_BROWSER_URL;
const username = process.env.EXAM_BROWSER_USER;
const password = process.env.EXAM_BROWSER_PASSWORD;
assert(origin && username && password, '必须指定隔离服务 URL 和验收账号');
assert(['127.0.0.1', 'localhost'].includes(new URL(origin).hostname), '仅允许本地隔离服务');
const evaluate = async (js) => JSON.parse(await command('eval', `(async()=>JSON.stringify(await (${js})))()`));
async function pointerClick(expression) {
  const point = await evaluate(`(() => { const e=${expression};
    e.scrollIntoView({block:'center',behavior:'instant'});
    const r=e.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2}; })()`);
  await command('cua', 'click', String(point.x), String(point.y));
}
async function act(selector, method, ...args) {
  const tags = 'form:not(.self-test-form) exam-tag-picker:has(.tag-picker-trigger[aria-label="题目标签"])';
  if (selector.startsWith('.tag-picker ')) selector = tags + selector.slice('.tag-picker'.length);
  else if (selector.startsWith('.tag-picker-trigger')) selector = `${tags} ${selector}`;
  const element = `document.querySelector(${JSON.stringify(selector)})`;
  if (method === 'click') return pointerClick(element);
  if (method === 'check') {
    if (!await evaluate(`${element}.checked`)) await pointerClick(`${element}.closest('label')`);
    assert.equal(await evaluate(`${element}.checked`), true);
    return;
  }
  return command('locator', JSON.stringify({
    steps: [{ m: 'locator', args: [selector] }], action: { m: method, args },
  }));
}
const button = (name) => pointerClick(`[...(document.querySelector('.tag-picker-panel:not([hidden])') || document).querySelectorAll('button')].find(e=>e.textContent===${JSON.stringify(name)})`);
async function select(name, value) {
  const index = await evaluate(`(() => { const label=[...document.querySelectorAll('label')]
    .find(e=>e.querySelector(':scope > span')?.textContent===${JSON.stringify(name)});
    return [...document.querySelectorAll('select')].indexOf(label?.querySelector('select')); })()`);
  assert(index >= 0, `缺少筛选控件：${name}`);
  return command('locator', JSON.stringify({ steps: [
    { m: 'locator', args: ['select'] }, { m: 'nth', args: [index] },
  ], action: { m: 'selectOption', args: [value] } }));
}
async function wait(js) {
  for (let n = 0; n < 100; n++) {
    if (await evaluate(js)) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.fail(`等待页面状态超时：${js}`);
}
const state = () => evaluate(`(() => { const p = document.querySelector('.tag-picker-trigger[aria-label="题目标签"]').parentElement; return {
  open: !p.panel.hidden, value: p.value, expanded: p.trigger.getAttribute('aria-expanded'),
  focusTrigger: document.activeElement === p.trigger, focusFirst: document.activeElement === p.inputs[0].input,
}; })()`);
async function layout(expectedWidth) {
  const data = await evaluate(`(() => { const p=document.querySelector('.tag-picker-trigger[aria-label="题目标签"]').parentElement;
    const controls=[p.trigger,...p.querySelectorAll('.tag-picker-option, .tag-picker-actions button')];
    return {width:innerWidth,scroll:document.documentElement.scrollWidth,
      targets:controls.map(e=>{const r=e.getBoundingClientRect();return {w:r.width,h:r.height,x:r.x,right:r.right}})}; })()`);
  assert.equal(data.width, expectedWidth, '必须在请求的真实 CSS 视口宽度验收');
  assert(data.scroll <= expectedWidth, JSON.stringify(data));
  assert(data.targets.every(r=>r.h>=44 && r.w>=44 && r.x>=0 && r.right<=expectedWidth), JSON.stringify(data));
  return data;
}

try {
  await command('goto', origin);
  await wait(`location.origin===${JSON.stringify(origin)} && !!document.querySelector('main')`);
  await evaluate(`(async()=>{await fetch('/api/auth/logout',{method:'POST'});location.hash='/login';return true})()`);
  await command('reload');
  await wait(`!!document.querySelector('input[name="employee_no"]')`);
  assert((await command('read')).includes('用户名'));
  await act('input[name="employee_no"]', 'fill', username);
  await act('input[name="password"]', 'fill', password);
  await act('button[type="submit"]', 'click');
  await wait(`!!document.querySelector('exam-tag-picker') && !document.querySelector('form button[type="submit"]').disabled`);
  const me = await evaluate(`(async()=> (await fetch('/api/auth/me')).json())()`);
  assert.equal(me.authorization, null, '验收账号必须无人员授权');
  const measurements = [];
  for (const width of [320, 390]) {
    await command('viewport', String(width), '844', '--mobile');
    await button('清空筛选');
    await act('.tag-picker-trigger', 'click');
    measurements.push(await layout(width));
    // 全部真实颜色/数据标签选中时摘要很长，也必须在窄屏换行且不溢出。
    const codes = await evaluate(`document.querySelector('.tag-picker-trigger[aria-label="题目标签"]').parentElement.inputs.map(e=>e.code)`);
    for (const code of codes) await act(`.tag-picker input[value="${code}"]`, 'check');
    assert.equal((await state()).value.length, codes.length);
    assert.equal((await state()).open, true);
    await layout(width);
    await button('取消');
    assert.deepEqual((await state()).value, []);
    await act('.tag-picker-trigger', 'click');
    await act('.tag-picker input[value="protected"]', 'check');
    await act('.tag-picker input[value="required"]', 'check');
    assert.deepEqual((await state()).value, ['protected', 'required']);
    assert.equal((await state()).open, true, '勾选必须保持展开');
    assert.equal((await state()).expanded, 'true');
    await button('完成');
    assert.equal((await state()).open, false);
    assert.equal((await state()).focusTrigger, true);
    await act('.tag-picker-trigger', 'click');
    await button('清空');
    assert.deepEqual((await state()).value, []);
    assert.equal((await state()).open, true);
    await button('取消');
    assert.deepEqual((await state()).value, ['protected', 'required']);
    assert.equal((await state()).focusTrigger, true);
    await act('.tag-picker-trigger', 'press', 'ArrowDown');
    assert.equal((await state()).focusFirst, true);
    await command('cua', 'keypress', 'Space');
    assert.deepEqual((await state()).value, ['required']);
    await act('.tag-picker input[value="protected"]', 'press', 'Escape');
    assert.equal((await state()).open, false);
    assert.equal((await state()).focusTrigger, true);
    await act('.tag-picker-trigger', 'click');
    await button('清空');
    await button('完成');
    assert.deepEqual((await state()).value, []);
    await act('.tag-picker-trigger', 'click');
    await act('h1', 'click');
    assert.equal((await state()).open, false, '外部点击必须关闭');
    await act('.tag-picker-trigger', 'press', 'ArrowDown');
    for (let n=0; n<13; n++) await command('cua', 'keypress', 'Tab');
    await wait(`!document.querySelector('.tag-picker-trigger[aria-label="题目标签"]').parentElement.contains(document.activeElement)`);
    assert.equal((await state()).open, false);
    assert.equal((await state()).focusTrigger, false);
    console.log(`PASS ${width}px：多选保持展开、取消回退、清空、完成、Esc、外点、键盘和焦点、44px、无横向溢出`);
  }
  await act('form:not(.self-test-form) select[aria-label="人员题库"]', 'selectOption', '一线人员');
  await act('.tag-picker-trigger', 'click');
  await act('.tag-picker input[value="protected"]', 'check');
  await button('完成');
  const fileId = await evaluate(`document.querySelector('.tag-picker-trigger[aria-label="题目来源"]').parentElement.inputs[0].code`);
  await act('form:not(.self-test-form) .tag-picker-trigger[aria-label="题目来源"]', 'click');
  await act(`form:not(.self-test-form) exam-tag-picker input[value="${fileId}"]`, 'check');
  await button('完成');
  await wait(`!document.querySelector('form [role="status"]').textContent.includes('正在')`);
  const expected = await evaluate(`(async()=>{const url=new URL('/api/practice/filters',location.origin);
    url.searchParams.set('person_category','一线人员');url.searchParams.set('tags','protected');
    url.searchParams.set('source_file_ids',${JSON.stringify(fileId)});return (await (await fetch(url)).json()).total})()`);
  assert((await evaluate(`document.querySelector('form [role="status"]').textContent`))
    .includes(expected ? `匹配 ${expected} 道题` : '没有符合条件'));
  await button('清空筛选');
  await wait(`!!document.querySelector('.self-test-form')`);
  await evaluate(`(()=>{document.querySelector('.self-test-form').closest('details').open=true;return true})()`);
  await act('.self-test-form select[aria-label="人员题库"]', 'selectOption', '一线人员');
  await wait(`!document.querySelector('.self-test-form button[type="submit"]').disabled`);
  // 保留原 100 题纯客观开考/交卷覆盖；首页不再选择模板或受限专业。
  await button('随机组卷并开考');
  await wait(`location.hash.startsWith('#/exam/') && !!document.querySelector('.timer')`);
  assert(!(await command('read')).includes('非正式'));
  const attempt = await evaluate(`(async()=> (await fetch('/api/exams/'+location.hash.split('/').pop())).json())()`);
  assert.equal(attempt.person_category, '一线人员');
  assert.equal(attempt.exam_mode, 'self_service');
  assert.equal(attempt.paper_type, 'mock');
  assert.equal(attempt.questions.length, 100);
  await button('交卷');
  await wait(`!!document.querySelector('.result-summary')`);
  assert.equal(await evaluate(`document.querySelector('.score').textContent`), '0');
  assert(!(await command('read')).includes('非正式'));
  await act('a[href="#/history"]', 'click');
  await wait(`document.body.innerText.includes('模拟考试') && !document.querySelector('.loading')`);
  assert(!(await command('read')).includes('非正式'));
  console.log('PASS 首页选题、100题开考、交卷计分和模拟考试记录');
  console.log(JSON.stringify(measurements));
} finally {
  close();
}
