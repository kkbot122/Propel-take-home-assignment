import hashlib

DEFAULT_POWER_LOSS_DELIVERY_RATIO = 0.70
DEFAULT_POWER_LOSS_DELIVERY_SEED = 287


def power_loss_delivery_succeeds(
    pole_id: str,
    *,
    context: str,
    ratio: float = DEFAULT_POWER_LOSS_DELIVERY_RATIO,
    seed: int = DEFAULT_POWER_LOSS_DELIVERY_SEED,
) -> bool:
    """Reproduce the capacitor-backed dying-message attempt for one device.

    The real fleet succeeds approximately 70% of the time. A stable hash gives
    the simulator the same Bernoulli outcome for the same seed, physical scope,
    and pole without introducing process-global random state.
    """
    if not 0 <= ratio <= 1:
        raise ValueError("power-loss delivery ratio must be between zero and one")
    if seed < 0:
        raise ValueError("power-loss delivery seed cannot be negative")
    if not pole_id or not context:
        raise ValueError("power-loss delivery requires a pole and fault context")

    payload = f"{seed}|power-loss-delivery|{context}|{pole_id}"
    rank = int.from_bytes(hashlib.sha256(payload.encode()).digest()[:8], "big")
    unit_interval = rank / ((1 << 64) - 1)
    return unit_interval < ratio
