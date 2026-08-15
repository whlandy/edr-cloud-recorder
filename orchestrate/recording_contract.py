"""把 recorder 的原始 JSON 变成可供云端客户端使用的请求契约。"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class RecordingContractError(RuntimeError):
    """录制数据不能唯一、安全地转换为云端请求。"""


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    url: str
    body: str | bytes | None
    response_status: int | None

    @property
    def json_body(self) -> Any:
        if self.body is None or isinstance(self.body, bytes):
            return None
        try:
            return json.loads(self.body)
        except (TypeError, json.JSONDecodeError) as e:
            raise RecordingContractError(f"{self.method} {self.url} 的请求体不是 JSON") from e

class RecordingContract:
    """查询和显式重放 recorder 捕获的请求；调用方负责认证与环境选择。"""

    def __init__(self, requests: list[RecordedRequest]):
        self.requests = requests

    @classmethod
    def load(cls, path: str | Path) -> "RecordingContract":
        source = Path(path)
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise RecordingContractError(f"无法读取录制文件 {source}: {e}") from e
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "RecordingContract":
        if not isinstance(data, dict) or not isinstance(data.get("net"), list):
            raise RecordingContractError("录制数据缺少 net 数组")

        responses_by_id = {
            event.get("requestId"): event
            for event in data["net"]
            if event.get("phase") == "res" and event.get("requestId") is not None
        }
        requests = []
        for event in data["net"]:
            if event.get("phase") != "req":
                continue
            response = responses_by_id.get(event.get("id"), {})
            body = event.get("body")
            if event.get("bodyBase64"):
                body = base64.b64decode(event["bodyBase64"])
            requests.append(RecordedRequest(
                method=str(event.get("method") or "GET").upper(),
                url=str(event.get("url") or ""),
                body=body,
                response_status=response.get("status"),
            ))
        return cls(requests)

    def one(self, *, method: str | None = None,
            url_contains: str | None = None) -> RecordedRequest:
        wanted_method = method.upper() if method else None
        matches = [
            request for request in self.requests
            if (wanted_method is None or request.method == wanted_method)
            and (url_contains is None or url_contains in request.url)
        ]
        if len(matches) != 1:
            sample = [f"{r.method} {r.url}" for r in matches[:5]]
            raise RecordingContractError(
                f"请求选择器必须唯一，实际匹配 {len(matches)} 条：{sample}"
            )
        return matches[0]

    def replay(self, send: Callable[..., Any], *, method: str,
               url_contains: str, allow_write: bool = False,
               url: str | None = None, body: Any = None) -> Any:
        """通过调用方的已认证 send 执行一条请求；写请求必须显式开闸。"""
        request = self.one(method=method, url_contains=url_contains)
        if request.method != "GET" and not allow_write:
            raise RecordingContractError(
                f"拒绝重放写请求 {request.method} {request.url}；"
                "确认已有基线和 cleanup 后显式传 allow_write=True"
            )
        if request.method != "GET" and url is None:
            raise RecordingContractError(
                "写请求必须显式提供目标 url，不能默认沿用录制环境地址"
            )

        target_url = url or request.url
        kwargs: dict[str, Any] = {"method": request.method, "url": target_url}
        if body is not None:
            kwargs["json"] = body
        elif isinstance(request.body, bytes):
            kwargs["data"] = request.body
        elif request.body is not None:
            try:
                kwargs["json"] = request.json_body
            except RecordingContractError:
                kwargs["data"] = request.body
        return send(**kwargs)
