"""《主音归来》节奏互动demo 入口。Python 3.11+。"""

import argparse
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description="音乐选择、手套／键盘拍掌与多小节节奏互动")
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().with_name("config.toml"))
    parser.add_argument("--list-devices", action="store_true", help="列出音频设备后退出")
    parser.add_argument("--check", action="store_true", help="验证配置、解码音乐与生成目标，不播放声音")
    parser.add_argument("--demo-seconds", type=float, help="运行指定秒数的自动软件输入验证，然后退出")
    parser.add_argument("--screenshot", type=Path, help="自动验证结束时保存画面，配合 --demo-seconds")
    args = parser.parse_args()
    if args.demo_seconds is not None and not 0 < args.demo_seconds <= 600:
        parser.error("--demo-seconds 必须在 0～600 秒之间")
    if args.screenshot and args.demo_seconds is None:
        parser.error("--screenshot 需要配合 --demo-seconds")
    try:
        if args.list_devices:
            import sounddevice
            print(sounddevice.query_devices())
            return 0
        from rhythm_game.configuration import load_config
        from rhythm_game.audio import AudioEngine
        from rhythm_game.app import RhythmApp, make_chart
        if args.check:
            config = load_config(args.config)
            engine = AudioEngine(config)
            chart = make_chart(config, engine.duration)
            print(f"音乐：{config.music_path.name}，{engine.duration:.3f}s，{engine.sample_rate}Hz")
            print(f"速度：四分音符 = {config.bpm:g}；首拍：{chart.start:.3f}s；目标数：{len(chart.targets)}")
            print(f"拍号：{config.beats_per_bar}/4；循环谱面：{len(config.patterns)} 小节；"
                  f"正式游戏：{config.game_bars} 小节；错误上限：{config.max_mistakes or '不限'}")
            print("配置与音乐检查通过；尚未打开音频设备。")
            return 0
        app = RhythmApp(args.config, enable_glove=args.demo_seconds is None)
        try:
            from rhythm_game.window import RhythmWindow
            window = RhythmWindow(app)
            result = window.run(args.demo_seconds, args.screenshot)
        finally:
            app.close()
        print(f"本轮命中 {result['hits']}，漏拍 {result['misses']}，错误 {result['mistakes']}，"
              f"{'失败' if result['failed'] else '未失败'}，音频欠载 {result['underruns']}")
        estimate = app.engine.latency_summary()
        if estimate["count"]:
            print(f"软件排程至计划 DAC 输出估计：P50 {estimate['p50_ms']:.1f}ms，"
                  f"P95 {estimate['p95_ms']:.1f}ms，最大 {estimate['max_ms']:.1f}ms；非声学实测")
        print(f"软件输入记录：{result['log']}")
        return 0
    except ImportError as exc:
        print(f"缺少运行依赖：{exc}。请先运行 python -m pip install -r requirements.txt", file=sys.stderr)
    except Exception as exc:
        print(f"启动／运行失败：{exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
