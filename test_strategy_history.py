import tempfile
import unittest
from pathlib import Path
from strategy_history import StrategyHistory

class HistoryTests(unittest.TestCase):
    def test_persistence_priority_and_new_contents(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            a, b = root/'a.bat', root/'b.bat'
            a.write_text('a'); b.write_text('b')
            history = StrategyHistory(root/'history.json')
            targets = {'YouTube': 'https://example.test'}
            self.assertIsNone(history.preferred([a,b], targets))
            history.record_success(b, targets)
            history = StrategyHistory(root/'history.json')
            self.assertEqual(history.order([a,b], targets), [b,a])
            self.assertEqual(history.preferred([a,b], targets), b)
            self.assertIsNone(history.preferred([a,b], {'Discord': 'https://other.test'}))
            b.write_text('updated')
            self.assertIsNone(history.preferred([a,b], targets))

    def test_corrupt_file_and_removed_strategy(self):
        with tempfile.TemporaryDirectory() as folder:
            filename = Path(folder)/'history.json'
            filename.write_text('{broken')
            history = StrategyHistory(filename)
            self.assertEqual(history.order([], {}), [])
            history.record_success(Path(folder)/'missing.bat', {})
            self.assertEqual(history.records, {})
    def test_invalid_timestamps_are_not_successes(self):
        invalid = [True, False, None, 'bad', '123', float('nan'),
                   float('inf'), -float('inf'), 0, -1, 10**10000]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            a, b = root/'a.bat', root/'b.bat'
            a.write_text('a'); b.write_text('b')
            targets = {'youtube': True}
            for value in invalid:
                with self.subTest(type=type(value).__name__):
                    history = StrategyHistory(root/'history.json')
                    history.records = {history.key(a, targets): {'last_success': value}}
                    self.assertEqual(history.order([b, a], targets), [b, a])
                    self.assertIsNone(history.preferred([a, b], targets))
                    history.records['valid'] = {'last_success': 1}
                    history.record_success(b, targets)
                    self.assertEqual(history.preferred([a, b], targets), b)
                    reloaded = StrategyHistory(root/'history.json')
                    self.assertEqual(reloaded.preferred([a, b], targets), b)
                    self.assertNotIn(history.key(a, targets), reloaded.records)

    def test_damaged_unrelated_record_does_not_block_save(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            a = root/'a.bat'
            a.write_text('a')
            history = StrategyHistory(root/'history.json')
            history.records = {'damaged': {'last_success': 'bad'}}
            history.record_success(a, {'youtube': True})
            self.assertEqual(StrategyHistory(root/'history.json').preferred(
                [a], {'youtube': True}), a)

    def test_valid_integer_and_float_timestamps_order(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            a, b = root/'a.bat', root/'b.bat'
            a.write_text('a'); b.write_text('b')
            history = StrategyHistory(root/'history.json')
            history.records = {history.key(a, {}): {'last_success': 1},
                               history.key(b, {}): {'last_success': 2.5}}
            self.assertEqual(history.order([a, b], {}), [b, a])
            self.assertEqual(history.preferred([a, b], {}), b)
