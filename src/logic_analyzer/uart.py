"""Offline, idle-high UART decoding from packed digital samples.

The decoder uses a falling start edge, validates the middle of the start bit,
then samples each bit at its center. Fractional samples-per-bit are preserved
throughout a frame. It requires at least four samples per bit. A capture that
begins low is treated as an in-progress frame until a fresh falling edge occurs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from numbers import Real
from typing import Iterable


@dataclass(frozen=True)
class UARTFrame:
    """One observed frame; sample bounds are [start_sample, end_sample).

    ``value`` is None when the capture cannot establish every data/parity/stop
    bit. Errors are ``truncated``, ``parity``, and/or ``framing``. A complete
    frame with bad parity or a bad stop bit retains its observed data value.
    """

    start_sample: int
    end_sample: int
    value: int | None
    errors: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        if self.value is None:
            return "incomplete"
        if self.value == 10:
            return r"\n"
        if self.value == 13:
            return r"\r"
        if self.value == 9:
            return r"\t"
        return chr(self.value) if 32 <= self.value <= 126 else "."

    def as_dict(self) -> dict:
        return {**asdict(self), "text": self.text}


def decode_uart(
    samples: Iterable[int],
    sample_rate_hz: float,
    baud: float,
    channel: int = 0,
    data_bits: int = 8,
    parity: str = "none",
    stop_bits: float = 1,
) -> list[UARTFrame]:
    """Decode UART; default framing is 8N1, least-significant data bit first.

    Stop bits may be 1, 1.5, or 2. Each complete stop bit is sampled at its
    center; a remaining half stop bit is sampled at its center too. Short
    negative pulses that are high by the start-bit midpoint are ignored.
    Decoding is edge-based and cannot recover a byte whose start edge preceded
    the capture. This is a sample-center decoder, not a noise-filtering UART.
    """
    for name, value in (("sample_rate_hz", sample_rate_hz), ("baud", baud)):
        if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be a positive finite number")
    if isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel < 64:
        raise ValueError("channel must be an integer from 0 to 63")
    if isinstance(data_bits, bool) or not isinstance(data_bits, int) or not 5 <= data_bits <= 9:
        raise ValueError("data_bits must be an integer from 5 to 9")
    if parity not in ("none", "even", "odd"):
        raise ValueError("parity must be 'none', 'even', or 'odd'")
    if isinstance(stop_bits, bool) or stop_bits not in (1, 1.5, 2):
        raise ValueError("stop_bits must be 1, 1.5, or 2")
    period = float(sample_rate_hz) / float(baud)
    if period < 4:
        raise ValueError("UART decoding requires at least four samples per bit")
    packed = list(samples)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in packed):
        raise ValueError("samples must contain nonnegative integers")
    bits = [(value >> channel) & 1 for value in packed]
    count = len(bits)
    frames: list[UARTFrame] = []
    parity_count = int(parity != "none")
    stop_start = 1 + data_bits + parity_count
    frame_bits = stop_start + stop_bits
    stop_centers = [stop_start + 0.5]
    if stop_bits == 1.5:
        stop_centers.append(stop_start + 1.25)
    elif stop_bits == 2:
        stop_centers.append(stop_start + 1.5)
    index = 1
    while index < count:
        if bits[index - 1] != 1 or bits[index] != 0:
            index += 1
            continue
        start = index
        midpoint = math.floor(start + 0.5 * period)
        if midpoint >= count:
            frames.append(UARTFrame(start, count, None, ("truncated",)))
            break
        if bits[midpoint] != 0:
            index += 1
            continue
        sample_positions = [math.floor(start + (1.5 + bit) * period) for bit in range(data_bits)]
        parity_position = math.floor(start + (1.5 + data_bits) * period)
        stop_positions = [math.floor(start + center * period) for center in stop_centers]
        end = math.ceil(start + frame_bits * period)
        if stop_positions[-1] >= count:
            frames.append(UARTFrame(start, count, None, ("truncated",)))
            break
        value = sum(bits[position] << bit for bit, position in enumerate(sample_positions))
        errors: list[str] = []
        if parity != "none":
            expected = value.bit_count() % 2
            if parity == "odd":
                expected ^= 1
            if bits[parity_position] != expected:
                errors.append("parity")
        if any(bits[position] != 1 for position in stop_positions):
            errors.append("framing")
        frames.append(UARTFrame(start, min(count, end), value, tuple(errors)))
        # Begin looking immediately after the final stop sample. Rounding a
        # full frame length upward can otherwise skip a back-to-back start edge.
        index = stop_positions[-1] + 1
    return frames
