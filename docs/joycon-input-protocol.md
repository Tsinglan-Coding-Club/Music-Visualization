# Joy-Con 输入协议与判定链路

更新：2026 年 11 月 18 日。范围：Joy-Con 脚本 → 本地 socket → 游戏输入桥 → 拍手声 → 最近目标判定 → 画面。原 Arduino 手套串口输入已移出运行路径，备份见 `experiments/serial_glove_backup/`。

## 总览

```text
Joy-Con A 键
  └─ joycon_clap_sender.py        （500Hz 轮询 + 按下沿检测）
       └─ Unix socket，一行一条 JSON         （tmp/joycon-clap.sock）
            └─ rhythm_game/clap_socket.py      （监听线程）
                 ├─ 校验 host_time / 版本 / 字段
                 ├─ InputBridge.receive()
                 │    ├─ engine.trigger_clap()  立即出声
                 │    └─ 队列 RoutedInput
                 └─ 主线程 app.poll_inputs()
                      └─ app.clap() → _judge_clap()
                           └─ RhythmSession.judge(music_time)
```

游戏不初始化 pygame；发送器只读取手柄并写 socket，不播放声音、不创建窗口。发送器也不初始化 `pygame.mixer`，只初始化 `pygame.display` 与 `pygame.joystick`，避免先占用 CoreAudio 影响游戏的音频输出。两者只通过 socket 和时间戳耦合。

## 接口：Unix socket + JSON 行

- 传输：Unix domain stream socket（`AF_UNIX`）。
- 地址：`config.toml` 的 `input.socket_path`，相对配置文件；默认 `tmp/joycon-clap.sock`。
- 编码：UTF-8，**一行一个 JSON 对象**，以 `\n` 结束。
- 方向：发送器 → 游戏。游戏不回包；连接本身即握手。
- 游戏同时只服务一个发送器；第二个连接会排队，等第一个断开后才被接受。

### 消息

连接后建议先发 `hello`（可选，用于设置页显示设备名）：

```json
{"v":1,"type":"hello","host_time":1712345678.9,"source":"joycon","device":"Nintendo Switch Joy-Con (L/R)","button":1}
```

每次拍掌：

