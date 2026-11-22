"""BadmintonLab metric metadata and ordered dashboard presets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .dashboard import Panel
from .log import ParsedLog
from .metrics import cn_title


@dataclass(frozen=True)
class DashboardProfile:
    name: str
    title: str
    keys: tuple[str, ...]


_PROFILES = (
    DashboardProfile(
        "selfplay",
        "战术自对弈",
        (
            "SP/win_vs_base",
            "SP/win_vs_hist",
            "SP/mean_rally_hits",
            "SP/self_fall_rate",
            "SP/serve_fault_rate",
            "SP/out_fault_rate",
            "SP/pool_size",
            "SP/timeout_rate",
        ),
    ),
    DashboardProfile(
        "athletic",
        "全场击球与高点拦截",
        (
            "Hit/receive_legal_rate",
            "Hit/target_hit_1m_rate",
            "Diag/peak_speed_mps",
            "Diag/chase_speed_mps",
            "Hit/contact_rate",
            "Hit/land_err_m",
            "Jump/airborne_hit_rate",
            "Hit/fall_rate",
        ),
    ),
    DashboardProfile(
        "power",
        "击球与功率精修",
        (
            "Hit/receive_legal_rate",
            "Hit/rally_legal_shots",
            "Diag/mech_power_w",
            "Diag/idle_power_w",
            "Hit/land_err_m",
            "Hit/fall_rate",
            "Hit/serve_legal_rate",
            "Mean action noise std",
        ),
    ),
    DashboardProfile(
        "hitting",
        "击球与发球",
        (
            "Hit/receive_legal_rate",
            "Hit/legal_return_rate",
            "Hit/contact_rate",
            "Hit/serve_legal_rate",
            "Hit/fall_rate",
            "Hit/land_err_m",
            "HitCur/receive_dist",
            "Hit/rally_legal_shots",
            "Mean reward",
        ),
    ),
    DashboardProfile(
        "sprint",
        "步法冲刺",
        (
            "Diag/peak_speed_mps",
            "Diag/move_speed_mps",
            "Diag/peak_speed_p90",
            "Episode_Termination/fall",
            "Loco/ready_rate",
            "Diag/action_clip_rate",
            "Episode_Reward/feet_air_time",
            "Mean reward",
        ),
    ),
    DashboardProfile(
        "footwork",
        "敏捷步法",
        (
            "Loco/ready_rate",
            "Loco/deadline_stable_rate",
            "Episode_Termination/fall",
            "Loco/track_kernel",
            "Mean reward",
            "Episode_Reward/progress_best",
            "Episode_Reward/feet_air_time",
            "Mean episode length",
            "Diag/action_clip_rate",
            "Mean mean_kl loss",
        ),
    ),
    DashboardProfile(
        "training",
        "训练曲线",
        (
            "Mean reward",
            "Mean episode length",
            "Episode_Termination/fall",
            "Steps/s",
            "Mean critic_ev loss",
            "Mean mean_kl loss",
        ),
    ),
)
PROFILES = {profile.name: profile for profile in _PROFILES}

_TITLES = {
    "Hit/receive_legal_rate": "接球合法率",
    "Hit/target_hit_1m_rate": "落点命中（≤1 m）",
    "Hit/forehand_rate": "右侧触球占比",
    "Hit/backhand_rate": "左侧触球占比",
    "Hit/face_net_yaw_deg": "追球面网偏角",
    "Hit/receive_fall": "接球跌倒率",
    "HitCur/receive_dist_p90": "接球距离 P90",
    "Diag/foot_impact_peak_bw": "落地冲击峰值",
    "SP/net_fault_rate": "下网率",
    "SP/double_hit_rate": "连击违例率",
    "SP/macro_steps": "战术步长",
    "SP/phase_selfplay": "阶段开关",
    "Jump/airborne_hit_rate": "空中触球率",
    "Jump/rate_smash": "专项起跳率",
    "Jump/rate_all": "起跳率",
    "Jump/height_cm": "起跳高度",
    "Jump/contact_z_high": "高点击球高度",
    "Diag/peak_speed_mps": "平均回合峰速",
    "Diag/peak_speed_p90": "回合峰速 P90",
    "Diag/chase_speed_mps": "追球速度",
    "Diag/move_speed_mps": "移动均速",
    "Diag/mech_power_w": "关节机械功率",
    "Diag/idle_power_w": "待机机械功率",
    "SP/win_vs_base": "对规则基线胜率",
    "SP/win_vs_hist": "对历史对手胜率",
    "SP/win_vs_self": "镜像自对弈胜率",
    "SP/mean_rally_hits": "平均拍数",
    "SP/self_fall_rate": "自对弈跌倒率",
    "SP/serve_fault_rate": "发球违例率",
    "SP/out_fault_rate": "出界率",
    "SP/timeout_rate": "超时率",
    "SP/pool_size": "历史对手数",
}
_LOWER = frozenset(
    (
        "Episode_Termination/fall",
        "Hit/fall_rate",
        "Hit/land_err_m",
        "Hit/serve_high_fault",
        "Hit/miss_rate",
        "Diag/action_clip_rate",
        "Loco/racket_fail_rate",
        "SP/self_fall_rate",
        "SP/serve_fault_rate",
        "SP/out_fault_rate",
        "SP/timeout_rate",
    )
)
_HIGHER = frozenset(
    (
        "Hit/legal_return_rate",
        "Hit/receive_legal_rate",
        "Hit/serve_legal_rate",
        "Hit/target_hit_1m_rate",
        "Hit/contact_rate",
        "Hit/rally_legal_shots",
        "Loco/ready_rate",
        "Loco/deadline_stable_rate",
        "SP/win_vs_base",
        "SP/win_vs_hist",
        "Mean reward",
    )
)
_UNITS = {"_mps": "m/s", "_p90": "m/s", "_deg": "°", "_cm": "cm", "_w": "W", "_m": "m"}


def metric_panel(key: str) -> Panel:
    title = _TITLES.get(key, cn_title(key))
    unit = ""
    if (
        key.endswith("_rate")
        or key.startswith(("Jump/rate_", "SP/win_vs_"))
        or key
        in {
            "Episode_Termination/fall",
            "Hit/serve_high_fault",
            "Hit/receive_fall",
        }
    ):
        unit = "%"
    elif key in {"HitCur/receive_dist", "HitCur/receive_dist_p90", "Jump/contact_z_high"}:
        unit = "m"
    elif key == "Diag/foot_impact_peak_bw":
        unit = "BW"
    else:
        for suffix, label in _UNITS.items():
            if key.startswith(("Diag/", "Hit/", "Jump/")) and key.endswith(suffix):
                unit = label
                break
    direction = int(key in _HIGHER) - int(key in _LOWER)
    return Panel(key, title, unit, direction)


def detect_profile(log: ParsedLog) -> DashboardProfile:
    keys = log.series
    name = Path(log.path).stem.lower()
    if any(key.startswith("SP/") for key in keys) or name.startswith("be2e_s3_"):
        return PROFILES["selfplay"]
    if any(key.startswith("Jump/") for key in keys) or name.startswith(("be2e_s2c", "be2e_s2d")):
        return PROFILES["athletic"]
    if log.is_stage2() and ("polish" in name or "Episode_Reward/joint_power" in keys):
        return PROFILES["power"]
    if log.is_stage2() or name.startswith("be2e_s2"):
        return PROFILES["hitting"]
    if "sprint" in name or "Diag/peak_speed_p90" in keys:
        return PROFILES["sprint"]
    if any(key.startswith(("Loco/", "Subtask/agile/")) for key in keys) or name.startswith("be2e_s1"):
        return PROFILES["footwork"]
    return PROFILES["training"]


_DENSE_KEYS = {
    "athletic": (
        "Hit/net_cross_rate",
        "Hit/sweet_rate",
        "Hit/miss_rate",
        "Hit/serve_legal_rate",
        "Hit/rally_legal_shots",
        "Jump/rate_smash",
        "Jump/contact_z_high",
        "Jump/height_cm",
        "HitCur/receive_dist_p90",
        "Hit/receive_fall",
        "Diag/foot_impact_peak_bw",
        "Diag/mech_power_w",
    ),
    "hitting": ("Hit/net_cross_rate", "Hit/sweet_rate", "Hit/miss_rate", "Hit/serve_high_fault", "Hit/receive_fall"),
    "selfplay": ("SP/win_vs_self", "SP/net_fault_rate", "SP/double_hit_rate", "SP/macro_steps", "SP/phase_selfplay"),
    "power": ("Hit/contact_rate", "Hit/target_hit_1m_rate", "Hit/net_cross_rate", "Diag/foot_impact_peak_bw"),
    "footwork": ("Curriculum/level", "Curriculum/ema_ready", "Curriculum/ema_fall", "Loco/racket_fail_rate"),
    "sprint": ("Loco/track_kernel", "Loco/deadline_stable_rate"),
}
_DENSE_COMMON = (
    "Mean mean_kl loss",
    "Mean critic_ev loss",
    "Mean actor_lr loss",
    "Mean kl_early_stop loss",
    "Mean action noise std",
    "Mean reward",
    "Steps/s",
)


def select_panels(
    log: ParsedLog,
    *,
    profile: DashboardProfile | str = "auto",
    keys: list[str] | None = None,
    dense: bool = False,
    all_metrics: bool = False,
) -> tuple[DashboardProfile, list[Panel]]:
    selected = (
        detect_profile(log) if profile == "auto" else (PROFILES[profile] if isinstance(profile, str) else profile)
    )
    if keys is None:
        available = [key for key in selected.keys if key in log.series]
        if profile != "auto":
            available = list(selected.keys)
        elif selected.name == "athletic" and "Hit/forehand_rate" in log.series:
            available.extend(key for key in ("Hit/forehand_rate", "Hit/face_net_yaw_deg") if key in log.series)
        if dense:
            available.extend(key for key in (*_DENSE_KEYS.get(selected.name, ()), *_DENSE_COMMON) if key in log.series)
        if all_metrics:
            available.extend(log.series)
        keys = list(dict.fromkeys(available)) or list(log.series)[:6]
    return selected, [metric_panel(key) for key in keys]
