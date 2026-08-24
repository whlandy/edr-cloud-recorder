"""把测试里手写的「平铺节点」翻成 v2 轨迹。

测试**不该**手抄 v2 的嵌套布局：那等于在测试侧再写一份形状定义，一旦和
generate_trace 漂移，测试就会拿自己那份形状去验回放器 —— 正好放过
「生成器产出的形状回放器读不懂」这一整类 bug。

所以这里只做翻译，节点的实际拼装全部交给 trace_schema.build_node/build_trace，
和生产代码走同一条路。测试仍然用最省事的写法表达意图：

    to_v2(_trace({"selector": {...}, "action": {...}, "optional": True}))
"""

from __future__ import annotations

from rec_visual import AMBIGUITY_MARGIN, MATCH_THRESHOLD, SCALE_FACTORS, VERIFY_THRESHOLD
import trace_schema as ts

PROVENANCE_FLAGS = (
    "optional", "dismissesOverlay", "sourceStepId", "sourceStepIds", "pageUrl",
)


def node_to_v2(flat: dict) -> dict:
    """flat 用的键名和 v1 一致：selector / action / recognition / geometry /
    expect / optional / dismissesOverlay / status / next。"""
    action = dict(flat.get("action") or {"type": "DoNothing"})
    param = dict(action.get("param") or {})
    recognition_in = flat.get("recognition") or {}
    templates = recognition_in.get("templates") or {}
    order = [kind for kind in ("context", "element") if kind in templates]

    provenance = {
        "status": flat.get("status", "ready"),
        "selector": dict(flat.get("selector") or {}),
    }
    for key in PROVENANCE_FLAGS:
        if flat.get(key) is not None:
            provenance[key] = flat[key]
    if order:
        provenance.update(
            templates=templates,
            templateOrder=order,
            scaleFactors=list(SCALE_FACTORS),
            ambiguityMargin=AMBIGUITY_MARGIN,
            geometry=dict(flat.get("geometry") or {}),
        )
    elif flat.get("geometry"):
        provenance["geometry"] = dict(flat["geometry"])

    verification: dict = {}
    if action.get("type") == "Assert":
        # v2 里断言不再是一种动作类型 —— 断言规格进 attach.verification.assertion
        spec = {key: param.pop(key) for key in
                ("assertion", "expected", "expectedFrom", "attribute")
                if key in param}
        verification["assertion"] = spec
        action["type"] = "DoNothing"
    responses = (flat.get("expect") or {}).get("responses")
    if responses:
        verification["responses"] = responses

    # 相对落点的家是 action.param.target_ratio（maa-fw 已有的字段）
    relative = param.pop("relativePoint", None)
    if relative is not None:
        param["target"] = True
        param["target_offset"] = [0, 0, 0, 0]
        param["target_ratio"] = [relative.get("x", 0.5), relative.get("y", 0.5)]
    action["param"] = param

    recognition = None
    if order:
        recognition = {
            "type": "TemplateMatch",
            "param": {
                "template": templates[order[0]],
                "threshold": recognition_in.get("threshold", MATCH_THRESHOLD),
                "roi": list(ts.FULL_FRAME_ROI),
                "method": ts.TEMPLATE_METHOD,
                "green_mask": False,
            },
        }

    nxt = flat.get("next")
    return ts.build_node(
        recognition=recognition,
        action=action,
        next_ids=[nxt] if nxt else [],
        provenance=provenance,
        verification=verification or None,
        confidence_policy={
            "direct_threshold": recognition_in.get("threshold", MATCH_THRESHOLD),
            "assist_threshold": VERIFY_THRESHOLD,
            "score_source": "TemplateMatch.score",
            "decision": "direct_if_score_gte_direct_threshold",
        } if order else None,
        gui_target={
            "source_action": "click",
            "bbox": [0, 0, 0, 0],
            "coordinate_space": "viewport",
            "label": "",
            "element_type": "visual",
            "crop_image": "",
            "source_params": {},
        } if order else None,
    )


def to_v2(flat_trace: dict) -> dict:
    if ts.META_KEY in flat_trace:
        return flat_trace                       # 已经是 v2 了，别翻两遍
    steps = flat_trace.get("steps") or {}
    nodes = {node_id: node_to_v2(node) for node_id, node in steps.items()}
    return ts.build_trace(
        nodes,
        name=flat_trace.get("name"),
        start_url=flat_trace.get("startUrl"),
        status=flat_trace.get("status", "ready"),
        entry=flat_trace.get("entry"),
    )
