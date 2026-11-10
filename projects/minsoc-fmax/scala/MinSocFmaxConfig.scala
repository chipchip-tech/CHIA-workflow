package chipyard

import org.chipsalliance.cde.config.Config

// Project minsoc-fmax (CHIA-workflow, nhánh project/minsoc-fmax). Chép vào fork bằng
// projects/minsoc-fmax/install.sh; sửa bản trong repo, không sửa bản trong fork.
// Ngoại lệ: ở Mốc 2 agent sửa bản trong fork (commit lên nhánh minsoc-fmax/agent của fork);
// KHÔNG chạy lại install.sh khi đang có lượt agent được nhận.
//
// Điểm gắn mixin của project. Baseline = chipyard.MinimalSoCConfig upstream.
// - Mixin dùng chung FPGA + ASIC: thêm vào MinSocFmaxConfig.
// - Mixin chỉ dành cho FPGA: thêm vào WithMinSocFpgaTweaks (bỏ ra là tắt).
class WithMinSocFpgaTweaks extends Config((site, here, up) => PartialFunction.empty)

class MinSocFmaxConfig extends Config(
  new chipyard.MinimalSoCConfig)

// Gate Verilator của Mốc 2: cùng RTL với bản Arty (có WithMinSocFpgaTweaks), không có harness FPGA.
class MinSocFmaxFpgaConfig extends Config(
  new WithMinSocFpgaTweaks ++ new MinSocFmaxConfig)
