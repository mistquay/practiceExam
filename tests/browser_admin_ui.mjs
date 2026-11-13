// 真实浏览器内运行；仅用于已登录管理员的本机隔离服务。
// 调用 checkAdminUi()；抛错即失败，不以配置分支代替实际 DOM 验证。
export async function checkAdminUi() {
  if (!['127.0.0.1', 'localhost'].includes(location.hostname)) throw Error('仅允许本机隔离服务');
  const assert = (ok, message) => { if (!ok) throw Error(message); };
  async function waitFor(predicate) {
    for (let n = 0; n < 100; n++) {
      if (predicate()) return;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw Error('用户管理未渲染完成');
  }
  location.hash = '/';
  await waitFor(() => !!document.querySelector('.self-test-form'));
  const measurements = [];
  for (const path of ['/admin/users', '/admin/auth', '/admin/auth?user=1', '/admin/unknown']) {
    const previous = document.querySelector('.view');
    location.hash = path;
    await waitFor(() => document.querySelector('.view') !== previous
      && document.querySelector('.tabs .active')?.textContent === '用户'
      && document.body.innerText.includes('free_user'));
    const text = document.getElementById('app').innerText;
    const links = [...document.querySelectorAll('a')].map(e => e.getAttribute('href'));
    const buttons = [...document.querySelectorAll('main button')].map(e => e.textContent);
    const headers = [...document.querySelectorAll('th')].map(e => e.textContent);
    const forms = [...document.querySelectorAll('main form')];
    const count = (text.match(/授权/g) || []).length;
    assert(count === 0, `${path} 仍展示授权文案`);
    assert(!links.some(href => href?.includes('/admin/auth')), '存在旧授权入口');
    assert(!headers.includes('人员类别'), '仍将人员类别展示为固定身份');
    assert(!text.includes('单独录入'), '仍有旧导入提示');
    assert(forms.length === 1 && forms[0].querySelector('input[placeholder="用户名 / 姓名"]'), '存在多余表单');
    assert(buttons.includes('重置密码') && buttons.includes('停用'), '账号管理操作丢失');
    assert(text.includes('role 取 admin / examinee'), '角色导入说明丢失');
    assert(!text.includes('不作为账号固定身份'), '仍有账号身份解释文案');
    assert(!performance.getEntriesByType('resource').some(e => /authorizations/.test(e.name)), '曾加载旧授权资源或 API');
    measurements.push({ hash: location.hash, authorizationTextCount: count, headers, buttons, formCount: forms.length });
  }
  const selection = await import('/static/state/selection.js');
  selection.saveSelection({ bankId: '1', specialtyCode: 'pd_live' });
  location.hash = '/wrong';
  await waitFor(() => [...document.querySelectorAll('button')].some(e => e.textContent === '练习错题'));
  [...document.querySelectorAll('button')].find(e => e.textContent === '练习错题').click();
  assert(location.hash.startsWith('#/practice?'), '错题练习未跳转');
  assert(new URLSearchParams(location.hash.split('?')[1]).get('person') === '', '错题练习仍绑定历史授权类别');
  location.hash = '/admin/users';
  await waitFor(() => document.body.innerText.includes('free_user'));
  return { result: 'PASS', width: innerWidth, measurements, wrongPracticePerson: '' };
}
