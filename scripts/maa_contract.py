"""用**真正的 maa-fw** 检查一条轨迹能不能跑。

为什么单独有这么一层：轨迹要喂给两个 runtime，而这条边此前是**单边声称**的 ——
我们照着 maa-fw 的规则在自己这边写镜像测试，从没让 maa-fw 真的加载过产物。
测试文件自己都写着「坏了只有 maa-fw 那边会炸，而那时候已经太晚了」。

实测证明这种担心不是多余的。拿 edr-wd 的桌面轨迹喂给 maa-fw：

    MaaNodeRunner._coerce_node  成功 4/5 个节点          ← 看着像通过了
    但每个节点都变成 DirectHit + DoNothing              ← 加载了，什么都不做
    next: "step-0002" → ['s','t','e','p','-','0','0','0','2']   ← 按字符拆开了

**加载成功不等于能跑。** 它会跑、会报成功、什么都不做 —— 正是可信度评估里
最坏的那一类（silent-pass）。所以这里检查的不是「能不能加载」，
而是「加载出来的东西还是不是原来那件事」。

maa-fw 不在就抛 MaaFwUnavailable，由调用方决定跳过还是失败。
位置用 MAA_FW_HOME 覆盖，默认找同级目录。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import trace_schema as ts

DEFAULT_HOME = Path(__file__).resolve().parent.parent.parent / "maa-fw"

# 能安全互通的动作集合在 trace_schema 里（唯一定义处）。
EXECUTABLE_ACTIONS = ts.MAA_ACTIONS

# 这一步本来就不产生动作：断言节点靠识别成功本身下结论。
INERT_BY_DESIGN = "DoNothing"


class MaaFwUnavailable(RuntimeError):
    """本机没有 maa-fw。这不是轨迹的问题。"""


def maa_fw_home() -> Path:
    home = Path(os.environ.get("MAA_FW_HOME") or DEFAULT_HOME).expanduser()
    if not (home / "agent" / "maa_node.py").exists():
        raise MaaFwUnavailable(f"在 {home} 下没找到 agent/maa_node.py")
    return home


def _runner():
    home = maa_fw_home()
    if str(home) not in sys.path:
        sys.path.insert(0, str(home))
    try:
        from agent.maa_node import MaaNodeRunner
    except Exception as error:                       # 依赖不全也算「不可用」
        raise MaaFwUnavailable(f"导入 maa-fw 失败：{type(error).__name__}: {error}")
    return MaaNodeRunner


def traversal_problems(trace: dict[str, Any]) -> list[str]:
    """让 maa-fw **真的把链条走一遍**，看顺序对不对、动作到没到执行器。

    `contract_problems` 里逐个 coerce 只证明「节点读得懂」。读得懂不等于走得通：
    `$meta` 不惰性会被当成一步执行、`next` 指错会让链条断在中间、动作丢了会
    让执行器收到 DoNothing —— 这三件事 coerce 全都看不出来。

    识别器和执行器都是桩：识别一律命中，执行一律成功并记下收到的动作。
    这样跑的是**节点图本身**，不碰任何界面。
    """
    runner_cls = _runner()
    from src.atomic.base import ActionResult          # maa-fw 的返回类型

    seen: list[str] = []
    got_actions: list[str] = []

    def recognizer(recognition, image, roi):
        return True                                   # 一律命中，只测图不测识别

    def executor(action, match, roi):
        got_actions.append(action.type)
        return ActionResult(action_name=action.type, params=dict(action.param),
                            success=True)

    runner = runner_cls(dict(trace), recognizer=recognizer, executor=executor)
    problems: list[str] = []

    # 不给 start_nodes：maa-fw 会把**整张表**排队，$meta 也在里面。
    # 它的 DirectHit 无条件命中，不惰性的话第一个跑的就是它。
    result = runner.run()
    guard = len(trace) + 5
    while result.ok and result.node_name and guard > 0:
        guard -= 1
        if result.node_name in seen:
            problems.append(f"链条走回头路：{result.node_name} 被走了两次")
            break
        seen.append(result.node_name)
        if not result.next_nodes:
            break
        result = runner.run(start_nodes=list(result.next_nodes))

    if ts.META_KEY in seen:
        problems.append(f"{ts.META_KEY} 被执行了 —— 它必须惰性，否则会被当成一个步骤")

    # $meta 的风险场景是「回放到一个完全不认识的界面」：前面的节点都匹配不上，
    # maa-fw 的 run() 沿队列一路 fallthrough，而 $meta 的 DirectHit **无条件命中**
    # —— 于是它被当成一步执行，还报成功。
    #
    # 顺着走一遍查不出这件事（第一步就成功返回，$meta 根本没轮到），
    # 靠「让别的节点都匹配不上」也不可靠（轨迹里本来就可能有别的 DirectHit 节点，
    # 比如断言步骤，它们会先被落到）。所以直接问：单独执行 $meta 会怎样。
    # 惰性的话 _should_skip_node 必须把它跳过。
    if ts.META_KEY in trace:
        alone = runner_cls(dict(trace), recognizer=recognizer, executor=executor).run(
            start_nodes=[ts.META_KEY])
        if alone.ok and alone.node_name == ts.META_KEY:
            problems.append(
                f"{ts.META_KEY} 不惰性：maa-fw 会把它当成一个步骤执行并报成功")

    expected = ts.meta(trace).get("nodeOrder") or []
    walked = [n for n in seen if n != ts.META_KEY]
    if expected and walked != expected:
        problems.append(
            f"走过的顺序和 nodeOrder 不一致：走了 {len(walked)} 步，记的是 {len(expected)} 步"
            f"（首个分歧：{next((a for a, b in zip(walked + [None], expected + [None]) if a != b), None)}）")

    # 执行器**实际收到**的动作，必须和文件里写的逐个对上。
    # 拿变异后的轨迹和它自己比是循环论证：动作被改成 DoNothing 时，
    # 「本该不做事的节点」也跟着 +1，永远发现不了。
    # 要比的是「文件怎么说」和「maa-fw 看到什么」—— coerce 会悄悄改写的正是这里。
    declared = [(trace.get(name) or {}).get("action", {}).get("type") for name in seen]
    if declared != got_actions:
        mismatch = next(
            ((n, d, g) for n, d, g in zip(seen, declared, got_actions) if d != g), None)
        problems.append(
            f"执行器收到的动作和文件写的不一致：{mismatch}（节点、文件写的、实际收到）"
            if mismatch else
            f"执行器收到 {len(got_actions)} 个动作，走过 {len(seen)} 个节点")
    return problems


def contract_problems(trace: dict[str, Any]) -> list[str]:
    """让 maa-fw 真的把每个节点 coerce 一遍，返回问题清单（空 = 没问题）。"""
    runner = _runner()
    problems: list[str] = []
    known = set(trace)

    for name, node in ts.nodes(trace):
        try:
            coerced = runner._coerce_node(name, node)
        except Exception as error:
            problems.append(f"{name}: maa-fw 加载失败 {type(error).__name__}: {error}")
            continue

        action = getattr(coerced.action, "type", None)
        declared = (ts.attach(node).get("gui_target") or {}).get("source_action") or ""
        if action == INERT_BY_DESIGN and declared:
            # 录制时明明做了一个动作，编译出来却什么都不做 —— 静默丢失，
            # 回放会报成功而不做事，正是最难查的那一类
            problems.append(
                f"{name}: 录制的是 {declared!r}，maa-fw 加载出来是 DoNothing（动作被丢了）")
        elif action not in EXECUTABLE_ACTIONS:
            # 标了 web-only 的是**已知**只有我们自己的回放器懂，不算问题；
            # 没标的才是问题 —— 那意味着没人知道桌面侧会拿它怎么办。
            scope = ts.provenance(node).get("actionScope")
            if not scope:
                problems.append(
                    f"{name}: 动作 {action!r} 不在 maa-fw 能执行的集合里，"
                    f"且没标 actionScope")

        nxt = coerced.next
        if not isinstance(nxt, list):
            problems.append(f"{name}: next 不是列表（{type(nxt).__name__}）")
        elif len(nxt) > 1 and all(len(x) == 1 for x in nxt):
            # "step-0002" 被当成可迭代对象按字符拆开的典型形状
            problems.append(f"{name}: next 被按字符拆开了 —— {nxt[:6]}…")
        else:
            missing = [x for x in nxt if x not in known]
            if missing:
                problems.append(f"{name}: next 指向不存在的节点 {missing}")

    problems.extend(traversal_problems(trace))

    meta = trace.get(ts.META_KEY)
    if meta is None:
        problems.append(f"缺少 {ts.META_KEY} 节点")
    else:
        # $meta 的 DirectHit 无条件命中；不惰性的话 MaaNodeRunner.run 会把它排队执行
        stats = (ts.attach(meta).get("stats") or {})
        if meta.get("max_hit") != 1 or stats.get("hit_count") != 1:
            problems.append(
                f"{ts.META_KEY}: 不是惰性的（max_hit={meta.get('max_hit')} "
                f"hit_count={stats.get('hit_count')}）—— maa-fw 会把它当成一个步骤执行")
    return problems
