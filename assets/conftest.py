"""回放工程的 pytest 入口。

fixture 本体在 rec_fixtures.py —— 那样它才能被别处的 conftest 也导入一份
（技能仓库里 recordings/ 下的草稿就靠这个跑起来）。conftest.py 对 pytest 有
特殊语义、不能当普通模块 import，所以这里只做转发。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rec_fixtures import *            # noqa: F401,F403  （fixture 靠名字被 pytest 发现）
