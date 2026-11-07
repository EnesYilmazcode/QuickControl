from types import SimpleNamespace
import unittest

from control_policy import EscapeTakeover, default_displays


class EscapeTests(unittest.TestCase):
    def test_two_presses_with_release(self):
        policy = EscapeTakeover()
        self.assertFalse(policy.key(27, True, 1, 2))
        self.assertFalse(policy.key(27, False, 1.1, 2))
        self.assertTrue(policy.key(27, True, 1.3, 2))

    def test_hold_is_not_double_tap(self):
        policy = EscapeTakeover()
        for now in (1, 1.1, 1.2, 1.3):
            self.assertFalse(policy.key(27, True, now, 2))

    def test_slow_taps_do_not_stop(self):
        policy = EscapeTakeover()
        policy.key(27, True, 1, 2)
        policy.key(27, False, 1.1, 2)
        self.assertFalse(policy.key(27, True, 2, 2))
        policy.key(27, False, 2.1, 2)
        self.assertTrue(policy.key(27, True, 2.2, 2))

    def test_other_key_or_new_lease_resets_taps(self):
        for interrupt in ('key', 'lease'):
            policy = EscapeTakeover()
            policy.key(27, True, 1, 2)
            policy.key(27, False, 1.1, 2)
            if interrupt == 'key':
                policy.key(65, True, 1.2, 2)
            self.assertFalse(policy.key(27, True, 1.3, 3 if interrupt == 'lease' else 2))


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.displays = [SimpleNamespace(index=0, primary=False), SimpleNamespace(index=1, primary=True)]

    def test_primary_is_not_assumed_to_be_zero(self):
        self.assertEqual(default_displays(self.displays, None, None), [1])

    def test_explicit_display_and_all_displays(self):
        self.assertEqual(default_displays(self.displays, [0], None), [0])
        self.assertEqual(default_displays(self.displays, [], None), [])

    def test_region_wins_and_missing_displays_fail(self):
        self.assertIsNone(default_displays([], None, [0, 0, 100, 100]))
        with self.assertRaises(RuntimeError):
            default_displays([], None, None)


if __name__ == '__main__':
    unittest.main()
