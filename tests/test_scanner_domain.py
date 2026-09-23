from datetime import UTC, datetime

import trading_copilot.domain.scanner as scanner_domain
from trading_copilot.domain.scanner import ScannerResult


def scanner_result() -> ScannerResult:
    return ScannerResult(
        symbol="BTCUSDT",
        setup_type="TREND_PULLBACK_LONG",
        side="LONG",
        status="WATCH",
        price=100,
        evaluated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_domain_has_no_alternate_macro_lifecycle_implementation():
    stale_helpers = {
        "macro_status",
        "project_trendline",
        "distance_to_interval_bps",
        "distance_to_level_bps",
    }

    assert stale_helpers.isdisjoint(vars(scanner_domain))


def test_scanner_result_containers_do_not_share_mutable_defaults():
    first = scanner_result()
    second = scanner_result()

    first.context["changed"] = True
    first.location["changed"] = True
    first.reaction["changed"] = True
    first.structure["changed"] = True
    first.conditions.append({"changed": True})
    first.blocking_reasons.append("changed")
    first.next_conditions.append("changed")

    assert second.context == {}
    assert second.location == {}
    assert second.reaction == {}
    assert second.structure == {}
    assert second.conditions == []
    assert second.blocking_reasons == []
    assert second.next_conditions == []
