"""record.py 无头（--headless）模式的回归测试。

缺陷：`--headless` 标注「给 CI 冒烟和自检用」，但记录循环只等「用户关窗」
（closed["v"]）才收尾。无头无人时永远没人关窗 → 无限挂起、零产出，CI 里
等于直接卡死。修复：无头且无 on_ready 驱动时，录一小段（HEADLESS_SMOKE_CYCLES
个泵周期）即自动关窗收尾。

本测试守两点：
- 无头 + 无驱动必须有限期内返回（不挂死）；
- 必须真正落盘 trace.json（record→generate 管线在 CI 里可走通）。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from record import record_session

HTML = (
    b"<!doctype html><html><head><meta charset='utf-8'>"
    b"<title>smoke</title></head><body><h1>smoke</h1>"
    b"<button id='go' onclick='this.dataset.x=1'>go</button></body></html>"
)


def _serve():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(HTML)

        def log_message(self, *_):   # 不往 stdout 刷访问日志
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.mark.timeout(45)
def test_record_headless_without_driver_self_terminates_and_writes_trace(tmp_path):
    srv = _serve()
    try:
        base = f"http://127.0.0.1:{srv.server_port}/"
        # 无 on_ready、headless=True：原来会永久挂起；修复后有限期内收尾并落盘。
        result = record_session(
            start_url=base, name="hd-smoke",
            out_dir=str(tmp_path), headless=True,
        )
    finally:
        srv.shutdown()

    trace_file = result.get("trace_file")
    assert trace_file is not None and trace_file.exists(), (
        "无头冒烟必须产出 trace.json，而不是挂死"
    )
    trace = json.loads(trace_file.read_text(encoding="utf-8"))
    assert trace is not None
