// browser_self_service.mjs 的零依赖 Chrome CDP 适配器；持久会话保留移动端仿真与原生弹窗处理。
import assert from 'node:assert/strict';
const endpoint = process.env.EXAM_BROWSER_CDP;
assert(endpoint?.startsWith('ws://127.0.0.1:'), '只允许本机隔离 Chrome 的 page WebSocket');
const ws = new WebSocket(endpoint);
await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
let id = 0;
let mobileViewport = false;
const pending = new Map();
function cdp(method, params = {}) {
  const key = ++id;
  return new Promise((resolve, reject) => {
    pending.set(key, { resolve, reject });
    ws.send(JSON.stringify({ id: key, method, params }));
  });
}
ws.onmessage = ({ data }) => {
  const msg = JSON.parse(data);
  if (msg.method === 'Page.javascriptDialogOpening') {
    cdp('Page.handleJavaScriptDialog', { accept: true }).catch((e) => console.error(e));
  }
  const task = pending.get(msg.id);
  if (task) {
    pending.delete(msg.id);
    if (msg.error) task.reject(new Error(JSON.stringify(msg.error)));
    else task.resolve(msg.result);
  }
};
async function evaluate(expression) {
  const result = await cdp('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
async function keypress(key) {
  const keys = { Space: [' ', 'Space', 32], Tab: ['Tab', 'Tab', 9],
    Escape: ['Escape', 'Escape', 27], ArrowDown: ['ArrowDown', 'ArrowDown', 40], Enter: ['Enter', 'Enter', 13] };
  assert(keys[key], `未实现按键 ${key}`);
  const [value, code, virtual] = keys[key];
  const params = { key: value, code, windowsVirtualKeyCode: virtual, nativeVirtualKeyCode: virtual };
  await cdp('Input.dispatchKeyEvent', { ...params, type: 'keyDown', ...(key === 'Space' ? { text: ' ' } : {}) });
  await cdp('Input.dispatchKeyEvent', { ...params, type: 'keyUp' });
}
export async function command(command, ...args) {
  await cdp('Page.enable');
  let result = true;
  if (command === 'eval') result = await evaluate(args[0]);
  else if (command === 'goto') await cdp('Page.navigate', { url: args[0] });
  else if (command === 'reload') await cdp('Page.reload', { ignoreCache: true });
  else if (command === 'viewport') {
    mobileViewport = args.includes('--mobile');
    await cdp('Emulation.setDeviceMetricsOverride', { width: Number(args[0]), height: Number(args[1]), deviceScaleFactor: 1, mobile: args.includes('--mobile') });
    await cdp('Emulation.setTouchEmulationEnabled', { enabled: args.includes('--mobile') });
  } else if (command === 'read') result = await evaluate('document.body.innerText');
  else if (command === 'dialog') { /* Page.enable 的事件处理器在每条命令期间接受真实原生确认框。 */ }
  else if (command === 'cua' && args[0] === 'click') {
    const coords = { x: Number(args[1]), y: Number(args[2]), button: 'left', clickCount: 1 };
    if (mobileViewport) {
      await cdp('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: coords.x, y: coords.y }] });
      await cdp('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    } else {
      await cdp('Input.dispatchMouseEvent', { ...coords, type: 'mousePressed' });
      await cdp('Input.dispatchMouseEvent', { ...coords, type: 'mouseReleased' });
    }
  } else if (command === 'cua' && args[0] === 'keypress') await keypress(args[1]);
  else if (command === 'locator') {
    const { steps, action } = JSON.parse(args[0]);
    assert.equal(steps[0].m, 'locator');
    const selector = JSON.stringify(steps[0].args[0]);
    const index = steps[1]?.m === 'nth' ? Number(steps[1].args[0]) : 0;
    const element = `document.querySelectorAll(${selector})[${index}]`;
    assert(await evaluate(`!!${element}`), `未找到 ${selector}`);
    if (action.m === 'press') {
      await evaluate(`${element}.focus()`);
      await keypress(action.args[0]);
    } else if (action.m === 'fill') {
      await evaluate(`(() => { const e=${element}; e.focus(); e.value=''; return true; })()`);
      await cdp('Input.insertText', { text: action.args[0] });
      await evaluate(`${element}.dispatchEvent(new Event('input',{bubbles:true}))`);
    } else if (action.m === 'selectOption') {
      result = await evaluate(`(() => { const e=${element}; e.value=${JSON.stringify(action.args[0])};
        e.dispatchEvent(new Event('input',{bubbles:true})); e.dispatchEvent(new Event('change',{bubbles:true})); return e.value; })()`);
      assert.equal(result, action.args[0]);
    } else throw new Error(`未实现 locator 动作 ${action.m}`);
  } else throw new Error(`未实现 CDP 命令 ${command}`);
  return typeof result === 'string' ? result : JSON.stringify(result);
}
export function close() { ws.close(); }
