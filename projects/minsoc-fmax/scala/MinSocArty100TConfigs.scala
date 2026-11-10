package chipyard.fpga.arty100t

import org.chipsalliance.cde.config.Config

// Project minsoc-fmax: cả SoC MinSocFmaxConfig (giữ L2 128 KiB, KHÔNG dùng WithBroadcastManager
// như RocketArty100TConfig) trên harness Arty A7-100T của fork RISCVTutorial.
// WithArty100TTweaks của fork không có tham số (ghi cứng 50 MHz), nên tần số được đè bằng các mixin
// đứng TRƯỚC nó (mixin đứng trước thắng). Một clock cho cả SoC.
// Mixin chỉ dành cho FPGA đặt trong chipyard.WithMinSocFpgaTweaks (MinSocFmaxConfig.scala), để không lẫn
// sang ASIC và để gate Verilator (MinSocFmaxFpgaConfig) chạy đúng RTL đó.

class WithMinSocFreqMHz(f: Double) extends Config(
  new chipyard.harness.WithHarnessBinderClockFreqMHz(f) ++
  new chipyard.config.WithMemoryBusFrequency(f) ++
  new chipyard.config.WithSystemBusFrequency(f) ++
  new chipyard.config.WithPeripheryBusFrequency(f))

class MinSocArty100TBase(f: Double) extends Config(
  new WithMinSocFreqMHz(f) ++ new WithArty100TTweaks ++ new chipyard.WithMinSocFpgaTweaks ++ new chipyard.MinSocFmaxConfig)

class MinSocArty100TF100Config extends MinSocArty100TBase(100.0)
class MinSocArty100TF150Config extends MinSocArty100TBase(150.0)
class MinSocArty100TF200Config extends MinSocArty100TBase(200.0)
