package chipyard.fpga.arty100t

import org.chipsalliance.cde.config.Config

// Project minsoc-fmax: cả SoC MinSocFmaxConfig (giữ L2 128 KiB, KHÔNG dùng WithBroadcastManager
// như RocketArty100TConfig) trên harness Arty A7-100T, một clock cho cả SoC ở freqMHz.
// Mixin chỉ dành cho FPGA (nếu loop thêm) đặt trong WithMinSocFpgaTweaks, để không lẫn sang ASIC.
class WithMinSocFpgaTweaks extends Config((site, here, up) => PartialFunction.empty)

class MinSocArty100TF100Config extends Config(
  new WithArty100TTweaks(freqMHz = 100) ++ new WithMinSocFpgaTweaks ++ new chipyard.MinSocFmaxConfig)

class MinSocArty100TF150Config extends Config(
  new WithArty100TTweaks(freqMHz = 150) ++ new WithMinSocFpgaTweaks ++ new chipyard.MinSocFmaxConfig)

class MinSocArty100TF200Config extends Config(
  new WithArty100TTweaks(freqMHz = 200) ++ new WithMinSocFpgaTweaks ++ new chipyard.MinSocFmaxConfig)
