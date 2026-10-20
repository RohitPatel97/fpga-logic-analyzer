`timescale 1ns/1ps
// Circular pretrigger history followed by a fixed total-length capture.
// All probe transitions must be synchronous to clk (or synchronized externally).
module capture_controller (
    input  wire         clk,
    input  wire         rst_n,
    input  wire         sample_tick,
    input  wire [7:0]   probes,
    input  wire         command_arm,
    input  wire         command_abort,
    input  wire         command_trigger,
    input  wire [31:0]  divider,
    input  wire [31:0]  capture_length,
    input  wire [31:0]  pretrigger,
    input  wire [7:0]   trigger_mask,
    input  wire [7:0]   trigger_value,
    input  wire [31:0]  trigger_mode,
    output reg          armed,
    output reg          triggered,
    output reg          done,
    output reg          config_error,
    output reg  [31:0]  sample_count,
    output reg  [31:0]  trigger_index,
    output reg  [31:0]  tick_count,
    output reg  [9:0]   first_address,
    output reg  [9:0]   write_address,
    output wire         write_enable
);
    reg [31:0] divider_counter;
    reg [31:0] history_count;
    reg [31:0] collected_count;
    reg [7:0] previous_sample;
    reg previous_valid;
    reg software_trigger_pending;
    reg trigger_match;

    wire configuration_valid = divider != 0 &&
                               capture_length >= 1 && capture_length <= 1024 &&
                               pretrigger < capture_length && trigger_mode <= 3;
    wire capture_now = armed && sample_tick &&
                       divider_counter == divider - 1 &&
                       !command_arm && !command_abort;
    assign write_enable = capture_now;

    always @* begin
        case (trigger_mode)
            0: trigger_match = 1'b1;
            1: trigger_match = (probes & trigger_mask) ==
                               (trigger_value & trigger_mask);
            2: trigger_match = previous_valid &&
                               |((~previous_sample) & probes & trigger_mask);
            3: trigger_match = previous_valid &&
                               |(previous_sample & (~probes) & trigger_mask);
            default: trigger_match = 1'b0;
        endcase
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            armed <= 0;
            triggered <= 0;
            done <= 0;
            config_error <= 0;
            sample_count <= 0;
            trigger_index <= 32'hffffffff;
            tick_count <= 0;
            first_address <= 0;
            write_address <= 0;
            divider_counter <= 0;
            history_count <= 0;
            collected_count <= 0;
            previous_sample <= 0;
            previous_valid <= 0;
            software_trigger_pending <= 0;
        end else if (command_abort) begin
            if (armed) begin
                armed <= 0;
                done <= 1;
                software_trigger_pending <= 0;
                if (triggered) begin
                    sample_count <= collected_count;
                end else begin
                    first_address <= write_address - history_count[9:0];
                    sample_count <= history_count;
                end
            end
        end else if (command_arm) begin
            // A second arm command cannot destroy an in-flight capture.
            if (!armed) begin
                armed <= configuration_valid;
                triggered <= 0;
                done <= 0;
                config_error <= !configuration_valid;
                sample_count <= 0;
                trigger_index <= 32'hffffffff;
                tick_count <= 0;
                first_address <= 0;
                write_address <= 0;
                divider_counter <= 0;
                history_count <= 0;
                collected_count <= 0;
                previous_sample <= 0;
                previous_valid <= 0;
                software_trigger_pending <= 0;
            end
        end else if (armed) begin
            if (command_trigger)
                software_trigger_pending <= 1;
            if (sample_tick) begin
                tick_count <= tick_count + 1;
                if (capture_now) begin
                    divider_counter <= 0;
                    previous_sample <= probes;
                    previous_valid <= 1;
                    write_address <= write_address + 1'b1;
                    if (triggered) begin
                        collected_count <= collected_count + 1;
                        if (collected_count + 1 == capture_length) begin
                            sample_count <= collected_count + 1;
                            armed <= 0;
                            done <= 1;
                        end
                    end else if (trigger_match || software_trigger_pending ||
                                 command_trigger) begin
                        triggered <= 1;
                        software_trigger_pending <= 0;
                        first_address <= write_address - history_count[9:0];
                        trigger_index <= history_count;
                        collected_count <= history_count + 1;
                        if (history_count + 1 == capture_length) begin
                            sample_count <= history_count + 1;
                            armed <= 0;
                            done <= 1;
                        end
                    end else if (history_count < pretrigger) begin
                        history_count <= history_count + 1;
                    end
                end else begin
                    divider_counter <= divider_counter + 1;
                end
            end
        end
    end
endmodule
