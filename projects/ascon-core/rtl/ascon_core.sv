`ifndef INCL_ASCON_CORE_COMBINED
`define INCL_ASCON_CORE_COMBINED

`include "ascon_pkg.svh"

// Generic Register Module
module register #(parameter DATA_WIDTH, parameter RST_VALUE = DATA_WIDTH'('d0)) (
  clk,
  rst,
  data_d, 
  data_q  
);

  input logic clk;
  input logic rst;
  input  logic[DATA_WIDTH-1:0] data_d;
  output logic[DATA_WIDTH-1:0] data_q;

  always_ff @(posedge clk, negedge rst) begin : register_update
    if (!rst) begin
      data_q <= RST_VALUE;
    end else begin
      data_q <= data_d;
    end
  end

endmodule

// Ascon Permutation Module 
// Implementation of the Ascon permutation (Ascon-p).
// Performs UROL rounds per clock cycle.
module asconp (
    input  logic [ 3:0] round_cnt,
    input  logic [63:0] x0_i,
    input  logic [63:0] x1_i,
    input  logic [63:0] x2_i,
    input  logic [63:0] x3_i,
    input  logic [63:0] x4_i,
    output logic [63:0] x0_o,
    output logic [63:0] x1_o,
    output logic [63:0] x2_o,
    output logic [63:0] x3_o,
    output logic [63:0] x4_o
);

  logic [UROL-1:0][63:0] x0_aff1, x0_chi, x0_aff2;
  logic [UROL-1:0][63:0] x1_aff1, x1_chi, x1_aff2;
  logic [UROL-1:0][63:0] x2_aff1, x2_chi, x2_aff2;
  logic [UROL-1:0][63:0] x3_aff1, x3_chi, x3_aff2;
  logic [UROL-1:0][63:0] x4_aff1, x4_chi, x4_aff2;
  logic [UROL : 0][63:0] x0, x1, x2, x3, x4;
  logic [UROL-1:0][3:0] t;

  assign x0[0] = x0_i;
  assign x1[0] = x1_i;
  assign x2[0] = x2_i;
  assign x3[0] = x3_i;
  assign x4[0] = x4_i;

  genvar i;
  generate
    for (i = 0; i < UROL; i++) begin : g_asconp
      // 1st affine layer
      assign t[i] = (4'hC) - (round_cnt - i);
      assign x0_aff1[i] = x0[i] ^ x4[i];
      assign x1_aff1[i] = x1[i];
      assign x2_aff1[i] = x2[i] ^ x1[i] ^ {56'd0, (4'hF - t[i]), t[i]};
      assign x3_aff1[i] = x3[i];
      assign x4_aff1[i] = x4[i] ^ x3[i];
      // non-linear chi layer
      assign x0_chi[i] = x0_aff1[i] ^ ((~x1_aff1[i]) & x2_aff1[i]);
      assign x1_chi[i] = x1_aff1[i] ^ ((~x2_aff1[i]) & x3_aff1[i]);
      assign x2_chi[i] = x2_aff1[i] ^ ((~x3_aff1[i]) & x4_aff1[i]);
      assign x3_chi[i] = x3_aff1[i] ^ ((~x4_aff1[i]) & x0_aff1[i]);
      assign x4_chi[i] = x4_aff1[i] ^ ((~x0_aff1[i]) & x1_aff1[i]);
      // 2nd affine layer
      assign x0_aff2[i] = x0_chi[i] ^ x4_chi[i];
      assign x1_aff2[i] = x1_chi[i] ^ x0_chi[i];
      assign x2_aff2[i] = ~x2_chi[i];
      assign x3_aff2[i] = x3_chi[i] ^ x2_chi[i];
      assign x4_aff2[i] = x4_chi[i];
      // linear layer
      assign x0[i+1] = x0_aff2[i] ^ {x0_aff2[i][18:0], x0_aff2[i][63:19]} ^ {x0_aff2[i][27:0], x0_aff2[i][63:28]};
      assign x1[i+1] = x1_aff2[i] ^ {x1_aff2[i][60:0], x1_aff2[i][63:61]} ^ {x1_aff2[i][38:0], x1_aff2[i][63:39]};
      assign x2[i+1] = x2_aff2[i] ^ {x2_aff2[i][0:0], x2_aff2[i][63:01]} ^ {x2_aff2[i][05:0], x2_aff2[i][63:06]};
      assign x3[i+1] = x3_aff2[i] ^ {x3_aff2[i][9:0], x3_aff2[i][63:10]} ^ {x3_aff2[i][16:0], x3_aff2[i][63:17]};
      assign x4[i+1] = x4_aff2[i] ^ {x4_aff2[i][6:0], x4_aff2[i][63:07]} ^ {x4_aff2[i][40:0], x4_aff2[i][63:41]};
    end
  endgenerate

  assign x0_o = x0[UROL];
  assign x1_o = x1[UROL];
  assign x2_o = x2[UROL];
  assign x3_o = x3[UROL];
  assign x4_o = x4[UROL];

endmodule

// Ascon Permutation Module
module ascon_core (
    input  logic              clk,
    input  logic              rst,
    input  logic  [  CCW-1:0] key,
    input  logic              key_valid,
    output logic              key_ready,
    input  logic  [  CCW-1:0] bdi,
    input  logic  [CCW/8-1:0] bdi_valid,
    output logic              bdi_ready,
    input  data_t             bdi_type,
    input  logic              bdi_eot,
    input  logic              bdi_eoi,
    input  logic              start,
    output logic  [  CCW-1:0] bdo,
    output logic              bdo_valid,
    input  logic              bdo_ready,
    output data_t             bdo_type,
    output logic              bdo_eot,
    output logic              auth,
    output logic              auth_valid
);

  typedef enum logic [3:0] {
    IDLE    = 'd0,
    LD_KEY  = 'd1,
    LD_NPUB = 'd2,
    INIT    = 'd3,
    KADD_2  = 'd4,
    ABS_AD  = 'd5,
    PAD_AD  = 'd6,
    PRO_AD  = 'd7,
    DOM_SEP = 'd8,
    ABS_MSG = 'd9,
    PAD_MSG = 'd10,
    PRO_MSG = 'd11,
    KADD_3  = 'd12,
    FINAL   = 'd13,
    KADD_4  = 'd14,
    VER_TAG = 'd15
  } fsm_t;

  // Register signals
  logic [W128-1:0][CCW-1:0] key_d, key_q;
  logic [LANES-1:0][W64-1:0][CCW-1:0] state_d, state_q;
  logic [3:0] round_cnt_d, round_cnt_q;
  logic [3:0] word_cnt_d, word_cnt_q;
  fsm_t fsm_d, fsm_q;
  logic auth_d, auth_q;
  logic auth_intern_d, auth_intern_q;
  logic auth_valid_d, auth_valid_q;
  logic ad_eot_d, ad_eot_q;    // last AD block was seen
  logic ad_pad_d, ad_pad_q;    // AD padding word already applied
  logic msg_pad_d, msg_pad_q;  // MSG padding word already applied
  logic eoi_d, eoi_q;          // no more input after this

  // Registers
  register #('d128) reg_key_i (
      .clk(clk), .rst(rst), .data_d(key_d), .data_q(key_q)
  );
  register #('d320) reg_state_i (
      .clk(clk), .rst(rst), .data_d(state_d), .data_q(state_q)
  );
  register #('d8) reg_cnt_i (
      .clk(clk), .rst(rst),
      .data_d({round_cnt_d, word_cnt_d}),
      .data_q({round_cnt_q, word_cnt_q})
  );
  register #('d4, IDLE) reg_fsm_i (
      .clk(clk), .rst(rst), .data_d(fsm_d), .data_q(fsm_q)
  );
  register #('d7) reg_flags_i (
      .clk(clk), .rst(rst),
      .data_d({auth_d, auth_intern_d, auth_valid_d, ad_eot_d, ad_pad_d, msg_pad_d, eoi_d}),
      .data_q({auth_q, auth_intern_q, auth_valid_q, ad_eot_q, ad_pad_q, msg_pad_q, eoi_q})
  );

  // Utility signals: state_idx is driven per-state below, state_slice is a
  // continuous read of that word of the Ascon state.
  logic [3:0] state_idx, lane_idx, word_idx;
  logic [CCW-1:0] state_slice, state_slice_nx, bdi_pad;

  assign word_idx    = (CCW == 64) ? 'd0 : state_idx % 2;
  assign lane_idx    = (CCW == 64) ? state_idx : state_idx / 2;
  assign state_slice = state_q[int'(lane_idx)][int'(word_idx)];

  // Shared write-port into the Ascon state array. Every FSM branch that
  // needs to write state_d[lane_idx][word_idx] sets these two signals
  // instead of writing the array directly; the actual (variable-indexed)
  // write happens once, after the case statement, so synthesis only has
  // to build one write-demux instead of one per branch.
  logic wr_en;
  logic [CCW-1:0] wr_val;

  // Shared event/condition wires. Each of these was previously written out
  // as the same literal expression in 2-6 different FSM branches; pulling
  // them out as top-level assigns means synthesis only has to build one
  // comparator per condition instead of one per occurrence, and it removes
  // any risk of it *not* getting recognized as the same expression.
  logic word_cnt_last;    // current word is the last one of a 128-bit rate block
  logic perm_round_last;  // current cycle is the last round of the running permutation
  logic bdi_has_valid;    // bdi carries at least one valid byte this cycle
  logic bdi_partial_word; // bdi's valid-byte mask is not full (last, partial word)
  logic bdi_last_word;    // this is the last word of the current input segment
  logic nonce_word_in;    // a nonce word is being consumed this cycle
  logic ad_word_in;       // an AD word is being consumed this cycle
  logic msg_word_in;      // a ciphertext word is being consumed (and plaintext produced)
  logic tag_word_in;      // a tag word is being consumed this cycle
  logic perm_active;      // the UROL-round permutation is running this cycle
                           // (INIT/PRO_AD/PRO_MSG/FINAL all just XOR asconp_o
                           // back into state_d and tick round_cnt_d down --
                           // sharing that write avoids 4 separate 320-bit
                           // mux legs into state_d, one per FSM branch)

  assign word_cnt_last    = (word_cnt_q == (W128 - 1));
  assign perm_round_last  = (round_cnt_q == UROL);
  assign bdi_has_valid    = (bdi_valid != 'd0);
  assign bdi_partial_word = (bdi_valid != '1);
  assign bdi_last_word    = word_cnt_last || bdi_eot;
  assign nonce_word_in    = (bdi_type == D_NONCE) && bdi_has_valid;
  assign ad_word_in       = (bdi_type == D_AD) && bdi_has_valid;
  assign msg_word_in      = (bdi_type == D_MSG) && bdi_has_valid && bdo_ready;
  assign tag_word_in      = (bdi_type == D_TAG) && bdi_has_valid;
  assign perm_active      = (fsm_q == INIT) || (fsm_q == PRO_AD)
                           || (fsm_q == PRO_MSG) || (fsm_q == FINAL);

  // Ascon-p permutation
  logic [LANES-1:0][W64-1:0][CCW-1:0] asconp_o;
  asconp asconp_i (
      .round_cnt(round_cnt_q),
      .x0_i(state_q[0]), .x1_i(state_q[1]), .x2_i(state_q[2]),
      .x3_i(state_q[3]), .x4_i(state_q[4]),
      .x0_o(asconp_o[0]), .x1_o(asconp_o[1]), .x2_o(asconp_o[2]),
      .x3_o(asconp_o[3]), .x4_o(asconp_o[4])
  );

// FSM
  always_comb begin
    fsm_d          = fsm_q;
    key_d          = key_q;
    state_d        = state_q;
    word_cnt_d     = word_cnt_q;
    round_cnt_d    = round_cnt_q;
    auth_d         = auth_q;
    auth_intern_d  = auth_intern_q;
    auth_valid_d   = auth_valid_q;
    ad_eot_d       = ad_eot_q;
    ad_pad_d       = ad_pad_q;
    msg_pad_d      = msg_pad_q;
    eoi_d          = eoi_q;

    key_ready      = 1'b0;
    bdi_ready      = 1'b0;
    bdo            = '0;
    bdo_valid      = 1'b0;
    bdo_type       = D_INVALID;
    bdo_eot        = 1'b0;
    auth           = auth_q;
    auth_valid     = auth_valid_q;

    state_idx      = 'd0;
    state_slice_nx = 'd0;
    bdi_pad        = 'd0;
    wr_en          = 1'b0;
    wr_val         = 'd0;

    unique case (fsm_q)

      IDLE: begin
        if (start) begin
          auth_d        = 1'b0;
          auth_intern_d = 1'b0;
          auth_valid_d  = 1'b0;
          ad_eot_d      = 1'b0;
          ad_pad_d      = 1'b0;
          msg_pad_d     = 1'b0;
          eoi_d         = bdi_eoi;
          fsm_d         = key_valid ? LD_KEY : LD_NPUB;
        end
      end

      // Load the 128-bit key, one CCW-wide word at a time.
      LD_KEY: begin
        key_ready = 1'b1;
        if (key_valid) begin
          key_d[word_cnt_q[(64/CCW)-1:0]] = key;
          word_cnt_d = word_cnt_q + 'd1;
          if (word_cnt_last) begin
            word_cnt_d = 'd0;
            fsm_d      = LD_NPUB;
          end
        end
      end

      // Load the 128-bit nonce directly into lanes x3/x4. Once the last
      // nonce word arrives, also drop IV||key into lanes x0..x2 (this is
      // the full Ascon-AEAD128 initial state) and preload the round
      // counter for the upcoming initialization permutation.
      LD_NPUB: begin
        state_idx      = word_cnt_q + W192;
        bdi_ready      = 1'b1;
        state_slice_nx = bdi;
        if (nonce_word_in) begin
          wr_en      = 1'b1;
          wr_val     = state_slice_nx;
          word_cnt_d = word_cnt_q + 'd1;
          if (word_cnt_last) begin
            word_cnt_d  = 'd0;
            state_d[0]  = IV_AEAD[0+:64];
            state_d[1]  = key_q[0+:W64];
            state_d[2]  = key_q[W64+:W64];
            if (bdi_eoi) eoi_d = 1'b1;
            fsm_d       = INIT;
            round_cnt_d = ROUNDS_A;
          end
        end
      end

      // Run the 12-round initialization permutation, UROL rounds/cycle.
      INIT: begin
        if (perm_round_last) fsm_d = KADD_2;
      end

      // Key addition 2: XOR key into lanes x3/x4. Stay here (without
      // consuming input) until we know whether there is AD, a message, or
      // neither, then branch accordingly.
      KADD_2: begin
        if (eoi_q || bdi_has_valid) begin
          state_d[3] = state_q[3] ^ key_q[0+:W64];
          state_d[4] = state_q[4] ^ key_q[W64+:W64];
          if (eoi_q) fsm_d = DOM_SEP;
          else if (bdi_type == D_AD) fsm_d = ABS_AD;
          else if (bdi_type == D_MSG) fsm_d = DOM_SEP;
        end
      end

      // Absorb one word of associated data per cycle (rate = 128 bits).
      ABS_AD: begin
        state_idx      = word_cnt_q;
        bdi_ready      = 1'b1;
        bdi_pad        = pad(bdi, bdi_valid);
        state_slice_nx = state_slice ^ bdi_pad;
        if (ad_word_in) begin
          wr_en      = 1'b1;
          wr_val     = state_slice_nx;
          word_cnt_d = word_cnt_q + 'd1;
          if (bdi_last_word) begin
            // Last AD block (full-rate boundary or explicit end-of-type).
            if (bdi_eot) ad_eot_d = 1'b1;
            if (bdi_eoi) eoi_d    = 1'b1;
            if (bdi_partial_word) begin
              // Padding byte already folded into this word by pad().
              ad_pad_d    = 1'b1;
              word_cnt_d  = 'd0;
              fsm_d       = PRO_AD;
              round_cnt_d = ROUNDS_B;
            end else if (!word_cnt_last) begin
              // Full word used, but rate not yet full: a whole extra
              // padding word is still needed before permuting.
              fsm_d = PAD_AD;
            end else begin
              // Rate exactly filled: permute now, extra pad block (if
              // needed) is added afterwards from PRO_AD's next state.
              word_cnt_d  = 'd0;
              fsm_d       = PRO_AD;
              round_cnt_d = ROUNDS_B;
            end
          end
        end
      end

      // Explicit padding word for AD (XOR 0x01 marker into current word).
      PAD_AD: begin
        state_idx   = word_cnt_q;
        wr_en       = 1'b1;
        wr_val      = state_slice ^ 'd1;
        ad_pad_d    = 1'b1;
        word_cnt_d  = 'd0;
        fsm_d       = PRO_AD;
        round_cnt_d = ROUNDS_B;
      end

      // Run the 8-round intermediate permutation over the AD block.
      PRO_AD: begin
        if (perm_round_last) begin
          if (!ad_eot_q) fsm_d = ABS_AD;              // more AD to absorb
          else if (!ad_pad_q) begin                   // rate was exactly full
            fsm_d = PAD_AD;
          end else fsm_d = DOM_SEP;                    // AD fully processed
        end
      end

      // Domain separation bit + implicit padding of an empty message.
      DOM_SEP: begin
        state_d[4] = state_q[4] ^ 64'h8000000000000000;
        if (eoi_q) begin
          state_d[0] = state_q[0] ^ 'd1;
          msg_pad_d  = 1'b1;
          fsm_d      = KADD_3;
        end else begin
          fsm_d = ABS_MSG;
        end
      end

      // Absorb+decrypt one 128-bit-rate word of ciphertext per cycle.
      // bdi_ready is tied to bdo_ready: a ciphertext word is only
      // consumed once the plaintext word it produces can be accepted.
      ABS_MSG: begin
        state_idx      = word_cnt_q;
        bdi_pad        = pad2(bdi, state_slice, bdi_valid);
        state_slice_nx = bdi_pad;
        bdo            = mask(state_slice ^ state_slice_nx, bdi_valid);
        bdi_ready      = bdo_ready;
        bdo_valid      = bdi_has_valid;
        bdo_type       = D_MSG;
        bdo_eot        = bdi_eot;
        if (msg_word_in) begin
          wr_en      = 1'b1;
          wr_val     = state_slice_nx;
          word_cnt_d = word_cnt_q + 'd1;
          
          if (bdi_last_word) begin
            if (bdi_eoi) eoi_d = 1'b1;
            if (bdi_partial_word) begin
              // Partial last word: byte-level padding already applied by
              // pad2(). No permutation after the final block.
              msg_pad_d  = 1'b1;
              word_cnt_d = 'd0;
              fsm_d      = KADD_3;
            end else if (!word_cnt_last) begin
              fsm_d = PAD_MSG;
            end else begin
              // Rate exactly filled with valid ciphertext: this block
              // still gets permuted like any non-final block. If it was
              // also the last one (eoi_q), PRO_MSG routes on to PAD_MSG
              // afterwards to append the required extra padding block.
              word_cnt_d  = 'd0;
              fsm_d       = PRO_MSG;
              round_cnt_d = ROUNDS_B;
            end
          end
        end
      end

      // Explicit padding word for the message (only reached when the
      // final ciphertext word was fully valid but didn't fill the rate).
      PAD_MSG: begin
        state_idx  = word_cnt_q;
        wr_en      = 1'b1;
        wr_val     = state_slice ^ 'd1;
        msg_pad_d  = 1'b1;
        word_cnt_d = 'd0;
        fsm_d      = KADD_3;
      end

      // Run the 8-round intermediate permutation, then go back for more
      // ciphertext (used only for non-final blocks).
      PRO_MSG: begin
        if (perm_round_last) begin
          if (!eoi_q) fsm_d = ABS_MSG;
          else if (!msg_pad_q) fsm_d = PAD_MSG;
        end
      end

      // Key addition 3, preload round counter for finalization.
      KADD_3: begin
        state_d[2]  = state_q[2] ^ key_q[0+:W64];
        state_d[3]  = state_q[3] ^ key_q[W64+:W64];
        fsm_d       = FINAL;
        round_cnt_d = ROUNDS_A;
      end

      // Run the 12-round finalization permutation.
      FINAL: begin
        if (perm_round_last) fsm_d = KADD_4;
      end

      // Key addition 4, then go verify the received tag.
      KADD_4: begin
        state_d[3]    = state_q[3] ^ key_q[0+:W64];
        state_d[4]    = state_q[4] ^ key_q[W64+:W64];
        auth_intern_d = 1'b1;
        fsm_d         = VER_TAG;
      end

      // Compare the received tag against the computed tag, word by word.
      VER_TAG: begin
        state_idx = word_cnt_q + W192;
        bdi_ready = 1'b1;
        if (tag_word_in) begin
          auth_intern_d = auth_intern_q && (bdi == state_slice);
          word_cnt_d = word_cnt_q + 'd1;
          if (word_cnt_last) begin
            word_cnt_d   = 'd0;
            auth_d       = auth_intern_q && auth_intern_d;
            auth_valid_d = 1'b1;
            fsm_d        = IDLE;
          end
        end
      end

      default: ;
    endcase

    // Shared permutation writeback: INIT/PRO_AD/PRO_MSG/FINAL all do the
    // exact same "state_d = asconp_o; round_cnt_d = round_cnt_q - UROL;"
    // -- doing it once here instead of once per branch means synthesis
    // only builds one 320-bit mux leg for state_d instead of four.
    if (perm_active) begin
      state_d     = asconp_o;
      round_cnt_d = round_cnt_q - UROL;
    end

    // Single shared write-port into the Ascon state array. lane_idx/word_idx
    // are already settled combinationally from whatever state_idx the branch
    // above set, so this reproduces exactly what each branch used to do
    // individually -- just synthesized once instead of once per branch.
    if (wr_en) begin
      state_d[int'(lane_idx)][int'(word_idx)] = wr_val;
    end
  end

  //////////////////////////////////////////////////
  // Debug Signals (can be removed for synthesis) //
  //////////////////////////////////////////////////

  logic [63:0] x0, x1, x2, x3, x4;
  assign x0 = state_q[0];
  assign x1 = state_q[1];
  assign x2 = state_q[2];
  assign x3 = state_q[3];
  assign x4 = state_q[4];

endmodule

`endif  // INCL_ASCON_CORE_COMBINED