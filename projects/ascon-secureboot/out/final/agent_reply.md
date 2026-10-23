Implemented the Ascon-Hash256 RoCC accelerator and config, without touching any build artifacts or the provided SystemVerilog core.

Files created/changed (all under `generators/chipyard/`):

- **Created `src/main/scala/example/AsconRoCC.scala`**
  - `AsconCoreV4` BlackBox with the required port names and `addResource("/vsrc/AsconCoreV4.sv")`.
  - `AsconRoCC` / `AsconRoCCModuleImp` implementing:
    - `INIT`: one-cycle core reset, digest-buffer clear, one-cycle `mode = 3` start pulse.
    - `ABSORB`: backpressure via `cmd.ready` until `bdi_ready`; correct `bdi_valid`, `bdi_type = 3`, `bdi_eot/eoi` handling; no response.
    - `SQUEEZE`: captures the four output digest words into a 4×64-bit buffer, holds `resp.valid` until the requested word is available and `resp.ready` is asserted.
    - `io.busy`, `io.interrupt`, and `io.mem.req.valid` tied as specified.

- **Modified `src/main/scala/config/fragments/RoCCFragments.scala`**
  - Added `WithAsconRoCC`, defaulting to `OpcodeSet.custom0`.

- **Created `src/main/scala/config/AsconConfigs.scala`**
  - Added `chipyard.AsconRocketConfig` as `RocketConfig + WithAsconRoCC`.

`src/main/resources/vsrc/AsconCoreV4.sv` was left unchanged.