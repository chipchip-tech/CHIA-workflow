package chipyard

import org.chipsalliance.cde.config.Config
import freechips.rocketchip.subsystem._
import freechips.rocketchip.rocket.MulDivParams

// Mixin cắt gọn RocketTile để rút ngắn đường găng (Chipyard 1.10 / rocket-chip 47f7b71).
// Spec: docs/timing-soc-config-spec.md. Cách dùng: docs/chipyard-timing-soc.md.
// Mỗi mixin sửa các Rocket tile đã có trong TilesLocated, nên phải đứng TRƯỚC (bên trái)
// mixin tạo core (WithNBigCores...) trong chuỗi `++`. Tile không phải Rocket giữ nguyên.

// Bộ nhân/chia lặp không early-out. Mặc định của MulDivParams là (1, 1, false, false);
// WithNBigCores đẩy lên mulUnroll = 8 và bật early-out, nằm trên đường găng core.div.
// Giá: mul tới 64 chu kỳ khi mulUnroll = 1. Vẫn đủ lệnh M.
class WithRocketMulDiv(mulUnroll: Int = 1, earlyOut: Boolean = false) extends Config((site, here, up) => {
  case TilesLocated(InSubsystem) => up(TilesLocated(InSubsystem), site) map {
    case tp: RocketTileAttachParams => tp.copy(tileParams = tp.tileParams.copy(
      core = tp.tileParams.core.copy(mulDiv = Some(MulDivParams(
        mulUnroll = mulUnroll,
        divUnroll = 1,
        mulEarlyOut = earlyOut,
        divEarlyOut = earlyOut)))))
    case other => other
  }
})

// Bỏ MMU (TLB, page-table walker) và L2 TLB (AbstractConfig 1.10 bật 1024 entry).
// Giá: không Linux, không chế độ S; chỉ chạy được test dạng -p-.
class WithNoRocketVM extends Config((site, here, up) => {
  case TilesLocated(InSubsystem) => up(TilesLocated(InSubsystem), site) map {
    case tp: RocketTileAttachParams => tp.copy(tileParams = tp.tileParams.copy(
      core = tp.tileParams.core.copy(useVM = false, nL2TLBEntries = 0)))
    case other => other
  }
})

// Bỏ dự đoán rẽ nhánh (BTB, BHT, RAS), giống WithNMedCores/WithNSmallCores.
// Giá: phạt rẽ nhánh mỗi lần nhảy.
class WithNoRocketBTB extends Config((site, here, up) => {
  case TilesLocated(InSubsystem) => up(TilesLocated(InSubsystem), site) map {
    case tp: RocketTileAttachParams => tp.copy(tileParams = tp.tileParams.copy(btb = None))
    case other => other
  }
})

// Tắt đường bypass load word nhanh (fastLoadWord), nhắm họ dcache -> core.mem_reg_wdata.
// Giá: thêm 1 chu kỳ load-use.
class WithRocketSlowLoad extends Config((site, here, up) => {
  case TilesLocated(InSubsystem) => up(TilesLocated(InSubsystem), site) map {
    case tp: RocketTileAttachParams => tp.copy(tileParams = tp.tileParams.copy(
      core = tp.tileParams.core.copy(fastLoadWord = false)))
    case other => other
  }
})
