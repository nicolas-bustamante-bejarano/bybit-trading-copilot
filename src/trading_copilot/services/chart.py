from decimal import Decimal


def aggregate_daily_to_3d(rows: list[list[str]]) -> list[list[str]]:
    """Normalize Bybit daily candles to chronological order before three-day aggregation."""
    ordered = sorted(rows, key=lambda row: int(row[0]))
    groups = [ordered[index : index + 3] for index in range(0, len(ordered), 3)]
    return [
        [
            group[0][0], group[0][1], str(max(Decimal(item[2]) for item in group)),
            str(min(Decimal(item[3]) for item in group)), group[-1][4],
            str(sum(Decimal(item[5]) for item in group)),
        ]
        for group in groups
        if len(group) == 3
    ]
