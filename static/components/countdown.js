// 服务端时钟倒计时：剩余时间 = deadline_at - server_now - 单调时钟流逝量。
// 使用 performance.now()（单调时钟），修改本机系统时间不影响剩余时间；
// 页面重新可见 / 网络恢复时由调用方 resync() 重新向服务端对时。

export function createCountdown({ onTick, onExpire }) {
  let remainingAtSync = null;
  let syncedAt = 0;
  let timer = null;
  let expired = false;

  const remainingMs = () =>
    remainingAtSync === null ? null : Math.max(0, remainingAtSync - (performance.now() - syncedAt));

  function tick() {
    const ms = remainingMs();
    if (ms === null) return;
    onTick(ms);
    if (ms <= 0 && !expired) {
      expired = true;
      stop();
      onExpire();
    }
  }

  function sync(deadlineAt, serverNow) {
    const deadline = Date.parse(deadlineAt);
    const now = Date.parse(serverNow);
    if (Number.isNaN(deadline) || Number.isNaN(now)) {
      throw new Error("服务端未返回有效的 deadline_at / server_now，无法计时");
    }
    remainingAtSync = deadline - now;
    syncedAt = performance.now();
    if (!timer && !expired) timer = setInterval(tick, 500);
    tick();
  }

  function stop() {
    if (timer) clearInterval(timer);
    timer = null;
  }

  return { sync, stop, remainingMs };
}

export function fmtRemaining(ms) {
  const total = Math.ceil(ms / 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(Math.floor(total / 3600))}:${p(Math.floor((total % 3600) / 60))}:${p(total % 60)}`;
}
