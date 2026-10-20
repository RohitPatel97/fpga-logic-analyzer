"""Tkinter waveform viewer and dependency-free, interactive HTML export.

Public entry points accept the version-1 capture dictionary used by the logger.
Tkinter is imported only by ``launch``; HTML export works on headless machines.
"""

from __future__ import annotations

import html
import json
import math
from numbers import Real
from pathlib import Path
from typing import Iterator

from .uart import UARTFrame, decode_uart


COLORS = ("#46d6b2", "#67b7ff", "#d6a0ff", "#f2c66d", "#fa8d9a", "#82d581", "#80dce2", "#c1b0ff")


def _validate_capture(capture: dict) -> None:
    if not isinstance(capture, dict) or capture.get("version") != 1:
        raise ValueError("viewer requires a version-1 capture dictionary")
    rate = capture.get("sample_rate_hz")
    if isinstance(rate, bool) or not isinstance(rate, Real) or not math.isfinite(rate) or rate <= 0:
        raise ValueError("sample_rate_hz must be a positive finite number")
    channels = capture.get("channels")
    if isinstance(channels, bool) or not isinstance(channels, int) or not 1 <= channels <= 64:
        raise ValueError("channels must be an integer from 1 to 64")
    samples = capture.get("samples")
    if not isinstance(samples, list) or any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v < (1 << channels) for v in samples):
        raise ValueError("samples must be a list of integers fitting the channel width")
    trigger = capture.get("trigger_index")
    if trigger is not None and (isinstance(trigger, bool) or not isinstance(trigger, int) or not 0 <= trigger < len(samples)):
        raise ValueError("trigger_index must identify a sample or be null")
    if not isinstance(capture.get("metadata", {}), dict):
        raise ValueError("metadata must be an object")


def waveform_runs(samples: list[int], channel: int, start: int = 0, end: int | None = None) -> Iterator[tuple[int, int, int]]:
    """Yield constant-level runs as (first, exclusive_end, level)."""
    start = max(0, start)
    end = len(samples) if end is None else min(len(samples), end)
    if start >= end:
        return
    first = start
    level = (samples[start] >> channel) & 1
    for index in range(start + 1, end):
        next_level = (samples[index] >> channel) & 1
        if next_level != level:
            yield first, index, level
            first, level = index, next_level
    yield first, end, level


def _pixel_envelopes(samples: list[int], start: int, end: int, columns: int) -> list[tuple[int, int, int, int]]:
    """Aggregate all channels per pixel while retaining even one-sample pulses.

    Each result is (start, end, bitwise_and, bitwise_or). A channel with unequal
    AND and OR bits crossed both levels inside that display column. This bounds
    rendering work without falsely presenting subsample timing when zoomed out.
    """
    if start >= end:
        return []
    stride = max(1, math.ceil((end - start) / max(1, columns)))
    envelope = []
    for begin in range(start, end, stride):
        stop = min(end, begin + stride)
        low = high = samples[begin]
        for index in range(begin + 1, stop):
            low &= samples[index]
            high |= samples[index]
        envelope.append((begin, stop, low, high))
    return envelope


def _channel_names(capture: dict) -> list[str]:
    names = capture.get("metadata", {}).get("channel_names", [])
    if not isinstance(names, list):
        names = []
    return [str(names[i]) if i < len(names) else f"D{i}" for i in range(capture["channels"])]


def _time_label(seconds: float) -> str:
    if abs(seconds) >= 1:
        return f"{seconds:.6g} s"
    if abs(seconds) >= 0.001:
        return f"{seconds * 1e3:.6g} ms"
    return f"{seconds * 1e6:.6g} µs"


def _decode(capture: dict, baud: float | None, channel: int) -> list[UARTFrame]:
    if not 0 <= channel < capture["channels"]:
        raise ValueError("UART channel is outside the captured channels")
    return [] if baud is None else decode_uart(capture["samples"], capture["sample_rate_hz"], baud, channel)


