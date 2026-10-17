"""软件练习控制器；音频与判定不依赖画面引擎。"""

from datetime import datetime
import json
from pathlib import Path
from time import perf_counter

from .audio import AudioEngine
from .configuration import load_config, prepare_config_update, save_config_text
from .core import RhythmChart, RhythmSession


BG = (12, 16, 25)
PANEL = (21, 27, 40)
LINE = (42, 51, 70)
TEXT = (237, 241, 250)
MUTED = (143, 157, 183)
GREEN = (114, 235, 187)
PURPLE = (176, 158, 255)
RED = (255, 135, 148)


def make_chart(config, duration):
    return RhythmChart(config.bpm, config.beats_per_bar, config.beat_start_seconds,
                       config.beat_offset_ms, config.note_values, duration)


class RhythmApp:
    def __init__(self, config_path, log_dir=None):
        self.config_path = Path(config_path)
        self.config = load_config(self.config_path)
        self.engine = AudioEngine(self.config)
        self.chart = make_chart(self.config, self.engine.duration)
        self.session = RhythmSession(self.chart, self.config.window_ms, self.config.input_offset_ms,
                                     lead_in_bars=self.config.lead_in_bars)
        self.desired_playing = False
        self.paused = False
        self.expected_generation = 0
        self.feedback = ("准备就绪", "按 Enter 播放音乐，空格键模拟拍掌", MUTED)
        self.feedback_time = 0.0
        self.last_judge_time = 0.0
        self._logged_states = set()
        self.auto_input = False
        self._auto_target_cursor = 0
        self._round_reported = False
        log_dir = Path(log_dir) if log_dir else self.config.source.parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / f"session-{datetime.now():%Y%m%d-%H%M%S-%f}.jsonl"
        self.log = self.log_path.open("w", encoding="utf-8")
        try:
            self.engine.open()
        except Exception:
            self.log.close()
            raise
        self.record("configuration", bpm=self.config.bpm, beat_start=self.chart.start,
                    beats_per_bar=self.config.beats_per_bar, note_values=self.config.note_values,
                    lead_in_bars=self.config.lead_in_bars,
                    beat_offset_ms=self.config.beat_offset_ms,
                    input_offset_ms=self.config.input_offset_ms, window_ms=self.config.window_ms,
                    sample_rate=self.engine.sample_rate, blocksize=self.config.blocksize,
                    output_latency_ms=self.engine.latency_ms, software_input=True)

    def record(self, event, **fields):
        self.log.write(json.dumps({"event": event, "generation": self.expected_generation,
                                   **fields}, ensure_ascii=False) + "\n")

    def message(self, title, subtitle="", color=MUTED):
        self.feedback = (title, subtitle, color)
        self.feedback_time = perf_counter()

    def start(self):
        self.expected_generation = self.engine.play(0)
        self.desired_playing = True
        self.paused = False
        self.last_judge_time = 0.0
        self._logged_states.clear()
        self._auto_target_cursor = 0
        self.session.reset()
        self._round_reported = False
        self.message("等待启动拍手", "任意小节第一拍按空格，预备后打一小节", PURPLE)
        self.record("start", entry_time=None)

    def reset_round(self):
        self.session.reset()
        self._logged_states.clear()
        self._round_reported = False
        self.message("等待启动拍手", "任意小节第一拍按空格，预备后打一小节", PURPLE)
        self.record("reset_round")

    def toggle_pause(self):
        if self.paused:
            self.engine.resume()
            self.paused = False
            self.desired_playing = True
            self.message("继续练习", "目标仍跟随原音乐位置", GREEN)
            self.record("resume")
        elif self.desired_playing:
            if self.engine.reading().position >= self.engine.duration:
                return
            self.engine.pause()
            self.paused = True
            self.desired_playing = False
            self.message("已暂停", "按 P 继续；空格不触发输入")
            self.record("pause")

    def stop(self):
        self.expected_generation = self.engine.stop()
        self.desired_playing = False
        self.paused = False
        self.session.reset()
        self.last_judge_time = 0.0
        self._logged_states.clear()
        self._round_reported = False
        self.message("已停止", "按 Enter 重新播放")
        self.record("stop")

    def clap(self, host_time=None, music_time=None):
        host_time = perf_counter() if host_time is None else host_time
        reading = self.engine.reading(host_time)
        grace = self.config.window_ms / 1000 + max(self.config.input_offset_ms, 0) / 1000
        in_tail = reading.position >= self.engine.duration and reading.tail_seconds <= grace
        if not self.desired_playing or not (reading.playing or in_tail) or reading.generation != self.expected_generation:
            self.message("当前未播放", "按 Enter 开始，或按 P 继续")
            return
        self.engine.trigger_clap()
        # 正常输入在键盘事件分发时计时。自动验证可传入已知音乐位置。
        position = reading.position + reading.tail_seconds if music_time is None else music_time
        position = max(position, self.last_judge_time)
        self.last_judge_time = position
        self._advance_session(position)
        if self.session.entry_time is None or self.session.finished:
            if not self.session.try_start(position):
                self.message("等待小节第一拍", "拍手声已触发；第一拍拍手才出现本小节短线", PURPLE)
                self.record("clap", host_time=host_time, music_time=position,
                            corrected_time=position - self.config.input_offset_ms / 1000,
                            target_index=None, target_time=None, status="waiting", error_ms=None)
                return
            self._logged_states.clear()
            self._round_reported = False
            self.record("round_start", entry_time=self.session.entry_time,
                        round_end_time=self.session.round_end_time,
                        trigger_time=self.session.trigger_time, lead_in_bars=self.config.lead_in_bars)
            if self.config.lead_in_bars:
                self.message("预备一小节" if self.config.lead_in_bars == 1 else "预备中",
                             f"{self.session.entry_time:.3f} 秒正式开始；这次拍手不计分", PURPLE)
                self.record("clap", host_time=host_time, music_time=position,
                            corrected_time=position - self.config.input_offset_ms / 1000,
                            target_index=None, target_time=None, status="preparing", error_ms=None)
                return
        result = self.session.judge(position)
        labels = {"hit": "命中", "early": "太早了", "late": "太晚了",
                  "duplicate": "重复拍掌", "waiting": "等待主拍"}
        color = GREEN if result.status == "hit" else PURPLE if result.status == "waiting" else RED
        detail = f"{result.error_ms:+.1f} ms" if result.error_ms is not None else "拍掌声已触发；尚未进入判定窗口"
        self.message(labels[result.status], detail, color)
        self.record("clap", host_time=host_time, music_time=position,
                    corrected_time=position - self.config.input_offset_ms / 1000,
                    target_index=result.target.index if result.target else None,
                    target_time=result.target.time if result.target else None,
                    status=result.status, error_ms=result.error_ms)

    def reload(self):
        try:
            config = load_config(self.config_path)
        except Exception as exc:
            self.message("配置未加载", str(exc), RED)
            return False
        return self._activate_config(config)

    def apply_settings(self, updates, expected_text):
        try:
            text, config = prepare_config_update(self.config_path, updates, expected_text)
        except Exception as exc:
            self.message("设置未保存", str(exc), RED)
            return False
        return self._activate_config(config, text, expected_text)

    def _activate_config(self, config, text=None, expected_text=None):
        # 先验证并解码；设备和保存均成功后才替换当前设置。
        try:
            replacement = AudioEngine(config)
            chart = make_chart(config, replacement.duration)
        except Exception as exc:
            self.message("配置未加载", str(exc), RED)
            return False
        self.stop()
        old_engine = self.engine
        try:
            old_engine.close()
        except Exception as exc:
            self.message("音频设备无法停止", str(exc), RED)
            return False
        try:
            replacement.open()
            if text is not None:
                save_config_text(self.config_path, text, expected_text)
        except Exception as exc:
            replacement.close()
            try:
                old_engine.open()
            except Exception as restore_error:
                self.message("音频设备无法打开", f"{exc}；恢复失败：{restore_error}", RED)
                return False
            self.message("设置未应用", str(exc), RED)
            return False
        self.engine, self.config, self.chart = replacement, config, chart
        self.session = RhythmSession(chart, config.window_ms, config.input_offset_ms,
                                     lead_in_bars=config.lead_in_bars)
        self.expected_generation = replacement.generation
        self.message("设置已保存" if text is not None else "配置已加载", f"首拍 {chart.start:.3f} 秒；按 Enter 重播", GREEN)
        self.record("reload", bpm=config.bpm, beat_start=chart.start,
                    beat_offset_ms=config.beat_offset_ms, input_offset_ms=config.input_offset_ms,
                    window_ms=config.window_ms, blocksize=config.blocksize)
        return True


    def _advance_session(self, music_time):
        self.session.advance(music_time)
        for index, state in self.session.states.items():
            if index not in self._logged_states:
                self.record("target", target_index=index, status=state,
                            target_time=self.chart.targets[index].time)
                self._logged_states.add(index)
        if self.session.finished and not self._round_reported:
            self._round_reported = True
            self.message("本小节完成", f"命中 {self.session.hits} · 漏拍 {self.session.misses}", GREEN)
            self.record("round_end", hits=self.session.hits, misses=self.session.misses,
                        round_end_time=self.session.round_end_time)

    def update(self):
        reading = self.engine.reading()
        if reading.generation == self.expected_generation:
            # 音乐播完后，按实际主机时钟等待最后目标的窗口与输入补偿余量。
            end_time = reading.position + reading.tail_seconds
            if end_time >= self.engine.duration:
                grace = self.config.window_ms / 1000 + max(self.config.input_offset_ms, 0) / 1000
                if self.desired_playing and reading.tail_seconds > grace:
                    self.message("播放结束", "按 Backspace 重来；本轮结果已保存", GREEN)
                    self.desired_playing = False
                    self.record("ended", music_time=reading.position)
            self._advance_session(end_time)
        if self.engine.stream is not None and not self.engine.stream.active:
            self.desired_playing = False
            self.message("音频输出已中断", "检查输出设备后按 F5 重新连接", RED)

    def inject_due_targets(self):
        """自动验证使用预先已知的输入时间戳，不模拟人的按键延迟。"""
        reading = self.engine.reading()
        grace = self.config.window_ms / 1000 + max(self.config.input_offset_ms, 0) / 1000
        in_tail = reading.position >= self.engine.duration and reading.tail_seconds <= grace
        if not (reading.playing or in_tail) or reading.generation != self.expected_generation:
            return
        corrected = reading.position + reading.tail_seconds - self.config.input_offset_ms / 1000
        # 一次启动信号，预备后只自动演示一个正式小节。
        count = len(self.config.note_values)
        first = self.config.lead_in_bars * count
        targets = self.chart.targets[first:first + count]
        if self.config.lead_in_bars:
            targets = self.chart.targets[:1] + targets
        while self._auto_target_cursor < len(targets):
            target = targets[self._auto_target_cursor]
            if target.time > corrected:
                break
            self._auto_target_cursor += 1
            if target.index not in self.session.states:
                self.clap(music_time=target.time + self.config.input_offset_ms / 1000)

    def close(self):
        self.engine.close()
        if not self.log.closed:
            self.log.close()
