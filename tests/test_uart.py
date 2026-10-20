"""UART decoder tests use independent, continuous-time frame synthesis."""

import math
import unittest

from logic_analyzer.uart import decode_uart


def uart_samples(values, *, rate=1_000_000, baud=100_000, channel=0, data_bits=8, parity="none", stop_bits=1, corrupt_parity=False, bad_stop=False):
    """Sample transmitter bits by time, independent of receiver sample positions."""
    symbols = [1] * 3
    for value in values:
        symbols += [0] + [(value >> bit) & 1 for bit in range(data_bits)]
        if parity != "none":
            parity_bit = sum((value >> bit) & 1 for bit in range(data_bits)) % 2
            parity_bit ^= int(parity == "odd") ^ int(corrupt_parity)
            symbols.append(parity_bit)
        symbols += [int(not bad_stop)] * math.ceil(stop_bits)
    symbols += [1] * 3
    period = rate / baud
    return [symbols[min(len(symbols) - 1, math.floor(index / period))] << channel for index in range(math.ceil(len(symbols) * period))]


class UARTTests(unittest.TestCase):
    def test_basic_capture_decodes_expected_bytes(self):
        values = [0x00, 0xFF, ord("H"), ord("i"), 10]
        frames = decode_uart(uart_samples(values), 1_000_000, 100_000)
        self.assertEqual([frame.value for frame in frames], values)
        self.assertTrue(all(not frame.errors for frame in frames))
        self.assertEqual(frames[0].start_sample, 30)
        self.assertEqual(frames[0].end_sample, 130)
        self.assertEqual(frames[-1].text, r"\n")

    def test_fractional_bit_period_and_back_to_back_frames(self):
        values = [0xA5, 0x5A, 0, 255, 0x81] * 3
        frames = decode_uart(uart_samples(values, baud=115_200), 1_000_000, 115_200)
        self.assertEqual([frame.value for frame in frames], values)
        self.assertTrue(all(not frame.errors for frame in frames))

    def test_selected_packed_channel(self):
        samples = [sample | 0b011 for sample in uart_samples([0x42], channel=5)]
        frames = decode_uart(samples, 1_000_000, 100_000, channel=5)
        self.assertEqual([frame.value for frame in frames], [0x42])
        self.assertEqual(decode_uart(samples, 1_000_000, 100_000, channel=0), [])

    def test_glitch_before_real_frame_is_ignored(self):
        samples = uart_samples([0x64])
        samples[5:7] = [0, 0]
        self.assertEqual([frame.value for frame in decode_uart(samples, 1_000_000, 100_000)], [0x64])

    def test_parity_good_and_bad(self):
        for parity in ("even", "odd"):
            with self.subTest(parity=parity):
                good = decode_uart(uart_samples([0x55, 0x7F], parity=parity), 1_000_000, 100_000, parity=parity)
                self.assertEqual([frame.value for frame in good], [0x55, 0x7F])
                self.assertTrue(all(not frame.errors for frame in good))
                bad = decode_uart(uart_samples([0x7F], parity=parity, corrupt_parity=True), 1_000_000, 100_000, parity=parity)
                self.assertEqual(bad[0].errors, ("parity",))

    def test_bad_stop_bit_is_reported(self):
        frames = decode_uart(uart_samples([0x31], bad_stop=True), 1_000_000, 100_000)
        self.assertEqual(frames[0].value, 0x31)
        self.assertEqual(frames[0].errors, ("framing",))

    def test_truncated_frame_does_not_invent_byte(self):
        for cutoff in (32, 55, 120):
            with self.subTest(cutoff=cutoff):
                frames = decode_uart(uart_samples([0x55])[:cutoff], 1_000_000, 100_000)
                self.assertEqual(len(frames), 1)
                self.assertIsNone(frames[0].value)
                self.assertIn("truncated", frames[0].errors)

    def test_capture_starting_low_has_no_assumed_start_edge(self):
        self.assertEqual(decode_uart([0] * 80 + [1] * 20, 1_000_000, 100_000), [])

    def test_nondefault_data_width_and_stop_bits(self):
        for width in (5, 7, 9):
            for stops in (1, 1.5, 2):
                with self.subTest(width=width, stops=stops):
                    value = (1 << width) - 1
                    frames = decode_uart(uart_samples([value], data_bits=width, stop_bits=stops), 1_000_000, 100_000, data_bits=width, stop_bits=stops)
                    self.assertEqual(frames[0].value, value)
                    self.assertEqual(frames[0].errors, ())

    def test_invalid_parameters_fail_clearly(self):
        cases = ({"sample_rate_hz": 0}, {"baud": float("nan")}, {"baud": 300_000}, {"channel": -1}, {"data_bits": 4}, {"parity": "mark"}, {"stop_bits": 3}, {"channel": True})
        for options in cases:
            with self.subTest(options=options):
                args = {"samples": [1, 0], "sample_rate_hz": 1_000_000, "baud": 100_000, **options}
                with self.assertRaises(ValueError):
                    decode_uart(**args)
        with self.assertRaises(ValueError):
            decode_uart([1, -1], 1_000_000, 100_000)

    def test_empty_and_idle_capture(self):
        self.assertEqual(decode_uart([], 1_000_000, 100_000), [])
        self.assertEqual(decode_uart([1] * 500, 1_000_000, 100_000), [])


if __name__ == "__main__":
    unittest.main()
