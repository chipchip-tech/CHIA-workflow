package chipyard

import org.chipsalliance.cde.config.Config

// SoC mẫu tối ưu timing: bare-metal, RV64IMAC, không L2, core cắt gọn.
// Spec: docs/timing-soc-config-spec.md. Cách dùng: docs/chipyard-timing-soc.md.
// Bản đầu có đủ mixin; mixin nào đo không có tác dụng sẽ bị gỡ (project/timing-soc).
//
// Thứ tự: mixin sửa tile đứng trước MinimalSoCConfig (nơi tạo core);
// WithBroadcastManager đứng trước để thắng WithInclusiveCache trong MinimalSoCConfig.
class TimingSoCConfig extends Config(
  new chipyard.WithRocketSlowLoad ++
  new freechips.rocketchip.subsystem.WithL1ICacheWays(1) ++
  new freechips.rocketchip.subsystem.WithL1DCacheWays(1) ++
  new chipyard.WithNoRocketBTB ++
  new chipyard.WithNoRocketVM ++
  new chipyard.WithRocketMulDiv(mulUnroll = 1, earlyOut = false) ++
  new freechips.rocketchip.subsystem.WithoutFPU ++
  new chipyard.config.WithBroadcastManager ++
  new chipyard.MinimalSoCConfig)
