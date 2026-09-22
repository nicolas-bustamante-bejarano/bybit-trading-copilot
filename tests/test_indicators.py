import pytest

from trading_copilot.services.indicators import ema, fib_retracements


def test_fib_long_pullback_levels() -> None:
    levels = fib_retracements(745.3, 807.5, "long")
    assert levels["0.500"] == pytest.approx(776.4)
    assert levels["0.618"] == pytest.approx(769.0604)
    assert levels["0.786"] == pytest.approx(758.6108)


def test_ema_length() -> None:
    values = [1, 2, 3, 4, 5]
    assert len(ema(values, 3)) == len(values)