def export_html(capture: dict, path: str | Path, baud: float | None = 100000, channel: int = 0) -> Path:
    """Write a self-contained interactive waveform report; pass baud=None for digital only."""
    _validate_capture(capture)
    frames = _decode(capture, baud, channel)
    # Decimal strings preserve all 64 bits when JavaScript parses the report.
    # JSON numbers alone lose low bits above the browser's 53-bit integer range.
    payload = {"capture": capture, "sample_words": [str(value) for value in capture["samples"]], "names": _channel_names(capture), "frames": [frame.as_dict() for frame in frames], "baud": baud, "channel": channel}
    encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    rows = []
    for frame in frames:
        rows.append(f'<tr data-start="{frame.start_sample}"><td>{frame.start_sample}</td><td>{html.escape(_time_label(frame.start_sample / capture["sample_rate_hz"]))}</td><td>{"—" if frame.value is None else f"0x{frame.value:02X}"}</td><td>{html.escape(frame.text)}</td><td class="{ "error" if frame.errors else "good" }">{html.escape(", ".join(frame.errors) or "OK")}</td></tr>')
    source = html.escape(str(capture.get("source", "capture")))
    body = _HTML_TEMPLATE.replace("__CAPTURE_SOURCE__", source).replace("__CAPTURE_PAYLOAD__", encoded).replace("__FRAME_ROWS__", "".join(rows) or '<tr><td colspan="5">No UART frames decoded.</td></tr>')
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(body, encoding="utf-8")
    return output


def launch(capture: dict, *, baud: float | None = None, channel: int = 0) -> None:
    """Open the desktop viewer. Click sets A; Shift-click sets B; wheel pans."""
    _validate_capture(capture)
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError as error:
        raise RuntimeError("Tkinter is unavailable. Use the HTML export command instead.") from error
    try:
        root = tk.Tk()
    except tk.TclError as error:
        raise RuntimeError("Cannot open a desktop display. Use the HTML export command instead.") from error
    _WaveformWindow(root, capture, baud, channel, tk, ttk, filedialog, messagebox)
    root.mainloop()


