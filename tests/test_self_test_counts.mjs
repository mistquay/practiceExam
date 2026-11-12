import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import { test } from 'node:test';

// 不改变项目模块配置；直接载入无 DOM 依赖的生产模块。
const source = await readFile(new URL('../static/components/self-test-counts.js', import.meta.url), 'utf8');
const { ratioCounts, countsToPercentages, validateCounts } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('100 题按 80/0/10/10 分配', () => {
  assert.deepEqual(ratioCounts(100, [80,0,10,10]), [80,0,10,10]);
});
test('三题四型均分，并列按固定顺序', () => {
  assert.deepEqual(ratioCounts(3, [25,25,25,25]), [1,1,1,0]);
});
test('非法总量和比例拒绝', () => {
  for (const total of [0,-1,201,1.5,'',NaN,Infinity]) assert.throws(() => ratioCounts(total, [25,25,25,25]));
  for (const ratios of [[80,0,10,9],[0,0,0,0],[-1,1,50,50],[101,0,0,-1],[25.5,24.5,25,25],['',0,50,50],[25,25,50]])
    assert.throws(() => ratioCounts(100, ratios));
});
test('数量校验和数量转比例', () => {
  assert.equal(validateCounts([80,0,10,10]), true);
  for (const counts of [[0,0,0,0],[-1,0,1,1],['',0,1,1],[201,0,0,0],[0.5,0,0,0]]) assert.equal(validateCounts(counts), false);
  assert.deepEqual(countsToPercentages([1,1,1,0]), [34,33,33,0]);
});
test('所有整数百分比组合在 1/3/100/200 题下精确守恒，零比例保持零', () => {
  for (let a=0; a<=100; a++) for (let b=0; b<=100-a; b++) for (let c=0; c<=100-a-b; c++) {
    const weights = [a,b,c,100-a-b-c];
    for (const total of [1,3,100,200]) {
      const result = ratioCounts(total, weights);
      assert.equal(result.reduce((s,v) => s+v, 0), total);
      result.forEach((v,i) => {
        assert(Number.isInteger(v) && v >= 0);
        assert(v === Math.floor(total*weights[i]/100) || v === Math.ceil(total*weights[i]/100));
        if (!weights[i]) assert.equal(v,0);
      });
    }
  }
});
