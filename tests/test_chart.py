from trading_copilot.services.chart import aggregate_daily_to_3d


def test_three_day_aggregation_sorts_reverse_bybit_rows_before_grouping():
    rows = [
        ["3000", "3", "5", "2", "4", "30"], ["2000", "2", "4", "1", "3", "20"],
        ["1000", "1", "3", "0", "2", "10"], ["6000", "6", "8", "5", "7", "60"],
        ["5000", "5", "7", "4", "6", "50"], ["4000", "4", "6", "3", "5", "40"],
    ]
    assert aggregate_daily_to_3d(rows) == []


def test_three_day_buckets_are_stable_when_history_window_changes():
    day = 86_400_000
    rows = [[str(day * index), str(index), str(index + 2), str(index - 1), str(index + 1), "1"] for index in range(1, 10)]
    full = aggregate_daily_to_3d(rows)
    subset = aggregate_daily_to_3d(rows[3:])
    assert [row for row in full if row[0] >= str(day * 6)] == subset
