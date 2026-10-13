from __future__ import annotations

from collections import OrderedDict
import ctypes
from datetime import date
from functools import lru_cache
import gc
import math
import os
from pathlib import Path
import queue
import random
import sys
import threading
import tkinter as tk
from typing import Any, Callable

try:
    from PIL import Image, ImageChops, ImageFilter, ImageMath, ImageOps, ImageTk
except Exception:  # pragma: no cover - packaged client carries Pillow; source fails soft
    Image = ImageChops = ImageFilter = ImageMath = ImageOps = ImageTk = None  # type: ignore[assignment]


_PET_PIL_WORK_LOCK = getattr(Image, "_bidking_pet_work_lock", None)
if _PET_PIL_WORK_LOCK is None:
    _PET_PIL_WORK_LOCK = threading.Lock()
    if Image is not None:
        setattr(Image, "_bidking_pet_work_lock", _PET_PIL_WORK_LOCK)
_PET_BUILD_CONTEXT = threading.local()


class _PetFrameBuildCancelled(Exception):
    pass


def _raise_if_pet_frame_build_cancelled() -> None:
    cancelled = getattr(_PET_BUILD_CONTEXT, "cancelled", None)
    if callable(cancelled) and bool(cancelled()):
        raise _PetFrameBuildCancelled


PET_COLOR_ORDER = ("orange", "navy", "ragdoll")
PET_COLOR_LABELS = {
    "orange": "橙色狸花",
    "navy": "深蓝",
    "ragdoll": "白色布偶",
}
PET_POSE_ORDER = (
    "idle",
    "blink",
    "perk",
    "meow",
    "stretch",
    "lick",
    "sleep",
    "yawn",
    "chase",
)
PET_OPTIONAL_ACTIONS = ("wave", "roll", "scratch")
PET_SETTLEMENT_ACTIONS = ("loss_peek", "loss_droop", "big_win")
PET_EYE_VARIANT_POSES = (
    "idle_eye",
    "perk_eye",
    "meow_eye",
    "stretch_eye",
    "lick_eye",
    "chase_eye",
    "wave_eye",
    "roll_eye",
    "scratch_eye",
)
PET_AUTHORED_TRANSITIONS = {
    "perk": ("perk_enter_1", "perk_enter_2"),
    "meow": ("meow_enter_1", "meow_enter_2"),
    "stretch": ("stretch_enter_1", "stretch_enter_2"),
    "sleep": ("sleep_enter_1", "sleep_enter_2"),
    "wave": ("wave_enter_1", "wave_enter_2"),
    "roll": ("roll_enter_1", "roll_enter_2", "roll_enter_3"),
    "scratch": ("scratch_enter_1", "scratch_enter_2"),
    "loss_peek": (
        "loss_peek_enter_1",
        "loss_peek_enter_2",
        "loss_peek_enter_3",
        "loss_peek_enter_4",
        "loss_peek_enter_5",
    ),
    "loss_droop": (
        "loss_droop_enter_1",
        "loss_droop_enter_2",
        "loss_droop_enter_3",
        "loss_droop_enter_4",
        "loss_droop_enter_5",
    ),
    "big_win": (
        "big_win_enter_1",
        "big_win_enter_2",
        "big_win_enter_3",
        "big_win_enter_4",
        "big_win_enter_5",
    ),
}
PET_TRANSITION_POSE_ORDER = tuple(
    pose
    for transition_poses in PET_AUTHORED_TRANSITIONS.values()
    for pose in transition_poses
)


def _resolve_pet_asset_dir() -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        bundled = Path(bundle_root) / "hero_ref" / "assets" / "pet_companion"
        if bundled.is_dir():
            return bundled
    return Path(__file__).resolve().parent.parent / "assets" / "pet_companion"


PET_ASSET_DIR = _resolve_pet_asset_dir()
PET_WINDOW_SIZE = 176
PET_SPRITE_SIZE = 164
PET_TRANSPARENT_KEY = "#010203"
PET_DRAG_THRESHOLD = 5
PET_INTRO_TEXT = "我只负责陪伴，不会影响报价喵～"
PET_TRANSITION_STEPS = 4
PET_ACTION_EYE_BEAT_CHANCE = 0.68
PET_COLORKEY_ALPHA_THRESHOLD = 112
PET_FRAME_RESULT_POLL_MS = 8
PET_TK_FRAME_CACHE_LIMIT = 72
PET_TK_TRANSITION_CACHE_LIMIT = 128
PET_PREPARED_TRANSITION_CACHE_LIMIT = 128
PET_WORKER_SHUTDOWN_TIMEOUT_SECONDS = 0.5
PET_TRANSPARENCY_STATES = (
    "ok",
    "unsupported",
    "set_failed",
    "readback_mismatch",
    "native_key_mismatch",
)

_WINDOWS_GWL_EXSTYLE = -20
_WINDOWS_WS_EX_LAYERED = 0x00080000
_WINDOWS_LWA_COLORKEY = 0x00000001
_WINDOWS_LWA_ALPHA = 0x00000002

PET_BASE_ACTIONS = ("perk", "meow", "stretch", "lick", "sleep", "yawn", "tilt", "chase")
PET_ACTIONS = (*PET_BASE_ACTIONS, *PET_OPTIONAL_ACTIONS)
PET_ACTION_BUBBLES = {
    "meow": ("喵～", "喵喵～", "呼噜噜～"),
    "stretch": ("伸个懒腰～",),
    "lick": ("认真洗脸中",),
    "sleep": ("困困…",),
    "yawn": ("哈欠～",),
    "tilt": ("嗯？",),
    "chase": ("抓到尾巴了吗？",),
    "eat": ("好吃！",),
    "perk": ("好耶！",),
    "wave": ("嗨～", "给你爪爪～"),
    "roll": ("翻个身～", "肚皮给你看～"),
    "scratch": ("挠挠耳朵～",),
    "loss_peek": ("差一点点…",),
    "loss_droop": ("呜…下局再来",),
    "big_win": ("太棒啦！",),
}


def normalize_pet_color(value: Any) -> str:
    color = str(value or "").strip().lower()
    return color if color in PET_COLOR_ORDER else "ragdoll"


