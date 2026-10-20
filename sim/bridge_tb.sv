`timescale 1ns/1ps
// Line-oriented Icarus bridge used by the Renode peripheral and Python driver.
module bridge_tb;
    reg clk = 0;
    reg rst_n = 0;
    reg sample_tick = 0;
    reg [7:0] probes = 0;
    reg bus_write = 0;
    reg [15:0] bus_addr = 0;
    reg [31:0] bus_wdata = 0;
    wire [31:0] bus_rdata;
    logic_analyzer dut (.*);

    localparam integer STDIN = 32'h80000000;
    localparam integer STDOUT = 32'h80000001;
    reg [8191:0] line;
    reg [63:0] command;
    reg [31:0] argument_a, argument_b;
    integer read_result, fields;
    reg [31:0] repeat_index;

    task pulse;
        begin
            #5 clk = 1;
            #5 clk = 0;
        end
    endtask

    initial begin
        pulse();
        pulse();
        rst_n = 1;
        pulse();
        $display("READY");
        $fflush(STDOUT);
        forever begin
            line = 0;
            command = 0;
            argument_a = 0;
            argument_b = 0;
            read_result = $fgets(line, STDIN);
            if (read_result == 0)
                $finish(0);
            fields = $sscanf(line, "%s %d %d", command, argument_a, argument_b);
            if (command == "W" && fields == 3) begin
                if (argument_a > 65535) begin
                    $display("ERROR address");
                end else begin
                    sample_tick = 0;
                    bus_addr = argument_a[15:0];
                    bus_wdata = argument_b;
                    bus_write = 1;
                    pulse();
                    bus_write = 0;
                    $display("OK");
                end
            end else if (command == "R" && fields == 2) begin
                if (argument_a > 65535) begin
                    $display("ERROR address");
                end else begin
                    sample_tick = 0;
                    bus_write = 0;
                    bus_addr = argument_a[15:0];
                    #1;
                    $display("VALUE %0d", bus_rdata);
                end
            end else if (command == "T" && fields == 3) begin
                if (argument_a > 255) begin
                    $display("ERROR probes");
                end else begin
                    probes = argument_a[7:0];
                    bus_write = 0;
                    sample_tick = 1;
                    for (repeat_index = 0; repeat_index < argument_b;
                         repeat_index = repeat_index + 1)
                        pulse();
                    sample_tick = 0;
                    $display("OK");
                end
            end else if (command == "Z" && fields == 1) begin
                sample_tick = 0;
                bus_write = 0;
                probes = 0;
                rst_n = 0;
                pulse();
                pulse();
                rst_n = 1;
                pulse();
                $display("OK");
            end else if (command == "X" && fields == 1) begin
                $display("BYE");
                $fflush(STDOUT);
                $finish(0);
            end else begin
                $display("ERROR command");
            end
            $fflush(STDOUT);
        end
    end
endmodule
