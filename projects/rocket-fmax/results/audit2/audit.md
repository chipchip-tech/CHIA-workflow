# Config audit: RocketConfig

Area ChipTop (Yosys, sky130hd, không tính RocketTile và SRAM): 1183581 um2. SRAM: 2143744 bit.

| Ứng viên | Nhóm | Fragment | Kết luận | Khối bảo vệ bị đụng | Area bớt (um2, %) | SRAM bit bớt | mcycle đổi | Chip thật cần? |
|---|---|---|---|---|---|---|---|---|
| mbus_scratchpad | memory | `new testchipip.soc.WithNoScratchpads` | bỏ được (gate pass, cycle giữ nguyên) | - | 39592 (3.35) | 532480 | - | no - general-purpose extra memory not required for the two target flows (FPGA/ASIC) described; can be omitted unless software explicitly depends on it. |
| bus_error_devices | peripheral | `new chipyard.config.WithNoBusErrorDevices` | bỏ được (gate pass, cycle giữ nguyên) | - | 21636 (1.83) | 0 | - | no - useful for debugging/bringing up but not strictly required for the specified FPGA/ASIC flows; can be removed to save area if not needed. |
| combined | all | `new testchipip.soc.WithNoScratchpads ++ new chipyard.config.WithNoBusErrorDevices` | bỏ được (gate pass, cycle giữ nguyên) | - | 61207 (5.17) | 532480 | - |  |

## Config xuất ra

`/home/dingus/rocket-fmax-out/audit2/patches/chipyard-config.patch`: thêm `RocketFmaxSoCConfig` (`generators/chipyard/src/main/scala/config/RocketFmaxSoC.scala`) và `RocketFmaxSoCArty100TConfig` (`fpga/src/main/scala/arty100t/RocketFmaxSoCArty100T.scala`). Áp bằng `git -C /home/ray/chipyard apply chipyard-config.patch`, cùng `best.patch` trong rocket-chip.

Gate còn treo (chưa chạy): Vivado synth/timing trên Arty A7-100T (50 MHz) và elaborate `RocketFmaxSoCArty100TConfig`; OpenROAD flow scripts (sky130) cho `ChipTop`. Adder viết tay trong `best.patch` cần kiểm lại timing trên FPGA (carry chain cứng).

## Khối bảo vệ (FPGA/ASIC)

```
block        FPGA Arty A7-100T        ASIC (ORFS)                      found in baseline ChipTop
debug        JTAG load/debug          JTAG silicon bring-up            5 ports, modules: TLDebugModule, TLDebugModuleInner, TLDebugModuleInnerAsync, TLDebugModuleOuter
uart         console (PMOD JD)        console                          2 ports, modules: TLUART, TLUARTClockSinkDomain
serial_tl    (tied off)               bring-up host, off-chip memory   7 ports, modules: TLSerdesser_serial_tl_0
mem_port     DDR (Arty: TL mem port)  kept, same SoC                   38 ports, modules: -
bootrom      boot                     boot                             0 ports, modules: TLROM
clint        software timer           software timer                   0 ports, modules: CLINT, CLINTClockSinkDomain
plic         interrupts               interrupts                       0 ports, modules: TLPLIC
boot_select  (unused)                 boot-select pin, boot-addr reg   1 ports, modules: TLFragmenter_BootAddrReg
clock_tap    (unused)                 clock measurement                1 ports, modules: -
clock_reset  Arty: passthrough        PLL/divider, reset sync          2 ports, modules: AsyncResetSynchronizerPrimitiveShiftReg_d3_i0, AsyncResetSynchronizerShiftReg_w1_d3_i0, AsyncResetSynchronizerShiftReg_w1_d3_i0_1, AsyncResetSynchronizerShiftReg_w4_d3_i0
```

## Bằng chứng của agent

- **mbus_scratchpad** (ChipTop.system.bank (ScratchpadBank), 64 KiB mbus scratchpad): On-chip mbus scratchpad added by default; programs are loaded over TSI into backing DRAM and reported via HTIF/tohost through the serial-TL/mem path. No obvious usage by riscv-tests/benchmarks in the standard harness flow; removing all banked scratchpads via WithNoScratchpads is used by other configs (e.g. TinyRocketConfig).
- **bus_error_devices** (ChipTop.system.cbus.wrapped_error_device (ErrorDeviceWrapper) and similar error devices on buses): WithNoBusErrorDevices sets errorDevice=None on SystemBusKey/ControlBusKey/PeripheryBusKey/MemoryBusKey/FrontBusKey and exists in fragments; used by NoCoreConfigs. Error devices catch unmapped accesses but standard riscv-tests/benchmarks should not require them; removing should not affect normal execution paths in the harness.

## Báo cáo đầy đủ của agent

(không có)
