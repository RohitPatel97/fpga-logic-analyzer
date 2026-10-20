`timescale 1ns/1ps
module tb_logic_analyzer;
    reg clk = 0;
    reg rst_n = 0;
    reg sample_tick = 0;
    reg [7:0] probes = 0;
    reg bus_write = 0;
    reg [15:0] bus_addr = 0;
    reg [31:0] bus_wdata = 0;
    wire [31:0] bus_rdata;
    logic_analyzer dut (.*);

    reg [7:0] expected [0:1023];
    integer test_count = 0;
    integer i;

    task pulse;
        begin
            #5 clk = 1;
            #5 clk = 0;
        end
    endtask

    task reset;
        begin
            sample_tick = 0;
            bus_write = 0;
            rst_n = 0;
            pulse();
            pulse();
            rst_n = 1;
            pulse();
        end
    endtask

    task write_reg(input [15:0] address, input [31:0] value);
        begin
            sample_tick = 0;
            bus_addr = address;
            bus_wdata = value;
            bus_write = 1;
            pulse();
            bus_write = 0;
        end
    endtask

    task check_reg(input [15:0] address, input [31:0] value, input string label);
        begin
            bus_addr = address;
            #1;
            if (bus_rdata !== value)
                $fatal(1, "%s: register 0x%04h expected 0x%08h, got 0x%08h",
                       label, address, value, bus_rdata);
        end
    endtask

    task tick(input [7:0] value);
        begin
            probes = value;
            bus_write = 0;
            sample_tick = 1;
            pulse();
            sample_tick = 0;
        end
    endtask

    task configure(input [31:0] length, input [31:0] history,
                   input [31:0] divide, input [31:0] mode,
                   input [7:0] mask, input [7:0] value);
        begin
            write_reg(8, divide);
            write_reg(12, length);
            write_reg(16, history);
            write_reg(20, {24'b0, mask});
            write_reg(24, {24'b0, value});
            write_reg(28, mode);
        end
    endtask

    task check_capture(input integer count, input [31:0] trigger_at,
                       input [31:0] status, input [31:0] ticks,
                       input string label);
        integer n;
        begin
            check_reg(4, status, label);
            check_reg(32, count, label);
            check_reg(36, trigger_at, label);
            check_reg(56, ticks, label);
            for (n = 0; n < count; n = n + 1) begin
                write_reg(40, n);
                check_reg(44, {24'b0, expected[n]}, label);
                // DATA does not auto-increment or destroy frozen memory.
                check_reg(44, {24'b0, expected[n]}, label);
            end
            write_reg(40, count);
            check_reg(44, 0, "out-of-range sample read");
            write_reg(40, 32'hffffffff);
            check_reg(44, 0, "large sample index cannot alias RAM");
            check_reg(56, ticks, "bus traffic cannot advance sample clock");
            test_count = test_count + 1;
            $display("PASS %s", label);
        end
    endtask

    initial begin
        reset();
        check_reg(4, 0, "reset status");
        check_reg(8, 1, "default divider");
        check_reg(12, 512, "default length");
        check_reg(16, 32, "default history");
        check_reg(20, 1, "default mask");
        check_reg(24, 0, "default value");
        check_reg(28, 3, "default falling trigger");
        check_reg(36, 32'hffffffff, "unset trigger");
        check_reg(44, 0, "reset RAM hidden");
        check_reg(48, 1024, "capacity");
        check_reg(52, 8, "channels");
        check_reg(16'hffff, 0, "unknown address");

        configure(4, 2, 1, 0, 1, 0);
        write_reg(0, 1);
        check_reg(4, 1, "armed");
        check_reg(32, 0, "no frozen samples while armed");
        tick(8'h11);
        check_reg(4, 3, "triggered but collecting");
        write_reg(0, 1); // ignored while armed
        write_reg(12, 1); // ignored while armed
        check_reg(12, 4, "active configuration immutable");
        tick(8'h22); tick(8'h33); tick(8'h44);
        expected[0] = 8'h11; expected[1] = 8'h22;
        expected[2] = 8'h33; expected[3] = 8'h44;
        tick(8'hff);
        check_capture(4, 0, 6, 4, "immediate capture and frozen hold");

        configure(8, 3, 1, 1, 8'h80, 8'h80);
        write_reg(0, 1);
        for (i = 0; i < 1100; i = i + 1)
            tick(i & 127);
        tick(8'hd5);
        tick(8'ha1); tick(8'ha2); tick(8'ha3); tick(8'ha4);
        expected[0] = 1097 & 127; expected[1] = 1098 & 127;
        expected[2] = 1099 & 127; expected[3] = 8'hd5;
        expected[4] = 8'ha1; expected[5] = 8'ha2;
        expected[6] = 8'ha3; expected[7] = 8'ha4;
        check_capture(8, 3, 6, 1105, "circular wrap preserves chronological prehistory");

        configure(8, 5, 1, 1, 8'hff, 8'haa);
        write_reg(0, 1);
        tick(8'h10); tick(8'h11); tick(8'haa);
        for (i = 0; i < 5; i = i + 1) tick(8'h20 + i);
        expected[0] = 8'h10; expected[1] = 8'h11; expected[2] = 8'haa;
        for (i = 0; i < 5; i = i + 1) expected[i + 3] = 8'h20 + i;
        check_capture(8, 2, 6, 8, "early trigger extends posttrigger collection");

        configure(1, 0, 1, 0, 0, 0);
        write_reg(0, 1);
        tick(8'h5a);
        expected[0] = 8'h5a;
        check_capture(1, 0, 6, 1, "one-sample first and last boundary");

        configure(4, 3, 1, 1, 8'hff, 8'hff);
        write_reg(0, 1);
        tick(1); tick(2); tick(3); tick(8'hff);
        expected[0] = 1; expected[1] = 2;
        expected[2] = 3; expected[3] = 8'hff;
        check_capture(4, 3, 6, 4, "trigger in last capture slot");

        configure(1024, 1023, 1, 1, 8'h80, 8'h80);
        write_reg(0, 1);
        for (i = 0; i < 1100; i = i + 1) tick(i & 127);
        tick(8'hff);
        for (i = 0; i < 1023; i = i + 1) expected[i] = (77 + i) & 127;
        expected[1023] = 8'hff;
        check_capture(1024, 1023, 6, 1101, "full-capacity wrapped capture");

        configure(3, 0, 1, 1, 8'h80, 8'h80);
        write_reg(0, 1);
        for (i = 0; i < 20; i = i + 1) tick(i);
        tick(8'hff); tick(8'h55); tick(8'haa);
        expected[0] = 8'hff; expected[1] = 8'h55; expected[2] = 8'haa;
        check_capture(3, 0, 6, 23, "zero prehistory drops all pretrigger samples");

        configure(3, 0, 3, 0, 1, 0);
        write_reg(0, 1);
        for (i = 1; i <= 8; i = i + 1) tick(i);
        check_reg(4, 3, "divider still collecting before ninth tick");
        check_reg(32, 0, "partial capture remains hidden");
        tick(9);
        expected[0] = 3; expected[1] = 6; expected[2] = 9;
        check_capture(3, 0, 6, 9, "divider samples every third input tick");

        configure(4, 2, 1, 2, 8'h03, 0);
        write_reg(0, 1);
        tick(8'h03); // initial high sample cannot create an edge
        tick(8'h83); // unmasked bit cannot trigger
        check_reg(4, 1, "rising trigger waits for a selected transition");
        tick(8'h00);
        tick(8'h02); // either selected mask bit may rise
        tick(8'h42);
        expected[0] = 8'h83; expected[1] = 8'h00;
        expected[2] = 8'h02; expected[3] = 8'h42;
        check_capture(4, 2, 6, 5, "rising edge mask and first-sample baseline");

        configure(3, 1, 1, 3, 8'h04, 0);
        write_reg(0, 1);
        tick(8'h00); tick(8'h04); tick(8'h84); tick(8'h80); tick(8'h55);
        expected[0] = 8'h84; expected[1] = 8'h80; expected[2] = 8'h55;
        check_capture(3, 1, 6, 5, "falling edge trigger");

        configure(3, 1, 2, 2, 0, 0); // zero edge mask never triggers
        write_reg(0, 1);
        tick(0); tick(8'h10);
        write_reg(0, 4);
        tick(8'h20);
        check_reg(4, 1, "software trigger waits for divider boundary");
        tick(8'h30); tick(8'h40); tick(8'h50);
        expected[0] = 8'h10; expected[1] = 8'h30; expected[2] = 8'h50;
        check_capture(3, 1, 6, 6, "software trigger sampled at divider boundary");

        configure(8, 3, 1, 1, 8'hff, 8'hff);
        write_reg(0, 1);
        for (i = 0; i < 8; i = i + 1) tick(i);
        write_reg(0, 2);
        expected[0] = 5; expected[1] = 6; expected[2] = 7;
        check_capture(3, 32'hffffffff, 4, 8, "abort freezes prehistory without inventing trigger");
        write_reg(0, 1);
        write_reg(0, 2);
        check_capture(0, 32'hffffffff, 4, 0, "abort before first sample");

        configure(8, 3, 1, 1, 8'hff, 8'hff);
        write_reg(0, 1);
        tick(1); tick(2); tick(8'hff); tick(3);
        write_reg(0, 2);
        expected[0] = 1; expected[1] = 2; expected[2] = 8'hff; expected[3] = 3;
        check_capture(4, 2, 6, 4, "abort after trigger freezes partial capture");

        configure(4, 0, 0, 0, 1, 0);
        write_reg(0, 1);
        check_reg(4, 8, "zero divider rejected");
        tick(8'hff);
        check_reg(56, 0, "invalid configuration cannot capture");
        check_reg(32, 0, "invalid arm clears old data");
        check_reg(36, 32'hffffffff, "invalid arm clears old trigger");
        configure(0, 0, 1, 0, 1, 0);
        write_reg(0, 1); check_reg(4, 8, "zero length rejected");
        configure(1025, 0, 1, 0, 1, 0);
        write_reg(0, 1); check_reg(4, 8, "over-capacity length rejected");
        configure(4, 4, 1, 0, 1, 0);
        write_reg(0, 1); check_reg(4, 8, "history equal to length rejected");
        configure(4, 0, 1, 4, 1, 0);
        write_reg(0, 1); check_reg(4, 8, "unsupported trigger mode rejected");
        configure(1, 0, 1, 1, 0, 8'hff);
        write_reg(0, 1);
        tick(8'hc3);
        expected[0] = 8'hc3;
        check_capture(1, 0, 6, 1, "valid rearm clears error; empty level mask matches");

        configure(8, 4, 1, 0, 1, 0);
        write_reg(0, 1); tick(8'hfa);
        reset();
        check_reg(4, 0, "reset interrupts active capture");
        check_reg(32, 0, "reset clears valid count");
        check_reg(36, 32'hffffffff, "reset clears trigger index");
        check_reg(56, 0, "reset clears ticks");
        check_reg(44, 0, "stale RAM inaccessible after reset");
        $display("PASS: %0d capture scenarios plus configuration/reset checks", test_count);
        $finish;
    end

    initial begin
        #10000000;
        $fatal(1, "testbench timeout");
    end
endmodule
