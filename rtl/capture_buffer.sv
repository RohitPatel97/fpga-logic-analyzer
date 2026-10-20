`timescale 1ns/1ps
// Single-clock capture RAM. Addresses wrap naturally at 2**ADDRESS_BITS.
// Read data is combinational; replace this module with a registered RAM adapter
// if targeting FPGA block RAM that requires synchronous readout.
module capture_buffer #(
    parameter integer WIDTH = 8,
    parameter integer ADDRESS_BITS = 10
) (
    input  wire                     clk,
    input  wire                     write_enable,
    input  wire [ADDRESS_BITS-1:0]  write_address,
    input  wire [WIDTH-1:0]         write_data,
    input  wire [ADDRESS_BITS-1:0]  read_address,
    output wire [WIDTH-1:0]         read_data
);
    reg [WIDTH-1:0] memory [0:(1 << ADDRESS_BITS)-1];

    always @(posedge clk) begin
        if (write_enable)
            memory[write_address] <= write_data;
    end

    assign read_data = memory[read_address];
endmodule
