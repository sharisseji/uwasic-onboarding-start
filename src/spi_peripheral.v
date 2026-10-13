/*
 * Copyright (c) 2024 Sharisse Ji
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// One frame:
//   [15]    r/w (1 = write)
//   [14:8]  register addr
//   [7:0]   register data

// COPI sampled on each rising SCLK edge while nCS = low
// SPI pins synchronized into the system clock domain before interpreted
module spi_peripheral (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       sclk,
    input  wire       copi,
    input  wire       ncs,
    output reg [7:0]  en_reg_out_7_0,
    output reg [7:0]  en_reg_out_15_8,
    output reg [7:0]  en_reg_pwm_7_0,
    output reg [7:0]  en_reg_pwm_15_8,
    output reg [7:0]  pwm_duty_cycle
);

    reg sclk_meta;                 // first SCLK synchronizer stage
    reg sclk_sync;                 // synchronized SCLK
    reg sclk_sync_d;               // prev SCLK sample
    reg copi_meta;                 // first COPI synchronizer stage
    reg copi_sync;                 // synchronized COPI
    reg ncs_meta;                  // first nCS synchronizer stage
    reg ncs_sync;                  // synchronized nCS
    reg ncs_sync_d;                // prev nCS sample
    reg [4:0] bit_count;           // # of sampled bits
    reg [14:0] shift_reg;          // first 15 received bits
    reg [15:0] frame_reg;          // complete received frame
    reg frame_valid;               // set when 16 bits arrive

    wire sclk_rise = sclk_sync && !sclk_sync_d;         // mode-0 sample edge
    wire ncs_rise = ncs_sync && !ncs_sync_d;            // end of transaction
    wire [15:0] received_frame = {shift_reg, copi_sync};

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            sclk_meta        <= 1'b0;
            sclk_sync        <= 1'b0;
            sclk_sync_d      <= 1'b0;
            copi_meta        <= 1'b0;
            copi_sync        <= 1'b0;
            ncs_meta         <= 1'b1;
            ncs_sync         <= 1'b1;
            ncs_sync_d       <= 1'b1;
            bit_count        <= 5'd0;
            shift_reg        <= 15'd0;
            frame_reg        <= 16'd0;
            frame_valid      <= 1'b0;
            en_reg_out_7_0   <= 8'd0;
            en_reg_out_15_8  <= 8'd0;
            en_reg_pwm_7_0   <= 8'd0;
            en_reg_pwm_15_8  <= 8'd0;
            pwm_duty_cycle   <= 8'd0;
        end else begin
            // synchronize async SPI inputs into clk domain
            sclk_meta   <= sclk;
            sclk_sync   <= sclk_meta;
            sclk_sync_d <= sclk_sync;
            copi_meta   <= copi;
            copi_sync   <= copi_meta;
            ncs_meta    <= ncs;
            ncs_sync    <= ncs_meta;
            ncs_sync_d  <= ncs_sync;

            // commit when chip select is deasserted only
            if (ncs_rise) begin
                bit_count   <= 5'd0;
                if (frame_valid) begin
                    if (frame_reg[15]) begin
                        case (frame_reg[14:8])
                            7'h00: en_reg_out_7_0  <= frame_reg[7:0];  // uo_out value
                            7'h01: en_reg_out_15_8 <= frame_reg[7:0];  // uio_out value
                            7'h02: en_reg_pwm_7_0  <= frame_reg[7:0];  // LOWER PWM mask
                            7'h03: en_reg_pwm_15_8 <= frame_reg[7:0];  // UPPER PWM mask
                            7'h04: pwm_duty_cycle  <= frame_reg[7:0];  // PWM duty cycle
                            default: begin
                                // ignore unimplemented addresses
                            end
                        endcase
                    end
                end
                frame_valid <= 1'b0;
            end else if (ncs_sync) begin
                bit_count <= 5'd0;
            end else if (sclk_rise) begin
                // shift in 1 COPI bit on each synchronized SCLK rising edge.
                if (bit_count < 5'd16) begin
                    shift_reg <= received_frame[14:0];
                end

                if (bit_count == 5'd15) begin
                    bit_count   <= 5'd16;
                    frame_reg   <= received_frame;  // save complete frame
                    frame_valid <= 1'b1;
                end else if (bit_count == 5'd16) begin
                    // > 16 makes the transaction invalid
                    frame_valid <= 1'b0;
                end else begin
                    bit_count <= bit_count + 1'b1;
                end
            end
        end
    end

endmodule
