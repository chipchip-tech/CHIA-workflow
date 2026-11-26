"""project.py - khai báo cho flow/ (xem flow/README.md): lõi Ascon-AEAD128 giải mã + kiểm tag, khối RTL đứng riêng.

Project chỉ có rtl/ (ascon_core.sv + ascon_pkg.svh) và sim/tb_ascon_core.sv; mọi file khác (run_sim.sh, config.mk,
constraint.sdc, build.tcl out-of-context của Vivado, ...) do flow tự sinh trong thư mục chạy.
"""

PROJECT = {
    "dut": "ascon_core",
    "title": "Ascon-AEAD128 decrypt + tag check core (32-bit bus, 1 round/cycle)",
    "steps": ["sim", "fpga", "asic"],
    "clocks": [{"port": "clk", "period_ns": 10.0}],          # reset `rst` tích cực mức thấp, bất đồng bộ
    "sim": {
        # Testbench in "[PASS] ..." / "[FAIL] ..." (không có dòng RESULT): cả hai kiểm tra phải PASS và mô phỏng chạy hết.
        "pass_patterns": [r"\[PASS\] Decrypted Plaintext Match", r"\[PASS\] Tag Verification", r"SIMULATION COMPLETED"],
        "fail_patterns": [r"\[FAIL\]"],
        "lint": "errors",                                     # 12 cảnh báo -Wall của RTL gốc được ghi lại, không chặn
        # Số liệu riêng hiện trên trang status (không ảnh hưởng gate): số dòng [PASS] testbench in ra.
        "metrics": [
            {"key": "pass_lines", "label": "[PASS] lines", "regex": r"\[PASS\]", "count": True, "better": 1},
        ],
    },
    "fpga": {"part": "xc7a100tcsg324-1", "board": "Artix-7 100T, out-of-context"},
    "asic": {"platform": "sky130hd"},
    "agent": {
        "description": "an Ascon-AEAD128 core that only implements decryption and tag verification (32-bit data bus "
                       "CCW=32, one Ascon-p round per clock, asynchronous active-low reset `rst`). The host loads the "
                       "key (start pulse + key words), then nonce, associated data and ciphertext on bdi (bdi_type "
                       "D_NONCE/D_AD/D_MSG, bdi_eot/bdi_eoi), reads the plaintext on bdo, then sends the tag (D_TAG); "
                       "auth_valid/auth report the tag check.",
        "sim_hints": "empty and multi-block associated data, messages that fill the 16-byte rate exactly or end with a "
                     "partial word, no associated data, wrong tag (auth must be 0), back-to-back operations without "
                     "reset, stalls on bdo_ready and gaps on bdi_valid; expected values must come from the Ascon "
                     "specification (for example a reference model written in the testbench)",
    },
}
