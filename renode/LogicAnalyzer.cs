// Renode owns virtual time; Icarus executes the actual capture RTL.
// This peripheral implements transport and stimulus only, not capture behavior.
using System;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text;
using Antmicro.Renode.Core;
using Antmicro.Renode.Exceptions;
using Antmicro.Renode.Peripherals.Bus;
using Antmicro.Renode.Peripherals.Timers;
using Antmicro.Renode.Time;

namespace Antmicro.Renode.Peripherals.Miscellaneous
{
    public sealed class LogicAnalyzer : IDoubleWordPeripheral, IKnownSize, IDisposable
    {
        public LogicAnalyzer(IMachine machine)
        {
            sampleClock = new LimitTimer(machine.ClockSource, 1000000, this,
                "logic-analyzer-sample", limit: 1, direction: Direction.Ascending,
                enabled: false, eventEnabled: true, workMode: WorkMode.Periodic);
            sampleClock.LimitReached += Sample;
        }

        public void Configure(string executable, string design)
        {
            if(!File.Exists(executable) || !File.Exists(design))
                throw new RecoverableException("vvp executable or compiled RTL does not exist");
            Dispose();
            responses = new BlockingCollection<string>();
            var responseQueue = responses;
            lastError = "";
            process = new Process();
            process.StartInfo = new ProcessStartInfo {
                FileName = executable,
                Arguments = "\"" + design.Replace("\"", "\\\"") + "\"",
                UseShellExecute = false,
                RedirectStandardInput = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true
            };
            process.OutputDataReceived += (sender, args) => {
                if(args.Data != null) responseQueue.Add(args.Data);
            };
            process.ErrorDataReceived += (sender, args) => {
                if(args.Data != null) lastError = args.Data;
            };
            try
            {
                process.Start();
                process.BeginOutputReadLine();
                process.BeginErrorReadLine();
                Require(ReadResponse(), "READY");
                GeneratedSamples = 0;
                LastProbes = 1;
                sampleClock.Reset();
            }
            catch(Exception)
            {
                Dispose();
                throw;
            }
        }

        public uint ReadDoubleWord(long offset)
        {
            ValidateOffset(offset);
            var response = Request("R " + offset.ToString(CultureInfo.InvariantCulture));
            uint result;
            if(!response.StartsWith("VALUE ") || !uint.TryParse(response.Substring(6),
                NumberStyles.Integer, CultureInfo.InvariantCulture, out result))
                throw new RecoverableException("Unexpected RTL read response: " + response);
            return result;
        }

        public void WriteDoubleWord(long offset, uint value)
        {
            ValidateOffset(offset);
            Require(Request(String.Format(CultureInfo.InvariantCulture,
                "W {0} {1}", offset, value)), "OK");
        }

        public void StartSource()
        {
            if(process == null) throw new RecoverableException("Call Configure first");
            sampleClock.Enabled = true;
        }

        public void StopSource() { sampleClock.Enabled = false; }

        // Export through the same RTL read port as MMIO, in chronological order.
        public string DumpSamples()
        {
            var count = ReadDoubleWord(32);
            if(count > 1024) throw new RecoverableException("RTL returned an invalid count");
            var result = new StringBuilder("SAMPLES:");
            for(uint i = 0; i < count; ++i)
            {
                WriteDoubleWord(40, i);
                if(i != 0) result.Append(',');
                result.Append(ReadDoubleWord(44).ToString(CultureInfo.InvariantCulture));
            }
            return result.ToString();
        }

        public void Reset()
        {
            sampleClock.Reset();
            GeneratedSamples = 0;
            LastProbes = 1;
            if(process != null && !process.HasExited) Require(Request("Z"), "OK");
        }

        public void Dispose()
        {
            // Renode may dispose the system bus before disposing peripherals.
            // LimitTimer.Enabled tries to notify that bus when it changes.
            try { sampleClock.Enabled = false; }
            catch(ObjectDisposedException) { }
            if(process == null) return;
            try
            {
                if(!process.HasExited)
                {
                    process.StandardInput.WriteLine("X");
                    process.StandardInput.Flush();
                    if(!process.WaitForExit(2000)) process.Kill();
                    process.WaitForExit(2000);
                }
            }
            catch(InvalidOperationException) { }
            catch(IOException) { }
            finally { process.Dispose(); process = null; }
        }

        public long Size { get { return 0x100; } }
        public ulong GeneratedSamples { get; private set; }
        public uint LastProbes { get; private set; }
        public bool SourceRunning { get { return sampleClock.Enabled; } }

        private void Sample()
        {
            // CH0: repeated 100 kbaud, 8N1 UART bytes 55 41 A3 0A.
            // There are 32 idle ticks then four 120-tick slots per 512-tick cycle.
            // Each slot is 100 ticks of UART plus 20 idle ticks.
            var phase = (int)(GeneratedSamples % 512);
            var uart = 1u;
            if(phase >= 32)
            {
                var slot = (phase - 32) / 120;
                var bit = ((phase - 32) % 120) / 10;
                if(slot < payload.Length && bit < 10)
                    uart = bit == 0 ? 0u : bit == 9 ? 1u : (uint)((payload[slot] >> (bit - 1)) & 1);
            }
            LastProbes = ((uint)GeneratedSamples << 1 & 0xFEu) | uart;
            Require(Request(String.Format(CultureInfo.InvariantCulture,
                "T {0} 1", LastProbes)), "OK");
            GeneratedSamples++;
        }

        private string Request(string command)
        {
            lock(sync)
            {
                if(process == null || process.HasExited)
                    throw new RecoverableException("RTL bridge is not running. " + lastError);
                process.StandardInput.WriteLine(command);
                process.StandardInput.Flush();
                return ReadResponse();
            }
        }

        private string ReadResponse()
        {
            string value;
            if(!responses.TryTake(out value, 5000))
                throw new RecoverableException("Timed out waiting for RTL bridge. " + lastError);
            return value.Trim();
        }

        private static void Require(string value, string expected)
        {
            if(value != expected)
                throw new RecoverableException("RTL bridge expected " + expected + ", got: " + value);
        }

        private static void ValidateOffset(long offset)
        {
            if(offset < 0 || offset > 56 || offset % 4 != 0)
                throw new RecoverableException("Invalid logic-analyzer register offset");
        }

        private readonly LimitTimer sampleClock;
        private readonly object sync = new object();
        private readonly byte[] payload = { 0x55, 0x41, 0xA3, 0x0A };
        private Process process;
        private BlockingCollection<string> responses;
        private string lastError;
    }
}
