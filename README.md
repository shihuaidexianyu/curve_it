# curve_it

`curve_it` 读取 RSL-RL 的文本训练日志，在终端显示曲线和最新指标，可导出独立 HTML 或 PNG。目录模式默认挑选最新的 `be2e_*.log`。安装和图表导出可在独立的工具目录完成，工具以只读方式打开训练日志。

## 安装

需要 Python 3.10 或以上。终端图和 HTML 使用 Python 标准库。要启用 PNG 导出，将最后一条命令的 `-e .` 换成 `-e '.[png]'`。

```bash
cd /personal_fast_storage/curve_it
uv venv .venv
uv pip install --python .venv/bin/python -e .
```

## 推荐用法是在 tty7 单独开一个监看窗口

在自己的 `tty7` 窗口里运行 `curve_it`，让训练继续在原来的进程或终端里跑。给工具传训练日志目录，它会每 5 秒检查最新的 `be2e_*.log`，有新训练时自动切过去。下面这条命令可以在任意目录执行。

```bash
/personal_fast_storage/curve_it/.venv/bin/curve_it \
  /personal_fast_storage/BadmintonLab-Game/logs/console --watch 5
```

监看直接打开四图模式。宽窗口使用 `2 × 2` 网格，窄窗口纵向排列，最后一页显示剩余曲线。底部一直显示当前页和可用按键，`›` 标记当前选中的曲线。

| 按键 | 四图模式 | 单图模式 |
| --- | --- | --- |
| `←` / `→` | 上一页 / 下一页 | 上一条曲线 / 下一条曲线 |
| `↑` / `↓` | 按从左到右、从上到下的顺序选图 | 上一条曲线 / 下一条曲线 |
| `Enter` | 放大选中的曲线 | 返回该曲线所在的四图页 |
| `Esc` | | 返回该曲线所在的四图页 |

空格暂停或继续刷新，`r` 手动刷新，`q` 或 `Ctrl+C` 退出。

想留在当前运行时，可以传具体日志路径，或在目录监看时加 `--pin-log`。SSH 和 tmux 也支持相同的按键。Unicode 终端使用 Braille，每个字符格容纳 `2 × 4` 个绘图点。Linux 虚拟控制台默认使用 ASCII，字体支持 Braille 时可加 `--unicode`。`--no-color` 可以关闭颜色。

## 使用场景

| 你在做什么 | 用法 |
| --- | --- |
| 训练时随时看进展 | 给出日志目录，添加 `--watch 5`。 |
| 只核对几条曲线 | 用 `--list` 查指标名，再用 `--metrics` 选择所需指标。 |
| 展开更多指标 | 用 `--all-metrics`，按 `←` / `→` 翻页。 |
| 调整历史范围 | 监看默认显示最近 120 轮，`--history 300` 显示最近 300 轮，`--history 0` 看全程。 |
| 固定一套指标 | 用 `--profile footwork`、`sprint`、`hitting`、`power`、`athletic` 或 `selfplay`。默认按日志识别。 |
| 复盘某一次训练或分享结果 | 给出具体日志文件，使用 `--html` 导出单页，或使用 `--png` 导出图像。 |
| 加目标线、参考区间或事件点 | 使用 `--annotations annotations.json`。 |
| 收到定时训练进度 | 配置 `SERVERCHAN_SENDKEY`，使用 `--notify-every 10m` 或 `30m`。 |

在工具目录里可以运行下面的命令。`LOG` 也可以换成某次训练的 `.log` 文件。遇到不同的日志命名规则时，可用 `--pattern '*.log'`。

```bash
LOG=/personal_fast_storage/BadmintonLab-Game/logs/console
.venv/bin/curve_it "$LOG" --list
.venv/bin/curve_it "$LOG" --metrics 'Mean reward,Episode_Termination/fall,Loco/ready_rate'
.venv/bin/curve_it "$LOG" --html outputs/training.html
.venv/bin/curve_it "$LOG" --png outputs/training.png
```

![BE2E 训练日志的 HTML 报告截图](docs/screenshots/html-report.png)

