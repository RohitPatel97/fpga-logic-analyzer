# RTL simulation and register contract

The synthesizable logic is in `rtl/logic_analyzer.sv`,
`rtl/capture_controller.sv`, and `rtl/capture_buffer.sv`. It captures eight
synchronous digital inputs into a 1024-byte circular buffer. The asynchronous
read port is portable to registers or distributed RAM; a board implementation
may substitute a synchronous block-RAM read adapter. Physical asynchronous
inputs require appropriate input synchronization outside this core.

From the project directory, compile and run the automatically checked testbench:

```sh
iverilog -g2012 -s tb_logic_analyzer -o build/testbench.vvp rtl/capture_buffer.sv rtl/capture_controller.sv rtl/logic_analyzer.sv sim/tb_logic_analyzer.sv
vvp build/testbench.vvp
```

Create `build/` first. Every capture checks exact bytes in chronological order,
count, trigger index, status, and clock count. A mismatch exits with `$fatal`.
The cases cover immediate/level/edge/software triggers, divider timing,
pretrigger history beyond RAM wrap, full capacity, early and last-slot triggers,
aborts, immutable active configuration, invalid configuration, rearm, and reset.

## Registers

The bus uses byte offsets and 32-bit accesses. Unknown or unaligned addresses
read zero and ignore writes. Reads are combinational. Configuration writes while
armed are ignored; `READ_INDEX` remains writable. The host holds `sample_tick=0`
for bus accesses. Writes additionally suppress sampling in hardware.

| Offset | Name | Access | Meaning / reset |
| --- | --- | --- | --- |
| `0x00` | CTRL | W | Bit 0 arm, bit 1 abort, bit 2 software trigger; reads zero |
| `0x04` | STATUS | R | Bit 0 armed, bit 1 triggered, bit 2 done, bit 3 config error |
| `0x08` | DIV | RW | Input ticks per stored sample, at least 1; reset 1 |
| `0x0c` | LENGTH | RW | Total capture length, 1..1024; reset 512 |
| `0x10` | PRE | RW | Maximum samples before trigger, 0..LENGTH-1; reset 32 |
| `0x14` | MASK | RW | Trigger mask, low eight bits; reset 1 |
| `0x18` | VALUE | RW | Masked level value, low eight bits; reset 0 |
| `0x1c` | MODE | RW | 0 immediate, 1 masked level, 2 rising, 3 falling; reset 3 |
| `0x20` | COUNT | R | Frozen valid sample count; zero until done |
| `0x24` | TRIGGER_INDEX | R | Trigger position in chronological data; `0xffffffff` if none |
| `0x28` | READ_INDEX | RW | Zero-based chronological index; reset 0 |
| `0x2c` | DATA | R | Selected byte, zero-extended; no index increment |
| `0x30` | CAPACITY | R | 1024 |
| `0x34` | CHANNELS | R | 8 |
| `0x38` | TICKS | R | Raw input ticks accepted while armed, modulo 2^32 |

Arm clears the capture flags, count, divider phase, trigger index, and tick count.
The read index remains unchanged. Invalid configuration leaves the core idle
with only `config_error` set. Valid rearm clears the error. Repeated arm while
armed is ignored. For multiple control bits, abort has priority, then arm, then
software trigger. Abort while idle and software trigger while idle are ignored.
Completion and captured data remain stable until the next arm or reset.

Only `sample_tick=1` advances the divider. The first sample is stored on the
DIV-th input tick after arm, then every DIV ticks. Thus with a base sample clock
of `base_hz`, stored samples have rate `base_hz / DIV`. Register transactions
never contribute to `TICKS`.

Before trigger, the latest PRE samples are retained. The trigger sample follows
the actual retained history, and posttrigger collection continues until LENGTH
samples exist. An early trigger therefore has a smaller trigger index and a
longer posttrigger portion. A trigger may occupy the final slot and finish the
capture on that same tick. DATA is zero before completion or for an index
outside COUNT, so stale uninitialized RAM is never exposed.

Masked level matches `(sample & MASK) == (VALUE & MASK)`; a zero mask always
matches. Rising/falling triggers match a transition on **any** selected bit
between successive stored samples. The first sample establishes the edge
baseline and cannot produce an edge trigger. A zero edge mask never matches.
Software trigger latches until the next stored sample, respecting DIV.

Abort before trigger freezes only retained prehistory and leaves
TRIGGER_INDEX=`0xffffffff`; an immediate abort can produce zero samples. Abort
after trigger freezes all collected samples and preserves its trigger index.

## Interactive RTL bridge

```sh
iverilog -g2012 -s bridge_tb -o build/bridge.vvp rtl/capture_buffer.sv rtl/capture_controller.sv rtl/logic_analyzer.sv sim/bridge_tb.sv
vvp build/bridge.vvp
```

The bridge automatically resets the DUT and emits `READY`. Send one ASCII
command per line, using **decimal** arguments. Every reply is flushed.

| Command | Reply | Action |
| --- | --- | --- |
| `W offset data` | `OK` | One register write, no sampling tick |
| `R offset` | `VALUE number` | Unsigned 32-bit register read, no sampling tick |
| `T probes count` | `OK` | Hold an 8-bit probe value for count raw sampling ticks |
| `Z` | `OK` | Reset the DUT, including configuration registers |
| `X` | `BYE` | Exit |

Malformed commands produce `ERROR command`, an offset above 65535 produces
`ERROR address`, and probes above 255 produces `ERROR probes`. EOF exits.
The protocol intentionally emits no waveform dump or diagnostic chatter on
standard output. The Renode peripheral forwards MMIO requests and timed probe
transitions to this bridge, so the capture behavior comes from the real RTL.
