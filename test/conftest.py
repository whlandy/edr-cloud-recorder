import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "test"))


@pytest.fixture(scope="session")
def recording():
    """真跑一遍浏览器，整个 session 只跑一次 —— 48 条检查共用这一份录制结果。"""
    from fixture_drive import drive
    return drive(os.environ.get("REC_CHROME_BIN"))