上图是浏览器打开 HTML 导出的实际截屏。横轴是训练轮次，缺失指标显示 `--` 或「暂无数据」。终端默认显示原始值，`--term-smooth 0.6` 启用指数移动平均。HTML 和 PNG 保留原始曲线，`--smooth 0` 可关闭平滑。省略导出路径时，文件保存到当前工作目录。HTML 可离线打开。

## 目标线、参考区间和标签

先用 `curve_it "$LOG" --list` 查找指标的完整键名。将配置保存为 `annotations.json`。下面的目标值仅用于演示，请按当前运行的任务单位和 PPO 配置填写。

```json
{
  "Jump/height_cm": [
    {"type": "line", "value": 14, "label": "目标高度 14 cm"},
    {"type": "point", "iteration": 35, "value": 7, "label": "第 35 轮实测"}
  ],
  "Mean mean_kl loss": [
    {"type": "band", "lower": 0.01, "upper": 0.03, "label": "KL 观察区间"}
  ],
  "Hit/receive_legal_rate": [
    {"type": "line", "value": 0.90, "label": "合法接球目标 90%"}
  ]
}
```

```bash
curve_it "$LOG" --watch 5 --annotations annotations.json
curve_it "$LOG" --annotations annotations.json --html outputs/annotated.html
```

`line` 是水平参考线，`band` 给出上下界，`point` 标记明确的轮次和值。所有值使用日志单位，例如百分比 `90%` 写 `0.90`，`Jump/height_cm` 用厘米。纵轴包含当前画面里的参考值。声称是实测的事件点应使用对应轮次的实际数值。四图模式、单图模式、HTML 和 PNG 都可显示标注。`--metrics` 可以限制显示的指标。单次快照会检查配置里的键名，目录监看允许同一份配置包含其他阶段的指标。

`validate_annotations()` 检查结构、数值、标签和上下界，`validate_annotation_keys()` 核对精确指标名。错误包含具体字段，例如 `annotations.json['Mean mean_kl loss'][0].upper: must exceed lower (0.03)`。Python 中可读取 `AnnotationError.issues` 的 `path` 和 `message`。`Panel` 在构造时还会检查百分比标注是否处于 `[0, 1]`。

## Server 酱定时推送

