import json
import tempfile
import unittest
from pathlib import Path

from ganss import profiles


class ProfilesTest(unittest.TestCase):
    def test_round_trip_and_builtin_not_persisted(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config' / 'profiles.json'
            data = {'工作': {'version': 1, 'layer': 'normal', 'mappings': {'A': 'B', 'S': 'CTRL+C'}},
                    '游戏': {'version': 1, 'layer': 'fn', 'mappings': {}}}
            profiles.save(data, path)
            self.assertEqual(profiles.load(path), data)
            self.assertNotIn(profiles.DEFAULT_NAME, profiles.load(path))

    def test_invalid_data_does_not_replace_saved_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'profiles.json'
            profiles.save({}, path)
            for data in ({profiles.DEFAULT_NAME: {'version': 1, 'layer': 'normal', 'mappings': {}}},
                         {'bad': {'version': 1, 'layer': 'normal', 'mappings': {'A': 'B', 'index:56': 'C'}}}):
                with self.assertRaises(ValueError):
                    profiles.save(data, path)
                self.assertEqual(json.loads(path.read_text()), {})

    def test_corrupt_file_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'profiles.json'
            path.write_text('{bad')
            with self.assertRaises(ValueError):
                profiles.load(path)
            self.assertEqual(path.read_text(), '{bad')
