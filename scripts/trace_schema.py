"""黄金轨迹 v2 的形状定义与访问器。

**为什么换形状**：轨迹要同时喂给两个 runtime —— 我们自己的 replay_trace，
以及 maa-fw 的 MaaNodeRunner。后者吃的是 MaaFramework 风格的节点表。
v1 那套 `{schema, name, entry, steps:{...}}` 外面裹了一层，maa-fw 加载不了；
而两边的**节点内部**本来就几乎一样，都是 recognition/action/next。

所以 v2 把外层拆掉：顶层就是节点表，轨迹级元数据放进保留节点 `$meta`。

**布局依据**：maa-fw 的 `MaaNodeRunner._coerce_node` 读的是
`LearnedNode.to_pipeline_node()` 那套 —— 顶层只有 recognition / action /
next / on_error / rate_limit / timeout / pre_delay / post_delay / max_hit，
其余全在 `attach` 底下。所以我们也这么写，不是自己发明一层。

    recognition.param.template   单个模板路径。maa-fw 自己编译出来就是单字符串，
                                 按它实际产出的形状来，不猜框架吃不吃列表
    action.param.target_ratio    元素框内的相对落点。maa-fw 已经有这个字段，
                                 注释明说是为了「跨尺度匹配后落点还准」
    attach.confidence_policy     阈值。**严格 kwargs**，见 STRICT_* 说明
    attach.gui_target            元素框、标签、裁图。同样严格 kwargs
    attach.verification          网络期望、手工断言
    attach.provenance            纯 web 的东西：DOM 选择器、多模板顺序、
                                 可选步骤标记、几何信息
    attach.stats                 命中计数。`$meta` 靠它把自己变成惰性节点

maa-fw 忽略不认识的键，我们忽略它特有的键 —— 两边各取所需，同一份文件。
"""

from __future__ import annotations

from typing import Any, Iterator

SCHEMA = "edr.success-trace/v2"
META_KEY = "$meta"

# maa-fw 用 `ConfidencePolicy(**attach["confidence_policy"])` 和
# `GuiTarget(**attach["gui_target"])` 反序列化 —— 多一个键就 TypeError，
# 整条轨迹加载失败。所以这两个字典必须**只有**这些键，我们自己的额外参数
# （尺度列表、歧义边界、多模板顺序）一律进 provenance。
STRICT_CONFIDENCE_KEYS = frozenset({
    "direct_threshold", "assist_threshold", "score_source", "decision",
})
STRICT_GUI_TARGET_KEYS = frozenset({
    "source_action", "bbox", "coordinate_space", "label",
    "element_type", "crop_image", "source_params",
})

# 桌面侧没有对应能力的验证，标出来让它显式跳过而不是假装验过。
# 网络断言（HTTP 状态码/请求体）桌面上根本看不到；运行时算的期望值
# （「显示的是当前时间」）也没法写成静态的 OCR expected。
VERIFY_SCOPE_WEB = "web-only"

# maa-fw 的 MaaNodeCompiler._compile_action 产得出的动作。这是两边能安全互通的
# 集合 —— 它的执行器是注入的，不认识的动作会怎样取决于调用方，我们控制不了。
#
# 落在集合外的动作（SetSwitch / Check / Uncheck 这些「读了状态再决定拨不拨」的）
# 必须在 provenance.actionScope 上标 web-only。不标的话，桌面侧多半会退化成
# 一次盲点击 —— 而「拨开关退化成盲点击」正是本仓库花了很大力气消灭的缺陷：
# 方向取决于当时的状态，拨反了也不报错。
MAA_ACTIONS = frozenset({
    "Click", "RightClick", "DoubleClick", "LongPress",
    "InputText", "Hotkey", "PressKey", "Scroll", "DoNothing",
})

# maa-fw 的 TemplateMatch 默认 method，对应 cv2.TM_CCOEFF_NORMED
TEMPLATE_METHOD = 5
# 我们的模板是整屏视口裁图，搜索范围就是整屏。MaaFramework 里全零即全图 ——
# 不能写死录制时的视口尺寸，回放的窗口大小本来就会变。
FULL_FRAME_ROI = [0, 0, 0, 0]


