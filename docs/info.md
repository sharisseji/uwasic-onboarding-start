<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

`ui_in[0]` is SCLK, `ui_in[1]` is COPI, 
and `ui_in[2]` is active-low chip select.
While chip select is low, the peripheral samples 1 bit on each rising SCLK
edge. 

The frame is committed only when chip select returns high, so an
incomplete or invalid-length frame cannot partially update a register. This is what the frame contians:

| Bits | Meaning |
| --- | --- |
| 15 | `1` for write, `0` for read/reserved |
| 14:8 | Register address |
| 7:0 | Data |

Write registers are `0x00` and `0x01` for the lower and upper
output bytes, `0x02` and `0x03` for the corresponding PWM enable masks, and
`0x04` for the PWM duty cycle. Invalid addresses and read frames are ignored.

## How to test

The cocotb testbench sends 16-bit SPI frames and checks the output registers.
Run it from the `test` directory with:

```sh
make -B
```

## External hardware

List external hardware used in your project (e.g. PMOD, LED display, etc), if any
