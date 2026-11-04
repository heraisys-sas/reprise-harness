def totals(records):
    return {key: sum(record.get(key, 0) or 0 for record in records)
            for key in ("cache_hit", "cache_miss", "output")}
