"""Seeded RTL regression against a chronological Python reference model.

The oracle uses a bounded history deque, not RTL addresses or internal signals.
Only the public stdin/MMIO bridge is exercised. Python's standard library and
Icarus Verilog are sufficient; no viewer dependencies are needed.
"""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass, field
import random
import subprocess

from toolchain import ROOT, build_design, find_tool, tool_environment


@dataclass
class Oracle:
    registers: dict[int, int] = field(default_factory=lambda: {
        8: 1, 12: 512, 16: 32, 20: 1, 24: 0, 28: 3, 40: 0,
    })
    armed: bool = False
    triggered: bool = False
    done: bool = False
    error: bool = False
    ticks: int = 0
    phase: int = 0
    previous: int | None = None
    trigger_index: int = 0xFFFFFFFF
    pending: bool = False
    history: deque[int] = field(default_factory=deque)
    data: list[int] = field(default_factory=list)

    def write(self, address: int, value: int) -> None:
        if address == 40:
            self.registers[address] = value
        elif address in self.registers and not self.armed:
            self.registers[address] = value & 255 if address in (20, 24) else value
        elif address == 0:
            if value & 2:
                if self.armed:
                    if not self.triggered:
                        self.data = list(self.history)
                    self.armed, self.done, self.pending = False, True, False
            elif value & 1:
                if not self.armed:
                    divider, length, pre, mode = (
                        self.registers[a] for a in (8, 12, 16, 28)
                    )
                    valid = divider > 0 and 1 <= length <= 1024 and pre < length and mode <= 3
                    self.armed, self.error = valid, not valid
                    self.triggered = self.done = self.pending = False
                    self.ticks = self.phase = 0
                    self.previous = None
                    self.trigger_index = 0xFFFFFFFF
                    self.history = deque(maxlen=pre)
                    self.data = []
            elif value & 4 and self.armed:
                self.pending = True

    def tick(self, sample: int) -> None:
        if not self.armed:
            return
        self.ticks = (self.ticks + 1) & 0xFFFFFFFF
        self.phase += 1
        if self.phase != self.registers[8]:
            return
        self.phase = 0
        mode, mask, value = (self.registers[a] for a in (28, 20, 24))
        match = mode == 0 or (mode == 1 and sample & mask == value & mask)
        if self.previous is not None:
            if mode == 2:
                match = bool((~self.previous) & sample & mask)
            elif mode == 3:
                match = bool(self.previous & (~sample) & mask)
        self.previous = sample
        if self.triggered:
            self.data.append(sample)
        elif match or self.pending:
            self.trigger_index = len(self.history)
            self.data = [*self.history, sample]
            self.triggered, self.pending = True, False
        else:
            self.history.append(sample)
        if self.triggered and len(self.data) == self.registers[12]:
            self.armed, self.done = False, True

    def read(self, address: int) -> int:
        if address in self.registers:
            return self.registers[address]
        if address == 4:
            return int(self.armed) | int(self.triggered) << 1 | int(self.done) << 2 | int(self.error) << 3
        if address == 32:
            return len(self.data) if self.done else 0
        if address == 36:
            return self.trigger_index
        if address == 44:
            index = self.registers[40]
            return self.data[index] if self.done and index < len(self.data) else 0
        return {48: 1024, 52: 8, 56: self.ticks}.get(address, 0)


