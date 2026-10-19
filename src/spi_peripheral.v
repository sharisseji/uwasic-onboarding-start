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

// Timing assumptions implied by 2-stage synchronizers: nCS must stay high
// for at least 3 clk cycles between transactions, and must not deassert within
// ~2 clk cycles of the 16th SCLK rising edge (a high nCS takes priority over a
// SCLK posedge detected in the same clk cycle).

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

    localparam [3:0] LAST_BIT = 4'd15;  // 16th bit: 1 r/w + 7 addr + 8 data

    // implemented registers, all other addresses are ignored
    localparam [6:0] ADDR_EN_OUT_7_0  = 7'h00;  // enable output on uo_out[7:0]
    localparam [6:0] ADDR_EN_OUT_15_8 = 7'h01;  // enable output on uio_out[7:0]
    localparam [6:0] ADDR_EN_PWM_7_0  = 7'h02;  // enable PWM mode on uo_out[7:0]
    localparam [6:0] ADDR_EN_PWM_15_8 = 7'h03;  // enable PWM mode on uio_out[7:0]
    localparam [6:0] ADDR_PWM_DUTY    = 7'h04;  // PWM duty cycle (0-255)

    reg sclk_meta;                 // first SCLK synchronizer stage
    reg sclk_sync;                 // synchronized SCLK
    reg sclk_sync_d;               // prev SCLK sample
    reg copi_meta;                 // first COPI synchronizer stage
    reg copi_sync;                 // synchronized COPI
    reg ncs_meta;                  // first nCS synchronizer stage
    reg ncs_sync;                  // synchronized nCS
    reg [3:0] bit_count;           // # of sampled bits, wraps after the 16th
    reg [15:0] rx_shift_reg;       // received bits, holds the frame once full
    reg transaction_valid;         // set when 16 bits arrive, safe to commit
    reg frame_overrun;             // sticky: more than 16 bits seen this frame

    wire sclk_posedge = sclk_sync && !sclk_sync_d;      // mode-0 sample edge

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            sclk_meta         <= 1'b0;
            sclk_sync         <= 1'b0;
            sclk_sync_d       <= 1'b0;
            copi_meta         <= 1'b0;
            copi_sync         <= 1'b0;
            ncs_meta          <= 1'b1;
            ncs_sync          <= 1'b1;
            bit_count         <= 4'd0;
            rx_shift_reg      <= 16'd0;
            transaction_valid <= 1'b0;
            frame_overrun     <= 1'b0;
            en_reg_out_7_0    <= 8'd0;
            en_reg_out_15_8   <= 8'd0;
            en_reg_pwm_7_0    <= 8'd0;
            en_reg_pwm_15_8   <= 8'd0;
            pwm_duty_cycle    <= 8'd0;
        end else begin
            // synchronize async SPI inputs into clk domain
            sclk_meta   <= sclk;
            sclk_sync   <= sclk_meta;
            sclk_sync_d <= sclk_sync;
            copi_meta   <= copi;
            copi_sync   <= copi_meta;
            ncs_meta    <= ncs;
            ncs_sync    <= ncs_meta;

            // commit when chip select is deasserted only
            if (ncs_sync) begin
                bit_count <= 4'd0;
                if (transaction_valid) begin
                    // writes only, reads are not supported
                    if (rx_shift_reg[15]) begin
                        case (rx_shift_reg[14:8])
                            ADDR_EN_OUT_7_0:  en_reg_out_7_0  <= rx_shift_reg[7:0];
                            ADDR_EN_OUT_15_8: en_reg_out_15_8 <= rx_shift_reg[7:0];
                            ADDR_EN_PWM_7_0:  en_reg_pwm_7_0  <= rx_shift_reg[7:0];
                            ADDR_EN_PWM_15_8: en_reg_pwm_15_8 <= rx_shift_reg[7:0];
                            ADDR_PWM_DUTY:    pwm_duty_cycle  <= rx_shift_reg[7:0];
                            default: begin
                                // ignore unimplemented addresses
                            end
                        endcase
                    end
                end
                // self-clearing, so a held-high nCS commits exactly once
                transaction_valid <= 1'b0;
                frame_overrun     <= 1'b0;
            end else if (sclk_posedge) begin
                // shift in 1 COPI bit on each synchronized SCLK rising edge.
                // transaction_valid doubles as the "frame full" flag, so
                // shifting stops and the frame survives until nCS rises.
                if (!transaction_valid && !frame_overrun) begin
                    rx_shift_reg <= {rx_shift_reg[14:0], copi_sync};
                    bit_count    <= bit_count + 1'b1;
                    if (bit_count == LAST_BIT) begin
                        transaction_valid <= 1'b1;
                    end
                end else begin
                    // a 17th SCLK edge invalidates the frame, and the overrun
                    // flag is sticky so no later edge can re-validate it
                    transaction_valid <= 1'b0;
                    frame_overrun     <= 1'b1;
                end
            end
        end
    end

endmodule
