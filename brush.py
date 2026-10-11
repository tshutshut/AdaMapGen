"""Normalized circular paint stamps retained separately from generated terrain."""
import numpy as np


# What the function does:
#     Paints one circular stamp into a binary map without affecting other cells.
# Inputs (if any):
#     land: destination; stamp: normalized x, y, radius (fraction of shorter side), land value.
# Outputs (if any):
#     None; modifies the destination in place.
def paint_stamp(land, stamp):
    # Region deletions use packed masks to retain their exact footprint without
    # storing a full Boolean image for every edit. Resize with nearest sampling.
    if isinstance(stamp, dict):
        mask = np.unpackbits(stamp['mask'], count=int(np.prod(stamp['shape']))).reshape(stamp['shape'])
        h, w = land.shape
        rows = np.arange(h)*mask.shape[0]//h
        columns = np.arange(w)*mask.shape[1]//w
        land[mask[rows[:, None], columns].astype(bool)] = stamp['value']
        return
    x, y, radius, value = stamp
    height, width = land.shape
    x, y, radius = x*width, y*height, radius*min(width, height)
    left, right = max(0, int(x-radius)), min(width, int(np.ceil(x+radius)))
    top, bottom = max(0, int(y-radius)), min(height, int(np.ceil(y+radius)))
    if left >= right or top >= bottom:
        return
    xx = np.arange(left, right)+.5-x
    yy = (np.arange(top, bottom)+.5-y)[:, None]
    land[top:bottom, left:right][xx*xx+yy*yy <= radius*radius] = value


# What the function does:
#     Rotates or inverts a saved brush stamp or region deletion.
# Inputs (if any):
#     stamp: saved edit; direction: quarter turn; invert: exchange land and sea.
# Outputs (if any):
#     A new edit, leaving history entries unchanged.
def transform_stamp(stamp, direction=0, invert=False):
    if isinstance(stamp, dict):
        mask = np.unpackbits(stamp['mask'], count=int(np.prod(stamp['shape']))).reshape(stamp['shape'])
        mask = np.rot90(mask, -direction)
        return {'mask': np.packbits(mask), 'shape': mask.shape, 'value': stamp['value'] ^ invert}
    x, y, radius, value = stamp
    if direction:
        x, y = (1-y, x) if direction == 1 else (y, 1-x)
    return x, y, radius, value ^ invert
