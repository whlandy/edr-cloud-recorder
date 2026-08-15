# 视觉模板定位设计

状态：第一阶段实现依据  
分支：`maa-dp`

## 目标

录制时保存用户点击元素的真实渲染图；回放时仍优先使用 Playwright 的 role、label、
text 等语义定位，只有普通点击的 DOM 定位失败时才用图片寻找元素并按录制落点点击。

视觉回退必须满足三个安全条件：

1. 最佳候选达到阈值；
2. 最佳候选明显优于第二候选；
3. 匹配框和点击点都在当前 viewport 内。

任一条件不满足就报错，不采用 `.first()`、最低分候选或录制坐标盲点。

## 非目标

- 不用视觉点击取代可靠的 DOM 定位。
- 第一阶段不回退开关、checkbox、radio 等有目标状态的操作。
- 不引入需要训练的目标检测模型。
- 不把 LightGlue、LoFTR 或 GPU 运行时加入默认依赖。
- 不承诺跨明暗主题识别；主题差异应保留不同模板或重新录制。

## 设计依据

Airtest、SikuliX/Oculix 和 Appium Images 的成熟路径都以 OpenCV 模板匹配为主，
按分辨率、位置和尺度缩小搜索，再用颜色或结构信息复核。SIFT/ORB 适合较大且有纹理的
图像，但小尺寸、纯色、扁平 UI 往往没有足够关键点，因此只作为后续降级，不作为主路径。

`maa-fw` 已有标准模板、边缘、SSIM、ORB、pHash 和颜色直方图能力。当前仓库第一阶段
实现一个依赖 OpenCV 的小型匹配器，数据格式保持可直接映射到 `maa-fw`，不把整个
`maa-fw` 或 Airtest 嵌入回放项目。

## 总体流程

```text
录制
  周期性截取当前 viewport，仅在内存保留最近一帧
    -> 点击事件携带元素 rect、viewport、DPR、相对落点
    -> 绑定时间足够接近的点击前帧
    -> 裁 element 模板
    -> 向外扩展 padding，裁 context 模板
    -> JSON 保存模板路径、尺寸、哈希和裁剪关系

回放
  DOM locator.wait_for(visible, timeout=短超时)
    -> 能唯一定位：执行一次 click；click 开始后绝不视觉重试
    -> 不能唯一定位：截当前 viewport
      -> 根据录制位置先构造 ROI
      -> 围绕 DPR 比例做多尺度模板匹配
      -> context 模板优先，element 模板兜底
      -> Top-2 唯一性检查 + RGB/SSIM 复核
      -> 从匹配框恢复元素内相对落点
      -> page.mouse.click
```

## 为什么使用点击前预帧

当前注入层在 `click` 后产生步骤。直接收到步骤后再截元素有两个问题：

- 开关和按钮可能已经切换到点击后状态；
- 导航型点击可能已经卸载原页面。

同步 Playwright 的 `expose_binding` 回调中不能调用截图 API，会产生重入等待。因此驱动层
每隔约 200ms 保存一张仅驻内存的 viewport PNG；binding 回调只把该不可变字节串和步骤
关联起来，主循环稍后裁图。预帧与点击时间差超过上限时不使用，退回到元素截图；截图失败
只缺失模板，不影响操作记录。

这不是录像：任何时刻只保留最近一帧，不持续落盘。

## 模板格式

每个普通点击最多保存两张 PNG：

```text
<name>.assets/
  step-0001.element.png
  step-0001.context.png
```

`element` 用于精确定位；`context` 在四周增加 12 CSS px 左右的稳定背景，解决纯色按钮、
小图标和重复图标特征不足的问题。页面边缘导致 padding 被裁掉时保存实际 padding。

步骤数据：

```json
{
  "type": "click",
  "ui": {
    "rect": {"x": 100, "y": 40, "width": 80, "height": 32},
    "pageRect": {"x": 100, "y": 40, "width": 80, "height": 32},
    "click": {"rx": 0.75, "ry": 0.5},
    "viewport": {"width": 1440, "height": 900},
    "deviceScaleFactor": 2,
    "templates": {
      "element": {"path": "flow.assets/step-0001.element.png", "width": 160, "height": 64},
      "context": {
        "path": "flow.assets/step-0001.context.png",
        "width": 208,
        "height": 112,
        "elementOffset": {"x": 24, "y": 24}
      }
    }
  }
}
```

`pageRect` 是换算到顶层 viewport 的矩形。同源 iframe 可以累加 frame 边界；跨域 iframe
无法安全读取父页面坐标时省略，第一阶段不做视觉回退。

## 成功 trace

录制结束为每个 `test_<name>.py` 额外生成一个 `<name>.trace.json`，二者一一对应。trace 的
`entry` 和 `steps[*].next` 将该脚本的点击、双击、输入、勾选、开关、按键和断言串成完整
成功路径。需要确定位置的步骤包含 `TemplateMatch`；默认先匹配 `context`，失败后再匹配
`element`。输入紧跟同一输入框的点击时复用点击前模板，语义是先匹配并聚焦光标，再执行
`InputText`，避免用已经填入文字的截图匹配空输入框。模板缺失的定位步骤保留为
`missing_template`，并将整条 trace 标记为 `incomplete`，避免轨迹静默缺步。

每个模板都从 `actionT` 之前最近的 viewport 历史帧裁取。延迟上报的 switch 不能使用
当前最新帧，因为该帧可能已经是切换后的状态；动作后的 locator screenshot 也不能作为
check/uncheck/switch 的模板。

