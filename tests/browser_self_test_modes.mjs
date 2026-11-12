// 在已经加载且库存查询完成的真实自由组卷表单中运行；无 Node/测试框架依赖。
export function checkSelfTestModes() {
  const form = document.querySelector('.self-test-form');
  const mode = form.querySelector('[aria-label="组卷模式"]');
  const start = form.querySelector('button[type=submit]');
  const assert = (ok, message) => { if (!ok) throw new Error(message); };
  const change = value => { mode.value = value; mode.dispatchEvent(new Event('change', {bubbles:true})); };
  const set = (input, value) => { input.value = value; input.dispatchEvent(new Event('input', {bubbles:true})); };
  const quantity = [...form.querySelectorAll('input[type=number]')].slice(0,4);
  const original = quantity.map(input => input.value);
  const ratios = [...form.querySelectorAll('input[aria-label$="百分比"]')];
  const total = form.querySelector('input[max="200"][min="1"]');
  const actual = () => ratios.map(input => Number(input.nextElementSibling.textContent.match(/实际 (\d+) 题/)?.[1]));
  change('ratio');
  set(total,100);
  [80,0,10,10].forEach((v,i) => set(ratios[i],v));
  assert(JSON.stringify(actual()) === '[80,0,10,10]', '100题80/0/10/10实际数');
  set(total,3);
  ratios.forEach(input => set(input,25));
  assert(JSON.stringify(actual()) === '[1,1,1,0]', '三题均分固定并列');
  assert(!quantity[0].getBoundingClientRect().width && quantity[0].disabled, '比例时数量字段隐藏且不参与表单校验');
  set(ratios[3],24);
  assert(start.disabled && form.innerText.includes('精确为 100%'), '比例和不等于100禁用');
  set(ratios[3],25);
  set(total,0);
  assert(start.disabled && form.innerText.includes('1~200'), '零总量拒绝');
  set(total,3);
  set(ratios[0],-1);
  assert(start.disabled && form.innerText.includes('0~100'), '负比例拒绝');
  set(ratios[0],25);
  change('quantity');
  assert(quantity.map(input => input.value).join() === '1,1,1,0', '切回数量保留实际题数');
  set(quantity[0],2);
  assert(quantity[0].value === '2' && !quantity[0].disabled, '切回数量可直接编辑');
  change('ratio');
  assert(total.value === '4', '数量调整后进入比例保留总题量');
  change('quantity');
  original.forEach((value,i) => set(quantity[i],value));
  assert(form.querySelector('input[min="5"]').value === '60', '切换不改变时长');
  const overflow = [...form.querySelectorAll('input,select,button')].filter(input => {
    const r = input.getBoundingClientRect();
    return r.width && (r.x < 0 || r.right > innerWidth);
  });
  assert(!overflow.length && document.documentElement.scrollWidth <= innerWidth, '数量模式无横向溢出');
  change('ratio');
  const ratioOverflow = [...form.querySelectorAll('input,select,button')].filter(input => {
    const r = input.getBoundingClientRect();
    return r.width && (r.x < 0 || r.right > innerWidth);
  });
  assert(!ratioOverflow.length && document.documentElement.scrollWidth <= innerWidth, '比例模式无横向溢出');
  change('quantity');
  original.forEach((value,i) => set(quantity[i],value));
  return {result:'PASS', width:innerWidth, scrollWidth:document.documentElement.scrollWidth};
}
