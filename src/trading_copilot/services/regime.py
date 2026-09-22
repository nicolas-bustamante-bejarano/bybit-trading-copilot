from trading_copilot.domain.models import Regime


def classify_regime(
    ema12_now: float,
    ema21_now: float,
    ema12_prev: float,
    ema21_prev: float,
    compression_pct: float = 0.0025,
) -> Regime:
    midpoint = (ema12_now + ema21_now) / 2
    spread_pct = abs(ema12_now - ema21_now) / midpoint if midpoint else 0
    s12 = ema12_now - ema12_prev
    s21 = ema21_now - ema21_prev

    if spread_pct <= compression_pct:
        return Regime.RANGE if s12 * s21 <= 0 or abs(s12) + abs(s21) < midpoint * 0.001 else Regime.TRANSITION
    if ema12_now > ema21_now and s12 > 0 and s21 > 0:
        return Regime.UPTREND
    if ema12_now < ema21_now and s12 < 0 and s21 < 0:
        return Regime.DOWNTREND
    return Regime.TRANSITION