```json
{"v":1,"type":"clap","host_time":1712345679.123,"source":"joycon","button":1}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `v` | 否，默认 1 | 协议版本；目前只接受 1 |
| `type` | 是 | `hello` 或 `clap`；其他类型记录后忽略 |
| `host_time` | `clap` 必填 | `perf_counter()` 秒，发送端按下沿的时间 |
| `source` | 否，默认 `joycon` | 日志来源标签，最长 32 字符 |
| `button` | 否 | 按键编号，仅用于日志；默认 1（A） |
| `device` | `hello` 可选 | 设备名，用于状态提示 |

### 校验与时钟

- `host_time` 必须是有限数字；字符串、NaN、Inf 都丢弃。
- 发送端时钟比游戏快（`host_time > now`）时改用游戏接收时间，并记录 `input_clock_adjust`。
- `now - host_time > input.max_age_ms` 的消息直接丢弃，记录 `input_late`，不出声、不判定。
- 发送器和游戏必须同机运行；`perf_counter` 跨进程同源只在这个前提下成立。
- 单行上限 1024 字节，缓冲上限 4KB；断线、半包、粘包都按行缓冲处理。

## 游戏侧处理

1. `ClapSocketInput` 后台线程只做接收、校验、`on_transition`／`on_record`，不操作 UI 或日志文件。
2. `InputBridge.receive()` 用 `host_time` 调 `engine.reading(host_time)`，把主机时间换成音乐位置；仍在播放且 generation 匹配时立即 `engine.trigger_clap()`，然后入队。
3. 主线程每帧 `app.poll_inputs()` 取队列，调用 `app.clap(..., source="joycon")`。
4. `app.clap()` 应用 `input_offset_ms` 后进入 `_judge_clap()`；外部输入启用时判定多等 20ms（`RECEIVE_SETTLE_SECONDS`），只用于同帧排序，声音不等。
5. 停止、暂停、重播、配置替换都会使旧队列失效；旧事件不会影响新一轮。

## 判题机逻辑

判定不读取“按得多重”或“按时长”，只接受一个时间点 `music_time`。核心在 `RhythmSession.judge()`：

- **定位**：`corrected = music_time - input_offset_ms/1000`；正补偿把输入时间往前提。
- **最近目标**：在正式目标中二分查找最近者；与前后目标等距时选较早的目标。
- **对称窗口**：`|corrected - target.time| <= window_ms/1000` 才算命中，默认 ±100ms。
- **状态**：
  - `hit`：窗口内且目标未命中。
  - `early` / `late`：窗口外；迟到且目标未处理时记为漏拍。
  - `duplicate`：同一目标已命中又打一次，记重复错误。
  - `waiting` / `preparing`：启动前或预备小节的输入，不计正式成绩。
  - `preparation_hit` / `preparation_duplicate`：预备小节只练习，不计输赢。
  - `failed` / `completed`：本轮已结束，后续输入不再改变成绩。
- **漏拍结算**：`advance()` 和输入本身都会推动结算水位；超过目标时间 + 窗口即记漏拍。
- **错误上限**：每个正式四分拍最多记 1 次错误（漏拍、早、晚、重复共享额度）；达到 `max_mistakes` 立即冻结本轮。`0` 表示不限。
- **成绩**：`hits / (hits + mistakes)`；无正式结果时显示 `—`。
- **启动**：任意拍掌可启动；先算归属小节，等归属小节结束，下一完整小节逐拍预备，再下一小节进入正式游戏。启动拍不计分。

所以发送器**不需要**知道谱面、目标或判定窗口；它只需要给出可靠的“按下发生在什么时刻”。错误补偿、最近目标、输赢都由游戏决定。

## 运行时序

```text
A 键物理按下
  → 发送器下一帧发现（≤2ms 轮询，实际受手柄报告率限制）
  → sendall JSON（本机 socket，通常 <1ms）
  → 游戏 recv + trigger_clap（声音进入音频回调队列）
  → 下一帧主线程 poll + 判定
```

- `host_time` 是“发送器发现按下”的时间，不是蓝牙包到达时间；实际链路还包含蓝牙、调度和音频缓冲。
- 拍手声不等待判定；判定多等 20ms 是为了排序，不扩大容差。
- `input.max_age_ms` 只防陈旧消息，不参与节奏判定。

## 运行

```sh
# 终端 1
.venv/bin/python beatgame.py

# 终端 2
.venv/bin/python joycon_clap_sender.py --button 1
```

发送器参数：

```sh
--list                列出手柄
--device 1            选第 1 个手柄
--button 1            A 键（SDL 编号；先跑 --list 核对）
--release-ms 40       松开稳定窗口，越大越不容易因抖动重复计数
--socket PATH         覆盖 socket 路径
```

游戏设置页“声音与输入”会显示“等待 Joy-Con 脚本连接 / 已连接 / 监听失败”，并可重启监听。

## 调试与测试

```sh
# 不接手柄，只测协议解析、时钟和重连
.venv/bin/python -m unittest tests.test_clap_socket -v

# 不接手柄，测“按一次只算一次”
.venv/bin/python experiments/joycon_press_edge.py --selftest

# 手动发一条拍掌（游戏需已启动）
.venv/bin/python - <<'PY'
import json, socket, time
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect("tmp/joycon-clap.sock")
s.sendall((json.dumps({"v":1,"type":"clap","host_time":time.perf_counter()}) + "\n").encode())
PY
```

日志在 `logs/session-*.jsonl`：`input_message` 记录 socket 原始消息与 `lag_ms`，`clap_transition` 记录桥接结果，`clap` 记录最终判定。

## 历史

串口手套代码、监测工具和 4 个串口测试已移至 `experiments/serial_glove_backup/`，恢复步骤见该目录的 README。原验证记录保留在 [glove-serial-validation.md](glove-serial-validation.md)，作为历史资料，不代表当前运行路径。