class Regression:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.oracle = Oracle()
        self.commands: list[str] = []
        self.expected: list[str] = ["READY"]
        self.context: list[str] = ["bridge startup"]
        self.case = "startup"
        self.reads = 0

    def command(self, command: str, reply: str) -> None:
        self.commands.append(command)
        self.expected.append(reply)
        self.context.append(f"{self.case}: {command}")

    def write(self, address: int, value: int) -> None:
        self.oracle.write(address, value)
        self.command(f"W {address} {value}", "OK")

    def read(self, address: int) -> None:
        self.command(f"R {address}", f"VALUE {self.oracle.read(address)}")
        self.reads += 1

    def ticks(self, sample: int, count: int = 1) -> None:
        for _ in range(count):
            self.oracle.tick(sample)
        self.command(f"T {sample} {count}", "OK")

    def sample(self, value: int, divider: int) -> None:
        # Change unsampled inputs too: glitches must not affect edge triggers.
        if divider > 1:
            self.ticks(self.rng.randrange(256), divider - 1)
        self.ticks(value)

    def state(self) -> None:
        for address in (4, 32, 36, 56):
            self.read(address)

    def run_case(self, number: int) -> None:
        rng = self.rng
        length = rng.choice([1, 2, 3, 4, 7, 16, 31, 64, 128, 256, 1024])
        pre = rng.choice([0, length - 1, rng.randrange(length)])
        divider = rng.randrange(1, 8)
        mode = number % 4
        mask = rng.randrange(1, 256)
        value = rng.randrange(256)
        operation = number % 7
        soft = operation == 4
        if soft:
            mode, mask = 2, 0
        before = rng.choice([0, 1, pre, max(0, pre - 1), pre + 1, rng.randrange(25)])
        if number % 9 == 0:
            before = 1024 + rng.randrange(65)
        if mode in (2, 3):
            before = max(1, before)
        self.case = f"case {number}, length={length}, pre={pre}, div={divider}, mode={mode}, op={operation}"
        for address, register_value in ((8, divider), (12, length), (16, pre),
                                        (20, mask), (24, value), (28, mode)):
            self.write(address, register_value)
        self.write(0, 1)
        self.state()
        self.read(44)  # Previously captured bytes must remain hidden after arm.
        # Register traffic and repeated ARM must neither reset phase nor capture.
        self.write(0, 1)
        self.write(12, 0)
        self.write(8, 0)
        self.read(12)
        self.read(8)
        if operation == 0:
            self.write(0, 2)  # Abort before the first input tick.
        else:
            selected_bit = (mask & -mask) if mask else 1
            for n in range(before):
                unselected = rng.randrange(256) & (~mask & 255)
                if mode == 1:
                    held = ((value ^ selected_bit) & mask) | unselected
                elif mode == 3:
                    held = mask | unselected
                else:
                    held = unselected
                self.sample(held, divider)
                if n == before // 2:
                    self.state()
            if operation == 1:
                self.write(0, 2)  # Abort with wrapped or short prehistory.
            else:
                if soft:
                    self.write(0, 4)
                    self.state()
                if mode == 1:
                    trigger_sample = (value & mask) | (rng.randrange(256) & (~mask & 255))
                elif mode == 3:
                    trigger_sample = mask & ~selected_bit
                else:
                    trigger_sample = selected_bit
                self.sample(trigger_sample, divider)
                self.state()
                post = rng.randrange(length) if operation == 2 else length
                for n in range(post):
                    self.sample(rng.randrange(256), divider)
                    if n == 0:
                        self.state()
                if operation == 2:
                    self.write(0, 2)  # Partial posttrigger abort, possibly already done.
        if self.oracle.armed:
            raise AssertionError(f"generator left capture active: {self.case}")
        self.state()
        # Frozen samples are stable despite subsequent clocks and idle abort.
        self.ticks(rng.randrange(256), rng.randrange(1, 31))
        self.write(0, 2)
        self.state()
        order = list(range(len(self.oracle.data)))
        rng.shuffle(order)
        for index in order:
            self.write(40, index)
            self.read(44)
        for index in (len(self.oracle.data), 0xFFFFFFFF):
            self.write(40, index)
            self.read(44)
        self.read(56)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=0x5EED1234)
    parser.add_argument("--cases", type=int, default=84)
    args = parser.parse_args()
    if not 1 <= args.cases <= 1000:
        parser.error("--cases must be between 1 and 1000")
    compiler, runtime = find_tool("iverilog"), find_tool("vvp")
    design = build_design(compiler)
    regression = Regression(args.seed)
    for number in range(args.cases):
        regression.run_case(number)
    regression.command("X", "BYE")
    result = subprocess.run(
        [str(runtime), str(design)],
        input="\n".join(regression.commands) + "\n",
        capture_output=True, text=True, timeout=120, check=True,
        cwd=ROOT, env=tool_environment(compiler),
    )
    actual = result.stdout.splitlines()
    for number, (wanted, observed) in enumerate(zip(regression.expected, actual)):
        if wanted != observed:
            raise AssertionError(
                f"seed={args.seed}, {regression.context[number]}: "
                f"expected {wanted!r}, got {observed!r}; stderr={result.stderr!r}"
            )
    if len(actual) != len(regression.expected):
        raise AssertionError(f"reply count {len(actual)} != {len(regression.expected)}: {result.stderr}")
    print(f"PASS: {args.cases} seeded RTL scenarios, {regression.reads} register/sample checks, seed={args.seed}")


if __name__ == "__main__":
    main()
