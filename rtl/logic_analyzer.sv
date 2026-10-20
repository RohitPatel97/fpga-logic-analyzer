`timescale 1ns/1ps
// 8-channel, 1024-sample memory-mapped logic analyzer.
// bus_addr is a BYTE offset. All registers are 32-bit, naturally aligned.
module logic_analyzer (
    input  wire         clk,
    input  wire         rst_n,
    input  wire         sample_tick,
    input  wire [7:0]   probes,
    input  wire         bus_write,
    input  wire [15:0]  bus_addr,
    input  wire [31:0]  bus_wdata,
    output reg  [31:0]  bus_rdata
);
    reg [31:0] divider;
    reg [31:0] capture_length;
    reg [31:0] pretrigger;
    reg [7:0] trigger_mask;
    reg [7:0] trigger_value;
    reg [31:0] trigger_mode;
    reg [31:0] read_index;

    wire armed, triggered, done, config_error;
    wire [31:0] sample_count, trigger_index, tick_count;
    wire [9:0] first_address, write_address;
    wire write_enable;
    wire [9:0] read_address = first_address + read_index[9:0];
    wire [7:0] read_data;
    wire control_write = bus_write && bus_addr == 16'h0000;

    capture_controller controller (
        .clk(clk), .rst_n(rst_n),
        .sample_tick(sample_tick && !bus_write), .probes(probes),
        .command_arm(control_write && bus_wdata[0] && !bus_wdata[1]),
        .command_abort(control_write && bus_wdata[1]),
        .command_trigger(control_write && bus_wdata[2] &&
                         !bus_wdata[1] && !bus_wdata[0]),
        .divider(divider), .capture_length(capture_length),
        .pretrigger(pretrigger), .trigger_mask(trigger_mask),
        .trigger_value(trigger_value), .trigger_mode(trigger_mode),
        .armed(armed), .triggered(triggered), .done(done),
        .config_error(config_error), .sample_count(sample_count),
        .trigger_index(trigger_index), .tick_count(tick_count),
        .first_address(first_address), .write_address(write_address),
        .write_enable(write_enable)
    );

    capture_buffer buffer (
        .clk(clk), .write_enable(write_enable),
        .write_address(write_address), .write_data(probes),
        .read_address(read_address), .read_data(read_data)
    );

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            divider <= 1;
            capture_length <= 512;
            pretrigger <= 32;
            trigger_mask <= 1;
            trigger_value <= 0;
            trigger_mode <= 3;
            read_index <= 0;
        end else if (bus_write) begin
            if (bus_addr == 16'h0028)
                read_index <= bus_wdata;
            if (!armed) begin
                case (bus_addr)
                    16'h0008: divider <= bus_wdata;
                    16'h000c: capture_length <= bus_wdata;
                    16'h0010: pretrigger <= bus_wdata;
                    16'h0014: trigger_mask <= bus_wdata[7:0];
                    16'h0018: trigger_value <= bus_wdata[7:0];
                    16'h001c: trigger_mode <= bus_wdata;
                    default: begin end
                endcase
            end
        end
    end

    always @* begin
        case (bus_addr)
            16'h0000: bus_rdata = 0;
            16'h0004: bus_rdata = {28'b0, config_error, done, triggered, armed};
            16'h0008: bus_rdata = divider;
            16'h000c: bus_rdata = capture_length;
            16'h0010: bus_rdata = pretrigger;
            16'h0014: bus_rdata = {24'b0, trigger_mask};
            16'h0018: bus_rdata = {24'b0, trigger_value};
            16'h001c: bus_rdata = trigger_mode;
            16'h0020: bus_rdata = sample_count;
            16'h0024: bus_rdata = trigger_index;
            16'h0028: bus_rdata = read_index;
            16'h002c: bus_rdata = (done && read_index < sample_count) ?
                                    {24'b0, read_data} : 0;
            16'h0030: bus_rdata = 1024;
            16'h0034: bus_rdata = 8;
            16'h0038: bus_rdata = tick_count;
            default: bus_rdata = 0;
        endcase
    end
endmodule
