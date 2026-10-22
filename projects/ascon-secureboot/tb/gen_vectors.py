#!/usr/bin/env python3
"""Print Ascon-Hash256 test vectors, one per line: "<message hex> <digest hex>".

Digests come from ascon.py. Covers every padding position (lengths 1..33),
block multiples, the 4 KiB firmware image and its tampered copy.
--with-empty adds the empty message (the SW path supports it, RoCC does not).
"""
import argparse
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from gen_image import TAMPER_MASK, TAMPER_OFFSET, load_ascon, make_image  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--ascon-py", required=True)
ap.add_argument("--with-empty", action="store_true")
args = ap.parse_args()
ascon = load_ascon(args.ascon_py)

rng = random.Random(7)
msgs = [bytes(rng.getrandbits(8) for _ in range(n))
        for n in list(range(1, 34)) + [64, 1023, 1024]]
image = make_image()
tampered = bytearray(image)
tampered[TAMPER_OFFSET] ^= TAMPER_MASK
msgs += [image, bytes(tampered)]
if args.with_empty:
    msgs.insert(0, b"")
for m in msgs:
    # "-" stands for the empty message so the line still has two fields.
    print(m.hex() or "-", ascon.ascon_hash(m, "Ascon-Hash256", 32).hex())
