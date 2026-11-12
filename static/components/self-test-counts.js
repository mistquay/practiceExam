// 固定题型顺序也是最大余数相同时的补题顺序。
export const TYPES = ["单选题", "多选题", "判断题", "简答题"];

function apportion(total, weights, divisor) {
  const counts = weights.map(w => Math.floor(total * w / divisor));
  const order = weights.map((w, i) => ({ i, remainder: total * w % divisor }))
    .sort((a, b) => b.remainder - a.remainder || a.i - b.i);
  const left = total - counts.reduce((a, b) => a + b, 0);
  order.slice(0, left).forEach(({ i }) => { counts[i]++; });
  return counts;
}

function integers(values, max) {
  return values.length === TYPES.length && values.every(v =>
    String(v).trim() !== "" && Number.isInteger(Number(v)) && Number(v) >= 0 && Number(v) <= max);
}

export function validateCounts(values) {
  const total = values.reduce((sum, v) => sum + Number(v), 0);
  return integers(values, 200) && total >= 1 && total <= 200;
}

export function ratioCounts(total, percentages) {
  if (String(total).trim() === "" || !Number.isInteger(Number(total)) || Number(total) < 1 || Number(total) > 200)
    throw new Error("总题量须为 1~200 的整数。");
  if (!integers(percentages, 100)) throw new Error("各题型比例须为 0~100 的整数百分比。");
  const weights = percentages.map(Number);
  if (weights.reduce((a, b) => a + b, 0) !== 100) throw new Error("四种题型比例之和须精确为 100%。");
  return apportion(Number(total), weights, 100);
}

export function countsToPercentages(values) {
  if (!validateCounts(values)) throw new Error("请填写总计 1~200 道题的非负整数数量。");
  return apportion(100, values.map(Number), values.reduce((sum, v) => sum + Number(v), 0));
}