class _WaveformWindow:
    def __init__(self, root, capture, baud, channel, tk, ttk, filedialog, messagebox):
        self.root, self.capture = root, capture
        self.tk, self.ttk, self.filedialog, self.messagebox = tk, ttk, filedialog, messagebox
        self.samples, self.rate = capture["samples"], capture["sample_rate_hz"]
        self.count = len(self.samples)
        self.names = _channel_names(capture)
        self.frames: list[UARTFrame] = []
        self.cursors = [None, None]
        self.left, self.row, self.top = 108, 52, 48
        self.scale = 2.0
        self.offset = 0.0
        self.active_baud = baud
        self.active_channel = channel
        root.title("FPGA Logic Analyzer · Waveform viewer")
        root.geometry("1200x780")
        root.minsize(760, 500)
        root.configure(bg="#0b1220")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#0b1220")
        style.configure("TLabel", background="#0b1220", foreground="#cdd9ea", font=("Segoe UI", 10))
        style.configure("TButton", padding=(10, 5), font=("Segoe UI", 10))
        style.configure("Treeview", background="#111d30", foreground="#d9e4f3", fieldbackground="#111d30", rowheight=25, borderwidth=0)
        style.configure("Treeview.Heading", background="#20314b", foreground="#e4edfb", padding=6)
        title = ttk.Frame(root, padding=(18, 14, 18, 6))
        title.pack(fill="x")
        ttk.Label(title, text="LOGIC / CAPTURE", font=("Segoe UI", 19, "bold"), foreground="#f1f6ff").pack(side="left")
        ttk.Label(title, text=str(capture.get("source", "capture")), foreground="#68d6b8").pack(side="right")
        summary = f'{self.count:,} samples   ·   {capture["channels"]} channels   ·   {self.rate:,.0f} samples/s   ·   {_time_label(self.count / self.rate)}'
        trigger = capture.get("trigger_index")
        summary += "   ·   Trigger " + (str(trigger) if trigger is not None else "none")
        ttk.Label(root, text=summary, padding=(18, 0, 18, 12)).pack(anchor="w")
        toolbar = ttk.Frame(root, padding=(18, 0, 18, 10))
        toolbar.pack(fill="x")
        for text, action in (("−", lambda: self.zoom(0.5)), ("+", lambda: self.zoom(2)), ("Fit", self.fit), ("Trigger", self.go_trigger)):
            ttk.Button(toolbar, text=text, command=action, width=7).pack(side="left", padx=(0, 5))
        ttk.Label(toolbar, text="UART  D").pack(side="left", padx=(12, 0))
        self.channel = tk.StringVar(value=str(channel))
        ttk.Combobox(toolbar, textvariable=self.channel, values=list(range(capture["channels"])), width=3, state="readonly").pack(side="left")
        self.baud = tk.StringVar(value=str(baud or 100000))
        ttk.Entry(toolbar, textvariable=self.baud, width=10).pack(side="left", padx=5)
        ttk.Button(toolbar, text="Decode 8N1", command=self.decode).pack(side="left")
        ttk.Button(toolbar, text="Export HTML", command=self.export).pack(side="right")
        plot_holder = ttk.Frame(root, padding=(18, 0, 18, 0))
        plot_holder.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(plot_holder, bg="#101b2d", highlightthickness=0, height=300)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        plot_holder.rowconfigure(0, weight=1)
        plot_holder.columnconfigure(0, weight=1)
        self.vertical = ttk.Scrollbar(plot_holder, orient="vertical", command=self.canvas.yview)
        self.vertical.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self.vertical.set)
        self.scroll = ttk.Scrollbar(plot_holder, orient="horizontal", command=self.pan)
        self.scroll.grid(row=1, column=0, sticky="ew")
        self.canvas.bind("<Configure>", lambda event: self.draw())
        self.canvas.bind("<Button-1>", self.set_cursor)
        self.canvas.bind("<MouseWheel>", self.wheel)
        self.canvas.bind("<Button-4>", lambda event: self.pan("scroll", -3, "units"))
        self.canvas.bind("<Button-5>", lambda event: self.pan("scroll", 3, "units"))
        self.status = tk.StringVar(value="Click waveform: cursor A   ·   Shift-click: cursor B   ·   Wheel: pan   ·   Ctrl+wheel: zoom")
        ttk.Label(root, textvariable=self.status, padding=(18, 8, 18, 8)).pack(anchor="w")
        table_frame = ttk.Frame(root, padding=(18, 0, 18, 18))
        table_frame.pack(fill="x")
        ttk.Label(table_frame, text="UART DECODE", foreground="#8c9eb7", padding=(0, 0, 0, 6)).pack(anchor="w")
        self.table = ttk.Treeview(table_frame, columns=("sample", "time", "hex", "text", "status"), show="headings", height=5)
        for column, title, width in (("sample", "Start sample", 120), ("time", "Time", 160), ("hex", "Value", 100), ("text", "ASCII", 120), ("status", "Status", 350)):
            self.table.heading(column, text=title)
            self.table.column(column, width=width, anchor="w")
        self.table.tag_configure("error", foreground="#ff889b")
        self.table.pack(side="left", fill="x", expand=True)
        table_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        table_scroll.pack(side="right", fill="y")
        self.table.configure(yscrollcommand=table_scroll.set)
        self.table.bind("<<TreeviewSelect>>", self.select_frame)
        root.after_idle(self.fit)
        if baud is not None:
            root.after_idle(self.decode)

    def width(self):
        return max(1, self.canvas.winfo_width() - self.left - 20)

    def clamp(self):
        visible = self.width() / self.scale
        self.offset = max(0.0, min(self.offset, max(0, self.count - visible)))
        denominator = max(1, self.count)
        self.scroll.set(self.offset / denominator, min(1, (self.offset + visible) / denominator))

    def fit(self):
        self.scale = self.width() / max(1, self.count)
        self.offset = 0
        self.draw()

    def zoom(self, factor):
        midpoint = self.offset + self.width() / self.scale / 2
        minimum = self.width() / max(1, self.count)
        self.scale = min(max(32.0, minimum), max(minimum, self.scale * factor))
        self.offset = midpoint - self.width() / self.scale / 2
        self.draw()

    def go_trigger(self):
        trigger = self.capture.get("trigger_index")
        if trigger is not None:
            self.offset = trigger - self.width() / self.scale / 2
            self.draw()

    def pan(self, *args):
        if args[0] == "moveto":
            self.offset = float(args[1]) * max(1, self.count)
        else:
            self.offset += int(args[1]) * self.width() / self.scale * (0.9 if args[2] == "pages" else 0.08)
        self.draw()

    def wheel(self, event):
        if event.state & 0x4:
            self.zoom(1.4 if event.delta > 0 else 1 / 1.4)
        else:
            self.pan("scroll", -3 if event.delta > 0 else 3, "units")
        return "break"

    def set_cursor(self, event):
        if self.count == 0 or event.x < self.left:
            return
        sample = min(self.count - 1, max(0, round(self.offset + (event.x - self.left) / self.scale)))
        self.cursors[1 if event.state & 0x1 else 0] = sample
        values = []
        trigger = self.capture.get("trigger_index") or 0
        for name, index in zip(("A", "B"), self.cursors):
            if index is not None:
                origin = "trigger" if self.capture.get("trigger_index") is not None else "start"
                values.append(f"{name}: sample {index:,}  ({_time_label((index - trigger) / self.rate)} from {origin})  value 0x{self.samples[index]:X}")
        if all(index is not None for index in self.cursors):
            delta = self.cursors[1] - self.cursors[0]
            values.append(f"Δ {delta:,} samples / {_time_label(delta / self.rate)}")
        self.status.set("   ·   ".join(values))
        self.draw()

    def decode(self):
        try:
            rate, channel = float(self.baud.get()), int(self.channel.get())
            frames = _decode(self.capture, rate, channel)
        except (TypeError, ValueError) as error:
            self.messagebox.showerror("UART settings", str(error))
            return
        self.frames, self.active_baud, self.active_channel = frames, rate, channel
        self.table.delete(*self.table.get_children())
        for index, frame in enumerate(frames):
            self.table.insert("", "end", iid=str(index), values=(frame.start_sample, _time_label(frame.start_sample / self.rate), "—" if frame.value is None else f"0x{frame.value:02X}", frame.text, ", ".join(frame.errors) or "OK"), tags=("error",) if frame.errors else ())
        self.status.set(f"Decoded {len(frames)} frame(s) on D{channel} at {rate:g} baud · {sum(bool(frame.errors) for frame in frames)} with errors")
        self.draw()

    def select_frame(self, event):
        selected = self.table.selection()
        if selected:
            frame = self.frames[int(selected[0])]
            self.offset = frame.start_sample - self.width() / self.scale / 4
            self.draw()

    def export(self):
        path = self.filedialog.asksaveasfilename(title="Export waveform report", defaultextension=".html", filetypes=[("HTML report", "*.html")])
        if path:
            try:
                export_html(self.capture, path, self.active_baud, self.active_channel)
                self.status.set(f"Exported {path}")
            except (OSError, ValueError) as error:
                self.messagebox.showerror("Export failed", str(error))

    def draw(self):
        self.clamp()
        canvas = self.canvas
        canvas.delete("all")
        width = canvas.winfo_width()
        bottom = self.top + self.row * self.capture["channels"] + 42
        canvas.configure(scrollregion=(0, 0, width, bottom))
        x = lambda sample: self.left + (sample - self.offset) * self.scale
        span = self.width() / self.scale
        raw_step = max(1, span / 8)
        magnitude = 10 ** math.floor(math.log10(raw_step))
        step = next((multiple * magnitude for multiple in (1, 2, 5, 10) if multiple * magnitude >= raw_step), magnitude * 10)
        grid = math.ceil(self.offset / step) * step
        while grid < min(self.count + 1, self.offset + span):
            position = x(grid)
            canvas.create_line(position, self.top - 10, position, bottom, fill="#26354c", dash=(2, 4))
            canvas.create_text(position + 3, 16, text=_time_label(grid / self.rate), fill="#8fa4c0", anchor="w", font=("Consolas", 9))
            canvas.create_text(position + 3, 32, text=f"#{int(grid):,}", fill="#576d8c", anchor="w", font=("Consolas", 8))
            grid += step
        first, last = max(0, int(self.offset)), min(self.count, math.ceil(self.offset + span))
        envelopes = _pixel_envelopes(self.samples, first, last, self.width()) if self.scale < 1 else None
        for channel in range(self.capture["channels"]):
            top = self.top + channel * self.row
            color = COLORS[channel % len(COLORS)]
            canvas.create_line(self.left, top + 32, width, top + 32, fill="#1b2c42")
            if envelopes is not None:
                for begin, end, low, high in envelopes:
                    left, right = max(self.left, x(begin)), min(width, x(end))
                    low, high = (low >> channel) & 1, (high >> channel) & 1
                    if low != high:
                        canvas.create_line((left + right) / 2, top + 5, (left + right) / 2, top + 28, fill=color, width=1)
                    else:
                        y = top + (5 if low else 28)
                        canvas.create_line(left, y, right, y, fill=color, width=2)
                continue
            previous_y = None
            for begin, end, level in waveform_runs(self.samples, channel, first, last):
                y = top + (5 if level else 28)
                start_x, end_x = max(self.left, x(begin)), min(width, x(end))
                if previous_y is not None:
                    canvas.create_line(start_x, previous_y, start_x, y, fill=color, width=2)
                canvas.create_line(start_x, y, end_x, y, fill=color, width=2)
                previous_y = y
        for frame in self.frames:
            if frame.end_sample < first or frame.start_sample > last:
                continue
            left, right = max(self.left, x(frame.start_sample)), min(width, x(frame.end_sample))
            y = self.top + self.active_channel * self.row + 34
            color = "#a3455a" if frame.errors else "#2c5970"
            canvas.create_rectangle(left, y, right, y + 16, fill=color, outline="")
            if right - left > 30:
                label = "?" if frame.value is None else f"{frame.value:02X} {frame.text}"
                canvas.create_text((left + right) / 2, y + 8, text=label, fill="#ecf5ff", font=("Consolas", 8))
        trigger = self.capture.get("trigger_index")
        markers = [(trigger, "T", "#f1c45f"), (self.cursors[0], "A", "#f785b2"), (self.cursors[1], "B", "#82cfff")]
        for sample, label, color in markers:
            if sample is not None and self.left <= x(sample) <= width:
                canvas.create_line(x(sample), self.top - 8, x(sample), bottom, fill=color, dash=(5, 3), width=2)
                canvas.create_text(x(sample) + 4, self.top - 7, text=label, fill=color, anchor="sw", font=("Segoe UI", 10, "bold"))
        canvas.create_rectangle(0, 0, self.left - 4, bottom, fill="#101b2d", outline="")
        canvas.create_text(14, 23, text="SIGNALS", fill="#7188a7", anchor="w", font=("Segoe UI", 9, "bold"))
        for channel, name in enumerate(self.names):
            canvas.create_text(14, self.top + channel * self.row + 17, text=name[:13], fill=COLORS[channel % len(COLORS)], anchor="w", font=("Segoe UI", 10, "bold"))
        if not self.count:
            canvas.create_text(width / 2, 100, text="Capture contains no samples", fill="#9caec5", font=("Segoe UI", 14))


