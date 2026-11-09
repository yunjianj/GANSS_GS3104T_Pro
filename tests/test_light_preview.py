import unittest

from ganss.light_preview import preview_color


class PreviewTest(unittest.TestCase):
    def sample(self, mode=1, rgb=(1, 0.5, 0), brightness=5, speed=3,
               colorful=False, x=200, y=100, index=10, elapsed=0):
        return preview_color(mode, rgb, brightness, speed, colorful, x, y, index, elapsed)

    def test_off_and_brightness(self):
        self.assertEqual(self.sample(mode=0), (0, 0, 0))
        self.assertEqual(self.sample(brightness=0), (0, 0, 0))
        self.assertEqual(self.sample(), (255, 128, 0))
        self.assertEqual(self.sample(brightness=2), (102, 51, 0))

    def test_patterns_and_animation(self):
        self.assertNotEqual(self.sample(mode=7, elapsed=0), self.sample(mode=7, elapsed=1))
        self.assertNotEqual(self.sample(mode=11, x=100), self.sample(mode=11, x=400))
        self.assertNotEqual(self.sample(colorful=True), self.sample(colorful=False))
        for mode in range(20):
            for component in self.sample(mode=mode, elapsed=3):
                self.assertTrue(0 <= component <= 255)

    def test_reactive_modes_wait_for_key_input(self):
        for mode in (2, 3, 13, 14, 15):
            a = self.sample(mode=mode, elapsed=0, colorful=True)
            self.assertEqual(a, self.sample(mode=mode, elapsed=5, colorful=True))
        self.assertEqual(self.sample(mode=2), (0, 0, 0))
        self.assertEqual(self.sample(mode=3), (255, 128, 0))
        triggered = preview_color(2, (1, .5, 0), 5, 3, False, 200, 100, 10, 0,
                                  [(10, 200, 100, .1)])
        self.assertGreater(triggered[0], 0)
        other = preview_color(2, (1, .5, 0), 5, 3, False, 200, 100, 10, 0,
                              [(11, 200, 100, .1)])
        self.assertEqual(other, (0, 0, 0))
        extinguished = preview_color(3, (1, .5, 0), 5, 3, False, 200, 100, 10, 0,
                                     [(10, 200, 100, 0)])
        self.assertEqual(extinguished, (0, 0, 0))


if __name__ == '__main__':
    unittest.main()