def normalize_pet_position(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        return int(value[0]), int(value[1])
    except (TypeError, ValueError):
        return None


def normalize_pet_touch_state(
    date_value: Any,
    today_value: Any,
    total_value: Any,
    *,
    current_date: str | None = None,
) -> tuple[str, int, int]:
    today_date = str(current_date or date.today().isoformat())
    try:
        today_count = max(0, int(today_value or 0))
    except (TypeError, ValueError):
        today_count = 0
    try:
        total_count = max(0, int(total_value or 0))
    except (TypeError, ValueError):
        total_count = 0
    if str(date_value or "") != today_date:
        today_count = 0
    total_count = max(total_count, today_count)
    return today_date, today_count, total_count


def clamp_pet_position(
    x: int,
    y: int,
    *,
    width: int,
    height: int,
    work_area: tuple[int, int, int, int],
) -> tuple[int, int]:
    left, top, right, bottom = (int(part) for part in work_area)
    available_width = max(1, right - left)
    available_height = max(1, bottom - top)
    final_x = left if width >= available_width else max(left, min(int(x), right - width))
    final_y = top if height >= available_height else max(top, min(int(y), bottom - height))
    return final_x, final_y


def system_reduced_motion_enabled() -> bool:
    """Best-effort Windows reduced-motion detection.

    SPI_GETCLIENTAREAANIMATION reports whether client-area animation is enabled.
    Failure stays animation-on so an unavailable OS probe never breaks the pet.
    """

    if os.name != "nt":
        return False
    try:
        enabled = ctypes.c_int(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(
            0x1042,  # SPI_GETCLIENTAREAANIMATION
            0,
            ctypes.byref(enabled),
            0,
        )
        return bool(ok) and not bool(enabled.value)
    except Exception:
        return False


def pet_asset_path(color: Any, pose: Any | None = None) -> Path:
    coat = normalize_pet_color(color)
    pose_name = str(pose or "").strip().lower()
    if (
        pose_name in PET_POSE_ORDER
        or pose_name in PET_OPTIONAL_ACTIONS
        or pose_name in PET_SETTLEMENT_ACTIONS
        or pose_name in PET_EYE_VARIANT_POSES
        or pose_name in PET_TRANSITION_POSE_ORDER
    ):
        return PET_ASSET_DIR / f"cat_{coat}_{pose_name}.png"
    return PET_ASSET_DIR / f"cat_{coat}.png"


def pet_actions_for_color(color: Any) -> tuple[str, ...]:
    coat = normalize_pet_color(color)
    optional = tuple(
        action
        for action in PET_OPTIONAL_ACTIONS
        if all(
            pet_asset_path(coat, pose).is_file()
            for pose in (action, *PET_AUTHORED_TRANSITIONS[action])
        )
    )
    return (*PET_BASE_ACTIONS, *optional)


def pet_assets_available() -> bool:
    return Image is not None and ImageTk is not None and all(
        pet_asset_path(color, pose).is_file()
        for color in PET_COLOR_ORDER
        for pose in PET_POSE_ORDER
    )


@lru_cache(
    maxsize=len(PET_COLOR_ORDER)
    * (
        len(PET_POSE_ORDER)
        + len(PET_OPTIONAL_ACTIONS)
        + len(PET_SETTLEMENT_ACTIONS)
        + len(PET_EYE_VARIANT_POSES)
        + len(PET_TRANSITION_POSE_ORDER)
    )
)
def _load_pet_asset(color: str, pose: str) -> Any:
    if Image is None:
        raise RuntimeError("Pillow is unavailable")
    _raise_if_pet_frame_build_cancelled()
    with Image.open(pet_asset_path(color, pose)) as source:
        loaded = source.convert("RGBA").copy()
    _raise_if_pet_frame_build_cancelled()
    return loaded


def _fit_sprite(image: Any, size: int) -> Any:
    if ImageOps is None:
        raise RuntimeError("Pillow is unavailable")
    return ImageOps.contain(image, (size, size), method=Image.Resampling.LANCZOS)


def _prepare_colorkey_frame(frame: Any) -> Any:
    """Prepare an RGBA sprite for Tk's exact-color window transparency.

    Windows removes only the canvas color key. If ImageTk first blends a partial
    alpha pixel into that near-black key, the blended pixel remains as a dark
    halo on pale desktops. Keep authored source frames untouched, but make the
    final Tk frame binary-alpha and gently lift only its outermost dark ring.
    Interior pixel-art linework, eyes, whiskers, and coat shadows are preserved.
    """

    if (
        Image is None
        or ImageChops is None
        or ImageFilter is None
        or ImageMath is None
        or ImageOps is None
    ):
        raise RuntimeError("Pillow is unavailable")
    rgba = frame.convert("RGBA")
    alpha = rgba.getchannel("A")
    hard_alpha = alpha.point(
        lambda value: 255 if value >= PET_COLORKEY_ALPHA_THRESHOLD else 0
    )
    rgb = rgba.convert("RGB")
    luminance = ImageOps.grayscale(rgb)
    interior = hard_alpha.filter(ImageFilter.MinFilter(7))
    outer_ring = ImageChops.subtract(hard_alpha, interior)
    dark_edge = luminance.point(lambda value: 255 if value < 82 else 0)

    # Normalize a small foreground-only blur so transparent black cannot darken
    # the sampled coat. This keeps the new edge in the cat's own palette rather
    # than introducing a white or channel-wise colored fringe.
    blur = ImageFilter.GaussianBlur(3.0)
    blurred_weight = interior.filter(blur)
    nearby_coat = Image.merge(
        "RGB",
        tuple(
            ImageMath.lambda_eval(
                lambda args: (args["color"] * 255) / (args["weight"] + 1),
                color=ImageChops.multiply(channel, interior).filter(blur),
                weight=blurred_weight,
            ).convert("L")
            for channel in rgb.split()
        ),
    )
    lift = ImageChops.subtract(
        ImageOps.grayscale(nearby_coat),
        luminance,
    ).point(lambda value: 255 if value >= 8 else 0)
    soften_mask = ImageChops.multiply(
        ImageChops.multiply(outer_ring, dark_edge),
        lift,
    )
    softened = Image.blend(rgb, nearby_coat, 0.82)
    rgb.paste(softened, (0, 0), soften_mask)
    output = rgb.convert("RGBA")
    output.putalpha(hard_alpha)
    return output


def _compose_frame(
    base: Any,
    *,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    offset_x: int = 0,
    offset_y: int = 0,
    rotate: float = 0.0,
) -> Any:
    if Image is None:
        raise RuntimeError("Pillow is unavailable")
    width = max(1, int(round(base.width * float(scale_x))))
    height = max(1, int(round(base.height * float(scale_y))))
    frame_sprite = base.resize((width, height), Image.Resampling.LANCZOS)
    if rotate:
        frame_sprite = frame_sprite.rotate(
            float(rotate),
            resample=Image.Resampling.NEAREST,
            expand=False,
            fillcolor=(0, 0, 0, 0),
        )
    frame = Image.new("RGBA", (PET_WINDOW_SIZE, PET_WINDOW_SIZE), (0, 0, 0, 0))
    x = (PET_WINDOW_SIZE - frame_sprite.width) // 2 + int(offset_x)
    y = PET_WINDOW_SIZE - frame_sprite.height - 3 + int(offset_y)
    frame.alpha_composite(frame_sprite, (x, y))
    return frame


def _ease_pose_frame(
    frame: Any,
    *,
    scale_x: float,
    scale_y: float,
    offset_y: int,
) -> Any:
    if Image is None:
        raise RuntimeError("Pillow is unavailable")
    bbox = frame.getchannel("A").getbbox()
    if bbox is None:
        return frame.copy()
    sprite = frame.crop(bbox)
    width = max(1, int(round(sprite.width * float(scale_x))))
    height = max(1, int(round(sprite.height * float(scale_y))))
    sprite = sprite.resize((width, height), Image.Resampling.LANCZOS)
    output = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    # Preserve the visible sprite's own centre and ground line. Re-anchoring every
    # transition to the window bottom made an idle cat jump 7 px even though the
    # Toplevel itself never moved.
    center_x = (int(bbox[0]) + int(bbox[2])) / 2.0
    x = int(round(center_x - width / 2.0))
    y = int(bbox[3]) - height + int(offset_y)
    output.alpha_composite(sprite, (x, y))
    return output


def _align_frame_ground(frame: Any, ground_y: int) -> Any:
    """Keep authored settlement frames on the awake cat's visible ground line."""

    if Image is None:
        raise RuntimeError("Pillow is unavailable")
    bbox = frame.getchannel("A").getbbox()
    if bbox is None:
        return frame.copy()
    offset_y = int(ground_y) - int(bbox[3])
    if offset_y == 0:
        return frame
    output = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    output.alpha_composite(frame, (0, offset_y))
    return output


def _build_pose_transition(source: Any, target: Any, step: int) -> Any:
    # A direct dissolve between two illustrated poses produces two faces/tails at
    # once. Use a restrained anticipation bridge instead: one opaque cat per frame,
    # with both source and target retaining their original ground line.
    if int(step) <= 0:
        return _ease_pose_frame(
            source,
            scale_x=1.004,
            scale_y=0.992,
            offset_y=0,
        )
    if int(step) == 1:
        return _ease_pose_frame(
            target,
            scale_x=1.006,
            scale_y=0.988,
            offset_y=0,
        )
    if int(step) == 2:
        return _ease_pose_frame(
            target,
            scale_x=0.998,
            scale_y=1.003,
            offset_y=0,
        )
    return target.copy()


def build_pet_frames(color: Any) -> dict[str, Any]:
    _raise_if_pet_frame_build_cancelled()
    coat = normalize_pet_color(color)
    poses: dict[str, Any] = {}
    for pose in PET_POSE_ORDER:
        _raise_if_pet_frame_build_cancelled()
        poses[pose] = _fit_sprite(_load_pet_asset(coat, pose), PET_SPRITE_SIZE)
    frames = {
        "normal": _compose_frame(poses["idle"]),
        "blink": _compose_frame(poses["blink"]),
        "perk": _compose_frame(poses["perk"]),
        "bounce": _compose_frame(poses["perk"], offset_y=-2),
        "squash": _compose_frame(
            poses["lick"],
            scale_x=1.018,
            scale_y=0.982,
            offset_y=2,
        ),
        "stretch": _compose_frame(poses["stretch"]),
        "tilt_left": _compose_frame(poses["perk"], rotate=-3.0, offset_x=-2),
        "tilt_right": _compose_frame(poses["perk"], rotate=3.0, offset_x=2),
        "left": _compose_frame(poses["chase"], offset_x=-4),
        "right": _compose_frame(poses["chase"], offset_x=4),
        "chase": _compose_frame(poses["chase"]),
        "sleep": _compose_frame(poses["sleep"]),
        "meow": _compose_frame(poses["meow"]),
        "lick": _compose_frame(poses["lick"]),
        "yawn": _compose_frame(poses["yawn"]),
    }
    # Breathing is a rare one-pixel body compression around fixed paws, not a
    # repeated translation of the whole animal.
    frames["breathe"] = _ease_pose_frame(
        frames["normal"],
        scale_x=1.004,
        scale_y=0.993,
        offset_y=0,
    )
    frames["sleep_breathe"] = _ease_pose_frame(
        frames["sleep"],
        scale_x=1.003,
        scale_y=0.994,
        offset_y=0,
    )
    # Authored in-between poses and new action targets are optional while each
    # coat is being approved. An action enters the deck only when its complete
    # target + bridge set exists for that coat.
    for action, transition_poses in PET_AUTHORED_TRANSITIONS.items():
        _raise_if_pet_frame_build_cancelled()
        authored_target = (
            (action,)
            if action in (*PET_OPTIONAL_ACTIONS, *PET_SETTLEMENT_ACTIONS)
            else ()
        )
        required_poses = (*authored_target, *transition_poses)
        if action in (*PET_OPTIONAL_ACTIONS, *PET_SETTLEMENT_ACTIONS) and not all(
            pet_asset_path(coat, pose).is_file()
            for pose in required_poses
        ):
            continue
        for pose in required_poses:
            _raise_if_pet_frame_build_cancelled()
            if pet_asset_path(coat, pose).is_file():
                composed = _compose_frame(
                    _fit_sprite(_load_pet_asset(coat, pose), PET_SPRITE_SIZE)
                )
                if action in PET_SETTLEMENT_ACTIONS:
                    normal_bbox = frames["normal"].getchannel("A").getbbox()
                    if normal_bbox is not None:
                        composed = _align_frame_ground(composed, normal_bbox[3])
                frames[pose] = composed
    for pose in PET_EYE_VARIANT_POSES:
        _raise_if_pet_frame_build_cancelled()
        if pet_asset_path(coat, pose).is_file():
            frames[pose] = _compose_frame(
                _fit_sprite(_load_pet_asset(coat, pose), PET_SPRITE_SIZE)
            )
    if "perk_eye" in frames:
        frames["tilt_left_eye"] = _compose_frame(
            _fit_sprite(_load_pet_asset(coat, "perk_eye"), PET_SPRITE_SIZE),
            rotate=-3.0,
            offset_x=-2,
        )
        frames["tilt_right_eye"] = _compose_frame(
            _fit_sprite(_load_pet_asset(coat, "perk_eye"), PET_SPRITE_SIZE),
            rotate=3.0,
            offset_x=2,
        )
    if "lick_eye" in frames:
        frames["squash_eye"] = _compose_frame(
            _fit_sprite(_load_pet_asset(coat, "lick_eye"), PET_SPRITE_SIZE),
            scale_x=1.018,
            scale_y=0.982,
            offset_y=2,
        )
    _raise_if_pet_frame_build_cancelled()
    return frames


_FRAME_POSE_FAMILY = {
    "normal": "idle",
    "breathe": "idle",
    "idle_eye": "idle",
    "blink": "blink",
    "perk": "perk",
    "perk_eye": "perk",
    "bounce": "perk",
    "tilt_left": "perk",
    "tilt_left_eye": "perk",
    "tilt_right": "perk",
    "tilt_right_eye": "perk",
    "squash": "lick",
    "squash_eye": "lick",
    "lick": "lick",
    "lick_eye": "lick",
    "stretch": "stretch",
    "stretch_eye": "stretch",
    "sleep": "sleep",
    "sleep_breathe": "sleep",
    "meow": "meow",
    "meow_eye": "meow",
    "yawn": "yawn",
    "chase": "chase",
    "chase_eye": "chase",
    "left": "chase",
    "right": "chase",
    "wave": "wave",
    "wave_eye": "wave",
    "wave_enter_1": "wave",
    "wave_enter_2": "wave",
    "roll": "roll",
    "roll_eye": "roll",
    "roll_enter_1": "roll",
    "roll_enter_2": "roll",
    "roll_enter_3": "roll",
    "scratch": "scratch",
    "scratch_eye": "scratch",
    "scratch_enter_1": "scratch",
    "scratch_enter_2": "scratch",
}
for _settlement_action in PET_SETTLEMENT_ACTIONS:
    _FRAME_POSE_FAMILY[_settlement_action] = _settlement_action
    for _transition_pose in PET_AUTHORED_TRANSITIONS[_settlement_action]:
        _FRAME_POSE_FAMILY[_transition_pose] = _settlement_action


def _needs_pose_transition(mode: str, source: str, target: str) -> bool:
    if mode == "idle":
        return False
    return _FRAME_POSE_FAMILY.get(source, source) != _FRAME_POSE_FAMILY.get(
        target,
        target,
    )


def _authored_transition_frame_name(
    frames: dict[str, Any],
    source: str,
    target: str,
    step: int,
) -> str | None:
    for action, transition_poses in PET_AUTHORED_TRANSITIONS.items():
        if action not in frames or not all(name in frames for name in transition_poses):
            continue
        if source == "normal" and target == action:
            keyframes = ("normal", *transition_poses, action)
            index = max(0, min(len(keyframes) - 1, int(step)))
            return keyframes[index]
        if source == action and target == "normal":
            keyframes = (action, *reversed(transition_poses), "normal")
            index = max(0, min(len(keyframes) - 1, int(step)))
            return keyframes[index]
    return None


def _transition_step_count(
    frames: dict[str, Any],
    source: str,
    target: str,
) -> int:
    for action, transition_poses in PET_AUTHORED_TRANSITIONS.items():
        if action not in frames or not all(name in frames for name in transition_poses):
            continue
        if (source, target) in {("normal", action), (action, "normal")}:
            return len(transition_poses) + 2
    return PET_TRANSITION_STEPS


def _build_transition_frame(
    frames: dict[str, Any],
    source: str,
    target: str,
    step: int,
) -> Any:
    authored_name = _authored_transition_frame_name(frames, source, target, step)
    if authored_name is not None:
        return frames[authored_name].copy()
    return _build_pose_transition(frames[source], frames[target], step)


def _transition_frame_duration(mode: str, source: str, target: str) -> int:
    enter_ms = {
        "perk": 64,
        "chase": 58,
        "meow": 72,
        "lick": 66,
        "eat": 62,
        "tilt": 66,
        "stretch": 82,
        "yawn": 82,
        "sleep": 90,
        "wave": 70,
        "roll": 88,
        "scratch": 78,
        "loss_peek": 108,
        "loss_droop": 112,
        "big_win": 96,
    }.get(mode, 64)
    if target == "normal":
        return max(58, enter_ms - 6)
    return enter_ms


_SEQUENCES: dict[str, tuple[tuple[str, int], ...]] = {
    "idle": (
        ("normal", 1_600),
        ("breathe", 460),
        ("normal", 2_200),
        ("blink", 150),
        ("normal", 1_400),
    ),
    "perk": (
        ("normal", 120),
        ("perk", 270),
        ("bounce", 180),
        ("perk", 200),
        ("normal", 200),
    ),
    # A single click produces one clean vocal/stretch/rest/yawn gesture. Repetition
    # remains only where it is the motion itself (grooming, chewing, tilting, chase).
    "meow": (("normal", 120), ("meow", 460), ("normal", 260)),
    # Sleep is a persistent resting state. A later non-sleep interaction wakes
    # the cat; it does not return to awake idle on its own.
    "sleep": (("normal", 180), ("sleep", 1_600)),
    "stretch": (("normal", 140), ("stretch", 720), ("normal", 240)),
    "lick": (
        ("normal", 120),
        ("lick", 340),
        ("normal", 220),
        ("lick", 340),
        ("normal", 240),
    ),
    "eat": (
        ("normal", 100),
        ("squash", 150),
        ("normal", 120),
        ("squash", 150),
        ("normal", 180),
    ),
    "yawn": (("normal", 160), ("yawn", 700), ("normal", 240)),
    "tilt": (
        ("normal", 120),
        ("tilt_left", 360),
        ("normal", 220),
        ("tilt_right", 360),
        ("normal", 240),
    ),
    "chase": (
        ("chase", 240),
        ("left", 160),
        ("right", 160),
        ("chase", 220),
        ("normal", 220),
    ),
    "wave": (("normal", 140), ("wave", 560), ("normal", 240)),
    "roll": (("normal", 160), ("roll", 900), ("normal", 280)),
    # Ear scratching benefits from one small repeated contact; it reads as the
    # action itself rather than replaying the whole gesture.
    "scratch": (
        ("normal", 140),
        ("scratch", 300),
        ("scratch_enter_2", 180),
        ("scratch", 300),
        ("normal", 240),
    ),
    # Settlement-only gestures use five authored bridges in both directions.
    # The target hold carries the emotion; the reverse bridge returns softly,
    # then the idle loop restores the user's persistent awake/sleep choice.
    "loss_peek": (("normal", 140), ("loss_peek", 820), ("normal", 260)),
    "loss_droop": (("normal", 160), ("loss_droop", 980), ("normal", 300)),
    "big_win": (("normal", 120), ("big_win", 880), ("normal", 260)),
}

_SLEEP_IDLE_SEQUENCE: tuple[tuple[str, int], ...] = (
    ("sleep", 2_400),
    ("sleep_breathe", 520),
    ("sleep", 2_800),
)

_IDLE_NEUTRAL_SEQUENCE: tuple[tuple[str, int], ...] = (
    ("normal", 1_800),
    ("breathe", 460),
    ("normal", 2_800),
)

_IDLE_GAZE_SEQUENCE: tuple[tuple[str, int], ...] = (
    ("normal", 1_600),
    ("breathe", 460),
    ("normal", 1_400),
    ("idle_eye", 360),
    ("normal", 1_300),
)

_ACTION_EYE_BEATS: dict[str, tuple[str, str, int]] = {
    "perk": ("perk", "perk_eye", 130),
    "meow": ("meow", "meow_eye", 135),
    "stretch": ("stretch", "stretch_eye", 150),
    "lick": ("lick", "lick_eye", 135),
    "eat": ("squash", "squash_eye", 110),
    "tilt": ("tilt_left", "tilt_left_eye", 135),
    "chase": ("chase", "chase_eye", 130),
    "wave": ("wave", "wave_eye", 135),
    "roll": ("roll", "roll_eye", 165),
    "scratch": ("scratch", "scratch_eye", 135),
}


def _awake_idle_sequence(frames: dict[str, Any]) -> tuple[tuple[str, int], ...]:
    choice = random.random()
    if choice < 0.38:
        return _SEQUENCES["idle"]
    if choice < 0.72 and "idle_eye" in frames:
        return _IDLE_GAZE_SEQUENCE
    return _IDLE_NEUTRAL_SEQUENCE


def _with_action_eye_beat(
    mode: str,
    sequence: tuple[tuple[str, int], ...],
    frames: dict[str, Any],
) -> tuple[tuple[str, int], ...]:
    spec = _ACTION_EYE_BEATS.get(mode)
    if spec is None or random.random() >= PET_ACTION_EYE_BEAT_CHANCE:
        return sequence
    anchor, eye_frame, beat_ms = spec
    if eye_frame not in frames:
        return sequence
    candidates = [
        index
        for index, (frame_name, _duration) in enumerate(sequence)
        if frame_name == anchor
    ]
    if not candidates:
        return sequence
    selected = random.choice(candidates)
    original_ms = int(sequence[selected][1])
    recovery_ms = 100
    lead_ms = max(100, original_ms - int(beat_ms) - recovery_ms)
    return (
        *sequence[:selected],
        (anchor, lead_ms),
        (eye_frame, int(beat_ms)),
        (anchor, recovery_ms),
        *sequence[selected + 1 :],
    )


def _windows_toplevel_hwnd(widget: tk.Misc) -> int:
    if os.name != "nt":
        return 0
    try:
        widget.update_idletasks()
        client_hwnd = int(widget.winfo_toplevel().winfo_id())
        user32 = ctypes.windll.user32
        get_ancestor = user32.GetAncestor
        get_ancestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        get_ancestor.restype = ctypes.c_void_p
        return int(get_ancestor(client_hwnd, 2) or client_hwnd)  # GA_ROOT
    except (AttributeError, OSError, tk.TclError, TypeError, ValueError):
        return 0


def _windows_colorkey_value(color: str) -> int | None:
    value = str(color or "").strip().lower()
    if len(value) != 7 or not value.startswith("#"):
        return None
    try:
        red = int(value[1:3], 16)
        green = int(value[3:5], 16)
        blue = int(value[5:7], 16)
    except ValueError:
        return None
    return red | (green << 8) | (blue << 16)


def _apply_and_verify_native_colorkey(
    hwnd: int,
    colorref: int,
    *,
    expected_alpha: int | None = None,
) -> bool | None:
    """Idempotently restore and read back one native Windows color key."""

    if os.name != "nt":
        return None
    try:
        user32 = ctypes.windll.user32
        get_window_long = user32.GetWindowLongW
        get_window_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
        get_window_long.restype = ctypes.c_long
        set_window_long = user32.SetWindowLongW
        set_window_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
        set_window_long.restype = ctypes.c_long
        get_layered = user32.GetLayeredWindowAttributes
        get_layered.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        get_layered.restype = ctypes.c_bool
        set_layered = user32.SetLayeredWindowAttributes
        set_layered.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_ubyte,
            ctypes.c_uint32,
        ]
        set_layered.restype = ctypes.c_bool

        ex_style = int(get_window_long(hwnd, _WINDOWS_GWL_EXSTYLE))
        if not ex_style & _WINDOWS_WS_EX_LAYERED:
            set_window_long(
                hwnd,
                _WINDOWS_GWL_EXSTYLE,
                ex_style | _WINDOWS_WS_EX_LAYERED,
            )

        previous_key = ctypes.c_uint32()
        previous_alpha = ctypes.c_ubyte(255)
        previous_flags = ctypes.c_uint32()
        previous_ok = bool(
            get_layered(
                hwnd,
                ctypes.byref(previous_key),
                ctypes.byref(previous_alpha),
                ctypes.byref(previous_flags),
            )
        )
        alpha = (
            max(0, min(255, int(expected_alpha)))
            if expected_alpha is not None
            else previous_alpha.value if previous_ok else 255
        )
        flags = _WINDOWS_LWA_COLORKEY
        if expected_alpha is not None or (
            previous_ok and previous_flags.value & _WINDOWS_LWA_ALPHA
        ):
            flags |= _WINDOWS_LWA_ALPHA
        if not set_layered(hwnd, colorref, alpha, flags):
            return False

        verified_key = ctypes.c_uint32()
        verified_alpha = ctypes.c_ubyte()
        verified_flags = ctypes.c_uint32()
        verified = bool(
            get_layered(
                hwnd,
                ctypes.byref(verified_key),
                ctypes.byref(verified_alpha),
                ctypes.byref(verified_flags),
            )
        )
        final_style = int(get_window_long(hwnd, _WINDOWS_GWL_EXSTYLE))
        return bool(
            verified
            and final_style & _WINDOWS_WS_EX_LAYERED
            and verified_flags.value & _WINDOWS_LWA_COLORKEY
            and verified_key.value == colorref
            and (
                expected_alpha is None
                or verified_flags.value & _WINDOWS_LWA_ALPHA
                and verified_alpha.value == alpha
            )
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _windows_layered_alpha(hwnd: int) -> int | None:
    """Read one positive native window alpha without exposing window identity."""

    if os.name != "nt" or not hwnd:
        return None
    try:
        user32 = ctypes.windll.user32
        get_layered = user32.GetLayeredWindowAttributes
        get_layered.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        get_layered.restype = ctypes.c_bool
        key = ctypes.c_uint32()
        alpha = ctypes.c_ubyte()
        flags = ctypes.c_uint32()
        if not get_layered(
            hwnd,
            ctypes.byref(key),
            ctypes.byref(alpha),
            ctypes.byref(flags),
        ):
            return None
        if not flags.value & _WINDOWS_LWA_ALPHA or alpha.value <= 0:
            return None
        return int(alpha.value)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _apply_and_verify_windows_transparency(
    window: tk.Misc,
    *,
    verify_native: bool = True,
    expected_native_alpha: int | None = None,
) -> str:
    """Apply the Pet color key and return only a fixed privacy-safe state."""

    if os.name != "nt":
        return "unsupported"
    try:
        window.attributes("-transparentcolor", PET_TRANSPARENT_KEY)
    except (AttributeError, tk.TclError, TypeError):
        return "set_failed"
    try:
        readback = str(window.attributes("-transparentcolor")).strip().lower()
    except (AttributeError, tk.TclError, TypeError, ValueError):
        return "readback_mismatch"
    if readback != PET_TRANSPARENT_KEY.lower():
        return "readback_mismatch"
    if not verify_native:
        return "ok"

    hwnd = _windows_toplevel_hwnd(window)
    colorref = _windows_colorkey_value(PET_TRANSPARENT_KEY)
    if not hwnd or colorref is None:
        return "native_key_mismatch"
    native_verified = _apply_and_verify_native_colorkey(
        hwnd,
        colorref,
        expected_alpha=expected_native_alpha,
    )
    if native_verified is None:
        return "unsupported"
    return "ok" if native_verified else "native_key_mismatch"


def _bind_owned_toplevel(window: tk.Toplevel, owner: tk.Misc) -> bool:
    """Keep one borderless companion window above its own app owner.

    Two independent ``-topmost`` windows still reorder when the user activates
    the main overlay. Tk's transient hint is useful off Windows, but an
    overrideredirect window does not reliably receive a native owner on Windows,
    so bind the final wrapper HWND explicitly as well.
    """

    transient_bound = False
    try:
        transient_bound = bool(window.transient())
        if not transient_bound:
            window.transient(owner)
            transient_bound = True
    except tk.TclError:
        pass
    if os.name != "nt":
        return transient_bound
    try:
        if not window.winfo_ismapped():
            return False
    except tk.TclError:
        return False

    window_hwnd = _windows_toplevel_hwnd(window)
    owner_hwnd = _windows_toplevel_hwnd(owner)
    if not window_hwnd or not owner_hwnd or window_hwnd == owner_hwnd:
        return False
    try:
        user32 = ctypes.windll.user32
        set_owner = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        set_owner.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        set_owner.restype = ctypes.c_void_p
        set_owner(window_hwnd, -8, owner_hwnd)  # GWLP_HWNDPARENT

        get_window = user32.GetWindow
        get_window.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        get_window.restype = ctypes.c_void_p
        return int(get_window(window_hwnd, 4) or 0) == owner_hwnd  # GW_OWNER
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _restore_owned_toplevel_layer(
    window: tk.Toplevel,
    owner: tk.Misc,
    apply_toolwindow: Callable[[tk.Misc], None] | None = None,
    after_rebind: Callable[[], None] | None = None,
    rebind_token: Any = None,
    rebind_is_current: Callable[[Any], bool] | None = None,
) -> None:
    def _is_current() -> bool:
        return rebind_is_current is None or rebind_is_current(rebind_token)

    if not _is_current():
        return
    if apply_toolwindow is not None:
        try:
            apply_toolwindow(window)
        except tk.TclError:
            pass
    if not _is_current():
        return
    _bind_owned_toplevel(window, owner)
    if not _is_current():
        return
    try:
        window.lift()
    except tk.TclError:
        pass
    if after_rebind is not None and _is_current():
        after_rebind()


def _install_owned_toplevel_layer(
    window: tk.Toplevel,
    owner: tk.Misc,
    *,
    apply_toolwindow: Callable[[tk.Misc], None] | None = None,
    after_rebind: Callable[[], None] | None = None,
    before_rebind: Callable[[], Any | None] | None = None,
    rebind_is_current: Callable[[Any], bool] | None = None,
) -> None:
    # Record Tk's transient relationship before the first map, then bind the
    # native owner after each Map event. Windows may recreate the wrapper HWND
    # during the first deiconify, so construction-time ownership is not durable.
    _bind_owned_toplevel(window, owner)

    def _restore_after_map(_event: tk.Event[Any]) -> None:
        rebind_token = before_rebind() if before_rebind is not None else None
        if before_rebind is not None and rebind_token is None:
            return
        try:
            window.after_idle(
                _restore_owned_toplevel_layer,
                window,
                owner,
                apply_toolwindow,
                after_rebind,
                rebind_token,
                rebind_is_current,
            )
        except tk.TclError:
            pass

    window.bind("<Map>", _restore_after_map, add="+")


class _PetFrameWorkerState:
    """Thread-owned state that deliberately contains no Tk objects."""

    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.frame_request: tuple[int, str] | None = None
        self.transition_request: tuple[
            int,
            int,
            tuple[str, str, int],
            dict[str, Any],
        ] | None = None
        self.results = queue.SimpleQueue()
        self.stopped = False


def _pet_frame_worker_loop(state: _PetFrameWorkerState) -> None:
    """Run Pillow work without retaining a Pet/Tk object on this thread."""

    while True:
        with state.condition:
            state.condition.wait_for(
                lambda: state.stopped
                or state.frame_request is not None
                or state.transition_request is not None
            )
            if state.stopped:
                return
            frame_request = state.frame_request
            if frame_request is not None:
                state.frame_request = None
                transition_request = None
            else:
                transition_request = state.transition_request
                state.transition_request = None

        if frame_request is not None:
            generation, color = frame_request
            try:
                with _PET_PIL_WORK_LOCK:
                    with state.condition:
                        cancelled = state.stopped or (
                            state.frame_request is not None
                            and state.frame_request[0] != generation
                        )
                    if cancelled:
                        continue
                    def _cancelled() -> bool:
                        with state.condition:
                            return state.stopped or (
                                state.frame_request is not None
                                and state.frame_request[0] != generation
                            )

                    _PET_BUILD_CONTEXT.cancelled = _cancelled
                    try:
                        frames = build_pet_frames(color)
                    finally:
                        if hasattr(_PET_BUILD_CONTEXT, "cancelled"):
                            del _PET_BUILD_CONTEXT.cancelled
                    prepared: dict[str, Any] = {}
                    for name, frame in frames.items():
                        with state.condition:
                            cancelled = state.stopped or (
                                state.frame_request is not None
                                and state.frame_request[0] != generation
                            )
                        if cancelled:
                            break
                        prepared[name] = _prepare_colorkey_frame(frame)
                if cancelled:
                    continue
                state.results.put(("frames", generation, color, frames, prepared))
            except _PetFrameBuildCancelled:
                continue
            except Exception:
                state.results.put(
                    ("frames_error", generation, color, None, None)
                )
            continue

        if transition_request is None:
            continue
        frame_generation, render_generation, key, frames = transition_request
        try:
            source, target, index = key
            with _PET_PIL_WORK_LOCK:
                prepared = _prepare_colorkey_frame(
                    _build_transition_frame(frames, source, target, index)
                )
            state.results.put(
                (
                    "transition",
                    frame_generation,
                    render_generation,
                    key,
                    prepared,
                )
            )
        except Exception:
            state.results.put(
                (
                    "transition_error",
                    frame_generation,
                    render_generation,
                    key,
                    None,
                )
            )


class PetCompanionOverlay:
    def __init__(
        self,
        root: tk.Misc,
        *,
        color: str,
        position: tuple[int, int],
        topmost: bool,
        reduced_motion: bool | None = None,
        work_area_for_point: Callable[[int, int], tuple[int, int, int, int]],
        on_click: Callable[[], None],
        on_context_menu: Callable[[int, int], None],
        on_position_changed: Callable[[tuple[int, int]], None],
        on_hide_requested: Callable[[], None],
        apply_toolwindow: Callable[[tk.Misc], None] | None = None,
        on_visibility_changed: Callable[[bool, Any, int], bool] | None = None,
    ) -> None:
        if not pet_assets_available():
            raise RuntimeError("pet companion assets or Pillow are unavailable")
        self.root = root
        self.color = normalize_pet_color(color)
        self.reduced_motion = (
            system_reduced_motion_enabled() if reduced_motion is None else bool(reduced_motion)
        )
        self._work_area_for_point = work_area_for_point
        self._on_click = on_click
        self._on_context_menu = on_context_menu
        self._on_position_changed = on_position_changed
        self._on_hide_requested = on_hide_requested
        self._apply_toolwindow = apply_toolwindow
        self._on_visibility_changed = on_visibility_changed
        self._running = False
        self._after_id: str | None = None
        self._idle_action_after_id: str | None = None
        self._bubble_after_id: str | None = None
        self._bubble: tk.Toplevel | None = None
        self._touch_feedback_after_id: str | None = None
        self._touch_feedback: tk.Toplevel | None = None
        self._touch_feedback_label: tk.Label | None = None
        self._mode = "idle"
        self._idle_pose = "normal"
        self._sequence_index = 0
        self._sequence_steps: list[tuple[str, Any, int]] = []
        self._current_frame_name = "normal"
        self._drag_origin: tuple[int, int, int, int] | None = None
        self._dragged = False
        self._destroyed = False

        # Pillow work is isolated from Tk. A single daemon worker owns one
        # in-flight request plus one replaceable latest request, so rapid coat
        # switches cannot grow an unbounded executor queue. Tk only polls fixed,
        # privacy-safe result variants and drops every stale generation.
        self._frame_generation = 0
        self._render_generation = 0
        self._frames_color: str | None = None
        self._frame_build_failed = False
        self._pil_frames: dict[str, Any] = {}
        self._prepared_frames: dict[str, Any] = {}
        self._prepared_transition_frames: OrderedDict[
            tuple[str, str, int], Any
        ] = OrderedDict()
        self._tk_frames: OrderedDict[str, Any] = OrderedDict()
        self._transition_tk_frames: OrderedDict[
            tuple[str, str, int], Any
        ] = OrderedDict()
        self._pending_action: str | None = None
        self._frame_worker_state = _PetFrameWorkerState()
        self._frame_poll_after_id: str | None = None
        self._tk_prepare_after_id: str | None = None
        self._tk_prepare_task: tuple[Any, ...] | None = None
        self._waiting_tk_task: tuple[Any, ...] | None = None
        self._tick_waiting = False
        self._static_render_pending: str | None = None
        self._initial_render_pending = False
        self._transparency_generation = 0
        self._transparency_after_id: str | None = None
        self._transparency_state = "unsupported"
        self._transparency_show_requested = False
        self._transparency_visible_alpha = 1.0
        self._transparency_request_token: Any = None
        self._visibility_ready = False
        self._visibility_pending = False
        self._visibility_failure_callback_active = False

        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=PET_TRANSPARENT_KEY)
        self.window.attributes("-topmost", bool(topmost))
        try:
            configured_alpha = float(self.window.attributes("-alpha"))
            if 0.0 < configured_alpha <= 1.0:
                self._transparency_visible_alpha = configured_alpha
        except (tk.TclError, TypeError, ValueError):
            pass
        initial_transparency = _apply_and_verify_windows_transparency(
            self.window,
            verify_native=False,
        )
        if initial_transparency != "ok":
            self._transparency_state = initial_transparency
        if self._apply_toolwindow is not None:
            self._apply_toolwindow(self.window)
        _install_owned_toplevel_layer(
            self.window,
            root,
            apply_toolwindow=self._apply_toolwindow,
            after_rebind=self._schedule_transparency_apply,
            before_rebind=self._mask_transparency_for_map,
            rebind_is_current=self._transparency_rebind_is_current,
        )
        self.canvas = tk.Canvas(
            self.window,
            width=PET_WINDOW_SIZE,
            height=PET_WINDOW_SIZE,
            bg=PET_TRANSPARENT_KEY,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self.canvas.pack()
        self.canvas.bind("<ButtonPress-1>", self._begin_drag, add="+")
        self.canvas.bind("<B1-Motion>", self._drag, add="+")
        self.canvas.bind("<ButtonRelease-1>", self._end_drag, add="+")
        self.canvas.bind(
            "<Button-3>",
            lambda event: self._on_context_menu(int(event.x_root), int(event.y_root)),
            add="+",
        )
        self.window.bind("<Escape>", lambda _event: self._on_hide_requested(), add="+")
        self._frame_worker = threading.Thread(
            target=_pet_frame_worker_loop,
            args=(self._frame_worker_state,),
            name="bidking-pet-frames",
            daemon=True,
        )
        self._frame_worker.start()
        self._request_frame_build(self.color)
        self.set_position(*position)

    @property
    def transparency_state(self) -> str:
        """Return the last fixed, privacy-safe transparency verification state."""

        return self._transparency_state

    @property
    def visibility_ready(self) -> bool:
        """Return whether this generation is safely visible, not merely mapped."""

        return bool(self._visibility_ready)

    @property
    def visibility_pending(self) -> bool:
        """Return whether a mapped generation is still transparency-masked."""

        return bool(self._visibility_pending)

    @property
    def visibility_generation(self) -> int:
        """Return the non-replayable transparency generation being presented."""

        return int(self._transparency_generation)

    def visibility_commit_is_current(self, generation: Any) -> bool:
        """Verify that one ready generation is still visibly safe to commit."""

        def _state_is_current() -> bool:
            return (
                type(generation) is int
                and generation == self._transparency_generation
                and not self._destroyed
                and self._transparency_show_requested
                and self._visibility_ready
                and not self._visibility_pending
                and self._transparency_allows_visibility(self._transparency_state)
            )

        if not _state_is_current():
            return False
        try:
            if not (self.window.winfo_exists() and self.window.winfo_viewable()):
                return False
            tk_alpha = float(self.window.attributes("-alpha"))
        except (tk.TclError, TypeError, ValueError):
            return False
        if not math.isfinite(tk_alpha) or tk_alpha <= 0.0:
            return False
        if os.name == "nt":
            native_alpha = _windows_layered_alpha(_windows_toplevel_hwnd(self.window))
            if native_alpha is None or native_alpha <= 0:
                return False
        if not _state_is_current():
            return False
        try:
            return bool(
                self.window.winfo_exists()
                and self.window.winfo_viewable()
                and float(self.window.attributes("-alpha")) > 0.0
            )
        except (tk.TclError, TypeError, ValueError):
            return False

    def _notify_visibility_changed(
        self,
        ready: bool,
        token: Any,
        generation: int,
    ) -> tuple[bool, BaseException | None]:
        callback = self._on_visibility_changed
        if callback is None:
            return True, None
        try:
            result = callback(bool(ready), token, int(generation))
        except BaseException as exc:  # restore visibility before control exceptions
            return False, exc
        return type(result) is bool and result is True, None

    @staticmethod
    def _reraise_visibility_control_exception(error: BaseException | None) -> None:
        if error is not None and not isinstance(error, Exception):
            raise error.with_traceback(error.__traceback__)

    def _fail_visibility_callback(
        self,
        error: BaseException,
    ) -> None:
        self._transparency_state = "readback_mismatch"
        terminal_error: BaseException | None = None
        try:
            self._withdraw_after_transparency_failure()
        except BaseException as exc:
            terminal_error = exc
        selected_error = (
            error
            if not isinstance(error, Exception)
            else terminal_error
            if terminal_error is not None
            and not isinstance(terminal_error, Exception)
            else None
        )
        self._reraise_visibility_control_exception(selected_error)

    def _finish_visibility_generation(self, ready: bool, generation: int) -> None:
        if type(generation) is not int or generation != self._transparency_generation:
            return
        token = self._transparency_request_token
        self._visibility_pending = False
        self._visibility_ready = bool(ready)
        acknowledged, callback_error = self._notify_visibility_changed(
            bool(ready),
            token,
            generation,
        )
        if (
            generation != self._transparency_generation
            or self._transparency_request_token is not token
        ):
            if callback_error is not None:
                self._fail_visibility_callback(callback_error)
            return
        if (
            acknowledged
            and self._visibility_ready is bool(ready)
            and not self._visibility_pending
        ):
            self._transparency_request_token = None
            return
        if ready:
            # An unchanged, unacknowledged ready generation must not remain
            # visibly unmetered. A Map/rebind has already advanced generation
            # and returns above, preserving the original request token.
            if callback_error is not None:
                self._fail_visibility_callback(callback_error)
                return
            self._transparency_state = "readback_mismatch"
            self._withdraw_after_transparency_failure()
        else:
            self._transparency_request_token = None
        self._reraise_visibility_control_exception(callback_error)

    def _capture_positive_visible_alpha(self) -> None:
        candidate: float | None = None
        try:
            value = float(self.window.attributes("-alpha"))
        except (tk.TclError, TypeError, ValueError):
            value = 0.0
        if math.isfinite(value) and 0.0 < value <= 1.0:
            candidate = value
        native_alpha = _windows_layered_alpha(_windows_toplevel_hwnd(self.window))
        if native_alpha is not None:
            candidate = native_alpha / 255.0
        if candidate is not None:
            self._transparency_visible_alpha = candidate

    def _transparency_alpha_matches(self, value: float) -> bool:
        try:
            readback = float(self.window.attributes("-alpha"))
        except (tk.TclError, TypeError, ValueError):
            return False
        return math.isfinite(readback) and abs(readback - value) <= (1.0 / 255.0)

    def _transparency_allows_visibility(self, state: str) -> bool:
        return state == "ok" or (os.name != "nt" and state == "unsupported")

    def _set_transparency_alpha(self, value: float) -> str:
        target = max(0.0, min(1.0, float(value)))
        try:
            self.window.attributes("-alpha", target)
        except (tk.TclError, TypeError, ValueError):
            return "set_failed"
        try:
            readback = float(self.window.attributes("-alpha"))
        except (tk.TclError, TypeError, ValueError):
            return "readback_mismatch"
        return "ok" if abs(readback - target) <= (1.0 / 255.0) else "readback_mismatch"

    def _cancel_transparency_apply(self) -> None:
        self._transparency_generation += 1
        if self._transparency_after_id is None:
            return
        try:
            self.window.after_cancel(self._transparency_after_id)
        except tk.TclError:
            pass
        self._transparency_after_id = None

    def _mask_transparency_for_map(self) -> int | None:
        if self._destroyed or not self._transparency_show_requested:
            return None
        self._cancel_transparency_apply()
        self._transparency_state = "unsupported"
        self._visibility_ready = False
        self._visibility_pending = True
        state = self._set_transparency_alpha(0.0)
        if state != "ok":
            self._transparency_state = state
            self._withdraw_after_transparency_failure()
            return None
        return self._transparency_generation

    def _transparency_rebind_is_current(self, generation: Any) -> bool:
        if (
            self._destroyed
            or not self._transparency_show_requested
            or generation != self._transparency_generation
        ):
            return False
        try:
            return bool(self.window.winfo_ismapped())
        except tk.TclError:
            return False

    def _schedule_transparency_apply(self) -> None:
        if self._destroyed:
            return
        try:
            if not self.window.winfo_ismapped():
                return
        except tk.TclError:
            return
        self._cancel_transparency_apply()
        self._transparency_state = "unsupported"
        generation = self._transparency_generation
        try:
            self._transparency_after_id = self.window.after_idle(
                self._apply_transparency_generation,
                generation,
            )
        except tk.TclError:
            self._transparency_after_id = None
            self._transparency_state = "set_failed"
            if os.name == "nt":
                self._withdraw_after_transparency_failure()

    def _apply_transparency_generation(self, generation: int) -> None:
        if self._destroyed or generation != self._transparency_generation:
            return
        self._transparency_after_id = None
        try:
            if not self.window.winfo_ismapped():
                return
        except tk.TclError:
            return
        state = _apply_and_verify_windows_transparency(
            self.window,
            expected_native_alpha=0,
        )
        if generation != self._transparency_generation:
            return
        if not self._transparency_allows_visibility(state):
            self._transparency_state = state
            self._withdraw_after_transparency_failure()
            return
        if not self._transparency_show_requested:
            return

        state = self._set_transparency_alpha(self._transparency_visible_alpha)
        if generation != self._transparency_generation:
            return
        if not self._transparency_allows_visibility(state):
            self._set_transparency_alpha(0.0)
            self._transparency_state = state
            self._withdraw_after_transparency_failure()
            return
        state = _apply_and_verify_windows_transparency(
            self.window,
            expected_native_alpha=round(self._transparency_visible_alpha * 255),
        )
        if generation != self._transparency_generation:
            return
        if (
            not self._transparency_allows_visibility(state)
            or not self._transparency_alpha_matches(self._transparency_visible_alpha)
        ):
            self._set_transparency_alpha(0.0)
            self._transparency_state = (
                state if not self._transparency_allows_visibility(state)
                else "readback_mismatch"
            )
            self._withdraw_after_transparency_failure()
            return
        self._transparency_state = "ok"
        self.start()
        self._finish_visibility_generation(True, generation)

    def _conceal_failed_visibility(
        self,
        *,
        cancel_pending: bool,
        clear_token: bool,
    ) -> None:
        self._transparency_show_requested = False
        if cancel_pending:
            self._cancel_transparency_apply()
        if clear_token:
            self._transparency_request_token = None
        self._visibility_pending = False
        self._visibility_ready = False
        if os.name == "nt":
            self._set_transparency_alpha(0.0)
        self.stop()
        self.hide_bubble()
        self.hide_touch_feedback()
        try:
            self.window.withdraw()
        except tk.TclError:
            pass

    def _withdraw_after_transparency_failure(self) -> None:
        if self._visibility_failure_callback_active:
            self._conceal_failed_visibility(
                cancel_pending=True,
                clear_token=True,
            )
            return
        terminal_error: BaseException | None = None
        self._visibility_failure_callback_active = True
        try:
            self._conceal_failed_visibility(
                cancel_pending=False,
                clear_token=False,
            )
            try:
                self._finish_visibility_generation(
                    False,
                    self._transparency_generation,
                )
            except BaseException as exc:
                terminal_error = exc
            finally:
                # A terminal callback may reenter show/hide/destroy. The
                # failed generation remains physically hidden and no second
                # terminal callback is permitted to recurse.
                self._conceal_failed_visibility(
                    cancel_pending=True,
                    clear_token=True,
                )
        finally:
            self._visibility_failure_callback_active = False
        self._reraise_visibility_control_exception(terminal_error)

    @staticmethod
    def _bounded_store(cache, key, value, limit: int) -> None:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > int(limit):
            cache.popitem(last=False)

    def _schedule_frame_poll(self) -> None:
        if self._destroyed or self._frame_poll_after_id is not None:
            return
        try:
            self._frame_poll_after_id = self.window.after(
                PET_FRAME_RESULT_POLL_MS,
                self._poll_frame_results,
            )
        except tk.TclError:
            self._frame_poll_after_id = None

    def _poll_frame_results(self) -> None:
        self._frame_poll_after_id = None
        if self._destroyed:
            return
        while True:
            try:
                result = self._frame_worker_state.results.get_nowait()
            except queue.Empty:
                break
            kind = result[0]
            if kind == "frames":
                _kind, generation, color, frames, prepared = result
                if generation == self._frame_generation and color == self.color:
                    self._apply_frame_build(generation, color, frames, prepared)
            elif kind == "frames_error":
                _kind, generation, color, _frames, _prepared = result
                if generation == self._frame_generation and color == self.color:
                    self._frame_build_failed = True
            elif kind == "transition":
                _kind, frame_generation, render_generation, key, prepared = result
                if (
                    frame_generation == self._frame_generation
                    and render_generation == self._render_generation
                    and self._frames_color == self.color
                ):
                    self._bounded_store(
                        self._prepared_transition_frames,
                        key,
                        prepared,
                        PET_PREPARED_TRANSITION_CACHE_LIMIT,
                    )
                    if self._waiting_tk_task == ("transition", *key):
                        self._schedule_tk_materialization(self._waiting_tk_task)
            elif kind == "transition_error":
                _kind, frame_generation, render_generation, key, _prepared = result
                if (
                    frame_generation == self._frame_generation
                    and render_generation == self._render_generation
                    and self._waiting_tk_task == ("transition", *key)
                ):
                    self._waiting_tk_task = None
                    self._tick_waiting = False
                    failed_edge = tuple(key[:2])
                    while self._sequence_steps:
                        step_kind, payload, _duration = self._sequence_steps[0]
                        if step_kind != "transition":
                            break
                        if tuple(payload[:2]) != failed_edge:
                            break
                        self._sequence_steps.pop(0)
                    # A generated bridge is decoration, not a liveness gate.
                    # Continue with the authored target pose instead of leaving
                    # this interaction permanently stuck on its previous frame.
                    self._tick()
        self._schedule_frame_poll()

    def _cancel_render_work(self) -> None:
        self._render_generation += 1
        if self._tk_prepare_after_id is not None:
            try:
                self.window.after_cancel(self._tk_prepare_after_id)
            except tk.TclError:
                pass
            self._tk_prepare_after_id = None
        self._tk_prepare_task = None
        self._waiting_tk_task = None
        self._tick_waiting = False
        self._static_render_pending = None
        with self._frame_worker_state.condition:
            self._frame_worker_state.transition_request = None

    def _request_frame_build(self, color: str) -> None:
        self._cancel_after()
        self._cancel_idle_action()
        self._cancel_render_work()
        self._frame_generation += 1
        generation = self._frame_generation
        self._frame_build_failed = False
        self._frames_color = None
        self._sequence_steps = []
        self._sequence_index = 0
        with self._frame_worker_state.condition:
            self._frame_worker_state.frame_request = (generation, color)
            self._frame_worker_state.transition_request = None
            self._frame_worker_state.condition.notify()
        self._schedule_frame_poll()

    def _reload_frames(self) -> None:
        self._request_frame_build(self.color)

    def _apply_frame_build(
        self,
        generation: int,
        color: str,
        frames: dict[str, Any],
        prepared: dict[str, Any],
    ) -> None:
        if generation != self._frame_generation or color != self.color:
            return
        self._cancel_after()
        self._cancel_idle_action()
        self._cancel_render_work()
        self._pil_frames = frames
        self._prepared_frames = prepared
        self._frames_color = color
        self._frame_build_failed = False
        self._tk_frames.clear()
        self._transition_tk_frames.clear()
        self._prepared_transition_frames.clear()
        self._sequence_steps = []
        self._sequence_index = 0
        idle_pose = self._idle_pose if self._idle_pose in frames else "normal"
        self._current_frame_name = idle_pose
        self._initial_render_pending = True
        self._request_static_render(idle_pose)

    def _frames_are_current(self) -> bool:
        if not bool(getattr(self, "_pil_frames", None)):
            return False
        if not hasattr(self, "_frames_color"):
            return True
        return self._frames_color == self.color

    def _resolve_frame_name(self, name: str) -> str:
        return name if name in self._prepared_frames else "normal"

    def _task_is_cached(self, task: tuple[Any, ...]) -> bool:
        if task[0] == "frame":
            return str(task[1]) in self._tk_frames
        return tuple(task[1:]) in self._transition_tk_frames

    def _schedule_tk_materialization(self, task: tuple[Any, ...]) -> None:
        if self._destroyed or self._task_is_cached(task):
            return
        self._tk_prepare_task = task
        if self._tk_prepare_after_id is not None:
            return
        try:
            self._tk_prepare_after_id = self.window.after_idle(
                self._materialize_tk_task
            )
        except tk.TclError:
            self._tk_prepare_after_id = None

    def _request_tk_task(self, task: tuple[Any, ...]) -> None:
        if self._task_is_cached(task):
            return
        if task[0] == "frame":
            name = str(task[1])
            if name in self._prepared_frames:
                self._schedule_tk_materialization(task)
            return
        key = tuple(task[1:])
        if key in self._prepared_transition_frames:
            self._schedule_tk_materialization(task)
            return
        with self._frame_worker_state.condition:
            self._frame_worker_state.transition_request = (
                self._frame_generation,
                self._render_generation,
                key,
                self._pil_frames,
            )
            self._frame_worker_state.condition.notify()

    def _materialize_tk_task(self) -> None:
        self._tk_prepare_after_id = None
        task = self._tk_prepare_task
        self._tk_prepare_task = None
        if self._destroyed or task is None or self._task_is_cached(task):
            return
        try:
            if task[0] == "frame":
                name = str(task[1])
                prepared = self._prepared_frames.get(name)
                if prepared is None:
                    return
                photo = ImageTk.PhotoImage(prepared, master=self.window)
                self._bounded_store(
                    self._tk_frames,
                    name,
                    photo,
                    PET_TK_FRAME_CACHE_LIMIT,
                )
            else:
                key = tuple(task[1:])
                prepared = self._prepared_transition_frames.get(key)
                if prepared is None:
                    return
                photo = ImageTk.PhotoImage(prepared, master=self.window)
                self._bounded_store(
                    self._transition_tk_frames,
                    key,
                    photo,
                    PET_TK_TRANSITION_CACHE_LIMIT,
                )
        except tk.TclError:
            return

        if self._static_render_pending is not None and task == (
            "frame",
            self._static_render_pending,
        ):
            pose = self._static_render_pending
            self._static_render_pending = None
            self._render_frame(pose)
            if self._initial_render_pending:
                self._initial_render_pending = False
                self._resume_after_initial_render()
            return
        if self._waiting_tk_task == task:
            self._waiting_tk_task = None
            if self._tick_waiting:
                self._tick_waiting = False
                self._tick()

    def _request_static_render(self, name: str) -> None:
        resolved = self._resolve_frame_name(name)
        self._static_render_pending = resolved
        task = ("frame", resolved)
        if self._task_is_cached(task):
            self._static_render_pending = None
            self._render_frame(resolved)
            if self._initial_render_pending:
                self._initial_render_pending = False
                self._resume_after_initial_render()
            return
        self._request_tk_task(task)

    def _resume_after_initial_render(self) -> None:
        if not self._running:
            return
        pending = self._pending_action
        self._pending_action = None
        if pending is not None:
            self.play(pending)
            return
        if self.reduced_motion:
            return
        self._mode = "idle"
        self._sequence_index = 0
        self._sequence_steps = []
        self._tick()
        self._schedule_idle_action()

    def _render_frame(self, name: str) -> bool:
        resolved = self._resolve_frame_name(name)
        frame = self._tk_frames.get(resolved)
        if frame is None:
            return False
        self._tk_frames.move_to_end(resolved)
        self.canvas.delete("sprite")
        self.canvas.create_image(
            PET_WINDOW_SIZE // 2,
            PET_WINDOW_SIZE // 2,
            image=frame,
            anchor="center",
            tags="sprite",
        )
        self._current_frame_name = resolved
        return True

    def _render_transition(self, source_name: str, target_name: str, step: int) -> bool:
        source = source_name if source_name in self._pil_frames else "normal"
        target = target_name if target_name in self._pil_frames else "normal"
        transition_steps = _transition_step_count(self._pil_frames, source, target)
        index = max(0, min(transition_steps - 1, int(step)))
        key = (source, target, index)
        authored_name = _authored_transition_frame_name(
            self._pil_frames,
            source,
            target,
            index,
        )
        frame = self._transition_tk_frames.get(key)
        if frame is None:
            return False
        self._transition_tk_frames.move_to_end(key)
        self.canvas.delete("sprite")
        self.canvas.create_image(
            PET_WINDOW_SIZE // 2,
            PET_WINDOW_SIZE // 2,
            image=frame,
            anchor="center",
            tags="sprite",
        )
        self._current_frame_name = (
            source
            if index == 0 or (authored_name is not None and index == 1)
            else target
        )
        return True

    def _build_sequence_steps(self, mode: str) -> list[tuple[str, Any, int]]:
        if mode == "idle":
            sequence = (
                _SLEEP_IDLE_SEQUENCE
                if getattr(self, "_idle_pose", "normal") == "sleep"
                else _awake_idle_sequence(self._pil_frames)
            )
        else:
            sequence = _with_action_eye_beat(
                mode,
                _SEQUENCES.get(mode, _SEQUENCES["idle"]),
                self._pil_frames,
            )
        steps: list[tuple[str, Any, int]] = []
        previous = self._current_frame_name
        deferred_initial_hold_ms = 0
        for sequence_index, (frame_name, duration) in enumerate(sequence):
            hold_ms = int(duration)
            if (
                mode != "idle"
                and sequence_index == 0
                and frame_name == previous
            ):
                # The already-visible starting pose needs no extra hold.  The
                # next transition can begin on the same click without the old
                # 100-180 ms dead period.  Preserve the authored total timing
                # by moving that hold to the first visible action pose.
                deferred_initial_hold_ms = hold_ms
                continue
            if frame_name != previous and _needs_pose_transition(
                mode,
                previous,
                frame_name,
            ):
                transition_frame_ms = _transition_frame_duration(
                    mode,
                    previous,
                    frame_name,
                )
                transition_steps = _transition_step_count(
                    self._pil_frames,
                    previous,
                    frame_name,
                )
                for index in range(transition_steps):
                    steps.append(
                        (
                            "transition",
                            (previous, frame_name, index),
                            transition_frame_ms,
                        )
                    )
                # Sequence durations are the readable pose holds after arrival.
                # Subtracting transition time collapsed many target poses to the
                # 50 ms floor, which made an otherwise smooth bridge feel abrupt.
            steps.append(("frame", frame_name, max(50, hold_ms)))
            if deferred_initial_hold_ms:
                steps.append(
                    ("frame", frame_name, max(50, deferred_initial_hold_ms))
                )
            deferred_initial_hold_ms = 0
            previous = frame_name
        return steps

    def _cancel_after(self) -> None:
        if self._after_id is not None:
            try:
                self.window.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def _cancel_idle_action(self) -> None:
        if self._idle_action_after_id is not None:
            try:
                self.window.after_cancel(self._idle_action_after_id)
            except tk.TclError:
                pass
            self._idle_action_after_id = None

    def _schedule_idle_action(self) -> None:
        if (
            self._idle_action_after_id is not None
            or not self._running
            or self.reduced_motion
            or self._idle_pose == "sleep"
        ):
            return
        try:
            self._idle_action_after_id = self.window.after(
                random.randint(18_000, 45_000),
                self._run_idle_action,
            )
        except tk.TclError:
            self._idle_action_after_id = None

    def _run_idle_action(self) -> None:
        self._idle_action_after_id = None
        if not self._running:
            return
        try:
            visible = bool(self.window.winfo_viewable())
        except tk.TclError:
            visible = False
        if visible and self._idle_pose != "sleep":
            self.play(
                random.choice(("perk", "stretch", "lick", "yawn", "tilt", "chase"))
            )
        self._schedule_idle_action()

    def _tick(self) -> None:
        if not self._running or self.reduced_motion:
            self._after_id = None
            return
        if not self._frames_are_current():
            self._after_id = None
            return
        if not self._sequence_steps:
            if self._sequence_index > 0 and self._mode != "idle":
                self._mode = "idle"
            self._sequence_index = 0
            self._sequence_steps = self._build_sequence_steps(self._mode)
        step_kind, payload, duration = self._sequence_steps[0]
        if step_kind == "transition":
            source_name, target_name, step = payload
            source = source_name if source_name in self._pil_frames else "normal"
            target = target_name if target_name in self._pil_frames else "normal"
            transition_steps = _transition_step_count(
                self._pil_frames,
                source,
                target,
            )
            index = max(0, min(transition_steps - 1, int(step)))
            task: tuple[Any, ...] = ("transition", source, target, index)
        else:
            task = ("frame", self._resolve_frame_name(str(payload)))
        if not self._task_is_cached(task):
            self._after_id = None
            self._tick_waiting = True
            self._waiting_tk_task = task
            self._request_tk_task(task)
            return
        self._sequence_steps.pop(0)
        if step_kind == "transition":
            source_name, target_name, step = payload
            self._render_transition(source_name, target_name, step)
        else:
            self._render_frame(str(payload))
        self._sequence_index += 1
        self._after_id = self.window.after(duration, self._tick)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._mode = "idle"
        self._sequence_index = 0
        self._sequence_steps = []
        if not self._frames_are_current():
            return
        pending = self._pending_action
        self._pending_action = None
        if pending is not None:
            self.play(pending)
            return
        if self.reduced_motion:
            self._request_static_render(self._idle_pose)
        else:
            self._tick()
            self._schedule_idle_action()

    def stop(self) -> None:
        self._running = False
        self._cancel_after()
        self._cancel_idle_action()
        self._cancel_render_work()

    def show(self, *, visibility_token: Any = None) -> None:
        if self._destroyed:
            return
        self._capture_positive_visible_alpha()
        self._transparency_show_requested = True
        self._transparency_request_token = visibility_token
        self._visibility_ready = False
        self._visibility_pending = True
        was_mapped = False
        if os.name == "nt":
            try:
                was_mapped = bool(self.window.winfo_ismapped())
            except tk.TclError:
                pass
            if self._mask_transparency_for_map() is None:
                return
        preflight = _apply_and_verify_windows_transparency(
            self.window,
            verify_native=False,
        )
        if not self._transparency_allows_visibility(preflight):
            self._transparency_state = preflight
            self._withdraw_after_transparency_failure()
            return
        try:
            self.window.deiconify()
            self.window.lift()
        except tk.TclError:
            self._transparency_state = "set_failed"
            self._withdraw_after_transparency_failure()
            return
        if os.name == "nt":
            if was_mapped:
                self._schedule_transparency_apply()
        else:
            self._transparency_state = preflight
            self.start()
            self._finish_visibility_generation(
                True,
                self._transparency_generation,
            )

    def hide(self) -> None:
        self._transparency_show_requested = False
        self._cancel_transparency_apply()
        self._transparency_request_token = None
        self._visibility_pending = False
        self._visibility_ready = False
        self.stop()
        self.hide_bubble()
        self.hide_touch_feedback()
        try:
            self.window.withdraw()
        except tk.TclError:
            pass

    def destroy(self) -> None:
        self.hide()
        self._destroyed = True
        if self._frame_poll_after_id is not None:
            try:
                self.window.after_cancel(self._frame_poll_after_id)
            except tk.TclError:
                pass
            self._frame_poll_after_id = None
        with self._frame_worker_state.condition:
            self._frame_worker_state.stopped = True
            self._frame_worker_state.frame_request = None
            self._frame_worker_state.transition_request = None
            self._frame_worker_state.condition.notify()
        # Release every Tcl-backed PhotoImage on the Tk thread.  The worker
        # state is deliberately Tk-free, but ImageTk.__del__ is not thread-safe.
        try:
            self.canvas.delete("sprite")
        except tk.TclError:
            pass
        self._tk_frames.clear()
        self._transition_tk_frames.clear()
        if threading.current_thread() is not self._frame_worker:
            self._frame_worker.join(timeout=PET_WORKER_SHUTDOWN_TIMEOUT_SECONDS)
        if self._touch_feedback is not None:
            try:
                self._touch_feedback.destroy()
            except tk.TclError:
                pass
            self._touch_feedback = None
            self._touch_feedback_label = None
        try:
            self.window.destroy()
        except tk.TclError:
            pass
        # CPython may run cyclic GC in whichever thread performs the next
        # allocation. Collect destroyed Tk wrapper cycles here, on the Tk
        # thread, before a later Pillow worker can become that collector.
        gc.collect()

    def play(self, action: str) -> None:
        action_name = str(action or "").strip().lower()
        if action_name not in _SEQUENCES or action_name == "idle":
            action_name = "perk"
        if (
            action_name in (*PET_OPTIONAL_ACTIONS, *PET_SETTLEMENT_ACTIONS)
            and self._frames_are_current()
            and action_name not in self._pil_frames
        ):
            action_name = "perk"
        if action_name not in PET_SETTLEMENT_ACTIONS:
            self._idle_pose = "sleep" if action_name == "sleep" else "normal"
        if self.reduced_motion:
            if not hasattr(self, "_render_generation"):
                self._render_frame(self._idle_pose)
                return
            if not self._frames_are_current():
                self._pending_action = action_name
                return
            self._cancel_render_work()
            self._request_static_render(self._idle_pose)
            return
        self._cancel_after()
        self._cancel_idle_action()
        self._cancel_render_work()
        self._mode = action_name
        self._sequence_index = 0
        self._sequence_steps = []
        if not self._frames_are_current():
            self._pending_action = action_name
            return
        if not self.canvas.find_withtag("sprite"):
            self._pending_action = action_name
            self._initial_render_pending = True
            self._request_static_render(self._idle_pose)
            return
        self._pending_action = None
        self._tick()
        self._schedule_idle_action()

    def perk(self) -> None:
        self.play("perk")

    def meow(self) -> None:
        self.play("meow")

    def sleep(self) -> None:
        self.play("sleep")

    def stretch(self) -> None:
        self.play("stretch")

    def lick(self) -> None:
        self.play("lick")

    def eat(self) -> None:
        self.play("eat")

    def yawn(self) -> None:
        self.play("yawn")

    def tilt(self) -> None:
        self.play("tilt")

    def chase(self) -> None:
        self.play("chase")

    def wave(self) -> None:
        self.play("wave")

    def roll(self) -> None:
        self.play("roll")

    def scratch(self) -> None:
        self.play("scratch")

    def loss_peek(self) -> None:
        self.play("loss_peek")

    def loss_droop(self) -> None:
        self.play("loss_droop")

    def big_win(self) -> None:
        self.play("big_win")

    def set_color(self, color: Any) -> None:
        normalized = normalize_pet_color(color)
        if normalized == self.color:
            return
        resume_action = self._pending_action
        if resume_action is None and self._mode != "idle":
            resume_action = self._mode
        self.color = normalized
        self._pending_action = resume_action
        self._reload_frames()

    def set_topmost(self, enabled: bool) -> None:
        try:
            self.window.attributes("-topmost", bool(enabled))
        except tk.TclError:
            pass
        if self._bubble is not None:
            try:
                self._bubble.attributes("-topmost", bool(enabled))
            except tk.TclError:
                pass
        if self._touch_feedback is not None:
            try:
                self._touch_feedback.attributes("-topmost", bool(enabled))
            except tk.TclError:
                pass

    def position(self) -> tuple[int, int]:
        try:
            return int(self.window.winfo_x()), int(self.window.winfo_y())
        except tk.TclError:
            return 0, 0

    def set_position(self, x: int, y: int) -> None:
        work_area = self._work_area_for_point(
            int(x) + PET_WINDOW_SIZE // 2,
            int(y) + PET_WINDOW_SIZE // 2,
        )
        final_x, final_y = clamp_pet_position(
            int(x),
            int(y),
            width=PET_WINDOW_SIZE,
            height=PET_WINDOW_SIZE,
            work_area=work_area,
        )
        self.window.geometry(
            f"{PET_WINDOW_SIZE}x{PET_WINDOW_SIZE}+{final_x}+{final_y}"
        )

    def _begin_drag(self, event: tk.Event[Any]) -> str:
        x, y = self.position()
        self._drag_origin = (int(event.x_root), int(event.y_root), x, y)
        self._dragged = False
        return "break"

    def _drag(self, event: tk.Event[Any]) -> str:
        if self._drag_origin is None:
            return "break"
        start_x, start_y, window_x, window_y = self._drag_origin
        delta_x = int(event.x_root) - start_x
        delta_y = int(event.y_root) - start_y
        if abs(delta_x) >= PET_DRAG_THRESHOLD or abs(delta_y) >= PET_DRAG_THRESHOLD:
            self._dragged = True
        if self._dragged:
            self.set_position(window_x + delta_x, window_y + delta_y)
        return "break"

    def _end_drag(self, _event: tk.Event[Any]) -> str:
        moved = self._dragged
        self._drag_origin = None
        self._dragged = False
        if moved:
            self._on_position_changed(self.position())
        else:
            self._on_click()
        return "break"

    def show_bubble(self, text: str, *, duration_ms: int = 2400) -> None:
        self.hide_bubble()
        try:
            bubble = tk.Toplevel(self.root)
            bubble.withdraw()
            bubble.overrideredirect(True)
            bubble.attributes("-topmost", True)
            if self._apply_toolwindow is not None:
                self._apply_toolwindow(bubble)
            _install_owned_toplevel_layer(bubble, self.window)
            bubble.configure(bg="#202838", highlightbackground="#6f91c2", highlightthickness=1)
            label = tk.Label(
                bubble,
                text=str(text),
                bg="#202838",
                fg="#f4f7fb",
                padx=10,
                pady=7,
                font=("Microsoft YaHei UI", 9, "bold"),
            )
            label.pack()
            bubble.update_idletasks()
            width = max(1, int(bubble.winfo_reqwidth()))
            height = max(1, int(bubble.winfo_reqheight()))
            pet_x, pet_y = self.position()
            work_area = self._work_area_for_point(pet_x, pet_y)
            x, y = clamp_pet_position(
                pet_x + (PET_WINDOW_SIZE - width) // 2,
                pet_y - height - 8,
                width=width,
                height=height,
                work_area=work_area,
            )
            bubble.geometry(f"+{x}+{y}")
            bubble.deiconify()
            bubble.lift()
            self._bubble = bubble
            self._bubble_after_id = self.window.after(
                max(500, int(duration_ms)),
                self.hide_bubble,
            )
        except tk.TclError:
            self._bubble = None
            self._bubble_after_id = None

    def hide_bubble(self) -> None:
        if self._bubble_after_id is not None:
            try:
                self.window.after_cancel(self._bubble_after_id)
            except tk.TclError:
                pass
            self._bubble_after_id = None
        if self._bubble is not None:
            try:
                self._bubble.destroy()
            except tk.TclError:
                pass
            self._bubble = None

    def show_touch_feedback(self, text: str = "摸摸  +1", *, duration_ms: int = 950) -> None:
        self.hide_touch_feedback()
        try:
            feedback = self._touch_feedback
            label = self._touch_feedback_label
            if (
                feedback is None
                or label is None
                or not bool(feedback.winfo_exists())
            ):
                feedback = tk.Toplevel(self.root)
                feedback.withdraw()
                feedback.overrideredirect(True)
                feedback.attributes("-topmost", True)
                if self._apply_toolwindow is not None:
                    self._apply_toolwindow(feedback)
                _install_owned_toplevel_layer(feedback, self.window)
                feedback.configure(
                    bg="#fff4dd",
                    highlightbackground="#e3a34c",
                    highlightthickness=1,
                )
                label = tk.Label(
                    feedback,
                    bg="#fff4dd",
                    fg="#955619",
                    padx=7,
                    pady=3,
                    font=("Microsoft YaHei UI", 8, "bold"),
                )
                label.pack()
                self._touch_feedback = feedback
                self._touch_feedback_label = label
            label.configure(text=str(text))
            feedback.update_idletasks()
            width = max(1, int(feedback.winfo_reqwidth()))
            height = max(1, int(feedback.winfo_reqheight()))
            pet_x, pet_y = self.position()
            work_area = self._work_area_for_point(pet_x, pet_y)
            x, y = clamp_pet_position(
                pet_x + PET_WINDOW_SIZE - width - 10,
                pet_y + 10,
                width=width,
                height=height,
                work_area=work_area,
            )
            feedback.geometry(f"+{x}+{y}")
            feedback.deiconify()
            feedback.lift()
            self._touch_feedback_after_id = self.window.after(
                max(450, int(duration_ms)),
                self.hide_touch_feedback,
            )
        except tk.TclError:
            if self._touch_feedback is not None:
                try:
                    self._touch_feedback.destroy()
                except tk.TclError:
                    pass
            self._touch_feedback = None
            self._touch_feedback_label = None
            self._touch_feedback_after_id = None

    def hide_touch_feedback(self) -> None:
        if self._touch_feedback_after_id is not None:
            try:
                self.window.after_cancel(self._touch_feedback_after_id)
            except tk.TclError:
                pass
            self._touch_feedback_after_id = None
        if self._touch_feedback is not None:
            try:
                self._touch_feedback.withdraw()
            except tk.TclError:
                self._touch_feedback = None
                self._touch_feedback_label = None