class TraceFormatError(ValueError):
    pass


def meta(trace: dict) -> dict:
    m = trace.get(META_KEY)
    if not isinstance(m, dict):
        raise TraceFormatError(f"轨迹缺少 {META_KEY} 节点 —— 不是 v2 格式？")
    return attach(m)


def check_schema(trace: dict) -> dict:
    m = meta(trace)
    if m.get("schema") != SCHEMA:
        raise TraceFormatError(f"不支持的 trace schema: {m.get('schema')!r}")
    return m


def nodes(trace: dict) -> Iterator[tuple[str, dict]]:
    """遍历真正的步骤节点，跳过 $meta。"""
    for key, value in trace.items():
        if key != META_KEY and isinstance(value, dict):
            yield key, value


def node_ids(trace: dict) -> set[str]:
    return {k for k, _ in nodes(trace)}


def attach(node: dict) -> dict:
    return node.get("attach") or {}


def next_of(node: dict) -> str | None:
    """v2 的 next 是列表（MaaFramework 语义）。我们的轨迹是线性的，只取第一个。"""
    nxt = node.get("next") or []
    if isinstance(nxt, str):          # 容错：手写的轨迹可能写成字符串
        return nxt or None
    return nxt[0] if nxt else None


def provenance(node: dict) -> dict:
    return attach(node).get("provenance") or {}


def selector_of(node: dict) -> dict:
    return provenance(node).get("selector") or {}


def geometry_of(node: dict) -> dict:
    return provenance(node).get("geometry") or {}


def templates_of(node: dict) -> dict:
    """按 templateOrder 排好的多模板。

    recognition.param.template 只放一个（maa-fw 的形状），完整的
    context → element 回退顺序留在 provenance —— 那是我们自己的能力。
    """
    return provenance(node).get("templates") or {}


def template_order(node: dict) -> list[str]:
    order = provenance(node).get("templateOrder")
    if order:
        return list(order)
    return [k for k in ("context", "element") if k in templates_of(node)]


def relative_point(node: dict) -> dict:
    """元素框内的相对落点，来自 action.param.target_ratio。

    没有用 target_offset：那是**像素**偏移，而匹配框会按尺度缩放，导出时算出的
    像素值在尺度≠1 时就偏了 —— 跨分辨率复用模板正是这套东西的全部意义。
    maa-fw 自己也是这么想的，它的 _observed_target_ratio 就是比例。
    """
    ratio = ((node.get("action") or {}).get("param") or {}).get("target_ratio")
    if isinstance(ratio, (list, tuple)) and len(ratio) >= 2:
        return {"x": float(ratio[0]), "y": float(ratio[1])}
    return {"x": 0.5, "y": 0.5}


def is_optional(node: dict) -> bool:
    return bool(provenance(node).get("optional"))


def dismisses_overlay(node: dict) -> bool:
    return bool(provenance(node).get("dismissesOverlay"))


def node_status(node: dict) -> str:
    return provenance(node).get("status") or "ready"


def verification(node: dict) -> dict:
    return attach(node).get("verification") or {}


def expected_responses(node: dict) -> list[dict]:
    return verification(node).get("responses") or []


def assertion_of(node: dict) -> dict:
    """手工断言的规格（assertion / expected / expectedFrom / attribute）。"""
    return verification(node).get("assertion") or {}


def recognition_params(node: dict) -> dict:
    return (node.get("recognition") or {}).get("param") or {}


def match_thresholds(node: dict) -> dict[str, Any]:
    """回放要用的阈值。confidence_policy 是 maa-fw 的叫法，语义一一对应。"""
    policy = attach(node).get("confidence_policy") or {}
    prov = provenance(node)
    return {
        "threshold": policy.get("direct_threshold"),
        "verify_threshold": policy.get("assist_threshold"),
        "ambiguity_margin": prov.get("ambiguityMargin"),
        "scale_factors": prov.get("scaleFactors"),
    }


# ---------------------------------------------------------------- 构造器
#
# 节点的形状只在这里定义一次。生产代码（generate_trace）和测试里的
# v1→v2 转换器都调它 —— 否则两边各写一份布局，一旦漂移，测试会用自己那份
# 形状去验回放器，正好放过「生成器产出的形状回放器读不懂」这类 bug。

