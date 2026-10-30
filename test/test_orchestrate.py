import pytest

from orchestrate.endpoint import Endpoint, EndpointError
from orchestrate.recording_contract import RecordingContract, RecordingContractError
from orchestrate.scenario import Scenario


def test_cleanup_runs_after_main_step_failure():
    calls = []
    scenario = Scenario("cleanup")
    scenario.cloud("apply", lambda: calls.append("apply"))
    scenario.endpoint(
        "verify", lambda: (_ for _ in ()).throw(RuntimeError("failed"))
    )
    scenario.cleanup("restore", lambda: calls.append("restore"))

    assert scenario.run() is False
    assert calls == ["apply", "restore"]
    assert scenario.results[-1].name == "清理：restore"
    assert scenario.results[-1].ok is True


def test_cleanup_failure_fails_otherwise_successful_scenario():
    scenario = Scenario("cleanup failure")
    scenario.cloud("apply", lambda: None)
    scenario.cleanup(
        "restore", lambda: (_ for _ in ()).throw(RuntimeError("restore failed"))
    )

    assert scenario.run() is False
    assert scenario.results[-1].ok is False


def test_scenario_execution_preserves_cloud_and_endpoint_evidence():
    scenario = Scenario("readonly")
    scenario.cloud("cloud", lambda: "score=100")
    scenario.endpoint("endpoint", lambda: "window=HiSec")

    assert scenario.run() is True
    execution = scenario.execution({"endpoint": {"title": "HiSec"}})

    assert execution["schema"] == "edr.end-cloud-execution/v1"
    assert execution["status"] == "success"
    assert [step["side"] for step in execution["steps"]] == ["cloud", "endpoint"]
    assert execution["evidence"]["endpoint"]["title"] == "HiSec"