_HTML_TEMPLATE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FPGA Logic Analyzer · Capture report</title>
<style>
:root{color-scheme:dark;font-family:Segoe UI,system-ui,sans-serif;color:#d8e5f5;background:#0b1220}*{box-sizing:border-box}body{margin:0 auto;padding:32px;max-width:1600px}header{display:flex;justify-content:space-between;align-items:center;gap:24px}h1{font-size:25px;letter-spacing:.05em;margin:0 0 8px}p{color:#92a6c1;font-size:14px;margin:0 0 22px}.source{color:#60d6b5;font:13px ui-monospace,monospace;max-width:40%;overflow-wrap:anywhere}.toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:12px}button{background:#1b2d46;color:#dceafd;border:1px solid #30465f;padding:8px 14px;border-radius:6px;cursor:pointer}button:hover{background:#294263}.hint{color:#8297b3;font-size:12px;margin-left:10px}.panel{border:1px solid #25374f;background:#101b2d;border-radius:8px;overflow:hidden}canvas{display:block;width:100%;touch-action:none}input[type=range]{width:100%;accent-color:#50cdaa}#cursors{font:12px ui-monospace,monospace;color:#b3c6df;min-height:35px;padding:12px 0}h2{font-size:12px;letter-spacing:.12em;color:#8ca4c3;margin:25px 0 12px}table{border-collapse:collapse;width:100%;font:13px ui-monospace,monospace}th{text-align:left;background:#192a42;color:#9fb7d7;padding:12px}td{padding:10px 12px;border-top:1px solid #23344b}tbody tr{cursor:pointer}tbody tr:hover{background:#1c304a}.good{color:#61d4ae}.error{color:#ff8ca5}footer{color:#7188a7;font-size:12px;margin-top:22px}@media(max-width:700px){body{padding:14px}header{display:block}.source{max-width:100%;margin-bottom:18px}.hint{display:none}}
</style></head><body>
<header><div><h1>LOGIC / CAPTURE</h1><p id="summary"></p></div><div class="source">__CAPTURE_SOURCE__</div></header>
<div class="toolbar"><button id="out">− Zoom</button><button id="in">+ Zoom</button><button id="fit">Fit capture</button><button id="trigger">Go to trigger</button><span class="hint">Click: cursor A · Shift-click: cursor B · Wheel: pan · Ctrl+wheel: zoom</span></div>
<div class="panel"><canvas id="wave" aria-label="Digital waveform capture"></canvas></div>
<input id="pan" type="range" min="0" max="10000" value="0" aria-label="Pan through samples"><div id="cursors">Place cursors to measure time and inspect sample values.</div>
<h2 id="decode-title">UART DECODE</h2><div class="panel"><table><thead><tr><th>Start sample</th><th>Time</th><th>Value</th><th>ASCII</th><th>Status</th></tr></thead><tbody>__FRAME_ROWS__</tbody></table></div>
<footer>Standalone capture report · All data is embedded locally. UART is idle-high, 8N1. Incomplete frames never produce a byte value.</footer>
<script id="capture-data" type="application/json">__CAPTURE_PAYLOAD__</script>
<script>
"use strict";
const payload=JSON.parse(document.getElementById('capture-data').textContent), cap=payload.capture, samples=payload.sample_words.map(BigInt), n=samples.length;
const canvas=document.getElementById('wave'), ctx=canvas.getContext('2d'), pan=document.getElementById('pan');
const colors=['#46d6b2','#67b7ff','#d6a0ff','#f2c66d','#fa8d9a','#82d581','#80dce2','#c1b0ff'];
let width=1000,height=70+cap.channels*58,left=110,offset=0,visible=Math.max(1,n),cursors=[null,null];
const time=s=>Math.abs(s)>=1?s.toPrecision(5)+' s':Math.abs(s)>=.001?(s*1e3).toPrecision(5)+' ms':(s*1e6).toPrecision(5)+' µs';
document.getElementById('summary').textContent=n.toLocaleString()+' samples · '+cap.channels+' channels · '+cap.sample_rate_hz.toLocaleString()+' samples/s · '+time(n/cap.sample_rate_hz)+' · Trigger '+(cap.trigger_index??'none');
document.getElementById('decode-title').textContent=payload.baud===null?'UART DECODE · disabled':'UART DECODE · D'+payload.channel+' · '+payload.baud.toLocaleString()+' baud · 8N1';
function resize(){width=canvas.parentElement.clientWidth;const dpr=window.devicePixelRatio||1;canvas.width=width*dpr;canvas.height=height*dpr;canvas.style.height=height+'px';ctx.setTransform(dpr,0,0,dpr,0,0);draw();}
function draw(){
 offset=Math.max(0,Math.min(offset,Math.max(0,n-visible)));pan.value=n>visible?offset/(n-visible)*10000:0;pan.disabled=visible>=n;
 const plot=Math.max(1,width-left-15),scale=plot/visible,x=i=>left+(i-offset)*scale,first=Math.max(0,Math.floor(offset)),end=Math.min(n,Math.ceil(offset+visible));
 ctx.fillStyle='#101b2d';ctx.fillRect(0,0,width,height);ctx.font='11px ui-monospace,monospace';
 const raw=Math.max(1,visible/8),mag=10**Math.floor(Math.log10(raw)),step=[1,2,5,10].map(v=>v*mag).find(v=>v>=raw);
 for(let i=Math.ceil(offset/step)*step;i<=Math.min(n,offset+visible);i+=step){const px=x(i);ctx.strokeStyle='#25364d';ctx.setLineDash([2,4]);ctx.beginPath();ctx.moveTo(px,45);ctx.lineTo(px,height);ctx.stroke();ctx.setLineDash([]);ctx.fillStyle='#8fa4c0';ctx.fillText(time(i/cap.sample_rate_hz),px+4,18);ctx.fillStyle='#617a9b';ctx.fillText('#'+i.toLocaleString(),px+4,34);}
 ctx.save();ctx.beginPath();ctx.rect(left,42,plot,height-42);ctx.clip();
 const envelopes=[];if(scale<1){const stride=Math.max(1,Math.ceil((end-first)/plot));for(let a=first;a<end;a+=stride){const b=Math.min(end,a+stride);let low=samples[a],high=low;for(let i=a+1;i<b;i++){low&=samples[i];high|=samples[i];}envelopes.push([a,b,low,high]);}}
 for(let ch=0;ch<cap.channels;ch++){
  const y=55+ch*58,shift=BigInt(ch),level=i=>Number((samples[i]>>shift)&1n);
  ctx.strokeStyle=colors[ch%colors.length];ctx.lineWidth=1.7;ctx.beginPath();
  if(envelopes.length){for(const [a,b,low,high] of envelopes){const lo=Number((low>>shift)&1n),hi=Number((high>>shift)&1n);if(lo!==hi){const px=(x(a)+x(b))/2;ctx.moveTo(px,y);ctx.lineTo(px,y+25);}else{ctx.moveTo(x(a),y+(lo?0:25));ctx.lineTo(x(b),y+(lo?0:25));}}ctx.stroke();continue;}
  if(first<end){let prev=level(first);ctx.moveTo(x(first),y+(prev?0:25));for(let i=first+1;i<end;i++){const val=level(i);if(val!==prev){ctx.lineTo(x(i),y+(prev?0:25));ctx.lineTo(x(i),y+(val?0:25));prev=val;}}ctx.lineTo(x(end),y+(prev?0:25));ctx.stroke();}
 }
 for(const f of payload.frames){if(f.end_sample<first||f.start_sample>end)continue;const a=Math.max(left,x(f.start_sample)),b=Math.min(width,x(f.end_sample)),y=55+payload.channel*58+31;ctx.fillStyle=f.errors.length?'#74354c':'#254e65';ctx.fillRect(a,y,b-a,17);if(b-a>35){ctx.fillStyle='#f0f6ff';ctx.fillText(f.value===null?'incomplete':f.value.toString(16).toUpperCase().padStart(2,'0')+' '+f.text,a+5,y+12);}}
 for(const [sample,label,color] of [[cap.trigger_index,'T','#f1c45f'],[cursors[0],'A','#f785b2'],[cursors[1],'B','#82cfff']]){if(sample===null||sample===undefined)continue;const px=x(sample);ctx.strokeStyle=color;ctx.lineWidth=1.5;ctx.setLineDash([5,3]);ctx.beginPath();ctx.moveTo(px,45);ctx.lineTo(px,height);ctx.stroke();ctx.setLineDash([]);ctx.fillStyle=color;ctx.fillText(label,px+4,50);}
 ctx.restore();ctx.fillStyle='#101b2d';ctx.fillRect(0,0,left-5,height);ctx.font='bold 11px system-ui,sans-serif';ctx.fillStyle='#7188a7';ctx.fillText('SIGNALS',15,25);
 for(let ch=0;ch<cap.channels;ch++){ctx.fillStyle=colors[ch%colors.length];ctx.fillText(payload.names[ch].slice(0,13),15,72+ch*58);}
 if(!n){ctx.fillStyle='#a3b7d1';ctx.fillText('Capture contains no samples',left+40,90);}
}
function zoom(factor){const center=offset+visible/2;visible=Math.max(Math.min(8,Math.max(1,n)),Math.min(Math.max(1,n),visible/factor));offset=center-visible/2;draw();}
document.getElementById('in').onclick=()=>zoom(2);document.getElementById('out').onclick=()=>zoom(.5);document.getElementById('fit').onclick=()=>{visible=Math.max(1,n);offset=0;draw();};document.getElementById('trigger').onclick=()=>{if(cap.trigger_index!==null){offset=cap.trigger_index-visible/2;draw();}};
pan.oninput=()=>{offset=Number(pan.value)/10000*Math.max(0,n-visible);draw();};
canvas.addEventListener('wheel',e=>{e.preventDefault();if(e.ctrlKey)zoom(e.deltaY<0?1.4:1/1.4);else{offset+=Math.sign(e.deltaY)*visible*.12;draw();}},{passive:false});
canvas.addEventListener('click',e=>{const px=e.clientX-canvas.getBoundingClientRect().left;if(!n||px<left)return;const index=Math.max(0,Math.min(n-1,Math.round(offset+(px-left)/(width-left-15)*visible)));cursors[e.shiftKey?1:0]=index;const origin=cap.trigger_index===null||cap.trigger_index===undefined?'start':'trigger';let text=cursors.map((v,i)=>v===null?'':(i?'B':'A')+': sample '+v+' ('+time((v-(cap.trigger_index??0))/cap.sample_rate_hz)+' from '+origin+'), value 0x'+samples[v].toString(16).toUpperCase()).filter(Boolean).join(' · ');if(cursors.every(v=>v!==null)){const delta=cursors[1]-cursors[0];text+=' · Δ '+delta+' samples / '+time(delta/cap.sample_rate_hz);}document.getElementById('cursors').textContent=text;draw();});
document.querySelectorAll('tr[data-start]').forEach(row=>row.onclick=()=>{offset=Number(row.dataset.start)-visible/4;draw();});
window.addEventListener('resize',resize);resize();
</script></body></html>'''
