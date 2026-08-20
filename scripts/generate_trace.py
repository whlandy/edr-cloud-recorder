"""把一次录制编译成一条 MaaFramework 风格的成功轨迹（v2）。

形状与字段归属见 trace_schema.py 的模块说明 —— 那是唯一的格式定义处。
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from rec_visual import (
    AMBIGUITY_MARGIN,
    MATCH_THRESHOLD,
    SCALE_FACTORS,
    VERIFY_THRESHOLD,
)
from generate_spec import pair_network_events, prepare_steps
from rec_secrets import redact_sensitive_values
from trace_schema import (
    FULL_FRAME_ROI,
    TEMPLATE_METHOD,
    VERIFY_SCOPE_WEB,
    build_node,
    build_trace,
)


POSITIONAL_STEP_TYPES = frozenset({
    "click", "dblclick", "check", "uncheck", "switch",
})

# OCR 兜底阈值，跟 maa-fw 的 _compile_recognition 保持一致
OCR_THRESHOLD = 0.3

ACTION_TYPES = {
    "click": "Click",
    "dblclick": "DoubleClick",
    "check": "Check",
    "uncheck": "Uncheck",
    "switch": "SetSwitch",
    "fill": "InputText",
    "press": "PressKey",
    "assert": "DoNothing",     # 断言不产生动作，识别成功本身就是结论
}


def node_id(index: int) -> str:
    """节点名。用下划线而不是连字符 —— maa-fw 的 node_name() 把名字规范到
    [0-9A-Za-z_]，我们主动对齐，省得两边的名字对不上号。"""
    return f"step_{index:04d}"


def _selector(step: dict) -> dict:
    return {
        key: step[key]
        for key in ("kind", "sel", "css", "label", "inFrame", "framePath")
        if step.get(key) is not None
    }


def _scene_key(step: dict) -> str:
    """节点所处的「场景」。web 上最接近的就是当时的路径。"""
    url = step.get("url") or ""
    try:
        return urlsplit(url).path or "/"
    except ValueError:
        return ""


def _templates(ui: dict) -> tuple[dict, list[str]]:
    templates = ui.get("templates") or {}
    order = [kind for kind in ("context", "element") if kind in templates]
    return templates, order


def _template_recognition(templates: dict, order: list[str]) -> dict | None:
    if not order:
        return None
    return {
        "type": "TemplateMatch",
        "param": {
            # 只放一个 —— maa-fw 的 recognition.param.template 是单个路径字符串。
            # context → element 的回退顺序是我们自己的能力，留在 provenance。
            "template": templates[order[0]],
            "threshold": MATCH_THRESHOLD,
            "roi": list(FULL_FRAME_ROI),
            "method": TEMPLATE_METHOD,
            "green_mask": False,
        },
    }


def _assert_recognition(step: dict) -> dict | None:
    """把断言映射成 maa-fw 认得的识别器；映射不了就返回 None。

    能映射的是「页面上有这段文字」这一类 —— OCR 的主场。映射不了的两类：

      * expectedFrom：期望值由回放此刻的时钟决定，OCR 的 expected 是**静态**
        字符串，硬塞一个录制时的字面量进去等于埋一颗定时炸弹
      * 纯可见性断言（断言对象不是文字）：理论上能用 TemplateMatch，但录制器
        目前只给定位类步骤截模板，断言步骤没有裁图 —— 要解锁得先改录制器
    """
    if step.get("expectedFrom"):
        return None
    text = _asserted_text(step)
    if not text:
        return None
    return {
        "type": "OCR",
        "param": {
            "expected": [text],
            "threshold": OCR_THRESHOLD,
            "roi": list(FULL_FRAME_ROI),
        },
    }


def _asserted_text(step: dict) -> str | None:
    """这一条断言究竟在断「哪段文字应该在」。"""
    expected = step.get("expected")
    if step.get("assertion") in ("text", "value") and isinstance(expected, str):
        return expected.strip() or None
    # 由「文本同义反复」改写来的存在性断言：用户右键选「文本等于」，本意就是
    # 「这段文字应该在」。改写只是换了个更诚实的**web 断言写法**，断的东西没变
    # —— 所以它照样是 OCR 的主场，不该因为形状变了就退成 web-only。
    # 实测很关键：录出来的断言绝大多数都会走这条改写路径。
    anchor = step.get("_wasTextTautology")
    if step.get("assertion") == "visible" and step.get("expected") is True:
        return (anchor or "").strip() or None
    return None


def _action(step: dict, *, relative_point: dict | None = None,
            focus_before_input: bool = False) -> dict:
    step_type = step.get("type")
    action_type = ACTION_TYPES.get(step_type, "RecordedAction")
    param: dict[str, Any] = {}
    if step_type in POSITIONAL_STEP_TYPES:
        click = relative_point or {}
        # target/target_offset 是 maa-fw 点击动作的标准参数；target_ratio 是它
        # 已有的「按比例落点」字段，注释明说是为了跨尺度匹配后落点还准 ——
        # 我们的相对落点就放这里，不用另造字段，也不用退化成点框中心。
        param["target"] = True
        param["target_offset"] = [0, 0, 0, 0]
        param["target_ratio"] = [
            round(float(click.get("rx", 0.5)), 4),
            round(float(click.get("ry", 0.5)), 4),
        ]
        if step_type == "switch":
            param.update(state=bool(step.get("to")), via=step.get("via") or {})
    elif step_type == "fill":
        if step.get("secret"):
            param["valueFromEnv"] = "REC_PASSWORD"
        elif (step.get("valueFrom") or {}).get("kind") == "localtime":
            # 日期类输入按回放当天算 —— 见 rec_assert.local_time_value 的说明。
            # text 仍然带上录制时的字面量，作证据也便于人工钉回去。
            param["valueFrom"] = step["valueFrom"]
            param["text"] = step.get("value", "")
        else:
            param["text"] = step.get("value", "")
        if focus_before_input:
            param["focusBeforeInput"] = True
    elif step_type == "press":
        param["key"] = step.get("key", "Enter")
    elif step_type == "assert":
        pass                    # 断言规格在 attach.verification.assertion
    else:
        param["recordedType"] = step_type
    return {"type": action_type, "param": param}


def _assertion_spec(step: dict) -> dict:
    spec = {
        "assertion": step.get("assertion"),
        "expected": step.get("expected"),
    }
    if step.get("attribute") is not None:
        spec["attribute"] = step["attribute"]
    # 期望值在回放那一刻算，录制里的 expected 只作证据 —— 见 rec_assert 的说明
    if step.get("expectedFrom"):
        spec["expectedFrom"] = step["expectedFrom"]
    return spec


def _confidence_policy() -> dict:
    # 严格 kwargs：只能有这四个键，见 trace_schema.STRICT_CONFIDENCE_KEYS
    return {
        "direct_threshold": MATCH_THRESHOLD,
        "assist_threshold": VERIFY_THRESHOLD,
        "score_source": "TemplateMatch.score",
        "decision": "direct_if_score_gte_direct_threshold",
    }


def _gui_target(step: dict, ui: dict, templates: dict, order: list[str]) -> dict | None:
    rect = ui.get("pageRect") or {}
    if not rect and not order:
        return None
    bbox = [
        int(round(rect.get("x", 0))), int(round(rect.get("y", 0))),
        int(round(rect.get("width", 0))), int(round(rect.get("height", 0))),
    ]
    element_type = {
        "fill": "text_input",
        "press": "keyboard",
        "switch": "toggle",
    }.get(step.get("type") or "", "visual")
    # 严格 kwargs：只能有这七个键，见 trace_schema.STRICT_GUI_TARGET_KEYS
    return {
        "source_action": step.get("type") or "",
        "bbox": bbox,
        # maa-fw 默认是 roi-local；我们的 roi 是整屏，坐标就是视口坐标
        "coordinate_space": "viewport",
        "label": str(step.get("label") or ""),
        "element_type": element_type,
        "crop_image": templates.get(order[0], "") if order else "",
        "source_params": {
            key: ui[key] for key in ("click", "pageViewport", "viewport",
                                     "deviceScaleFactor")
            if ui.get(key) is not None
        },
    }


def _same_target(left: dict, right: dict) -> bool:
    return any(
        left.get(key) and left.get(key) == right.get(key)
        for key in ("css", "sel")
    )


def _fold_input_focus(steps: list[dict]) -> list[dict]:
    folded = []
    for step in steps:
        if (
            step.get("type") == "fill"
            and folded
            and folded[-1].get("type") == "click"
            and _same_target(folded[-1], step)
        ):
            focus = folded.pop()
            folded.append({
                **step,
                "t": focus.get("t", step.get("t")),
                "actionT": focus.get("actionT", focus.get("t")),
                "_focusUi": focus.get("ui") or {},
                "_sourceStepIds": [focus.get("id"), step.get("id")],
            })
        else:
            folded.append(step)
    return folded


def _request_payload(request: dict | None) -> dict | None:
    if not request:
        return None
    payload = {
        key: request[key]
        for key in ("body", "bodyBase64", "bodyEncoding")
        if request.get(key) is not None
    }
    if isinstance(payload.get("body"), str):
        try:
            payload["body"] = json.loads(payload["body"])
        except (TypeError, ValueError):
            pass
    if "body" in payload:
        payload["body"] = redact_sensitive_values(payload["body"])
    return payload or None


def _network_expectation(request: dict | None, response: dict) -> dict:
    param = {
        "method": response["method"],
        "url": response["url"],
        "expectedStatus": response["status"],
        # 读请求只有在**状态真的变化**时才会重发，所以不能当作必发。
        #
        # 实测：录制时从「未选中」点到「选中」，触发了一次 list-group-asset；
        # 回放时 sessionStorage 恢复出的选中项正好就是它，同样一下点击什么都不发
        # —— 那一步其实是成功的（作用域已经对了），却因为等不到响应被判失败。
        #
        # 这条规则 generate_spec 早就有了：只断言写请求，GET 留作注释。
        # 轨迹这边一直没跟上，同一份录制又出现了两套语义。
        #
        # 写请求仍然必发：它是这一步真正产生的副作用，没发出去就是没做成。
        "required": response["method"] != "GET",
        # HTTP 层的事实桌面侧看不到 —— 显式标出来，别让它以为验过了
        "scope": VERIFY_SCOPE_WEB,
    }
    payload = _request_payload(request)
    if payload:
        param["request"] = payload
    if response.get("body") is not None:
        param["expectedBody"] = response["body"]
    return param


def generate_trace(steps: list[dict], net: list[dict] | None = None, *,
                   name: str, start_url: str) -> dict[str, Any]:
    _, steps = prepare_steps(steps)
    steps = _fold_input_focus(steps)
    network_pairs = pair_network_events(net or [])
    # 二次确认型开关：确认框只在开关**真的需要拨**的时候才弹。回放遇到开关已经
    # 在目标状态就跳过点击（拨开关是幂等的），于是确认框根本不出现，后面那几步
    # 就成了无处可点的孤儿步骤。标成可选 —— 现有语义是「证实不存在才跳过」，
    # 确认框没弹正是这种情形；真弹出来了它就不算不存在，照旧必须点。
    gated_ids = {
        step_id
        for step in steps
        for step_id in ((step.get("via") or {}).get("gatedSteps") or [])
    }

    linear: list[dict] = []
    for step in steps:
        own_ui = step.get("ui") or {}
        visual_ui = step.get("_focusUi") or own_ui
        templates, order = _templates(visual_ui)

        recognition = _template_recognition(templates, order)
        focus_before_input = bool(step.get("_focusUi") and recognition)
        needs_template = (
            step.get("type") in POSITIONAL_STEP_TYPES or "_focusUi" in step
        )
        status = "missing_template" if needs_template and not recognition else "ready"

        provenance: dict[str, Any] = {
            "sourceStepId": step.get("id"),
            "timestampMs": step.get("t"),
            "status": status,
            "selector": _selector(step),
        }
        if step.get("_sourceStepIds"):
            provenance["sourceStepIds"] = step["_sourceStepIds"]
        if order:
            provenance.update(
                templates=templates,
                templateOrder=order,
                scaleFactors=list(SCALE_FACTORS),
                ambiguityMargin=AMBIGUITY_MARGIN,
                geometry={
                    key: visual_ui[key]
                    for key in ("pageRect", "pageViewport", "viewport",
                                "deviceScaleFactor")
                    if visual_ui.get(key) is not None
                },
            )
        # 可选步骤要标出来，否则轨迹回放会把它当必经节点。
        #
        # 落到 CSS 兜底的点击，绝大多数是关首启弹窗、提示条 —— 它们出现与否
        # 取决于账号状态和历史操作，录制时出现过，回放时往往已经不再出现
        # （第一次关掉后应用记住了）。
        #
        # generate_spec 早就把这类步骤生成为「存在则点」，轨迹这边如果不标，
        # 同一份录制就有了两套语义：pytest 草稿跳过它，轨迹却判整条失败。
        # 实测正是这样断在第 2 步，完成率 1/9。
        if step.get("kind") == "css" and step.get("type") == "click":
            provenance["optional"] = True
        # 关浮层的那一步天生是条件步骤：弹窗出现与否取决于账号状态和历史操作。
        # 原来靠「选择器落到 CSS 兜底」当代理信号 —— 那只是因为这类图标以前
        # 只能产出绝对路径。选择器变好之后代理就不成立了，弹窗步骤反而变成
        # 必经节点。现在用录制时观察到的事实：点完之后那个浮层不在了。
        #
        # 同时把这个事实带进轨迹：回放遇到「找到了但点不动」时，可以把关浮层
        # 的步骤提前做掉再重试 —— 挡路的东西和关它的那一步就是这么对上的。
        if step.get("dismissesOverlay"):
            provenance["optional"] = True
            provenance["dismissesOverlay"] = True
        if step.get("id") in gated_ids:
            provenance["optional"] = True

        verification: dict[str, Any] = {}
        if step.get("type") == "assert":
            spec = _assertion_spec(step)
            mapped = _assert_recognition(step)
            if mapped is not None:
                recognition = mapped
            else:
                # 桌面侧没有对应能力，显式标出来，别让它以为验过了
                spec["scope"] = VERIFY_SCOPE_WEB
            verification["assertion"] = spec

        node = build_node(
            recognition=recognition,
            action=_action(
                step,
                relative_point=visual_ui.get("click"),
                focus_before_input=focus_before_input,
            ),
            task_key=name,
            scene_key=_scene_key(step),
            provenance=provenance,
            confidence_policy=_confidence_policy() if order else None,
            gui_target=_gui_target(step, visual_ui, templates, order) if order else None,
        )
        linear.append((step, node, verification))

    # 网络期望按「这一步到下一步之间发生的」归属
    nodes: dict[str, Any] = {}
    ids = [node_id(i) for i in range(1, len(linear) + 1)]
    for index, (step, node, verification) in enumerate(linear):
        high = (
            linear[index + 1][0].get("t", float("inf"))
            if index + 1 < len(linear) else float("inf")
        )
        responses = [
            _network_expectation(request, response)
            for request, response in network_pairs
            if step.get("t", 0) <= (request or response).get("t", 0) < high
        ]
        if responses:
            verification["responses"] = responses
        if verification:
            node["attach"]["verification"] = verification
        if index + 1 < len(ids):
            node["next"] = [ids[index + 1]]
        nodes[ids[index]] = node

    incomplete = any(
        node["attach"]["provenance"].get("status") == "missing_template"
        for node in nodes.values()
    )
    return build_trace(
        nodes,
        name=name,
        start_url=start_url,
        status="incomplete" if incomplete else "ready",
        entry=ids[0] if ids else None,
    )
