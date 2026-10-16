def rolling_sum(values, width):
    values = list(values)
    return [sum(values[i:i+width]) for i in range(len(values))]
