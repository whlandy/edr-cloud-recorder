"""
云端 + 端侧交替编排

核心难点不是"怎么调两边"，而是**两边之间的时间差**：

  云端接口返回 200 只代表服务端记下了配置。终端上的 Agent 要等下一次心跳才拉到
  新策略，中间几秒到几分钟不等。而云侧接口**未必暴露这个进度** —— 实测过一个产品，
  一次真实下发前后，策略详情和资产树里的 enableStatus 都纹丝不动，
  根本没有可用的云侧收敛信号。

  所以"等生效"只能**朝端侧轮询**：反复读终端上的界面或日志，直到它反映出新配置。
  用固定 sleep 是错的：短了必然偶发失败，长了每个场景白等几分钟。

这个模块提供三种步骤和一个执行器，把上面这条约束固化下来，
让写场景的人不必每次重新想一遍。
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Callable

try:
    from .endpoint import EndpointError
except ImportError:                 # 允许被当作脚本直接 import
    from endpoint import EndpointError


@dataclass
class StepResult:
    name: str
    side: str            # cloud | endpoint | sync
    ok: bool
    seconds: float
    detail: str = ""


@dataclass
class Scenario:
    """一串交替的步骤。执行到第一个失败就停，并把已完成的步骤报出来。"""

    name: str
    steps: list[tuple[str, str, Callable]] = field(default_factory=list)
    cleanup_steps: list[tuple[str, str, Callable]] = field(default_factory=list)
    results: list[StepResult] = field(default_factory=list)

    # ---------- 声明 ----------

    def cloud(self, name: str, fn: Callable) -> "Scenario":
        self.steps.append((name, "cloud", fn))
        return self

    def endpoint(self, name: str, fn: Callable) -> "Scenario":
        self.steps.append((name, "endpoint", fn))
        return self

    def cleanup(self, name: str, fn: Callable, *, side: str = "cloud") -> "Scenario":
        """注册始终执行的清理步骤；清理失败会让场景失败。"""
        if side not in {"cloud", "endpoint"}:
            raise ValueError("cleanup side 必须是 cloud 或 endpoint")
        self.cleanup_steps.append((name, side, fn))
        return self

    def until(self, name: str, probe: Callable[[], bool],
              timeout: float = 300.0, interval: float = 10.0) -> "Scenario":
        """
        轮询直到 probe() 为真 —— 这是云端到端侧那段时间差的唯一正确等法。

        interval 按被观测对象的更新节奏选：策略下发这类心跳驱动的用 10s 起步，
        立即生效的操作用 5s 就够。1s 一次通常只是徒增负载。
        """
        def _wait():
            deadline = time.time() + timeout
            n = 0
            while time.time() < deadline:
                n += 1
                if probe():
                    return f"第 {n} 次探测命中"
                time.sleep(interval)
            raise TimeoutError(f"{timeout:.0f}s 内未等到（探测 {n} 次）")

        self.steps.append((name, "sync", _wait))
        return self

    # ---------- 执行 ----------

    def run(self, stop_on_fail: bool = True) -> bool:
        print(f"\n=== {self.name} ===")
        self.results.clear()
        try:
            for index, (label, side, fn) in enumerate(self.steps):
                r = self._execute(label, side, fn)
                self.results.append(r)
                if not r.ok and stop_on_fail:
                    print(f"  —— 在第 {index + 1} 步中断，后面 "
                          f"{len(self.steps) - index - 1} 步未执行")
                    break
        finally:
            for label, side, fn in self.cleanup_steps:
                self.results.append(self._execute(f"清理：{label}", side, fn))
            self._summary()
        return all(result.ok for result in self.results)

    def execution(self, evidence: dict | None = None) -> dict:
        """Serialize one cloud/endpoint run without losing side attribution."""
        passed = bool(self.results) and all(result.ok for result in self.results)
        return {
            "schema": "edr.end-cloud-execution/v1",
            "name": self.name,
            "status": "success" if passed else "failed",
            "steps": [asdict(result) for result in self.results],
            "evidence": evidence or {},
        }

    @staticmethod
    def _execute(label: str, side: str, fn: Callable) -> StepResult:
        tag = {"cloud": "云", "endpoint": "端", "sync": "等"}[side]
        t0 = time.time()
        try:
            detail = fn()
            result = StepResult(label, side, True, time.time() - t0, str(detail or ""))
            print(f"  [{tag}] {label:<34} ✅ {result.seconds:5.1f}s  {result.detail[:60]}")
        except Exception as e:
            result = StepResult(
                label, side, False, time.time() - t0, f"{type(e).__name__}: {e}"
            )
            print(f"  [{tag}] {label:<34} ❌ {result.seconds:5.1f}s  {result.detail[:100]}")
            if isinstance(e, EndpointError):
                print("       ↑ 端侧失败：先确认目标机器上的 MCP 服务和被测程序都在")
        return result

    def _summary(self) -> None:
        ok = sum(1 for r in self.results if r.ok)
        secs = sum(r.seconds for r in self.results)
        print(f"  —— {ok}/{len(self.results)} 步通过，用时 {secs:.1f}s")
        # 点出最慢的一步：交替场景里它几乎总是那个"等生效"，
        # 是判断轮询间隔和超时是否需要调整的依据
        if self.results:
            slow = max(self.results, key=lambda r: r.seconds)
            if slow.seconds > 5:
                print(f"     最慢：{slow.name}（{slow.seconds:.1f}s）")