def test_cleanup_runs_before_keyboard_interrupt_is_reraised():
    calls = []
    scenario = Scenario("interrupt")
    scenario.cloud("apply", lambda: calls.append("apply"))
    scenario.endpoint(
        "interrupt", lambda: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    scenario.cleanup("restore", lambda: calls.append("restore"))

    with pytest.raises(KeyboardInterrupt):
        scenario.run()
    assert calls == ["apply", "restore"]


def test_identity_match_rejects_ip_prefix_false_positive():
    """端侧 IP 是云端的**前缀**时不得误匹配（IP 前缀不算身份）。

    反例（2026-10-31）：旧夹具把 IP 写成脱敏占位串 `REDACTED-INTERNAL-IP-1`。
    那不是合法 IPv4，`assert_matches` 的 `_valid_ipv4s` 正确地拒掉它 —— 于是
    「精确匹配」断言永远无法满足（假红）。本测试的意图是：端侧 `192.168.1.5`
    不得与云端 `192.168.1.50-prod` 前缀匹配；精确一致的 IPv4 必须匹配。
    产品契约见 orchestrate/endpoint.py `_valid_ipv4s`（只认合法 IPv4）。
    """
    endpoint = Endpoint("wrong", "EDRClient.exe")
    endpoint.identity = lambda *_a: {"ip": "192.168.1.5", "hostname": None}

    # 前缀（192.168.1.5 ⊂ 192.168.1.50）不算同一台机器 -> 必须拒绝
    with pytest.raises(EndpointError):
        endpoint.assert_matches("192.168.1.50-prod")
    # 精确一致的 IPv4 -> 必须匹配
    assert "192.168.1.5" in endpoint.assert_matches({"ip": "192.168.1.5"})


def test_identity_match_ip_and_hostname_boundaries():
    """IP/主机名匹配的边界：相似前缀、IPv6、hostname 词边界、端口 ——
    避免 startswith 假匹配，也不误拒绝精确一致的对象。"""
    def ep(me):
        e = Endpoint("t", "E.exe")
        e.identity = lambda *_a: dict(me)
        return e

    # 相似前缀（更长 octet）不得匹配
    with pytest.raises(EndpointError):
        ep({"ip": "10.0.0.1", "hostname": None}).assert_matches("10.0.0.10-zone")
    # IPv6 期望串不触发 v4 匹配 -> 无命中即拒（不被 v4 逻辑误认）
    with pytest.raises(EndpointError):
        ep({"ip": "192.168.1.5", "hostname": None}).assert_matches({"ip": "fe80::1"})
    # hostname 词边界匹配（全词一致）-> 匹配
    assert ep({"ip": "192.168.1.5", "hostname": "WIN-ABC"}).assert_matches(
        {"hostname": "WIN-ABC"})
    # hostname 前缀（WIN-A ⊄ WIN-ABC 词边界）不得匹配
    with pytest.raises(EndpointError):
        ep({"ip": "192.168.1.5", "hostname": "WIN-ABC"}).assert_matches(
            {"hostname": "WIN-A"})
    # 期望串带端口（192.168.1.5:8080）—— IP 身份仍是 192.168.1.5，精确一致即匹配
    assert "192.168.1.5" in ep({"ip": "192.168.1.5", "hostname": None}).assert_matches(
        "192.168.1.5:8080")


def test_identity_match_refuses_non_ipv4_placeholder():
    """非 IPv4 的脱敏占位串作为 IP 期望时，必须被拒绝而不是当成 IP 匹配
    （_valid_ipv4s 只认合法 IPv4）。这把旧夹具的假想行为钉成「拒」——
    避免将来再把占位串当 IP。"""
    endpoint = Endpoint("wrong", "EDRClient.exe")
    endpoint.identity = lambda *_a: {"ip": "REDACTED-INTERNAL-IP-1", "hostname": None}
    # 占位串不是合法 IPv4：精确 IP 匹配不可能满足 —— 必须拒绝（而不是假绿）
    with pytest.raises(EndpointError):
        endpoint.assert_matches({"ip": "REDACTED-INTERNAL-IP-1"})


def test_attach_updates_process_and_locks_exact_window(monkeypatch):
    endpoint = Endpoint("target", "Old.exe")
    endpoint.session_id = "session"
    calls = []

    def fake_call(tool, args=None, timeout=None):
        calls.append((tool, args))
        return {"ok": True}

    monkeypatch.setattr(endpoint, "call", fake_call)
    endpoint.attach("EDRClient.exe", "^日志中心$")

    assert endpoint.process_name == "EDRClient.exe"
    assert endpoint.window_re == "^日志中心$"
    assert calls == [
        ("connect", {"process_name": "EDRClient.exe", "title_re": "^日志中心$"}),
        ("lock_window", {
            "process_name": "EDRClient.exe", "strict": True,
            "title_re": "^日志中心$",
        }),
        ("verify_window_lock", {"activate": True}),
    ]


def test_click_rejects_ambiguous_text(monkeypatch):
    endpoint = Endpoint("target", "EDRClient.exe", home_window="Main")
    endpoint.session_id = "session"

    def fake_call(tool, args=None, timeout=None):
        if tool == "verify_window_lock":
            return {"ok": True}
        if tool == "dump_tree":
            return {"ok": True, "controls": [
                {"control_id": 1, "text": "刷新"},
                {"control_id": 2, "text": "刷新"},
            ]}
        raise AssertionError(f"不应调用 {tool}")

    monkeypatch.setattr(endpoint, "call", fake_call)
    with pytest.raises(EndpointError, match="匹配 2 个"):
        endpoint.click("刷新")


def test_click_uses_fresh_unique_semantic_target_and_reobserves(monkeypatch):
    endpoint = Endpoint("target", "EDRClient.exe", home_window="Main")
    endpoint.session_id = "session"
    dumps = iter([
        {"ok": True, "controls": [
            {"control_id": 7, "text": "刷新", "automation_id": "refreshButton"},
        ]},
        {"ok": True, "controls": [
            {"control_id": 7, "text": "已刷新", "automation_id": "refreshButton"},
        ]},
    ])
    click_args = []

    def fake_call(tool, args=None, timeout=None):
        if tool == "verify_window_lock":
            return {"ok": True}
        if tool == "dump_tree":
            return next(dumps)
        if tool == "click":
            click_args.append(args)
            return {"ok": True, "method": "uia_invoke"}
        raise AssertionError(tool)

    monkeypatch.setattr(endpoint, "call", fake_call)
    result = endpoint.click("刷新")

    assert click_args == [{
        "automation_id": "refreshButton",
        "expected_process_name": "EDRClient.exe",
    }]
    assert result["observation_changed"] is True


def test_table_rows_uses_one_display_value_per_control(monkeypatch):
    endpoint = Endpoint("target", "EDRClient")
    monkeypatch.setattr(endpoint, "tree", lambda *_args, **_kwargs: {
        "ok": True,
        "controls": [
            {"title": "2026-08-15 12:00:00", "text": "2026-08-15 12:00:00"},
            {"title": "策略更新", "text": "策略更新"},
            {"title": "成功", "text": "成功"},
        ],
    })

    assert endpoint.table_rows(cols=3) == [
        "2026-08-15 12:00:00 | 策略更新 | 成功"
    ]


def _recording():
    return {
        "startUrl": "https://cloud.example/",
        "net": [
            {"id": 1, "phase": "req", "method": "POST",
             "url": "https://cloud.example/api/policy", "body": '{"enabled":true}'},
            {"requestId": 1, "phase": "res", "method": "POST",
             "url": "https://cloud.example/api/policy", "status": 200},
        ],
    }


def test_recording_contract_selects_request_and_guards_writes():
    contract = RecordingContract.from_dict(_recording())
    request = contract.one(method="POST", url_contains="/api/policy")
    assert request.json_body == {"enabled": True}
    assert request.response_status == 200

    with pytest.raises(RecordingContractError, match="拒绝重放写请求"):
        contract.replay(lambda **_: None, method="POST", url_contains="/api/policy")

    with pytest.raises(RecordingContractError, match="显式提供目标 url"):
        contract.replay(
            lambda **_: None, method="POST", url_contains="/api/policy",
            allow_write=True,
        )


def test_recording_contract_replays_through_caller_authenticated_sender():
    contract = RecordingContract.from_dict(_recording())
    sent = []

    result = contract.replay(
        lambda **kwargs: sent.append(kwargs) or {"status": 200},
        method="POST", url_contains="/api/policy", allow_write=True,
        url="https://staging.example/api/policy",
    )

    assert result == {"status": 200}
    assert sent == [{
        "method": "POST",
        "url": "https://staging.example/api/policy",
        "json": {"enabled": True},
    }]
