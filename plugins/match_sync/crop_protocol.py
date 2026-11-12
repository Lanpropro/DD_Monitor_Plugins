"""Small crop result contract; no stream, account or host widget state."""
import math

PREFIX = 'DDM_CROP:'


def valid_crop(values):
    return (isinstance(values, list) and len(values) == 4
            and all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)
            and values[0] >= 0 and values[1] >= 0 and values[2] > 0 and values[3] > 0
            and values[0]+values[2] <= 1.000001 and values[1]+values[3] <= 1.000001)