def build_node(
    *,
    recognition: dict | None = None,
    action: dict | None = None,
    next_ids: list[str] | tuple[str, ...] = (),
    app: str = "web",
    task_key: str = "",
    scene_key: str = "",
    confidence_policy: dict | None = None,
    gui_target: dict | None = None,
    verification: dict | None = None,
    provenance: dict | None = None,
    stats: dict | None = None,
    max_hit: int | None = None,
) -> dict:
    """按 LearnedNode.to_pipeline_node() 的布局拼一个节点。"""
    node: dict[str, Any] = {
        # 映射不到 maa-fw 识别器的步骤用 DirectHit：它是 MaaFramework 的
        # 「无条件命中」，语义上就是「这一步不靠图像识别定位」。
        "recognition": recognition or {"type": "DirectHit", "param": {}},
        "action": action or {"type": "DoNothing", "param": {}},
        "next": list(next_ids),
        "attach": {
            "app": app,
            "task_key": task_key,
            "scene_key": scene_key,
            "provenance": dict(provenance or {}),
            "stats": dict(stats or {
                "hit_count": 0, "miss_count": 0, "last_matched": 0.0,
            }),
        },
    }
    if max_hit is not None:
        node["max_hit"] = max_hit
    if confidence_policy:
        node["attach"]["confidence_policy"] = dict(confidence_policy)
    if gui_target:
        node["attach"]["gui_target"] = dict(gui_target)
    if verification:
        node["attach"]["verification"] = dict(verification)
    return node


def build_trace(nodes: dict[str, dict], *, name: str, start_url: str | None,
                status: str, entry: str | None,
                node_order: list[str] | None = None) -> dict:
    order = list(node_order if node_order is not None else nodes.keys())
    meta = build_node(
        task_key=name,
        max_hit=1,
        stats={"hit_count": 1, "miss_count": 0, "last_matched": 0.0},
    )
    meta["attach"].update(
        schema=SCHEMA,
        name=name,
        startUrl=start_url,
        status=status,
        entry=entry,
        nodeOrder=order,
    )
    return {**nodes, META_KEY: meta}


def has_template(node: dict) -> bool:
    """这一步有没有可用的视觉模板。

    **不能**用 `node.get("recognition")` 来判断 —— v2 里每个节点都有
    recognition，映射不到图像识别的那些是 DirectHit。照旧那么写会把所有
    无模板步骤都当成「有视觉兜底」，DOM 失败时不再原样抛出真正的原因，
    而是去做一次注定失败的视觉匹配，最后报「视觉匹配分数不足」。
    """
    if (node.get("recognition") or {}).get("type") != "TemplateMatch":
        return False
    return bool(templates_of(node))


def strict_attach_errors(node_id: str, node: dict) -> list[str]:
    """检查 maa-fw 用 `**` 反序列化的两个字典。

    `MaaNodeRunner._coerce_node` 里写的是 `ConfidencePolicy(**attach[...])`
    和 `GuiTarget(**attach[...])` —— 多一个键就 TypeError，**整条轨迹加载失败**。
    这个错在我们这边看不见（我们自己的回放器不碰这两个字典），所以必须由
    校验来守：否则「maa-fw 能不能读」这件事只能等到 maa-fw 那边炸了才知道。
    """
    problems = []
    for field, allowed, required in (
        ("confidence_policy", STRICT_CONFIDENCE_KEYS,
         {"direct_threshold", "assist_threshold"}),
        ("gui_target", STRICT_GUI_TARGET_KEYS, {"source_action", "bbox"}),
    ):
        value = attach(node).get(field)
        if value is None:
            continue
        if not isinstance(value, dict):
            problems.append(f"{node_id}.attach.{field} 必须是对象")
            continue
        extra = set(value) - allowed
        if extra:
            problems.append(
                f"{node_id}.attach.{field} 含 maa-fw 不认的键 {sorted(extra)}"
                f" —— 它用 ** 反序列化，多一个键整条轨迹就加载不了"
            )
        missing = required - set(value)
        if missing:
            problems.append(
                f"{node_id}.attach.{field} 缺少必填键 {sorted(missing)}"
            )
    return problems
