#!/usr/bin/env python3
"""
示例：云端改一个开关 → 到终端的操作日志里验证它真的落地了 → 始终还原

这是「云操作 + 端操作交替」最有说服力的一类用例：
只验云端，你只知道服务端记下了；只验端侧，你不知道是谁改的。两边串起来才证明链路通。

**这是模板，不是能直接跑的脚本。** 云端那部分（CloudClient）需要你按自己的
接口实现；端侧那部分是通用的。标了 ← 改这里 的地方是产品相关的。
还原注册为 cleanup，不是普通末尾步骤，因此中途失败或 Ctrl-C 也会执行。

配套阅读：
  references/endpoint-orchestration.md   edr-wd 怎么装、怎么配合
  references/ui-assertions.md            为什么断言点要选日志而不是开关状态
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scenario import Scenario          # noqa: E402
from endpoint import Endpoint          # noqa: E402

# ── 改这里：被测产品的进程与窗口 ───────────────────────────────
CLIENT_PROCESS = "YourClient.exe"       # 带界面的那个进程
HOME_WINDOW = "主窗口标题"               # 首页窗口标题的匹配式
LOG_ENTRY = "打开日志页面的按钮文本"        # 例如 "Log Center"
LOG_WINDOW = "日志窗口标题"               # 日志通常是同进程下的另一个顶层窗口
REFRESH = "刷新按钮文本"                  # 例如 "Refresh"
LOG_MARK = "预期日志里的关键字"            # 例如 "Self-protection update status"


class CloudClient:
    """
    ← 改这里：换成你自己的云端客户端。

    需要四个能力：
      find_object(keyword)          按名称找到目标对象，返回含 name/id 的 dict
      read_config(obj)              读当前配置
      write_config(obj, cfg)        下发配置
      confirm_written(obj, cfg)     回读确认云端已记录（不是"终端已生效"）
    """

    def find_object(self, keyword: str) -> dict: raise NotImplementedError
    def read_config(self, obj: dict) -> dict: raise NotImplementedError
    def write_config(self, obj: dict, cfg: dict) -> None: raise NotImplementedError
    def confirm_written(self, obj: dict, cfg: dict) -> None: raise NotImplementedError

    @staticmethod
    def flip(cfg: dict) -> dict:
        """← 改这里：产生一个与当前不同的配置，用来观察变化"""
        raise NotImplementedError


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--object", required=True, help="云端对象的名称片段")
    ap.add_argument("--target", required=True, help="edr-wd 配置里的目标名")
    ap.add_argument("--dry-run", action="store_true", help="只跑云端，不碰端侧")
    a = ap.parse_args()

    cloud = CloudClient()
    obj = cloud.find_object(a.object)
    print(f"目标对象：{obj['name']}")

    ep = Endpoint(a.target, process_name=CLIENT_PROCESS, home_window=HOME_WINDOW)
    st: dict = {}

    def read_cloud():
        st["origin"] = cloud.read_config(obj)
        return "已记录原始配置"

    def connect_endpoint():
        ep.connect()
        # 云端对象与端侧机器必须是同一台，否则整条链路会"跑通"但验的是无关设备
        return ep.assert_matches(obj["name"])

    def baseline_log():
        ep.click(LOG_ENTRY)                    # 日志通常开在独立窗口里
        ep.attach(CLIENT_PROCESS, LOG_WINDOW)  # 重新绑定并锁定日志窗口
        st["log0"] = set(ep.table_rows(refresh_text=REFRESH))
        return f"基线 {len(st['log0'])} 行"

    def apply_change():
        st["want"] = cloud.flip(st["origin"])
        st["write_attempted"] = True
        cloud.write_config(obj, st["want"])
        cloud.confirm_written(obj, st["want"])
        return "已下发，云端回读一致"

    def new_log_arrived() -> bool:
        # refresh_text 不能省：表格不会自己重画，不刷新就永远是旧内容。
        # 只在目标日志出现时结束；无关后台日志不能提前终止轮询。
        st["new"] = [x for x in ep.table_rows(refresh_text=REFRESH) if x not in st["log0"]]
        st["marked"] = [x for x in st["new"] if LOG_MARK in x]
        return bool(st["marked"])

    def assert_log():
        if not st.get("marked"):
            raise AssertionError(f"端侧有新日志但不含预期内容。新增：{st['new'][:3]}")
        return st["marked"][0]

    def restore():
        if not st.get("write_attempted"):
            return "未尝试写入，无需还原"
        cloud.write_config(obj, st["origin"])
        cloud.confirm_written(obj, st["origin"])
        return "已还原"

    sc = Scenario(f"云→端联动（{obj['name']}）")
    sc.cloud("读取当前配置", read_cloud)
    if not a.dry_run:
        sc.endpoint("连接并校验身份", connect_endpoint)
        sc.endpoint("打开日志并采基线", baseline_log)
    sc.cloud("下发变更", apply_change)
    if not a.dry_run:
        # 间隔按被观测对象的更新节奏定：立即生效的用 5s，心跳驱动的用 10s 起步
        sc.until("等待端侧出现新记录", new_log_arrived, timeout=90, interval=5)
        sc.endpoint("断言新记录符合预期", assert_log)
    sc.cleanup("还原原始配置", restore, side="cloud")

    return 0 if sc.run() else 1


if __name__ == "__main__":
    sys.exit(main())