在启动进程的环境中设置 `SERVERCHAN_SENDKEY`。`CURVE_IT_NOTIFY_PROVIDER` 默认是 `serverchan`。程序根据 SendKey 选择 Server 酱³ 或 Turbo。Server 酱³ 的原生标签为 `curve`，Turbo 的消息标题加 `[curve]`。两个版本都接收 Markdown 报告。 [Server 酱官方 SDK 文档](https://github.com/easychen/serverchan-sdk)

```bash
curve_it "$LOG" --watch 5 --notify-every 10m
curve_it "$LOG" --watch 5 --notify-every 30m --pin-log
```

报告包含运行名、阶段、轮次和所选指标，当前轮次有吞吐和 ETA 时也会附上。`--metrics` 可选报告中的重点指标。首次推送在间隔到达后的下次刷新进行。相同运行的同一轮次成功发送后会跳过，失败会在下个间隔再尝试。终端刷新与网络请求并行。省略 `--watch` 时每 5 秒读取日志，空格暂停时同时暂停读取和定时报告。无 TTY 的进程也可以运行。请按账号的发送额度选间隔。 [Server 酱额度与频率说明](https://sct.ftqq.com/docs/getting-started/faq/)

如果已有手机能访问的 HTTPS 图片地址，可把 PNG 导出到该地址对应的目录。每条消息会嵌入当前图片的链接，固定链接会随文件刷新。没有图片地址时直接发送文字报告。

```bash
# 将地址和本地路径换成你已有的 HTTPS 发布目录。
CURVE_IT_CHART_URL=https://charts.example.org/training.png \
  curve_it "$LOG" --notify-every 30m --png /srv/charts/training.png
```

Python 代码可以使用同一套接口。已有进程可在刷新数据时调用 `ReportSchedule.update(log, panels, profile.title)`，退出前调用 `close()`。其他推送适配器只需实现 `Notifier.send(Notification)`，发送失败时抛出 `NotificationError`。

```python
from curve_it import LogReader, ReportSchedule, notifier_from_env, select_panels

reader = LogReader("training.log")
reports = ReportSchedule(notifier_from_env(), interval=600)

def refresh():
    log = reader.read()
    profile, panels = select_panels(log)
    reports.update(log, panels, profile.title)

# 在现有监看循环里周期性调用 refresh()，退出时调用 reports.close()。
```

## 硬件监控

看板上方的硬件小窗用横条显示运行 `curve_it` 的机器。整根横条表示满载，填充长度表示已用比例。Linux 上有有限的 cgroup 配额时，CPU 和内存按对应配额统计。CPU 同时显示物理核数、逻辑线程数和采样期间实际使用的等效逻辑核数。例如 `8.0/20 核` 表示采样期间平均用掉了 20 个逻辑核配额中的 8 个。

| 显卡指标 | 含义 |
| --- | --- |
| GPU 忙碌 | 采样期间至少有一个 kernel 在执行的时间比例。 |
| SM 活跃 | NVIDIA NVML GPM 采样得到的 SM 平均活跃度。无法读取时可显示标为「SM 常规」的驱动读数。 |
| Warp 占用 | SM 上活跃 warp 相对于可驻留 warp 上限的比例，来自 NVML GPM。 |
| 显存 | 已用和总容量（GiB），以及容量占用率。 |
| 显存读写 | 采样期间设备显存有读写活动的时间比例。 |

每张可见的 NVIDIA 显卡单独显示。问号或斜纹表示该项暂无读数。终端和 `--watch --html` 每次刷新时更新硬件读数，即使训练日志没有追加。PNG 保存导出时的读数。复盘历史训练或在紧凑窗口留出更多曲线空间时，可以用 `--no-hardware` 隐藏实时硬件小窗。

## 怎么看训练表现

| 指标 | 看法 |
| --- | --- |
| `Mean reward` | 看同一运行中的总体趋势。奖励定义或课程变动后，结合任务指标一起读。 |
| `Episode_Termination/fall` | 训练回合的跌倒比例，越低越好。`0.05` 表示约 5%。 |
| `Loco/ready_rate`、`Loco/deadline_stable_rate` | 前者表示截止前曾经到位并稳住，后者表示截止时仍稳定。结合两者可看出机器人是否到得了、停得住。 |
| `Loco/track_kernel` | 速度跟踪的相似度，越接近 1 越好。关注它是否随前进进度一起提高。 |
| `Hit/contact_rate`、`Hit/legal_return_rate` | 触球与合法回球的比例。看两条曲线的差距，可定位是否经常碰到球但未合法回球。 |
| `Mean mean_kl loss` 或 `mean_kl` | 策略更新幅度。突然升高时，结合跌倒率和独立评测判断训练是否退化。 |

这些曲线来自训练日志。S1b 到位、正式回球与多拍成绩使用训练仓库的独立评测结果。

## 实现与验证

`LogReader` 增量读取完整日志行，日志截短或轮转后重新建表。`DashboardProfile` 定义指标顺序，`Panel` 定义名称、单位和趋势方向。终端渲染根据窗口尺寸排布，监看时只重绘变化的行。HTML 和 PNG 在日志指标更新时原子替换导出文件。

下面用同一套面板生成终端画面和 HTML。

```python
from dataclasses import replace

from curve_it import LogReader, ReferenceBand, ReferenceLine, render_dashboard, select_panels
from curve_it.export import save_html

log = LogReader("training.log").read()
profile, panels = select_panels(log, keys=["Hit/receive_legal_rate", "Mean mean_kl loss"])
panels[0] = replace(panels[0], annotations=(ReferenceLine(0.90, "接球目标"),))
panels[1] = replace(panels[1], annotations=(ReferenceBand(0.01, 0.03, "KL 观察区间"),))
frame = render_dashboard(log, panels, profile.title, width=100, height=None)
print("\n".join(frame.lines))
save_html(log, panels, "receive.html", title=profile.title)
```

```bash
uv pip install --python .venv/bin/python -e '.[dev,png]'
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
```
