// 真实独立 Chrome CDP：自由组卷表单 + 简答交卷、自评与历史。
// 环境变量与 browser_self_service.mjs 相同，仅允许本机隔离服务。
import assert from 'node:assert/strict';
import { command, close } from './browser_cdp.mjs';
import { checkSelfTestModes } from './browser_self_test_modes.mjs';
const origin = process.env.EXAM_BROWSER_URL;
assert(origin && ['127.0.0.1','localhost'].includes(new URL(origin).hostname));
const evaluate = async js => JSON.parse(await command('eval', `(async()=>JSON.stringify(await (${js})))()`));
async function wait(js) {
  for(let i=0;i<100;i++) { if(await evaluate(js)) return; await new Promise(r=>setTimeout(r,100)); }
  assert.fail(`等待超时：${js}`);
}
async function click(selector) {
  const p = await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)throw Error('missing element');e.scrollIntoView({block:'center',behavior:'instant'});const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
  await command('cua','click',String(p.x),String(p.y));
}
const fill = (selector,value,index=0) => command('locator',JSON.stringify({steps:[{m:'locator',args:[selector]},{m:'nth',args:[index]}],action:{m:'fill',args:[String(value)]}}));
const select = (value,index=0) => command('locator',JSON.stringify({steps:[{m:'locator',args:['.self-test-form select']},{m:'nth',args:[index]}],action:{m:'selectOption',args:[value]}}));
const nums='.self-test-form input[type=number]:not(:disabled)';
const measurements=[];
try {
  await command('goto',origin);
  await wait(`!!document.querySelector('main')`);
  await evaluate(`(async()=>{await fetch('/api/auth/logout',{method:'POST'});location.hash='/login';return true})()`);
  await command('reload');
  await wait(`!!document.querySelector('input[name=employee_no]')`);
  await fill('input[name=employee_no]',process.env.EXAM_BROWSER_USER);
  await fill('input[name=password]',process.env.EXAM_BROWSER_PASSWORD);
  await click('button[type=submit]');
  await wait(`!!document.querySelector('.self-test-form') && !document.querySelector('.self-test-form button[type=submit]').disabled`);
  for(const width of [1280,320,390]) {
    await command('viewport',String(width),'900',...(width<500?['--mobile']:[]));
    await select('领导人员');
    await wait(`!document.querySelector('.self-test-form button[type=submit]').disabled`);
    await click('.self-test-form > button[type=button]');
    assert.deepEqual(await evaluate(`[...document.querySelectorAll('${nums}')].map(e=>Number(e.value))`),[80,0,10,10,60]);
    assert.equal((await evaluate(`(${checkSelfTestModes.toString()})()`)).result, 'PASS');
    // 标签菜单实际打开与关闭，保留手机下拉组件交互。
    await click('.self-test-form .tag-picker-trigger');
    await wait(`!document.querySelector('.self-test-form exam-tag-picker').panel.hidden`);
    const m=await evaluate(`(()=>{const f=document.querySelector('.self-test-form');return {width:innerWidth,scroll:document.documentElement.scrollWidth,overflow:[...f.querySelectorAll('input,select,button')].filter(e=>{const r=e.getBoundingClientRect();return r.width && (r.x<0||r.right>innerWidth)}).map(e=>e.tagName)}})()`);
    assert.equal(m.width,width);assert(m.scroll<=width,JSON.stringify(m));assert.deepEqual(m.overflow,[]);
    measurements.push(m);
    await click('.self-test-form .tag-picker-trigger');
    await fill(nums,200,3);
    await wait(`document.querySelector('.self-test-form button[type=submit]').disabled && document.querySelector('.self-test-form').innerText.includes('不足')`);
    await fill(nums,10,3);
    await select('专业管理人员');
    await wait(`!document.querySelector('.self-test-form button[type=submit]').disabled`);
  }
  // 三题含简答，精度也能在实际 UI 中核对。
  for(const [i,n] of [1,0,1,1].entries()) await fill(nums,n,i);
  await click('.self-test-form button[type=submit]');
  await wait(`!!document.querySelector('textarea') && location.hash.startsWith('#/exam/')`);
  const aid=await evaluate(`location.hash.split('/').pop()`);
  assert.equal(await evaluate(`document.querySelectorAll('.question-card').length`),3);
  assert.equal(await evaluate(`(async()=> (await fetch('/api/self-tests/${aid}/review')).status)()`),409);
  await fill('textarea','这是我的真实浏览器简答作答，用于对照参考答案后自评。');
  await click('.q-option input');
  await command('reload');
  await wait(`!!document.querySelector('textarea')`);
  assert((await evaluate(`document.querySelector('textarea').value`)).includes('真实浏览器'));
  await click('.exam-bar button');
  await wait(`document.body.innerText.includes('待自评：') && document.body.innerText.includes('参考答案：')`);
  assert((await evaluate(`document.body.innerText`)).includes('尚无最终总成绩'));
  await command('reload');
  await wait(`!!document.querySelector('input[step="0.01"]')`);
  await fill('input[step="0.01"]',12.34);
  await click('form button[type=submit]');
  await wait(`document.body.innerText.includes('已确认 12.34') && !document.body.innerText.includes('待自评：')`);
  const final=await evaluate(`(async()=> (await fetch('/api/exams/${aid}')).json())()`);
  assert.equal(final.status,'graded');assert.equal(final.total_score,100);assert(final.score>=12.34&&final.score<=45.68);
  await command('goto',origin+'/#/history');
  await wait(`document.body.innerText.includes('自评完成（非正式）')`);
  assert((await evaluate(`document.body.innerText`)).includes('自主模拟（非正式成绩）'));
  console.log(JSON.stringify({result:'PASS',measurements,attempt_id:aid,final_score:final.score,flow:'无授权→领导/管理筛选→混合三题→刷新草稿→交卷→刷新→自评12.34→历史'},null,2));
} finally { close(); }
