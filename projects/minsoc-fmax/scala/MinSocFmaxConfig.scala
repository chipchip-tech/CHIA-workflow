package chipyard

import org.chipsalliance.cde.config.Config

// Project minsoc-fmax (CHIA-workflow, nhánh project/minsoc-fmax). Chép vào fork bằng
// projects/minsoc-fmax/install.sh; sửa bản trong repo, không sửa bản trong fork.
//
// Điểm gắn mixin của project. Hiện chưa thêm gì: baseline = chipyard.MinimalSoCConfig upstream.
// Thay đổi chỉ dành cho FPGA hoặc chỉ cho ASIC KHÔNG đặt ở đây (xem MinSocArty100TConfigs.scala).
class MinSocFmaxConfig extends Config(
  new chipyard.MinimalSoCConfig)
