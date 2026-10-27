# Fmax RocketTile sau khi bỏ khối SoC (soc-fmax)

Flow `v2`, chu kỳ cố định 21.2 ns, sky130hd tt. Fragment: `new testchipip.soc.WithNoScratchpads`, `new chipyard.config.WithNoBusErrorDevices`.

| Điểm | Config | Patch | Worst slack (ns) | Đường găng (ns) | Fmax (MHz) | Area tile (um2) | RTL tile (sha) | Đường găng |
|---|---|---|---|---|---|---|---|---|
| base | RocketConfig | - | -1.001 | 22.201 | 45.04 | 1069069 | 9f03310ee4f0f652 | `core.div.divisor[1]_reg` -> `core.div.remainder[63]_reg` |
| cfg | fragment audit | - | -0.911 | 22.111 | 45.23 | 1064271 | 115351c9a631d973 | `core.div.remainder[65]_reg` -> `core.div.remainder[101]_reg` |
| best | RocketConfig | best.patch | 6.980 | 14.220 | 70.32 | 1083776 | 43266a3001476670 | `dcache.pstore1_data[1]_reg` -> `dcache.pstore2_storegen_data_r_7[7]_reg` |
| best_cfg | fragment audit | best.patch | 7.350 | 13.850 | 72.20 | 1078378 | 5c6c6ea04386ceaf | `dcache.s2_data[1]_reg` -> `dcache.pstore2_storegen_data_r_7[7]_reg` |

RTL `RocketTile` (từng module, đã gồm SRAM model):

- base vs cfg: đổi 6: DCache, Frontend, ICache, PMAChecker, PTW, RocketTile; mất TLMonitor_38, TLMonitor_39, TLXbar_MasterXbar_RocketTile_i2_o1_a32d64s2k3z4c; thêm TLMonitor_33, TLMonitor_34, TLXbar_MasterXbar_RocketTile_i2_o1_a32d64s2k3z3c
- best vs best_cfg: đổi 6: DCache, Frontend, ICache, PMAChecker, PTW, RocketTile; mất TLMonitor_38, TLMonitor_39, TLXbar_MasterXbar_RocketTile_i2_o1_a32d64s2k3z4c; thêm TLMonitor_33, TLMonitor_34, TLXbar_MasterXbar_RocketTile_i2_o1_a32d64s2k3z3c

Gate `best_cfg` (so với baseline): ISA pass, mcycle benchmark giữ nguyên.

Area tile ở đây là của luồng `v2` (flatten); area phần ngoài tile xem `audit.md` của audit.
