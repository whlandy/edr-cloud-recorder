"""DOM 点击失败时使用录制的渲染模板做安全视觉回退。"""

from __future__ import annotations

from dataclasses import dataclass
from math import log
from pathlib import Path
from typing import Any


SCALE_FACTORS = (0.80, 0.90, 1.0, 1.10, 1.25)
MATCH_THRESHOLD = 0.80
AMBIGUITY_MARGIN = 0.04
VERIFY_THRESHOLD = 0.65


class VisualMatchError(RuntimeError):
    pass


class VisualAbsent(VisualMatchError):
    """页面上没有足够像这个模板的东西 —— 目标大概率不存在。"""


class VisualAmbiguous(VisualMatchError):
    """有多个同样像的候选 —— 目标**存在**，只是分不清是哪一个。

    和 VisualAbsent 分开是有实际后果的：可选步骤只能因为「不存在」被跳过。
    存在却分不清还跳过的话，那一步实际没做，而后面的步骤会以千奇百怪的方式
    失败（最典型的是弹窗没关掉，遮罩把后续点击全吞了）。
    """


@dataclass(frozen=True)
class VisualMatch:
    x: int
    y: int
    w: int
    h: int
    score: float
    second_score: float
    verify_score: float
    scale: float


@dataclass(frozen=True)
class VisualTarget:
    x: float
    y: float
    kind: str
    match: VisualMatch


def _deps():
    try:
        import cv2
        import numpy as np
    except ImportError as e:
        raise VisualMatchError(
            "视觉回退需要 opencv-python-headless 和 numpy"
        ) from e
    return cv2, np


def _decode_png(data: bytes):
    cv2, np = _deps()
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise VisualMatchError("无法解码当前页面截图")
    return image


def _ssim(a, b) -> float:
    cv2, np = _deps()
    a, b = a.astype(np.float32), b.astype(np.float32)
    c1, c2 = 6.5025, 58.5225
    mu_a = cv2.GaussianBlur(a, (11, 11), 1.5)
    mu_b = cv2.GaussianBlur(b, (11, 11), 1.5)
    sigma_a = cv2.GaussianBlur(a * a, (11, 11), 1.5) - mu_a * mu_a
    sigma_b = cv2.GaussianBlur(b * b, (11, 11), 1.5) - mu_b * mu_b
    sigma_ab = cv2.GaussianBlur(a * b, (11, 11), 1.5) - mu_a * mu_b
    score = ((2 * mu_a * mu_b + c1) * (2 * sigma_ab + c2)) / (
        (mu_a * mu_a + mu_b * mu_b + c1) * (sigma_a + sigma_b + c2)
    )
    return float(np.clip(np.mean(score), -1.0, 1.0))


def _iou(a: dict, b: dict) -> float:
    left, top = max(a["x"], b["x"]), max(a["y"], b["y"])
    right = min(a["x"] + a["w"], b["x"] + b["w"])
    bottom = min(a["y"] + a["h"], b["y"] + b["h"])
    intersection = max(0, right - left) * max(0, bottom - top)
    union = a["w"] * a["h"] + b["w"] * b["h"] - intersection
    return intersection / union if union else 0.0


