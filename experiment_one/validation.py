"""Strict comparison of encoded and recovered sample bytes."""
import hashlib
from compression import bits


def verify(raw, restored, codec_error=''):
    original = bits(raw)
    recovered = bits(restored)
    positions = [i for i,(one,two) in enumerate(zip(original,recovered)) if one != two]
    positions.extend(range(min(len(original),len(recovered)),max(len(original),len(recovered))))
    sha_raw = hashlib.sha256(raw).hexdigest()
    sha_restored = hashlib.sha256(restored).hexdigest()
    return dict(ok=not positions and sha_raw == sha_restored and not codec_error,
                bit_error_count=len(positions), bit_errors=positions,
                sha256_original=sha_raw, sha256_restored=sha_restored,
                original_bits=len(original), restored_bits=len(recovered),
                error=codec_error or ('Length or bit mismatch' if positions else ''))
