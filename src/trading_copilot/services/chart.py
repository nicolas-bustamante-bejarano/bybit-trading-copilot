from decimal import Decimal


def aggregate_daily_to_3d(rows: list[list[str]]) -> list[list[str]]:
    """UTC-align three-day bars to Unix-epoch day buckets, independent of fetch window."""
    ordered = sorted(rows, key=lambda row: int(row[0]))
    buckets: dict[int, list[list[str]]] = {}
    for row in ordered:
        day = int(row[0]) // 86_400_000
        buckets.setdefault(day - day % 3, []).append(row)
    groups = [buckets[key] for key in sorted(buckets)]
    return [
        [
            group[0][0], group[0][1], str(max(Decimal(item[2]) for item in group)),
            str(min(Decimal(item[3]) for item in group)), group[-1][4],
            str(sum(Decimal(item[5]) for item in group)),
        ]
        for group in groups
        if len(group) == 3
    ]
