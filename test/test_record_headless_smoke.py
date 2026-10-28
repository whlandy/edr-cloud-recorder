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


def _serve_popup():
    opener = (b"<!doctype html><a id='open' target='_blank' href='/popup'>open</a>")
    popup = (b"<!doctype html><button id='inside' onclick='this.dataset.hit=1'>inside</button>")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = popup if self.path == "/popup" else opener
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
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


@pytest.mark.timeout(45)
def test_recording_persists_popup_page_identity_and_opener(tmp_path):
    srv = _serve_popup()

    def drive(page, pump):
        pump()
        with page.expect_popup() as popup_info:
            page.click("#open")
        popup = popup_info.value
        popup.wait_for_load_state("domcontentloaded")
        pump()
        popup.click("#inside")
        pump()

    try:
        base = f"http://127.0.0.1:{srv.server_port}/"
        result = record_session(
            start_url=base, name="popup-smoke", out_dir=str(tmp_path),
            headless=True, on_ready=drive,
        )
    finally:
        srv.shutdown()

    opener_step = next(s for s in result["steps"] if s.get("label") == "open")
    popup_step = next(s for s in result["steps"] if s.get("label") == "inside")
    assert opener_step["pageId"] == "page-0001"
    assert "openerPageId" not in opener_step
    assert popup_step["pageId"] == "page-0002"
    assert popup_step["openerPageId"] == "page-0001"
    assert popup_step["pageOrigin"] == base.rstrip("/")
    spec = result["spec_file"].read_text(encoding="utf-8")
    assert "expect_popup()" in spec
    assert 'recorded_pages["page-0002"]' in spec