脚本关联到动作的网络响应会编译到动作的 `expect.responses`，包含 method、URL、录制时状态码
和请求体。runner 必须先建立监听再执行动作，不能在后继节点才开始等待；否则快速响应会在
监听建立前结束。这样 trace 的“成功”同时覆盖 UI 路径和接口条件，而不只是动作列表。

匹配阈值和尺度来自 `rec_visual.py` 的运行时常量，trace 与 pytest 视觉回退不会各自维护
一套参数。

`replay_trace` 支持两种定位策略：`dom_first` 优先使用语义选择器，定位失败后才匹配模板；
`visual_only` 不构造 DOM locator，只依赖页面截图和模板。两种策略都会先进入 `startUrl`，
再逐步回放；某一步声明了 `expect.responses` 时，必须先建立全部响应监听，然后才执行该步
动作。网络响应按请求发出时刻归属到动作，即使慢响应在下一步录制动作之后才结束，也不会
被错误挂到下一步。

回放结果另存为 `edr.execution-trace/v1`，保留每步状态、实际动作、DOM/视觉定位方式、模板
匹配分数、响应校验、耗时和错误。`evaluate_trace` 对照黄金 trace 输出任务成功、步骤完成率、
动作准确率、网络断言率、额外动作数、重试数和平均视觉匹配分数。网络命中按每个黄金节点
分别计算并以期望数量封顶，额外伪造的成功响应不能抬高得分；任一步失败时 `taskSuccess`
必为 false。

```json
{
  "schema": "edr.success-trace/v1",
  "name": "flow",
  "status": "ready",
  "entry": "step-0001",
  "steps": {
    "step-0001": {
      "status": "ready",
      "sourceStepIds": ["focus-input", "fill-input"],
      "recognition": {
        "type": "TemplateMatch",
        "templateOrder": ["context", "element"],
        "templates": {
          "context": {"path": "flow.assets/step-0001.context.png"},
          "element": {"path": "flow.assets/step-0001.element.png"}
        },
        "threshold": 0.8,
        "ambiguityMargin": 0.04,
        "verifyThreshold": 0.65,
        "scaleFactors": [0.8, 0.9, 1.0, 1.1, 1.25]
      },
      "action": {
        "type": "InputText",
        "param": {"text": "alice", "focusBeforeInput": true}
      },
      "expect": {
        "responses": [{
          "method": "POST",
          "url": "https://app.example/api/save",
          "expectedStatus": 200
        }]
      },
      "next": null
    }
  }
}
```

## 匹配算法

### 搜索范围

第一阶段搜索整个 viewport，才能发现页面其他位置的重复图标并执行全局唯一性检查。
录制位置保留给后续粗到细优化使用，但不能用它绕过全屏 Top-2 检查。页面布局变化很大时，
全屏搜索也比按旧坐标盲点安全。

### 尺度

中心尺度为：

```text
当前截图像素/CSS像素 ÷ 录制截图像素/CSS像素
```

第一阶段搜索中心尺度附近的离散尺度，而不是 Airtest 的全区间细步长扫描。浏览器 UI 通常
只有 DPR、系统缩放和少量 CSS 响应式差异，窄范围更快，也减少错误候选。

### 方法选择

- 正常模板：灰度 `TM_CCOEFF_NORMED`。
- 方差极低的模板：`TM_SQDIFF_NORMED`，转换成“越大越好”的分数。
- 有明显边缘的模板：边缘匹配可产生额外候选，但不能单独决定点击。
- 候选区域缩放到模板尺寸后，用 RGB 差异和 SSIM 做复核。

SSIM 是同尺寸图像的复核指标，不作为全屏滑窗定位器；pHash 同样只适合粗筛或去重。

### 拒绝规则

阈值没有跨站点通用常数，默认值只是保守起点，后续应基于真实录制集校准。第一阶段至少
要求：

- `score >= threshold`；
- `score(top1) - score(top2) >= ambiguity_margin`；
- 候选复核分数达到下限；
- 模板和候选尺寸合法；
- 最终点击点位于匹配区域及 viewport 内。

失败信息必须包含最佳分、第二名分、尺度、模板路径和候选框，便于调阈值而不是猜。

## 回放接口

生成脚本对普通点击使用：

```python
visual_click(
    page,
    locator,
    template=Path(__file__).parent / "flow.assets/step-0001.context.png",
    ui={...},
)
```

函数先用短超时确认 DOM locator 能否唯一且可见。确认成功后只执行一次普通 click；该
click 即使因导航等待等原因报错也不再视觉补点，避免写操作被执行两次。只有定位阶段失败
才加载 OpenCV，因此正常用例不承担图像处理成本。函数返回 `"dom"` 或 `"visual"`。

## 与 maa-fw 的边界

第一阶段匹配器保持纯函数：输入截图、模板和参数，输出候选框与分数。后续端侧 Windows/
macOS 场景可把相同模板交给 `maa-fw` 的 `template_match`、`ssim_match` 或
`feature_match`；网页回放继续直接使用 Playwright 截图和鼠标，不为此启动 MCP 服务。

## 测试与验收

必须覆盖：

- 点击前帧裁出 element/context 两张模板；
- DPR 和 viewport 不同的多尺度命中；
- 页面边缘 padding 正确；
- 两个相同图标因分差不足而拒绝点击；
- DOM 成功时不导入/调用 OpenCV；
- DOM 失败、视觉唯一时按录制相对落点点击；
- 低分、无模板、跨域 iframe 均明确失败且不点击；
- 导航型点击不丢步骤，录制器不闪窗、不死锁。

第一阶段完成标准：全量现有测试通过，新增算法测试使用合成图片稳定复现，真实浏览器自检
至少验证一次 DOM 回退到视觉点击的闭环。
