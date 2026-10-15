# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0

from collections import namedtuple

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import Edge, ClockCycles, Timer, with_timeout
from cocotb.result import SimTimeoutError
from cocotb.types import LogicArray

CLK_PERIOD_NS = 100       # 10 MHz system clock
SCLK_PERIOD_NS = 10_000   # 100 kHz SPI clock, heavily oversampled by clk

PWM_OUTPUT_MASK = 0x01    # the bit under test on each output bus
EXPECTED_PWM_FREQUENCY_HZ = 3000
PWM_FREQUENCY_TOLERANCE = 0.01
PWM_PERIOD_US = 333       # 256 counts * (12 + 1) clk * 100 ns
PWM_STEADY_US = 2 * PWM_PERIOD_US  # spans a full period with margin
EDGE_TIMEOUT_MS = 2       # generous bound for "an edge should have happened"

# The two PWM-capable output buses and the registers that drive them.
PwmBus = namedtuple("PwmBus", "name out_reg pwm_reg signal_name")
UO_BUS = PwmBus("uo_out", 0x00, 0x02, "uo_out")
UIO_BUS = PwmBus("uio_out", 0x01, 0x03, "uio_out")
PWM_BUSES = (UO_BUS, UIO_BUS)

DUTY_REG = 0x04


async def await_half_sclk(dut):
    """Wait for the SCLK signal to go high or low."""
    await Timer(SCLK_PERIOD_NS // 2, units="ns")

def ui_in_logicarray(ncs, bit, sclk):
    """Setup ui_in value as a LogicArray."""
    return LogicArray(f"00000{ncs}{bit}{sclk}")

async def send_spi_transaction(dut, r_w, address, data):
    """
    Send an SPI transaction.
    
    Parameters:
    - r_w: boolean, 1-bit, True=write, False=read
    - address: int, 7-bit (0-127)
    - data: LogicArray or int, 8-bit 
    """
    
    # Convert data to int if type = LogicArray
    if isinstance(data, LogicArray):
        data_int = int(data)
    else:
        data_int = data

    # Validate inputs
    if address < 0 or address > 127:                    
        raise ValueError("Address must be 7-bit (0-127)")
    if data_int < 0 or data_int > 255:
        raise ValueError("Data must be 8-bit (0-255)")
    
    # Combine RW and address into first byte
    first_byte = (int(r_w) << 7) | address             
    
    # Start transaction: pull CS low
    sclk = 0
    ncs = 0
    bit = 0
    
    # Set initial state with CS low
    dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
    await ClockCycles(dut.clk, 1)

    # Send first byte (RW + Address)
    for i in range(8):
        bit = (first_byte >> (7-i)) & 0x1
        # SCLK low, set COPI
        sclk = 0
        dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
        await await_half_sclk(dut)
        # SCLK high, keep COPI
        sclk = 1
        dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
        await await_half_sclk(dut)

    # Send second byte (Data)
    for i in range(8):
        bit = (data_int >> (7-i)) & 0x1
        # SCLK low, set COPI
        sclk = 0
        dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
        await await_half_sclk(dut)
        # SCLK high, keep COPI
        sclk = 1
        dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
        await await_half_sclk(dut)
    
    # End transaction - return CS high
    sclk = 0
    ncs = 1
    bit = 0
    dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
    await ClockCycles(dut.clk, 600)
    return ui_in_logicarray(ncs, bit, sclk)


def start_clock(dut):
    """Start the system clock. Call once per test, never inside a loop."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, units="ns").start())

async def apply_reset(dut):
    """Apply active-low reset, leaving nCS deasserted."""
    dut.ena.value = 1
    dut.ui_in.value = ui_in_logicarray(1, 0, 0)
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)

async def reset_dut(dut):
    """Start the system clock, apply active-low reset."""
    start_clock(dut)
    await apply_reset(dut)

async def configure_pwm(dut, bus, duty_cycle):
    """Enable PWM on the bus's masked bit, set shared duty-cycle register."""
    await send_spi_transaction(dut, 1, bus.out_reg, PWM_OUTPUT_MASK)
    await send_spi_transaction(dut, 1, bus.pwm_reg, PWM_OUTPUT_MASK)
    await send_spi_transaction(dut, 1, DUTY_REG, duty_cycle)
    await ClockCycles(dut.clk, 10)

def pwm_level(dut, bus):
    """Current value of the bus's masked PWM bit."""
    return int(getattr(dut, bus.signal_name).value) & PWM_OUTPUT_MASK

async def wait_for_pwm_transition(dut, bus, rising):
    """
    Wait for a transition on the bus's PWM bit.

    rising=True waits for a low->high edge, False for high->low, and
    None matches a transition in either direction.
    """
    signal = getattr(dut, bus.signal_name)
    previous = pwm_level(dut, bus)
    while True:
        await Edge(signal)
        current = pwm_level(dut, bus)
        if current != previous and (
            rising is None
            or (rising and current != 0)
            or (not rising and current == 0)
        ):
            return
        previous = current

async def wait_for_edge(dut, bus, rising):
    """Wait for a PWM edge, failing if the signal is stuck."""
    direction = {True: "rising", False: "falling", None: "any"}[rising]
    try:
        await with_timeout(
            wait_for_pwm_transition(dut, bus, rising), EDGE_TIMEOUT_MS, "ms"
        )
    except SimTimeoutError as error:
        raise AssertionError(
            f"Timed out waiting for a {direction} PWM edge on {bus.name}"
        ) from error

async def assert_pwm_steady(dut, bus, expected_level):
    """
    Assert the bus's PWM bit sits at expected_level and does not transition
    for a full PWM period. Stronger than sampling the level once, which would
    accept e.g. 99.6% duty as 100%.
    """
    assert pwm_level(dut, bus) == expected_level, (
        f"Expected {bus.name} PWM bit at {expected_level}, "
        f"got {pwm_level(dut, bus)}"
    )
    try:
        await with_timeout(
            wait_for_pwm_transition(dut, bus, None), PWM_STEADY_US, "us"
        )
    except SimTimeoutError:
        pass  # no transition within a full period, which is what we want
    else:
        raise AssertionError(
            f"Expected {bus.name} PWM bit to hold at {expected_level}, "
            f"but it transitioned"
        )
    assert pwm_level(dut, bus) == expected_level, (
        f"Expected {bus.name} PWM bit to remain at {expected_level}, "
        f"got {pwm_level(dut, bus)}"
    )


@cocotb.test()
async def test_spi(dut):
    dut._log.info("Start SPI test")

    dut._log.info("Reset")
    await reset_dut(dut)

    dut._log.info("Test project behavior")
    dut._log.info("Write transaction, address 0x00, data 0xF0")
    await send_spi_transaction(dut, 1, 0x00, 0xF0)  # Write transaction
    assert dut.uo_out.value == 0xF0, f"Expected 0xF0, got {dut.uo_out.value}"
    await ClockCycles(dut.clk, 1000)

    dut._log.info("Write transaction, address 0x01, data 0xCC")
    await send_spi_transaction(dut, 1, 0x01, 0xCC)  # Write transaction
    assert dut.uio_out.value == 0xCC, f"Expected 0xCC, got {dut.uio_out.value}"
    await ClockCycles(dut.clk, 100)

    dut._log.info("Write transaction, address 0x30 (invalid), data 0xAA")
    await send_spi_transaction(dut, 1, 0x30, 0xAA)
    await ClockCycles(dut.clk, 100)

    dut._log.info("Read transaction (invalid), address 0x00, data 0xBE")
    await send_spi_transaction(dut, 0, 0x30, 0xBE)
    assert dut.uo_out.value == 0xF0, f"Expected 0xF0, got {dut.uo_out.value}"
    await ClockCycles(dut.clk, 100)

    dut._log.info("Read transaction (invalid), address 0x41 (invalid), data 0xEF")
    await send_spi_transaction(dut, 0, 0x41, 0xEF)
    await ClockCycles(dut.clk, 100)

    dut._log.info("Write transaction, address 0x02, data 0xFF")
    await send_spi_transaction(dut, 1, 0x02, 0xFF)  # Write transaction
    await ClockCycles(dut.clk, 100)

    dut._log.info("SPI test completed successfully")

@cocotb.test()
async def test_pwm_freq(dut):
    start_clock(dut)

    for bus in PWM_BUSES:
        dut._log.info(f"PWM frequency on {bus.name}")
        await apply_reset(dut)
        await configure_pwm(dut, bus, 0x80)

        await wait_for_edge(dut, bus, True)
        first_edge_ns = cocotb.utils.get_sim_time(units="ns")
        await wait_for_edge(dut, bus, True)
        second_edge_ns = cocotb.utils.get_sim_time(units="ns")

        period_ns = second_edge_ns - first_edge_ns
        measured_frequency = 1e9 / period_ns
        lower_bound = EXPECTED_PWM_FREQUENCY_HZ * (1 - PWM_FREQUENCY_TOLERANCE)
        upper_bound = EXPECTED_PWM_FREQUENCY_HZ * (1 + PWM_FREQUENCY_TOLERANCE)

        assert lower_bound <= measured_frequency <= upper_bound, (
            f"Expected {bus.name} PWM frequency between {lower_bound:.0f} and "
            f"{upper_bound:.0f} Hz, got {measured_frequency:.2f} Hz"
        )

@cocotb.test()
async def test_pwm_duty(dut):
    start_clock(dut)

    for bus in PWM_BUSES:
        dut._log.info(f"PWM duty cycle on {bus.name}")
        await apply_reset(dut)

        # Zero duty must stay low for a full PWM period.
        await configure_pwm(dut, bus, 0x00)
        await assert_pwm_steady(dut, bus, 0)

        # Measure the high-time ratio at 50% duty.
        await configure_pwm(dut, bus, 0x80)
        await wait_for_edge(dut, bus, True)
        rising_edge_ns = cocotb.utils.get_sim_time(units="ns")
        await wait_for_edge(dut, bus, False)
        falling_edge_ns = cocotb.utils.get_sim_time(units="ns")
        await wait_for_edge(dut, bus, True)
        next_rising_edge_ns = cocotb.utils.get_sim_time(units="ns")

        high_time_ns = falling_edge_ns - rising_edge_ns
        period_ns = next_rising_edge_ns - rising_edge_ns
        measured_duty = high_time_ns / period_ns
        assert 0.49 <= measured_duty <= 0.51, (
            f"Expected approximately 50% duty cycle on {bus.name}, "
            f"got {measured_duty:.3%}"
        )

        # Full duty must stay high for a full PWM period.
        await configure_pwm(dut, bus, 0xFF)
        await assert_pwm_steady(dut, bus, PWM_OUTPUT_MASK)

@cocotb.test()
async def test_pwm_output_enable_override(dut):
    """A cleared output-enable bit forces the output low even with PWM enabled."""
    start_clock(dut)

    for bus in PWM_BUSES:
        dut._log.info(f"Output enable override on {bus.name}")
        await apply_reset(dut)

        # Enable PWM mode and set a mid duty cycle, but leave the output
        # enable register at its 0x00 reset value.
        await send_spi_transaction(dut, 1, bus.pwm_reg, PWM_OUTPUT_MASK)
        await send_spi_transaction(dut, 1, DUTY_REG, 0x80)
        await ClockCycles(dut.clk, 10)

        await assert_pwm_steady(dut, bus, 0)
