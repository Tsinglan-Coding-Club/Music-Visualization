"""预加载音乐和拍手声，以同一输出流混音，并发布实际输出时间锚点。

这是 Python 软件验证实现，不对操作系统调度或物理发声延迟作实时保证。
音频回调不读取文件、不绘图、不等待判定；资源均在打开输出流前准备。
"""

from collections import deque
from dataclasses import dataclass
import math
from time import perf_counter

import numpy as np
import sounddevice as sd
import soundfile as sf

from .configuration import Config


@dataclass(frozen=True)
class ClockReading:
    generation: int
    position: float
    playing: bool
    tail_seconds: float = 0.0


def generated_clap(sample_rate: int, channels: int) -> np.ndarray:
    """无外部素材时使用的短噪声拍手音；不是正式演出音效。"""
    t = np.arange(round(sample_rate * 0.16), dtype=np.float32) / sample_rate
    noise = np.random.default_rng(90).uniform(-1, 1, len(t)).astype(np.float32)
    # 多个短瞬态模拟双手拍击，避免用延迟起音的提示音乐替代拍掌反馈。
    envelope = np.exp(-t * 45)
    envelope += np.where(t >= 0.012, 0.55 * np.exp(-np.maximum(t - 0.012, 0) * 65), 0)
    envelope += np.where(t >= 0.025, 0.30 * np.exp(-np.maximum(t - 0.025, 0) * 80), 0)
    mono = noise * envelope
    mono /= max(float(np.max(np.abs(mono))), 1e-6)
    return np.repeat(mono[:, None], channels, axis=1)


