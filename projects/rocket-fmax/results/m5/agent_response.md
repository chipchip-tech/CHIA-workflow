Made two source-only, semantics-preserving edits. I did **not** build or simulate (per the workflow), but I did validate both rewrites with bit-level Python models.

## 1. `rocket/ALU.scala` — shorten the MAX/MIN load-use path (the `mem_reg_wdata` 12.32 ⇒ 8.762 slack path)

- **Folded the compare inversion** into the sign mux branches:
  - `maxmin_out` is only selected for `FN_MAX/MIN/MAXU/MINU`, which all have `cmpEq(fn)==false`, so `io.cmp_out == cmpInverted(fn) ^ slt`. Rebuilt `maxmin_sel` as `slt ^ cmpInverted(fn)` directly in the three sign-mux branches, removing the `cmpEq` mux from the path (adder → slt → max → result). `io.cmp_out` itself is untouched (`mem_br_taken` unaffected).
- **Peeled MAX/MIN out of the shared priority mux**: the `MuxLookup` entries are now `ADD/SUB/UNARY/ROL/ROR` (`out0`), and `out = Mux(isMaxMin(fn), maxmin_out, out0)`. MAX/MIN previously sat at index 3 of the chain; now they take one mux level. Other functions only gain ≤1 level.
- Model check: 200k random operand pairs × 4 max/min fns confirm identical results.

## 2. `tile/FPU.scala` — rebalance the FMA carry-propagate across the `mulAddResult` register (12.351 ⇒ 8.745 path)

Kept the same `fastAdd` prefix tree and the same latency/valid boundary, but changed what is registered:
- Lower 64 bits: unchanged (`loSum` with the complete prefix + XOR).
- Upper 43 bits: register the prefix state **one level short** (`gA/pA = gShort(63+j)`, `gB/pB = gShort(31+j)`) plus the completed low-group carries `gC = gFull(j-1)`, and finish after the register with `carry(j) = gA | pA&gB | pA&pB&gLow` (= `G([0,63+j])`).
- Stage 0 upper-bit depth drops one prefix level; stage 1 (which had ~1.25 ns of spare slack) absorbs the final combine + upper XORs. Written as an OR of AND terms so stage 1 stays shallow.
- Model check: 5000 random `p0/g0` vectors confirm `Cat(hiSum, loSum)` is bit-identical to the full 107-bit prefix sum; `6/2000` and then `5000` random vectors passed for both the old and new decompositions.

## Expected effect
- `mem_reg_wdata`: ~3 gate levels removed from the MAX/MIN cone ⇒ roughly 9.2 slack.
- FMA: ~1 prefix level removed from the worst (upper-bit) endpoint; low group becomes the limiter (~11.98 ns) ⇒ roughly 9.1 slack.
- Net expected worst slack ≈ 9.1 (vs 8.745), with `ADD/SUB` (≈1 extra mux level) still having margin, since the reported 64-bit family at 8.762 is uniform — the signature of the shared late `slt` select, i.e. the MAX/MIN cone, not the ripple of an ADD.

## Risk / next steps
- If synthesis shows the mem worst is actually `ADD/SUB`, the peel costs it ~1 level; the safe fallback is to reorder so `ADD/SUB` stay first (sacrificing ~1 level of MAX/MIN gain).
- Future rounds: the remaining FMA headroom is in `reduceMulAdd` (9-level partial-product reduction, `maj3` chain). A radix-4 Booth recode would cut ~2 tree levels and the `in2` fanout, but needs careful correctness work.