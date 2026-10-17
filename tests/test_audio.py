from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from rhythm_game.audio import AudioEngine
from rhythm_game.configuration import load_config
from tests.helpers import write_config


class AudioTests(unittest.TestCase):
    """直接检查回调的采样和时间锚点，不打开音频设备。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        sf.write(self.root / "music.wav", np.full((48000, 2), 0.2, np.float32), 48000, subtype="FLOAT")
        cfg = load_config(write_config(self.root))
        self.engine = AudioEngine(cfg)
        self.engine._clock_offset = 100.0

    def render(self, dac=10.0, frames=128):
        output = np.full((frames, 2), np.nan, dtype=np.float32)
        self.engine._callback(output, frames,
                              SimpleNamespace(outputBufferDacTime=dac, currentTime=dac - 0.02),
                              SimpleNamespace(output_underflow=False))
        self.assertTrue(np.isfinite(output).all())
        return output

    def test_clock_uses_dac_time_not_render_cursor(self):
        generation = self.engine.play()
        self.render()
        self.assertEqual(self.engine.reading(109.999).generation, 0)
        reading = self.engine.reading(110.001)
        self.assertEqual(reading.generation, generation)
        self.assertAlmostEqual(reading.position, 0.001)
        self.assertNotAlmostEqual(reading.position, 128 / 48000)

    def test_future_pause_does_not_freeze_audio_already_in_flight(self):
        self.engine.play()
        self.render()
        self.engine.pause()
        self.render(dac=10 + 128 / 48000)
        self.assertTrue(self.engine.reading(110.001).playing)
        paused = self.engine.reading(110.01)
        self.assertFalse(paused.playing)
        self.assertAlmostEqual(paused.position, 128 / 48000)
        self.engine.resume()
        self.render(dac=11)
        self.assertTrue(self.engine.reading(111.001).playing)
        self.assertAlmostEqual(self.engine.reading(111.001).position, 128 / 48000 + 0.001)

    def test_clap_mixes_with_music_and_restart_clears_voice(self):
        self.engine.play()
        baseline = self.render()
        self.engine.trigger_clap()
        clap = self.render(dac=10.1)
        self.assertFalse(np.allclose(clap, baseline))
        self.assertTrue((np.abs(clap) <= 1).all())
        self.engine.play()
        restarted = self.render(dac=10.2)
        np.testing.assert_allclose(restarted, baseline)

    def test_output_gap_does_not_predict_unrendered_content_then_jump_back(self):
        self.engine.play()
        self.render(dac=10)
        self.render(dac=10.11)
        before = self.engine.reading(110.1)
        after = self.engine.reading(110.11)
        self.assertAlmostEqual(before.position, 128 / 48000)
        self.assertAlmostEqual(after.position, before.position)

    def test_trigger_schedule_estimate_is_distinct_from_device_latency(self):
        self.engine.play()
        with patch("rhythm_game.audio.perf_counter", return_value=109.99):
            self.engine.trigger_clap()
        self.render(dac=10)
        estimate = self.engine.latency_summary()
        self.assertEqual(estimate["count"], 1)
        self.assertAlmostEqual(estimate["p50_ms"], 10.0)
        self.assertAlmostEqual(estimate["max_ms"], 10.0)

    def test_stop_generation_isolates_queued_old_clap(self):
        self.engine.play()
        self.engine.trigger_clap()
        new_generation = self.engine.stop()
        output = self.render()
        np.testing.assert_array_equal(output, 0)
        self.assertEqual(self.engine.reading(110.01).generation, new_generation)
        self.assertFalse(self.engine.reading(110.01).playing)

    def test_eof_is_silent_and_does_not_run_past_music(self):
        self.engine.play(0.999)
        output = self.render()
        np.testing.assert_allclose(output[:48], 0.1)
        np.testing.assert_array_equal(output[48:], 0)
        self.render(dac=10.01)
        self.assertEqual(self.engine.reading(110.02).position, 1)
        self.assertFalse(self.engine.reading(110.02).playing)
        self.assertAlmostEqual(self.engine.reading(110.02).tail_seconds, 0.019)

    def test_close_before_pending_stop_is_consumed_reopens_stopped(self):
        self.engine.play()
        self.render()
        generation = self.engine.stop()
        self.engine.close()
        output = self.render(dac=11)
        np.testing.assert_array_equal(output, 0)
        reading = self.engine.reading(111.01)
        self.assertEqual(reading.generation, generation)
        self.assertFalse(reading.playing)
        self.assertEqual(reading.position, 0)

    def test_clap_resampling_and_channel_conversion_before_play(self):
        clap_path = self.root / "clap.wav"
        sf.write(clap_path, np.ones(2205, dtype=np.float32) * 0.5, 22050, subtype="FLOAT")
        engine = AudioEngine(replace(self.engine.config, clap_path=clap_path))
        self.assertEqual(engine.clap.shape, (4800, 2))
        self.assertEqual(engine.clap.dtype, np.float32)
        np.testing.assert_allclose(engine.clap, 0.2)


if __name__ == "__main__":
    unittest.main()
