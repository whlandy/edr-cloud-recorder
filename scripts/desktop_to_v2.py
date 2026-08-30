#!/usr/bin/env python3
"""把 edr-wd 的桌面黄金轨迹导出成 v2 节点表。

**为什么是导出而不是改格式**：edr-wd 的 `edr.desktop-golden-trace/v1` 和它的
严格校验器、JSON schema、回放引擎、pytest 生成器绑在一起。改它的原生格式等于
动地基。而 web 侧本来就是「原始录制 → maa 形状的轨迹」两份产物 —— 桌面侧照做即可。

**为什么放在这个仓库**：v2 的形状定义只在 `trace_schema.py` 一处。导出器必须
调它的 `build_node`，否则就是第二份会漂移的形状定义。

不这么做的代价是实测过的：edr-wd 的原生轨迹直接喂给 maa-fw，5 个节点 coerce
成功 4 个，但每个都变成 DirectHit + DoNothing，`next` 的 "step-0002" 被按字符
拆成 ['s','t','e','p',…]。**加载成功、能跑、什么都不做**，两边都不报错。

    python3 scripts/desktop_to_v2.py <edr-wd 录制目录> [-o trace.v2.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from trace_schema import (                                     # noqa: E402
    FULL_FRAME_ROI,
    MAA_ACTIONS,
    TEMPLATE_METHOD,
    VERIFY_SCOPE_DESKTOP,
    build_node,
    build_trace,
)

DESKTOP_SCHEMA = "edr.desktop-golden-trace/v1"
OCR_THRESHOLD = 0.3

# edr-wd 的动作 → maa-fw 的动作。左边这些是实测语料里出现过的全部。
ACTIONS = {
    "gui.click": "Click",
    "pointer.right_click": "RightClick",
    "pointer.double_click": "DoubleClick",
    "pointer.scroll": "Scroll",
    # 拖拽 maa-fw 的编译器产不出来（_compile_action 对它返回 None）。
    # 保留语义并标 actionScope —— 不能悄悄退化成点击或 DoNothing。
    "pointer.drag": "Drag",
}

# 这些断言只有桌面侧验得了。窗口是否打开、控件是否勾选，maa-fw 的识别器里
# 没有对应能力；运行时算出来的期望值（当前日期）更不能塞进静态的 OCR expected。
DESKTOP_ONLY_VERIFIERS = frozenset({
    "window_open", "checked", "window_text_contains_time",
})


class DesktopTraceError(ValueError):
    pass


def node_id(step_id: str) -> str:
    """maa-fw 的 node_name() 把名字规范到 [0-9A-Za-z_]，主动对齐。"""
    return "".join(c if c.isalnum() else "_" for c in step_id)


def _action(step: dict) -> tuple[dict, str | None]:
    """返回 (动作, 需要标的 actionScope)。"""
    action_id = step.get("actionId")
    if not action_id:
        return {"type": "DoNothing", "param": {}}, None    # 只做校验的步骤

    mapped = ACTIONS.get(action_id)
    if mapped is None:
        # 没见过的动作。**不猜** —— 猜错的后果是回放做了另一件事而且不报错。
        return ({"type": "DoNothing", "param": {"unmappedAction": action_id}},
                VERIFY_SCOPE_DESKTOP)

    args = step.get("args") or {}
    if mapped == "Scroll":
        clicks = int(args.get("clicks", 0))
        param = {"direction": "down" if clicks >= 0 else "up", "clicks": abs(clicks)}
    elif mapped == "Drag":
        param = {"endSelector": step.get("endSelector"), **args}
    else:
        param = {"target": True, "target_offset": [0, 0, 0, 0]}
        point = ((step.get("selector") or {}).get("visual") or {}).get("relativePoint")
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            param["target_ratio"] = [round(float(point[0]), 4), round(float(point[1]), 4)]
    return {"type": mapped, "param": param}, (
        None if mapped in MAA_ACTIONS else VERIFY_SCOPE_DESKTOP)


def _recognition(step: dict) -> dict:
    visual = (step.get("selector") or {}).get("visual") or {}
    template = visual.get("template")
    if template:
        return {"type": "TemplateMatch", "param": {
            "template": template, "threshold": 0.8,
            "roi": list(FULL_FRAME_ROI), "method": TEMPLATE_METHOD,
            "green_mask": False,
        }}
    # 有一条文本断言时，OCR 是 maa-fw 验得了的 —— 别浪费掉
    for verifier in step.get("verifiers") or []:
        if verifier.get("type") == "text_equals" and isinstance(verifier.get("expected"), str):
            return {"type": "OCR", "param": {
                "expected": [verifier["expected"]], "threshold": OCR_THRESHOLD,
                "roi": list(FULL_FRAME_ROI),
            }}
    return {"type": "DirectHit", "param": {}}


def _verification(step: dict) -> dict:
    """把 verifier 搬进 attach.verification，验不了的显式标出来。

    不标的话 maa-fw 会以为验过了 —— 这和 web 侧标 web-only 是同一件事。
    """
    verifiers = list(step.get("verifiers") or [])
    if not verifiers:
        return {}
    out: dict[str, Any] = {"verifiers": verifiers}
    if any(v.get("type") in DESKTOP_ONLY_VERIFIERS for v in verifiers):
        out["scope"] = VERIFY_SCOPE_DESKTOP
    return out


def _selector(step: dict) -> dict:
    selector = step.get("selector") or {}
    control = selector.get("control") or {}
    ancestry = control.get("ancestry") or []

    # 定位依据按可靠性排。**不能只看控件自己的 automationId** ——
    # 实测三种都真实存在：
    #   滚动步骤：控件层没有 id，id 在祖先链上（目标是「那张表」）
    #   菜单项：  没有 id，但有 name（「语言设置」）
    # Qt 控件本来就是 automation_id **或** text 二选一。只认前者会把一堆
    # 定位得了的步骤误判成「没有任何依据」，而误报正是可信度工具最不能有的东西。
    candidates = [
        ("automationId", control.get("automationId")),
        ("name", control.get("name")),
        ("automationIdAncestor",
         next((a.get("automationId") for a in ancestry if a.get("automationId")), None)),
        ("nameAncestor",
         next((a.get("name") for a in ancestry if a.get("name")), None)),
    ]
    kind, anchor = next(((k, v) for k, v in candidates if v), ("none", None))
    return {
        # 桌面侧的「选择器」就是 automationId / name + 祖先链，和 web 的 DOM 选择器同位
        "kind": kind,
        "sel": anchor or "",
        "anchoredOnAncestor": kind.endswith("Ancestor"),
        "label": control.get("name") or "",
        "controlType": control.get("controlType"),
        "fingerprint": control.get("fingerprint"),
        "ancestry": control.get("ancestry") or [],
        "window": selector.get("window") or {},
    }


def _gui_target(step: dict) -> dict | None:
    visual = (step.get("selector") or {}).get("visual") or {}
    if not visual.get("template"):
        return None
    # 严格 kwargs：只能有 STRICT_GUI_TARGET_KEYS 里那七个键
    return {
        "source_action": step.get("actionId") or "",
        "bbox": [0, 0, 0, 0],          # 桌面录制不落 bbox，模板匹配自己定位
        "coordinate_space": "screen",
        "label": str(((step.get("selector") or {}).get("control") or {}).get("name") or ""),
        "element_type": "visual",
        "crop_image": visual.get("template", ""),
        "source_params": {"relativePoint": visual.get("relativePoint")},
    }


def convert(desktop: dict) -> dict:
    if desktop.get("schema") != DESKTOP_SCHEMA:
        raise DesktopTraceError(f"不是桌面黄金轨迹：schema={desktop.get('schema')!r}")
    steps = desktop.get("steps") or {}
    if not steps:
        raise DesktopTraceError("轨迹没有步骤")

    # 按 next 链走，而不是按字典顺序 —— 顺序就是这条轨迹的全部意义
    order: list[str] = []
    current, seen = desktop.get("entry"), set()
    while current and current not in seen:
        seen.add(current)
        order.append(current)
        current = (steps.get(current) or {}).get("next")

    nodes: dict[str, dict] = {}
    for index, step_id in enumerate(order):
        step = steps[step_id]
        action, scope = _action(step)
        visual = (step.get("selector") or {}).get("visual") or {}

        provenance: dict[str, Any] = {
            "sourceStepId": step_id,
            "status": step.get("status", "ready"),
            "selector": _selector(step),
        }
        if scope:
            provenance["actionScope"] = scope
        if not step.get("required", True):
            provenance["optional"] = True
        if step.get("issues"):
            provenance["issues"] = list(step["issues"])
        if visual.get("template"):
            templates = {"element": visual["template"]}
            if visual.get("contextTemplate"):
                templates["context"] = visual["contextTemplate"]
            provenance["templates"] = templates
            provenance["templateOrder"] = [
                k for k in ("context", "element") if k in templates]

        nodes[node_id(step_id)] = build_node(
            recognition=_recognition(step),
            action=action,
            # next 必须是**列表**。写成字符串的话 maa-fw 会按字符迭代它，
            # "step-0002" 变成 ['s','t','e','p',…] —— 实测就是这么坏的。
            next_ids=[node_id(order[index + 1])] if index + 1 < len(order) else [],
            app="desktop",
            task_key=desktop.get("name", ""),
            scene_key=((step.get("selector") or {}).get("window") or {}).get(
                "processName", ""),
            verification=_verification(step) or None,
            provenance=provenance,
            gui_target=_gui_target(step),
        )

    trace = build_trace(
        nodes, name=desktop.get("name", ""), start_url=None,
        status=desktop.get("status", "incomplete"),
        entry=node_id(order[0]) if order else None,
        node_order=[node_id(s) for s in order],
    )
    # 桌面侧特有的东西没有 v2 字段可放，进 $meta 的 provenance 留证
    trace["$meta"]["attach"]["provenance"] = {
        "sourceSchema": DESKTOP_SCHEMA,
        "sourceRecording": desktop.get("sourceRecording") or {},
        "environment": desktop.get("environment") or {},
        "catalog": desktop.get("catalog") or {},
        "cleanup": desktop.get("cleanup") or [],
    }
    return trace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", help="edr-wd 录制目录，或 golden-trace.json 路径")
    parser.add_argument("-o", "--out", help="输出路径，默认写到录制目录下 trace.json")
    args = parser.parse_args(argv)

    case = Path(args.case)
    source = case / "golden-trace.json" if case.is_dir() else case
    trace = convert(json.loads(source.read_text(encoding="utf-8")))
    out = Path(args.out) if args.out else source.parent / "trace.json"
    out.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{source} → {out}（{len(trace) - 1} 个节点）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