def _top_candidates(screen, template, scales: tuple[float, ...],
                    expected_scale: float) -> list[dict]:
    cv2, np = _deps()
    screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
    template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    # Browser text rasterization changes across DPR. A small low-pass filter removes
    # subpixel differences; the unfiltered patch is still used for final verification.
    screen_filtered = cv2.GaussianBlur(screen_gray, (3, 3), 0)
    candidates = []
    for scale in scales:
        w = max(2, round(template.shape[1] * scale))
        h = max(2, round(template.shape[0] * scale))
        if w > screen.shape[1] or h > screen.shape[0]:
            continue
        resized = cv2.resize(template_gray, (w, h), interpolation=(
            cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
        ))
        if float(resized.std()) < 5:
            response = 1.0 - cv2.matchTemplate(
                screen_gray, resized, cv2.TM_SQDIFF_NORMED
            )
        else:
            filtered = cv2.GaussianBlur(resized, (3, 3), 0)
            response = cv2.matchTemplate(
                screen_filtered, filtered, cv2.TM_CCOEFF_NORMED
            )
        response = np.nan_to_num(response, nan=-1.0, posinf=-1.0, neginf=-1.0)
        for _ in range(2):
            _, score, _, loc = cv2.minMaxLoc(response)
            candidates.append({
                "x": loc[0], "y": loc[1], "w": w, "h": h,
                "score": float(score), "scale": scale,
            })
            x1, y1 = max(0, loc[0] - w // 2), max(0, loc[1] - h // 2)
            x2 = min(response.shape[1], loc[0] + w // 2 + 1)
            y2 = min(response.shape[0], loc[1] + h // 2 + 1)
            response[y1:y2, x1:x2] = -1

    unique = []
    ranked = sorted(
        candidates,
        # Break near-ties toward the recorded scale; flat patches can score 1.0
        # at every smaller size that fits inside the same solid-color region.
        key=lambda c: c["score"] - 0.001 * abs(log(c["scale"] / expected_scale)),
        reverse=True,
    )
    for candidate in ranked:
        if all(_iou(candidate, kept) < 0.35 for kept in unique):
            unique.append(candidate)
    return unique



# 几何消歧要求「明显更近」的倍数。取 2 是想让它只在差距一目了然时才生效：
# 两个弹窗一左一右、距离差几倍，可以判；两个并排的图标差 10%，不该判。
GEOMETRY_MARGIN_RATIO = 2.0


def _nearest_candidate(candidates, expected_point, ambiguity_margin):
    """分数分不出高下时，用录制时的位置挑一个。

    只在**位置差距一目了然**时才给结论：最近的那个必须比次近的近一倍以上。
    否则返回 None，让调用方照常报歧义 —— 位置本身也分不清的时候，
    硬挑一个就成了猜，而猜错的表现是「点了另一个弹窗」这种最难查的错。

    只在分数已经过阈值的候选里挑：几何是**第二**判据，不能让它把一个
    根本不像的东西拉进来。
    """
    if expected_point is None:
        return None
    tied = [c for c in candidates if candidates[0]["score"] - c["score"] < ambiguity_margin]
    if len(tied) < 2:
        return None

    def distance(c):
        cx, cy = c["x"] + c["w"] / 2, c["y"] + c["h"] / 2
        return ((cx - expected_point[0]) ** 2 + (cy - expected_point[1]) ** 2) ** 0.5

    ranked = sorted(tied, key=distance)
    nearest, runner_up = distance(ranked[0]), distance(ranked[1])
    if nearest * GEOMETRY_MARGIN_RATIO > runner_up:
        return None          # 位置也分不出高下
    return ranked[0]


def locate_template(
    screenshot: bytes,
    template_path: str | Path,
    *,
    expected_scale: float = 1.0,
    threshold: float = MATCH_THRESHOLD,
    ambiguity_margin: float = AMBIGUITY_MARGIN,
    verify_threshold: float = VERIFY_THRESHOLD,
    expected_point: tuple[float, float] | None = None,
) -> VisualMatch:
    """在 viewport 截图中定位模板；低分或不唯一时拒绝返回坐标。

    expected_point 是录制时该元素在截图坐标系里的位置。分数分不出高下时，
    用它当第二判据 —— 这不是放宽阈值：歧义仍然被识别，只是多了一个正当的
    区分依据。两个长得一模一样的弹窗关闭图标，位置是它们唯一的区别。
    """
    cv2, np = _deps()
    screen = _decode_png(screenshot)
    template = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
    if template is None:
        raise VisualMatchError(f"无法读取视觉模板 {template_path}")

    scales = tuple(sorted({
        round(expected_scale * factor, 4) for factor in SCALE_FACTORS
    }))
    candidates = _top_candidates(screen, template, scales, expected_scale)
    if not candidates:
        raise VisualMatchError(f"模板大于 viewport 或没有候选：{template_path}")

    best = candidates[0]
    second_score = candidates[1]["score"] if len(candidates) > 1 else -1.0
    if best["score"] < threshold:
        raise VisualAbsent(
            f"视觉匹配分数不足：best={best['score']:.3f} < {threshold:.3f}，"
            f"template={template_path}"
        )
    if best["score"] - second_score < ambiguity_margin:
        resolved = _nearest_candidate(candidates, expected_point, ambiguity_margin)
        if resolved is None:
            raise VisualAmbiguous(
                f"视觉匹配不唯一：best={best['score']:.3f}，second={second_score:.3f}，"
                f"margin<{ambiguity_margin:.3f}，template={template_path}"
                + ("" if expected_point is None else "；且几何位置也分不出高下")
            )
        best = resolved

    patch = screen[best["y"]:best["y"] + best["h"],
                   best["x"]:best["x"] + best["w"]]
    normalized = cv2.resize(patch, (template.shape[1], template.shape[0]))
    gray_a = cv2.cvtColor(normalized, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    structural = _ssim(gray_a, gray_b)
    color = 1.0 - float(np.mean(cv2.absdiff(normalized, template))) / 255.0
    verify = 0.7 * structural + 0.3 * color
    if verify < verify_threshold:
        raise VisualMatchError(
            f"视觉候选复核失败：verify={verify:.3f} < {verify_threshold:.3f}，"
            f"template={template_path}"
        )

    return VisualMatch(
        x=best["x"], y=best["y"], w=best["w"], h=best["h"],
        score=best["score"], second_score=second_score,
        verify_score=verify, scale=best["scale"],
    )


def visual_click(
    page: Any,
    locator: Any,
    *,
    template_root: str | Path,
    ui: dict,
    dom_timeout: float = 1_500,
    click_count: int = 1,
) -> str:
    """优先 DOM 点击；DOM 无法唯一定位时使用视觉候选。"""
    try:
        locator.wait_for(state="visible", timeout=dom_timeout)
    except Exception as dom_error:
        return _visual_fallback(page, template_root, ui, dom_error, click_count)

    # wait_for 成功后 click 可能已经发出请求才因导航等原因报错，不能再视觉补点。
    locator.dblclick() if click_count == 2 else locator.click()
    return "dom"


def _visual_fallback(page: Any, template_root: str | Path,
                     ui: dict, dom_error: Exception, click_count: int) -> str:
    try:
        target = locate_visual_target(page, template_root=template_root, ui=ui)
    except VisualMatchError as error:
        raise error from dom_error
    if click_count == 2:
        page.mouse.click(target.x, target.y, click_count=2)
    else:
        page.mouse.click(target.x, target.y)
    print(
        f"[visual] {target.kind} score={target.match.score:.3f} "
        f"verify={target.match.verify_score:.3f} "
        f"click=({target.x:.1f},{target.y:.1f})"
    )
    return "visual"


def locate_visual_target(page: Any, *, template_root: str | Path,
                         ui: dict) -> VisualTarget:
    """Locate a recorded target without performing its action."""
    templates = ui.get("templates") or {}
    if not templates or not ui.get("pageRect"):
        raise VisualMatchError("录制步骤没有可用视觉模板")

    screenshot = page.screenshot()
    viewport = page.evaluate("() => ({width: innerWidth, height: innerHeight})")
    size = _png_dimensions(screenshot)
    current_px_per_css = size[0] / float(viewport["width"])
    recorded_element = templates.get("element") or {}
    recorded_width = float(recorded_element.get("width") or 0)
    recorded_css_width = float(ui["pageRect"].get("width") or 0)
    if not recorded_width or not recorded_css_width:
        raise VisualMatchError("视觉模板缺少录制尺寸，无法计算尺度")
    expected_scale = current_px_per_css / (recorded_width / recorded_css_width)

    errors = []
    for kind in ("context", "element"):
        meta = templates.get(kind)
        if not meta:
            continue
        path = Path(template_root) / meta["path"]
        # 录制时该元素在**截图像素坐标**里的中心。pageRect 是 CSS 坐标，
        # 截图按设备像素比放大过，所以要用当前实测的 px/css 换算 ——
        # 不能直接用 deviceScaleFactor：录制机和回放机可能不同。
        rect = ui.get("pageRect") or {}
        offset = (meta.get("elementOffset") or {}) if kind == "context" else {}
        # 取不到坐标就不做几何消歧。它是**第二**判据，缺了只是少一个区分依据，
        # 绝不能因为它自己算不出来而让整条视觉定位失败。
        expected_point = None
        if rect.get("x") is not None and rect.get("y") is not None:
            expected_point = (
                (float(rect["x"]) + float(rect.get("width", 0)) / 2
                 - float(offset.get("x", 0))) * current_px_per_css,
                (float(rect["y"]) + float(rect.get("height", 0)) / 2
                 - float(offset.get("y", 0))) * current_px_per_css,
            )
        try:
            match = locate_template(
                screenshot, path, expected_scale=expected_scale,
                expected_point=expected_point,
            )
        except VisualMatchError as e:
            errors.append(str(e))
            continue

        rx = float((ui.get("click") or {}).get("rx", 0.5))
        ry = float((ui.get("click") or {}).get("ry", 0.5))
        if kind == "context":
            offset = meta.get("elementOffset") or {"x": 0, "y": 0}
            px = match.x + (float(offset["x"]) + rx * recorded_element["width"]) * match.scale
            py = match.y + (float(offset["y"]) + ry * recorded_element["height"]) * match.scale
        else:
            px, py = match.x + rx * match.w, match.y + ry * match.h
        x, y = px / current_px_per_css, py / current_px_per_css
        if not (0 <= x < viewport["width"] and 0 <= y < viewport["height"]):
            raise VisualMatchError(f"视觉点击点越界：({x:.1f}, {y:.1f})")
        return VisualTarget(x=x, y=y, kind=kind, match=match)

    raise VisualMatchError("视觉定位失败：" + "；".join(errors))


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise VisualMatchError("页面截图不是有效 PNG")
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
