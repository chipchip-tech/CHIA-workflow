`timescale 1ns/1ps
`include "ascon_pkg.svh"  // data_t, D_* (cũng dùng khi mô phỏng trên netlist)

module tb_ascon_core;

  // Parameters matching default configuration
  localparam int CCW = 32;
  localparam int CCWD8 = CCW / 8;

  // Clock and Reset Signals
  logic              clk;
  logic              rst;

  // DUT Interface Signals
  logic [CCW-1:0]    key;
  logic              key_valid;
  logic              key_ready;

  logic [CCW-1:0]    bdi;
  logic [CCWD8-1:0]  bdi_valid;
  logic              bdi_ready;
  data_t             bdi_type;
  logic              bdi_eot;
  logic              bdi_eoi;
  logic              start;

  logic [CCW-1:0]    bdo;
  logic              bdo_valid;
  logic              bdo_ready;
  logic [3:0]        bdo_type;   // vector, not data_t: the netlist port is a plain [3:0] (gate-level sim)
  logic              bdo_eot;

  logic              auth;
  logic              auth_valid;

  // Instantiate DUT
  ascon_core u_ascon_core (
    .clk        (clk),
    .rst        (rst),
    .key        (key),
    .key_valid  (key_valid),
    .key_ready  (key_ready),
    .bdi        (bdi),
    .bdi_valid  (bdi_valid),
    .bdi_ready  (bdi_ready),
    .bdi_type   (bdi_type),
    .bdi_eot    (bdi_eot),
    .bdi_eoi    (bdi_eoi),
    .start      (start),
    .bdo        (bdo),
    .bdo_valid  (bdo_valid),
    .bdo_ready  (bdo_ready),
    .bdo_type   (bdo_type),
    .bdo_eot    (bdo_eot),
    .auth       (auth),
    .auth_valid (auth_valid)
  );

  // 100 MHz Clock Generation
  always #5 clk = ~clk;

  // =========================================================================
  // TASK: LOAD KEY (Little Endian: Byte 0 -> key[7:0])
  // =========================================================================
  // NOTE: ascon_full.sv's ascon_core has no `mode` port/mode_t type (it's a
  // decrypt-only core). Instead, the IDLE->LD_KEY transition is kicked off
  // by a single-cycle `start` pulse, asserted here together with the first
  // key word / key_valid.
  task automatic send_key(input logic [7:0] key_in[16]);
    integer k, kk;
    logic [CCW-1:0] key_tmp;
    bit first_beat;
    begin
      k = 0;
      first_beat = 1'b1;
      while (k < 16) begin
        key_tmp = '0;
        for (kk = 0; kk < CCWD8 && (k + kk) < 16; kk++) begin
          key_tmp[kk*8 +: 8] = key_in[k + kk];
        end

        @(negedge clk);
        key       = key_tmp;
        key_valid = 1'b1;
        start     = first_beat; // pulse start while core is still in IDLE

        @(posedge clk);

        first_beat = 1'b0;

        if (key_ready) begin
          k = k + CCWD8;
        end
      end

      @(negedge clk);
      key       = '0;
      key_valid = 1'b0;
      start     = 1'b0;
    end
  endtask

  // =========================================================================
  // TASK: SEND BDI DATA (Little Endian: Byte 0 -> bdi[7:0])
  // =========================================================================
  task automatic send_data(
    input logic [7:0] data_in[],
    input data_t      d_type,
    input logic       is_eoi
  );
    integer d, dd, dlen;
    logic [CCW-1:0]   bdi_tmp;
    logic [CCWD8-1:0] valid_tmp;
    begin
      dlen = data_in.size();
      d = 0;

      if (dlen == 0) begin
        @(negedge clk);
        bdi       = '0;
        bdi_valid = '0;
        bdi_type  = d_type;
        bdi_eot   = 1'b1;
        bdi_eoi   = is_eoi;
        @(posedge clk);
      end else begin
        while (d < dlen) begin
          bdi_tmp   = '0;
          valid_tmp = '0;
          for (dd = 0; dd < CCWD8 && (d + dd) < dlen; dd++) begin
            bdi_tmp[dd*8 +: 8] = data_in[d + dd];
            valid_tmp[dd]     = 1'b1;
          end

          @(negedge clk);
          bdi       = bdi_tmp;
          bdi_valid = valid_tmp;
          bdi_type  = d_type;
          bdi_eot   = ((d + CCWD8) >= dlen) ? 1'b1 : 1'b0;
          bdi_eoi   = (((d + CCWD8) >= dlen) && is_eoi) ? 1'b1 : 1'b0;

          @(posedge clk);

          if (bdi_valid != 0 && bdi_ready) begin
            d = d + CCWD8;
          end
        end
      end

      @(negedge clk);
      bdi       = '0;
      bdi_valid = '0;
      bdi_type  = D_INVALID;
      bdi_eot   = 1'b0;
      bdi_eoi   = 1'b0;
    end
  endtask

  // =========================================================================
  // TASK: RECEIVE BDO DATA (Little Endian: Byte 0 <- bdo[7:0])
  // =========================================================================
  task automatic receive_data(
    input  data_t      d_type,
    input  integer     len,
    output logic [7:0] data_out[]
  );
    integer r, i;
    begin
      data_out = new[len];
      r = 0;
      @(negedge clk);
      bdo_ready = 1'b1;

      while (r < len) begin
        @(posedge clk);
        if (bdo_valid && (bdo_type == d_type)) begin
          for (i = 0; i < CCWD8 && (r + i) < len; i++) begin
            data_out[r + i] = bdo[i*8 +: 8];
          end
          r = r + CCWD8;
        end
      end

      @(negedge clk);
      bdo_ready = 1'b0;
    end
  endtask

  // Wait for the core to finish: auth_valid rises in the same cycle the FSM returns to IDLE (VER_TAG -> IDLE).
  // Only ports are used (no u_ascon_core.fsm_q), so the testbench also runs on synthesized netlists.
  task automatic wait_for_idle();
    begin
      @(posedge clk);
      while (!auth_valid) @(posedge clk);
      $display("[%0t ns] [LOG] Core response: auth_valid asserted, FSM back in IDLE state.", $time);
    end
  endtask

  // Helper function to print Byte array in Hex format
  function automatic void print_hex_array(string label, input logic [7:0] arr[]);
    $write("  %-22s: 0x", label);
    for (int i = 0; i < arr.size(); i++) begin
      $write("%02h", arr[i]);
    end
    $write("\n");
  endfunction

  // Helper function for comparison and result reporting
  function automatic void compare_result(
    input string step_name,
    input logic [7:0] actual[],
    input logic [7:0] expected[]
  );
    bit match = 1;
    if (actual.size() != expected.size()) begin
      match = 0;
    end else begin
      for (int i = 0; i < actual.size(); i++) begin
        if (actual[i] !== expected[i]) match = 0;
      end
    end

    if (match) begin
      $display("  [PASS] %s: MATCH!", step_name);
    end else begin
      $display("  [FAIL] %s: MISMATCH!", step_name);
      $write("    Expected: ");
      for (int i = 0; i < expected.size(); i++) $write("%02h ", expected[i]);
      $write("\n    Actual:   ");
      for (int i = 0; i < actual.size(); i++) $write("%02h ", actual[i]);
      $write("\n");
    end
  endfunction

  // =========================================================================
  // MAIN TESTBENCH EXECUTION
  // =========================================================================
  initial begin
    // Output data buffer
    logic [7:0] pt_out[];

    // Test vectors
    automatic logic [7:0] key_bytes[16] = '{'h00,'h01,'h02,'h03,'h04,'h05,'h06,'h07,'h08,'h09,'h0A,'h0B,'h0C,'h0D,'h0E,'h0F};
    automatic logic [7:0] npub[]      = '{'h00,'h01,'h02,'h03,'h04,'h05,'h06,'h07,'h08,'h09,'h0A,'h0B,'h0C,'h0D,'h0E,'h0F};
    automatic logic [7:0] ad[]        = '{'h41,'h42,'h43,'h44,'h45,'h46,'h47,'h48}; // "ABCDEFGH"
    automatic logic [7:0] pt[]        = '{'h11,'h22,'h33,'h44,'h55,'h66,'h77,'h88};

    // Known-answer AEAD-128 ciphertext/tag for the (key, npub, ad, pt) above.
    // ascon_full.sv's ascon_core only implements the *decrypt* datapath
    // (see FSM comment: "no encrypt path"), so instead of exercising an
    // encrypt mode that the core cannot run, we feed this known-correct
    // ciphertext/tag pair straight into the decrypt/verify path.
    automatic logic [7:0] exp_ct[]   = '{'h2f,'hde,'h0a,'h68,'h4f,'ha6,'h03,'h0f};
    automatic logic [7:0] exp_tag[] = '{'h36,'h2d,'h89,'h2e,'h86,'h02,'hf8,'h31,'hfd,'he8,'h8d,'h49,'hdd,'hd7,'hbf,'hdc};

    $dumpfile("tb_ascon_core.vcd");
    $dumpvars(0, tb_ascon_core);

    // Initial reset sequence
    clk       = 1'b0;
    rst       = 1'b0;
    key       = '0;
    key_valid = 1'b0;
    bdi       = '0;
    bdi_valid = '0;
    bdi_type  = D_INVALID;
    bdi_eot   = 1'b0;
    bdi_eoi   = 1'b0;
    start     = 1'b0;
    bdo_ready = 1'b0;

    // Hold reset for 100 ns before any stimulus: in gate-level simulation of the Xilinx netlist every flop is held in
    // global reset (GSR, glbl.v) for the first 100 ns, as in Vivado-generated testbenches ("wait 100 ns for global
    // reset to finish"). Same behaviour in RTL simulation, only later.
    #100;
    rst = 1'b1;
    #20;

    $display("==========================================================================");
    $display("            STARTING ASCON CORE RTL FULL SYSTEM SIMULATION               ");
    $display("==========================================================================");

    // =========================================================================
    // AEAD-128 DECRYPTION & TAG VERIFICATION (Known-Answer Test)
    //
    // ascon_full.sv's ascon_core is a decrypt-only core (its FSM only
    // implements one straight-line pass through the AEAD128 decrypt
    // datapath, and ABS_MSG implements the ciphertext -> plaintext
    // conversion). So there is no encrypt stage
    // here: we feed the known-correct ciphertext/tag pair (exp_ct/exp_tag)
    // straight in and check that the core recovers the original plaintext
    // and reports the tag as authentic.
    // =========================================================================
    $display("\n==========================================================================");
    $display(" AEAD-128 DECRYPTION & TAG VERIFICATION");
    $display("==========================================================================");

    $display("[%0t ns] 1. Pulsing start & loading Key (ascon_full core is decrypt-only)...", $time);
    send_key(key_bytes);

    $display("[%0t ns] 2. Sending Nonce (D_NONCE)...", $time);
    send_data(npub, D_NONCE, 1'b0);

    $display("[%0t ns] 3. Sending Associated Data (D_AD)...", $time);
    send_data(ad, D_AD, 1'b0);

    // eoi marks the end of the *input* the core absorbs into its state, which
    // is the last ciphertext word -- not the tag. The tag is only compared
    // against what the core already computed, so it carries eot but no eoi.
    // This matches ascon_secure_boot_demo.c: BDI_EOT | BDI_EOI on the last
    // MSG word in msg_xfer(), EOT only in send_tag().
    //
    // The core needs this whenever a message fills the 16-byte rate exactly:
    // ABS_MSG then leaves via PRO_MSG, which only advances to PAD_MSG once
    // eoi_q is set, otherwise it waits forever for more ciphertext. This
    // 8-byte vector takes the partial-block path and reaches KADD_3 either
    // way, so the flags below are about protocol agreement with the firmware,
    // not about making this particular vector pass. tb_ascon_secure.sv covers
    // the exact-rate case with a 16-byte message.
    $display("[%0t ns] 4. Sending Ciphertext (D_MSG) & receiving Plaintext...", $time);
    fork
      send_data(exp_ct, D_MSG, 1'b1); // eoi = 1: last input the core absorbs
      #1
      receive_data(D_MSG, 8, pt_out);
    join

    $display("[%0t ns] 5. Sending Tag (D_TAG) to RTL for verification...", $time);
    send_data(exp_tag, D_TAG, 1'b0); // eot only; VER_TAG never inspects eoi

    wait_for_idle();

    $display("\n--- RESULTS ---");
    print_hex_array("Ciphertext Input", exp_ct);
    print_hex_array("Plaintext (Actual)", pt_out);
    print_hex_array("Plaintext (Expected)", pt);

    compare_result("Decrypted Plaintext Match", pt_out, pt);

    if (auth_valid && auth) begin
      $display("  [PASS] Tag Verification SUCCESS: Data is authentic and intact!");
    end else begin
      $display("  [FAIL] Tag Verification FAILED! (auth_valid=%b, auth=%b)", auth_valid, auth);
    end


    // =========================================================================
    // SIMULATION SUMMARY
    // =========================================================================
    #100;
    $display("\n==========================================================================");
    $display(" ASCON-AEAD128 CORE RTL SIMULATION COMPLETED!");
    $display(" Inspect wave details in file: tb_ascon_core.vcd");
    $display("==========================================================================");
    $finish;
  end

endmodule
