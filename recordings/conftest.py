"""让 recordings/ 下的草稿在**技能仓库里**也能直接跑。

用户工程里不需要这个文件：那边 `cp -r <skill>/assets/* .` 之后，conftest.py 和
rec_assert.py 等就都在工程根目录，草稿 import 得到。技能仓库不是那个布局 ——
assets/ 只是个子目录，所以草稿会以 `ModuleNotFoundError: rec_assert` 收场。

放在 recordings/ 而不是每个用例目录里：一个文件服务全部录制，
每份产物仍然只有 recording.json / test_*.py / trace.json + assets/。
"""

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
for _p in (_REPO / "assets", _REPO / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from rec_fixtures import *            # noqa: E402,F401,F403
