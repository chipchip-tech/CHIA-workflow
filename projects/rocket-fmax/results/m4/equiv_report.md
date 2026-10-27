# Equivalence: iter5 so với baseline

- Module đổi: AMOALU, FPU, IntToFP
- Module thêm: (không); bỏ: (không)
- Bỏ qua: (không)

| Đơn vị | Formal | proven/total | Formal (s) | Lockstep | Cycle | Cycle lệch | Lệch đầu tiên |
|---|---|---|---|---|---|---|---|
| AMOALU | proven | 195/195 | 0 | pass | 10000000 | 0 |  |
| FPU | timeout | None/None | 1870 | pass | 10000000 | 0 |  |

Kết luận: PASS (lockstep phải pass mọi đơn vị; formal `unproven`/`timeout` là chưa chứng minh được, không phải sai).
