import unittest

from glide import ease, glide_path, target_of


class GlideTest(unittest.TestCase):
    def test_ease_ends_at_both_ends(self):
        self.assertEqual(ease(0), 0)
        self.assertEqual(ease(1), 1)
        self.assertLess(ease(0.1), 0.1)

    def test_path_ends_on_target_and_is_deterministic(self):
        a, d = glide_path((0, 0), (800, 400), 450)
        b, _ = glide_path((0, 0), (800, 400), 450)
        self.assertEqual(a, b)
        self.assertEqual(a[-1], (800, 400))
        self.assertGreater(len(a), 5)
        self.assertLessEqual(d * len(a), 0.451)

    def test_short_hops_and_disabled_glide_skip(self):
        self.assertEqual(glide_path((10, 10), (12, 11), 450), ([], 0.0))
        self.assertEqual(glide_path((0, 0), (900, 900), 0), ([], 0.0))

    def test_short_moves_are_quicker(self):
        _, d1 = glide_path((0, 0), (100, 0), 450)
        p1, _ = glide_path((0, 0), (100, 0), 450)
        p2, d2 = glide_path((0, 0), (1500, 0), 450)
        self.assertLess(d1 * len(p1), d2 * len(p2))

    def test_target_of_only_takes_plain_points(self):
        self.assertEqual(target_of([3, 4]), (3, 4))
        self.assertIsNone(target_of('label'))
        self.assertIsNone(target_of([True, 2]))
        self.assertIsNone(target_of(None))


if __name__ == '__main__':
    unittest.main()
