import sys
import unittest
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins"))
from match_sync.engine import mix_pcm, route_pcm_s16_stereo


class ChannelRoutingTests(unittest.TestCase):
    def test_unchanged_stereo(self):
        pcm = array("h", [32767, -32768, 123, 456]).tobytes()
        for channel in (0, 1, 2, 5):
            self.assertIs(route_pcm_s16_stereo(pcm, channel), pcm)

    def test_physical_left_and_right(self):
        pcm = array("h", [32767, 32767, -32768, -32768, 100, -200, 1, -2]).tobytes()
        self.assertEqual(list(array("h", route_pcm_s16_stereo(pcm, 3))),
                         [32767, 0, -32768, 0, -50, 0, -1, 0])
        self.assertEqual(list(array("h", route_pcm_s16_stereo(pcm, 4))),
                         [0, 32767, 0, -32768, 0, -50, 0, -1])

    def test_empty_and_incomplete_frames(self):
        for channel in (3, 4):
            self.assertEqual(route_pcm_s16_stereo(b"", channel), b"")
            for length in (1, 2, 3, 5):
                with self.assertRaises(ValueError):
                    route_pcm_s16_stereo(bytes(length), channel)

    def test_routing_before_multiroom_mix(self):
        first = route_pcm_s16_stereo(array("h", [1000, 3000]).tobytes(), 3)
        second = route_pcm_s16_stereo(array("h", [-1000, -3000]).tobytes(), 4)
        self.assertEqual(list(array("h", mix_pcm([(first, 100), (second, 100)], 1))),
                         [1000, -1000])


if __name__ == "__main__":
    unittest.main()
