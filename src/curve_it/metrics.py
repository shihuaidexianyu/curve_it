"""Metric labels, panel presets and BE2E diagnostic hints."""

from __future__ import annotations

from .log import ParsedLog


def ema_smooth(values: list[float], alpha: float) -> list[float]:
    if not values or alpha <= 0.0:
        return list(values)
    beta = max(0.0, min(0.99, alpha))
    out = [values[0]]
    cur = values[0]
    for v in values[1:]:
        cur = beta * cur + (1.0 - beta) * v
        out.append(cur)
    return out


# 常用指标的纯中文名称映射（用于 PNG / HTML / 终端图头）
_CN_TITLES: dict[str, str] = {
    "Mean reward": "平均总回报",
    "Mean episode length": "平均存活步数",
    "Episode_Reward/progress_best": "前进进度奖励",
    "Episode_Reward/vel_track": "速度跟踪奖励",
    "Episode_Reward/yaw_track": "朝向跟踪奖励",
    "Episode_Reward/feet_air_time": "抬脚腾空奖励",
    "Episode_Reward/stall_penalty": "原地停滞惩罚",
    "Episode_Reward/goto_pos": "靠近目标奖励",
    "Episode_Reward/stop_speed": "到点停稳速度奖",
    "Episode_Reward/action_oob": "动作超限拉回罚",
    "Episode_Termination/fall": "回合跌倒率",
    "Loco/track_kernel": "速度跟踪精度",
    "Loco/ready_rate": "到点稳定率",
    "Loco/deadline_stable_rate": "截止稳定率",
    "Loco/racket_fail_rate": "球拍触地率",
    "Diag/action_clip_rate": "动作裁剪率",
    "Curriculum/level": "当前课程等级",
    "Mean mean_kl loss": "策略 KL 散度",
    "Mean critic_ev loss": "价值函数拟合度 (EV)",
    "Mean actor_lr loss": "策略学习率",
    "Mean kl_early_stop loss": "KL 早停比例",
    "mean_kl": "策略 KL 散度",
    "critic_ev": "价值函数拟合度 (EV)",
    "actor_lr": "策略学习率",
    "kl_early_stop": "KL 早停比例",
    "Mean action noise std": "动作探索标准差",
    "Hit/legal_return_rate": "合法回球率",
    "Hit/contact_rate": "有效触球率",
    "Hit/sweet_rate": "甜区击球率",
    "Hit/net_cross_rate": "合法过网率",
    "Hit/serve_legal_rate": "合法开球率",
    "Hit/serve_high_fault": "开球超高违例率",
    "Hit/fall_rate": "击球跌倒率",
    "Hit/miss_rate": "漏球率",
    "Hit/land_err_m": "落点误差 (米)",
    "Hit/rally_legal_shots": "连续多拍合法拍数",
    "HitCur/receive_dist": "接球移动距离上限 (米)",
    "HitCur/bridge_scale": "探索桥梁退火系数",
    "Steps/s": "仿真吞吐 (步/秒)",
}


def cn_title(key: str) -> str:
    return _CN_TITLES.get(key, key)


def default_panels(log: ParsedLog) -> list[tuple[str, str]]:
    """Select the 6 most informative (metric_key, Chinese display title) panels for the detected stage."""
    if log.is_stage2():
        keys = [
            "Mean reward",
            "Hit/legal_return_rate",
            "Hit/contact_rate",
            "Hit/serve_legal_rate",
            "Hit/fall_rate",
            "Hit/land_err_m",
            "HitCur/receive_dist",
            "Mean critic_ev loss",
            "Mean mean_kl loss",
        ]
    elif any(key.startswith(("Loco/", "Subtask/agile/")) for key in log.series):
        keys = [
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
        ]
    else:
        keys = ["Mean reward", "Mean episode length", "Episode_Termination/fall", "Steps/s"]
    panels = [(k, cn_title(k)) for k in keys if k in log.series]
    return panels[:6] if panels else [(k, cn_title(k)) for k in list(log.series)[:6]]


def diagnose_health(log: ParsedLog) -> list[str]:
    """Produce concise plain-Chinese diagnostic bullets from the latest metrics."""
    notes: list[str] = []
    if not log.iters:
        return ["⏳ 日志中尚未出现完整 iteration 输出（环境正在初始化）"]
    if not log.series:
        return ["等待训练输出指标"]
    it = log.iters[-1]

    kl_stop = log.latest("kl_early_stop" if "kl_early_stop" in log.series else "Mean kl_early_stop loss")
    if kl_stop is not None and kl_stop > 0.5 and it > 5:
        notes.append(f"⚠️ KL 早停触发偏高 ({kl_stop:.2f})，可留意 actor_lr 适应情况")

    clip = log.latest("Diag/action_clip_rate")
    if clip is not None and clip > 0.05:
        notes.append(f"⚠️ 动作裁剪率偏高 ({clip:.1%} > 5%)，检查 action_oob 权重")

    if log.is_stage2():
        lr = log.latest("actor_lr" if "actor_lr" in log.series else "Mean actor_lr loss")
        ev = log.latest("critic_ev" if "critic_ev" in log.series else "Mean critic_ev loss")
        if lr is not None and lr == 0.0:
            notes.append(f"🧊 Critic 预热阶段（Actor 冻结中，当前 critic_ev={ev or 0.0:.2f}，目标 ≥0.85）")
        ret = log.latest("Hit/legal_return_rate")
        cont = log.latest("Hit/contact_rate")
        if cont is not None and ret is not None:
            notes.append(f"🏸 当前触球率 {cont:.1%}，合法回球率 {ret:.1%}")
    elif "Loco/track_kernel" in log.series or "Episode_Reward/progress_best" in log.series:
        lvl = log.latest("Curriculum/level")
        air = log.latest("Episode_Reward/feet_air_time") or 0.0
        prog = log.latest("Episode_Reward/progress_best") or 0.0
        fall = log.latest("Episode_Termination/fall")
        track = log.latest("Loco/track_kernel") or 0.0
        ready = log.latest("Loco/ready_rate") or 0.0
        if it >= 100 and air < 1e-4 and prog < 0.02:
            notes.append("🚨 警告：检测到疑似「死站不动」局部最优（feet_air_time≈0, progress_best≈0）")
        elif it >= 80 and air > 0.05 and prog < 0.06 and track < 0.03:
            notes.append("🚨 警告：检测到疑似「原地踏步」刷分（feet_air_time高，但前进进度与速度跟踪极低）")
        elif prog > 0.05 or air > 0.002:
            notes.append(f"🏃 已开始迈步移动（progress_best={prog:.3f}, feet_air_time={air:.4f}, track={track:.3f}）")
        else:
            notes.append(f"🌱 冷启动探索中（iter={it}, track={track:.3f}, fall={(fall or 0.0):.1%}）")
        if lvl is not None:
            notes.append(f"📈 当前课程等级 L{int(round(lvl))}（ready_rate={ready:.1%}, fall={(fall or 0.0):.1%}）")
    return notes


def format_value(v: float) -> str:
    av = abs(v)
    if av >= 1000:
        return f"{v:,.0f}"
    if av >= 10:
        return f"{v:.2f}"
    if av >= 0.1 or av == 0.0:
        return f"{v:.3f}"
    return f"{v:.4f}"
