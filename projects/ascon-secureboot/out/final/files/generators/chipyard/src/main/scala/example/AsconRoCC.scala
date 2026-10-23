package chipyard.example

import chisel3._
import chisel3.util._
import freechips.rocketchip.tile._
import org.chipsalliance.cde.config.Parameters

/** Thin Chisel wrapper around the SystemVerilog Ascon-Hash256 core. */
class AsconCoreV4 extends BlackBox with HasBlackBoxResource {
  val io = IO(new Bundle {
    val clk       = Input(Clock())
    val rst       = Input(Bool())
    val key       = Input(UInt(64.W))
    val key_valid = Input(Bool())
    val key_ready = Output(Bool())
    val bdi       = Input(UInt(64.W))
    val bdi_valid = Input(UInt(8.W))
    val bdi_ready = Output(Bool())
    val bdi_type  = Input(UInt(4.W))
    val bdi_eot   = Input(Bool())
    val bdi_eoi   = Input(Bool())
    val mode      = Input(UInt(4.W))
    val bdo       = Output(UInt(64.W))
    val bdo_valid = Output(Bool())
    val bdo_ready = Input(Bool())
    val bdo_type  = Output(UInt(4.W))
    val bdo_eot   = Output(Bool())
    val bdo_eoo   = Input(Bool())
    val auth      = Output(Bool())
    val auth_valid = Output(Bool())
  })

  addResource("/vsrc/AsconCoreV4.sv")
}

class AsconRoCC(opcodes: OpcodeSet)(implicit p: Parameters) extends LazyRoCC(opcodes) {
  override lazy val module = new AsconRoCCModuleImp(this)
}

class AsconRoCCModuleImp(outer: AsconRoCC)(implicit p: Parameters) extends LazyRoCCModuleImp(outer) {
  val core = Module(new AsconCoreV4)

  val s_idle      :: s_init_rst :: s_init_start :: s_resp :: Nil = Enum(4)
  val state = RegInit(s_idle)

  val cmd   = io.cmd
  val funct = cmd.bits.inst.funct
  val isInit    = funct === 0.U
  val isAbsorb  = funct === 1.U
  val isSqueeze = funct === 2.U

  val nbytes = cmd.bits.rs2(7, 0)
  val last   = cmd.bits.rs2(8)
  // nbytes is in 1..8; for 8 this evaluates to 0xff.
  val bdiValidMask = (0xff.U >> (8.U - nbytes))(7, 0)

  val digest       = RegInit(VecInit(Seq.fill(4)(0.U(64.W))))
  val digestValid  = RegInit(VecInit(Seq.fill(4)(false.B)))
  val captureIdx   = RegInit(0.U(2.W))
  val respIdx      = RegInit(0.U(2.W))
  val respRd       = Reg(chiselTypeOf(io.resp.bits.rd))
  val hashActive   = RegInit(false.B)

  // Accept an ABSORB only when the core can consume it in this cycle.  Other
  // commands are accepted while idle and then held by the state machine until
  // they have completed.
  cmd.ready := (state === s_idle) && (!isAbsorb || core.io.bdi_ready)

  when (cmd.fire) {
    when (isInit) {
      state       := s_init_rst
      captureIdx  := 0.U
      hashActive  := false.B
      for (i <- 0 until 4) {
        digestValid(i) := false.B
      }
    }
    when (isSqueeze) {
      respIdx := cmd.bits.rs1(1, 0)
      respRd  := cmd.bits.inst.rd
      state   := s_resp
    }
    when (isAbsorb) {
      hashActive := true.B
    }
  }

  when (state === s_init_rst) {
    state      := s_init_start
    captureIdx := 0.U
    for (i <- 0 until 4) {
      digestValid(i) := false.B
    }
  }
  when (state === s_init_start) {
    state      := s_idle
    hashActive := true.B
  }

  when (io.resp.fire) {
    state := s_idle
  }

  // Core connection.  The reset pulse starts one cycle after INIT is accepted;
  // mode is then asserted for exactly one cycle to start the hash.
  val coreRst = reset.asBool || (state === s_init_rst)
  core.io.clk       := clock
  core.io.rst       := coreRst
  core.io.key       := 0.U
  core.io.key_valid := false.B
  core.io.bdi       := cmd.bits.rs1
  core.io.bdi_valid := Mux(cmd.fire && isAbsorb, bdiValidMask, 0.U)
  core.io.bdi_type  := 3.U
  core.io.bdi_eot   := cmd.fire && isAbsorb && last
  core.io.bdi_eoi   := cmd.fire && isAbsorb && last
  core.io.mode      := Mux(state === s_init_start, 3.U, 0.U)
  core.io.bdo_ready := true.B
  core.io.bdo_eoo   := false.B

  // Capture the digest as the core squeezes it out.
  when (core.io.bdo_valid && core.io.bdo_ready &&
        (core.io.bdo_type === 5.U) && !coreRst) {
    digest(captureIdx)      := core.io.bdo
    digestValid(captureIdx) := true.B
    captureIdx              := captureIdx + 1.U
    when (captureIdx === 3.U) {
      hashActive := false.B
    }
  }

  io.resp.valid     := (state === s_resp) && digestValid(respIdx)
  io.resp.bits.rd   := respRd
  io.resp.bits.data := digest(respIdx)

  io.busy         := (state =/= s_idle) || hashActive || cmd.valid
  io.interrupt    := false.B
  io.mem.req.valid := false.B
}
