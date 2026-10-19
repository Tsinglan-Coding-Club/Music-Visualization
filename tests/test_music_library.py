from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rhythm_game.music_library import discover_music


class MusicLibraryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.config_path = self.root / "config.toml"
        self.formats = patch("rhythm_game.music_library.sf.available_formats",
                             return_value={name: name for name in ("WAV", "MP3", "FLAC", "OGG", "AIFF")})
        self.formats.start()
        self.addCleanup(self.formats.stop)

    def make_file(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not decoded during enumeration")
        return path

    def test_recursively_enumerates_supported_extensions_and_stable_order(self):
        files = [self.make_file(name) for name in (
            "music/z.wav", "music/另一首.MP3", "music/nested/A.ogg", "music/b.flac",
            "music/c.aiff", "music/d.AIF")]
        self.make_file("music/notes.txt")
        self.make_file("music/unsupported.m4a")
        self.make_file("outside.wav")
        (self.root / "music/fake.wav").mkdir()
        with patch("rhythm_game.music_library.sf.read", side_effect=AssertionError("must not decode")), \
                patch("rhythm_game.music_library.sf.info", side_effect=AssertionError("must not inspect audio")):
            result = discover_music(self.config_path)
        self.assertEqual(result, tuple(sorted(files, key=lambda path: (str(path).casefold(), str(path)))))

    def test_decoder_format_capabilities_filter_candidates(self):
        wav = self.make_file("music/a.wav")
        self.make_file("music/b.mp3")
        self.make_file("music/c.ogg")
        with patch("rhythm_game.music_library.sf.available_formats", return_value={"WAV": "WAV"}):
            self.assertEqual(discover_music(self.config_path), (wav,))
        with patch("rhythm_game.music_library.sf.available_formats", return_value={}):
            self.assertEqual(discover_music(self.config_path), ())

    def test_preserves_current_path_outside_library_and_resolves_relative_path(self):
        music = self.make_file("outside/旧 # 曲.m4a")
        self.assertEqual(discover_music(self.config_path, "outside/旧 # 曲.m4a"), (music,))
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside).resolve() / "external.mp3"
            external.write_bytes(b"test")
            self.assertEqual(discover_music(self.config_path, external), (external,))

    def test_deduplicates_current_and_symbolic_links(self):
        music = self.make_file("music/current.wav")
        (self.root / "music/alias.wav").symlink_to(music)
        self.assertEqual(discover_music(self.config_path, music), (music,))

    def test_missing_library_invalid_current_and_broken_links_are_ignored(self):
        for current in (None, "", " ", "missing.wav", ".", "bad\0path"):
            with self.subTest(current=current):
                self.assertEqual(discover_music(self.config_path, current), ())
        (self.root / "music").mkdir()
        (self.root / "music/missing.wav").symlink_to(self.root / "missing.wav")
        self.assertEqual(discover_music(self.config_path), ())

    def test_inaccessible_candidates_are_ignored(self):
        self.make_file("music/a.wav")
        with patch("rhythm_game.music_library.Path.is_file", side_effect=PermissionError("denied")):
            self.assertEqual(discover_music(self.config_path, "outside.wav"), ())


if __name__ == "__main__":
    unittest.main()