class AudioEngine:
    """UI 发送短命令，音频线程独占播放游标和音效声部。"""

    def __init__(self, config: Config):
        self.config = config
        self.music, self.sample_rate = sf.read(config.music_path, dtype="float32", always_2d=True)
        if not len(self.music) or not np.isfinite(self.music).all():
            raise ValueError("音乐为空或包含无效采样")
        self.channels = self.music.shape[1]
        if self.channels not in (1, 2):
            raise ValueError("首版仅支持单声道／立体声音乐")
        self.duration = len(self.music) / self.sample_rate
        if config.clap_path:
            clap, rate = sf.read(config.clap_path, dtype="float32", always_2d=True)
            if not len(clap) or not np.isfinite(clap).all():
                raise ValueError("拍手音效为空或包含无效采样")
            if rate != self.sample_rate:
                old_t = np.arange(len(clap)) / rate
                new_t = np.arange(round(len(clap) / rate * self.sample_rate)) / self.sample_rate
                clap = np.column_stack([np.interp(new_t, old_t, clap[:, c]) for c in range(clap.shape[1])])
            if clap.shape[1] != self.channels:
                clap = np.repeat(clap.mean(axis=1, keepdims=True), self.channels, axis=1)
            self.clap = np.asarray(clap * config.clap_volume, dtype=np.float32)
        else:
            self.clap = generated_clap(self.sample_rate, self.channels) * config.clap_volume
        self.music *= config.music_volume
        self.generation = 0
        self._render_generation = 0
        self._cursor = 0
        self._playing = False
        self._commands = deque()
        self._anchors = deque(maxlen=2048)
        self._trigger_delay_ms = deque(maxlen=4096)
        self._voice_positions = [-1] * 16
        self.underruns = 0
        self.stream = None
        self._clock_offset = 0.0
        self._end_marker = None

    def open(self):
        self.stream = sd.OutputStream(
            samplerate=self.sample_rate, channels=self.channels, dtype="float32",
            blocksize=self.config.blocksize, latency="low", device=self.config.device,
            callback=self._callback,
        )
        try:
            before = perf_counter()
            stream_time = self.stream.time
            after = perf_counter()
            self._clock_offset = (before + after) / 2 - stream_time
            self.stream.start()
        except Exception:
            self.stream.close()
            self.stream = None
            raise

    @property
    def latency_ms(self):
        return self.stream.latency * 1000 if self.stream is not None else 0.0

    def play(self, position=0.0):
        if not math.isfinite(position) or not 0 <= position < self.duration:
            raise ValueError("播放位置必须在音乐范围内")
        self.generation += 1
        self._commands.append(("play", self.generation, round(position * self.sample_rate)))
        return self.generation

    def pause(self):
        self._commands.append(("pause", self.generation, 0))

    def resume(self):
        self._commands.append(("resume", self.generation, 0))

    def stop(self):
        self.generation += 1
        self._commands.append(("stop", self.generation, 0))
        return self.generation

    def trigger_clap(self):
        # 无论视觉帧率如何，音频线程都会在下一可用块中直接启动预载声部。
        if len(self._commands) < 256:
            self._commands.append(("clap", self.generation, perf_counter()))

    def latency_summary(self):
        """从入队到计划 DAC 输出的估计，不能替代声学延迟实测。"""
        delays = np.asarray(tuple(self._trigger_delay_ms))
        if not len(delays):
            return {"count": 0}
        return {"count": len(delays), "p50_ms": float(np.percentile(delays, 50)),
                "p95_ms": float(np.percentile(delays, 95)), "max_ms": float(delays.max())}

    def _callback(self, outdata, frames, timing, status):
        dac_host_time = timing.outputBufferDacTime + self._clock_offset
        if status.output_underflow:
            self.underruns += 1
        for _ in range(256):
            try:
                action, generation, value = self._commands.popleft()
            except IndexError:
                break
            if action in ("play", "stop"):
                self._render_generation = generation
                self._cursor = value
                self._playing = action == "play"
                self._end_marker = None
                for i in range(len(self._voice_positions)):
                    self._voice_positions[i] = -1
            elif generation == self._render_generation:
                if action == "pause":
                    self._playing = False
                    for i in range(len(self._voice_positions)):
                        self._voice_positions[i] = -1
                elif action == "resume":
                    self._playing = self._cursor < len(self.music)
                elif action == "clap" and (self._playing or self._end_marker is not None):
                    delay = (dac_host_time - value) * 1000
                    if delay >= 0:
                        self._trigger_delay_ms.append(delay)
                    try:
                        voice = self._voice_positions.index(-1)
                    except ValueError:
                        voice = max(range(len(self._voice_positions)), key=self._voice_positions.__getitem__)
                    self._voice_positions[voice] = 0

        # timing 与 stream.time 同源；只换算至 perf_counter，不再次叠加声卡缓冲。
        span = min(frames, len(self.music) - self._cursor) / self.sample_rate if self._playing else 0.0
        self._anchors.append((dac_host_time, self._cursor / self.sample_rate,
                              self._render_generation, self._playing, span))
        outdata.fill(0)
        if self._playing:
            count = min(frames, len(self.music) - self._cursor)
            outdata[:count] = self.music[self._cursor:self._cursor + count]
            self._cursor += count
            if self._cursor == len(self.music):
                self._playing = False
                self._end_marker = (self._render_generation, dac_host_time + count / self.sample_rate)
        for i, position in enumerate(self._voice_positions):
            if position < 0:
                continue
            count = min(frames, len(self.clap) - position)
            outdata[:count] += self.clap[position:position + count]
            self._voice_positions[i] = position + count if position + count < len(self.clap) else -1
        np.clip(outdata, -1.0, 1.0, out=outdata)

    def reading(self, host_time=None) -> ClockReading:
        host_time = perf_counter() if host_time is None else host_time
        # 在主线程复制；回调只 append，不遍历，也不获取绘图线程持有的锁。
        anchors = tuple(self._anchors)
        for dac_time, position, generation, playing, span in reversed(anchors):
            if dac_time <= host_time:
                if playing:
                    # 输出欠载时不能凭旧锚点外推未渲染的音乐，更不能先判漏再倒退。
                    position += min(host_time - dac_time, span)
                end_marker = self._end_marker
                tail = (max(0.0, host_time - end_marker[1])
                        if end_marker is not None and end_marker[0] == generation else 0.0)
                return ClockReading(generation, min(position, self.duration),
                                    playing and position < self.duration, tail)
        return ClockReading(0, 0.0, False)

    def close(self):
        if self.stream is not None:
            self.stream.abort()
            self.stream.close()
            self.stream = None
        self._commands.clear()
        self._anchors.clear()
        # abort 后回调已退出，才能同步重置；配置重载失败重新打开时保持停止。
        self._cursor = 0
        self._playing = False
        self._render_generation = self.generation
        self._end_marker = None
        for i in range(len(self._voice_positions)):
            self._voice_positions[i] = -1
