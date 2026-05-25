"""Emit deterministic agent identity material for pinning in registry config.

Usage:
    uv run python scripts/gen_keys.py registry-a.playground.local agent-alpha [agent-beta...]
"""

from __future__ import annotations

import hashlib
import json
import sys

from acdp import AcdpProducer


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    authority = sys.argv[1]
    slugs = sys.argv[2:]
    out = []
    for slug in slugs:
        # Deterministic from authority + slug; replace with random for prod.
        seed = hashlib.sha256(f"{authority}:{slug}".encode()).digest()
        did = f"did:web:{authority}:agents:{slug}"
        key_id = f"{did}#key-1"
        producer = AcdpProducer.from_seed(seed, did, key_id)
        out.append({
            "slug": slug,
            "agent_did": did,
            "key_id": key_id,
            "public_key_b64": producer.public_key_b64,
            "seed_hex": seed.hex(),
        })
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
