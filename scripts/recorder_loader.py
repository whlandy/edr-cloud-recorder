"""把 recorder-inject.mjs 原样喂给 Python 侧的 add_init_script。

JS 侧 addInitScript 可以直接收一个函数，Playwright 帮你序列化；
Python 侧 add_init_script 只收字符串，所以要把
`export const RECORDER = () => {...};` 里的箭头函数取出来包成 IIFE。

注入层不改一个字 —— 那 552 行全是实测出来的 DOM 细节，
改写只有风险没有收益，这里只做外壳剥离。
"""

from pathlib import Path

PREFIX = "export const RECORDER ="
DEFAULT = Path(__file__).resolve().parent / "recorder-inject.mjs"


def recorder_source(mjs_path: Path | None = None) -> str:
    path = Path(mjs_path) if mjs_path else DEFAULT
    src = path.read_text(encoding="utf-8")
    try:
        i = src.index(PREFIX)
    except ValueError:
        raise SystemExit(f"{path} 里没有 `{PREFIX}` —— 注入层的导出形式变了？")
    body = src[i + len(PREFIX):].strip()
    if body.endswith(";"):
        body = body[:-1]
    return f"({body})();"
