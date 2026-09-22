from collections.abc import Sequence


FIB_RATIOS = (0.236, 0.382, 0.5, 0.618, 0.786, 0.886, 1.0)


def ema(values: Sequence[float], period: int) -> list[float]:
    if period <= 0:
        raise ValueError("period must be positive")
    if not values:
        return []
    alpha = 2 / (period + 1)
    out = [float(values[0])]
    for value in values[1:]:
        out.append(alpha * float(value) + (1 - alpha) * out[-1])
    return out


def fib_retracements(swing_low: float, swing_high: float, direction: str) -> dict[str, float]:
    if swing_high <= swing_low:
        raise ValueError("swing_high must be greater than swing_low")
    span = swing_high - swing_low
    if direction == "long":
        return {f"{r:.3f}": swing_high - span * r for r in FIB_RATIOS}
    if direction == "short":
        return {f"{r:.3f}": swing_low + span * r for r in FIB_RATIOS}
    raise ValueError("direction must be 'long' or 'short'")


def rsi(values: Sequence[float], period: int = 14) -> list[float | None]:
    values = [float(x) for x in values]
    if len(values) < period + 1:
        return [None] * len(values)

    out: list[float | None] = [None] * len(values)
    gains = []
    losses = []
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    def calc(ag: float, al: float) -> float:
        if al == 0:
            return 100.0
        rs = ag / al
        return 100 - (100 / (1 + rs))

    out[period] = calc(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
        out[i] = calc(avg_gain, avg_loss)
    return out


def _sma_nullable(values: Sequence[float | None], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        if any(v is None for v in window):
            continue
        out[i] = sum(float(v) for v in window) / period
    return out


def stoch_rsi(
    closes: Sequence[float],
    rsi_period: int = 14,
    stoch_period: int = 14,
    k_period: int = 3,
    d_period: int = 3,
) -> tuple[list[float | None], list[float | None]]:
    rsis = rsi(closes, rsi_period)
    raw: list[float | None] = [None] * len(rsis)

    for i in range(len(rsis)):
        start = i - stoch_period + 1
        if start < 0:
            continue
        window = rsis[start : i + 1]
        if any(v is None for v in window) or rsis[i] is None:
            continue
        lo = min(float(v) for v in window)
        hi = max(float(v) for v in window)
        raw[i] = 0.0 if hi == lo else 100 * (float(rsis[i]) - lo) / (hi - lo)

    k = _sma_nullable(raw, k_period)
    d = _sma_nullable(k, d_period)
    return k, d
