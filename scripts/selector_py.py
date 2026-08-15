"""把录制下来的 JS 语法选择器转成 Python 语法。

录制器（recorder-inject.mjs）产出的 `sel` 是 JS 写法：

    getByRole("button", { name: "提交订单", exact: true })
    locator("tr", { hasText: "李四" }).getByText("删除", { exact: true })

Python 侧要的是：

    get_by_role("button", name="提交订单", exact=True)
    locator("tr", has_text="李四").get_by_text("删除", exact=True)

为什么是转译而不是改录制器：注入层要原样保留（那 552 行全是实测出来的
DOM 细节），而且 JS 侧的自检还得继续绿。选择器这门语言是**封闭的**——
只有下面 METHODS 里那几个构造函数、OPTIONS 里那三个选项键，
所以转译是可穷尽的，不是在通用地解析 JS。

等 Python 成为唯一宿主之后，更干净的做法是让录制器直接吐结构化的
{kind, args}，两边各自渲染，这个文件就可以删掉。
"""

import json

# 录制器能产出的全部终结符（见 recorder-inject.mjs 的 selectorFor / floatingScope）
METHODS = {
    "getByTestId": "get_by_test_id",
    "getByRole": "get_by_role",
    "getByText": "get_by_text",
    "getByLabel": "get_by_label",
    "getByPlaceholder": "get_by_placeholder",
    "getByAltText": "get_by_alt_text",
    "getByTitle": "get_by_title",
    "locator": "locator",
}

# 录制器能产出的全部选项键
OPTIONS = {
    "name": "name",
    "exact": "exact",
    "hasText": "has_text",
}


class SelectorError(ValueError):
    """选择器不在录制器能产出的形态之内 —— 说明录制器改了，转译器要跟着改。"""


def _split_top(src: str, sep: str) -> list[str]:
    """按 sep 切分，但跳过字符串字面量和括号/花括号内部。

    CSS 路径里有的是点（`div.opt`），选项对象里有的是逗号，
    都不能当分隔符 —— 所以不能用 str.split。
    """
    parts, buf, depth, quote, esc = [], [], 0, None, False
    for ch in src:
        if quote:
            buf.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in "([{":
            depth += 1
            buf.append(ch)
        elif ch in ")]}":
            depth -= 1
            buf.append(ch)
        elif ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def _js_options(src: str) -> dict:
    """`{ name: "x", exact: true }` → {"name": "x", "exact": True}

    键是不带引号的标识符，不是合法 JSON，所以先按已知键名补上引号再交给
    json.loads —— 不认识的键直接报错，好过悄悄丢掉一个约束。
    """
    body = src.strip()
    if not (body.startswith("{") and body.endswith("}")):
        raise SelectorError(f"选项不是对象字面量: {src!r}")
    out = {}
    inner = body[1:-1].strip()
    if not inner:
        return out
    for pair in _split_top(inner, ","):
        pair = pair.strip()
        if not pair:
            continue
        k, _, v = pair.partition(":")
        k = k.strip()
        if k not in OPTIONS:
            raise SelectorError(f"未知选项键 {k!r}（来自 {src!r}）")
        out[OPTIONS[k]] = json.loads(v.strip())
    return out


def _py_literal(v) -> str:
    if isinstance(v, bool):
        return "True" if v else "False"
    if v is None:
        return "None"
    # ensure_ascii=False 让中文原样留在代码里；JSON 的字符串转义规则是 Python 的子集
    return json.dumps(v, ensure_ascii=False)


def _segment(seg: str) -> str:
    seg = seg.strip()
    if seg == "first()":
        # Python 的 first 是**属性**不是方法 —— 写成 .first() 会 TypeError
        return "first"
    if not seg.endswith(")"):
        raise SelectorError(f"不是调用形式: {seg!r}")
    name, _, rest = seg.partition("(")
    name = name.strip()
    if name not in METHODS:
        raise SelectorError(f"未知选择器方法 {name!r}")
    args = _split_top(rest[:-1], ",")
    args = [a for a in (a.strip() for a in args) if a]

    rendered = []
    for i, a in enumerate(args):
        if a.startswith("{"):
            if i == 0:
                raise SelectorError(f"选项不能是第一个参数: {seg!r}")
            for k, v in _js_options(a).items():
                rendered.append(f"{k}={_py_literal(v)}")
        else:
            rendered.append(_py_literal(json.loads(a)))
    return f"{METHODS[name]}({', '.join(rendered)})"


def to_python(sel: str) -> str:
    """转译一整条选择器链。"""
    segs = _split_top(sel, ".")
    # CSS 兜底选择器整条都在字符串里，_split_top 不会把它切开；
    # 但如果第一段就不是调用形式，说明拿到的不是录制器产出的东西。
    return ".".join(_segment(s) for s in segs)
