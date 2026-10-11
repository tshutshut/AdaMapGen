"""Shared-coastline displacement with seeded noise and approximate signed distance."""
import numpy as np


# What the function does:
#     Measures approximate Euclidean distance to marked pixels using a chamfer scan.
# Inputs (if any):
#     seeds: Boolean boundary image.
# Outputs (if any):
#     Float32 distances; infinity when there are no marked pixels.
def boundary_distance(seeds):
    distance = np.where(seeds, 0., np.inf).astype(np.float32)
    x = np.arange(seeds.shape[1], dtype=np.float32)
    diagonal = np.float32(np.sqrt(2))
    # Two directional passes include horizontal, vertical, and diagonal steps.
    # Each row is vectorized; no per-pixel Python loops or extra dependency.
    for order, step in ((range(len(distance)), -1), (range(len(distance)-1, -1, -1), 1)):
        for y in order:
            row = distance[y]
            neighbor = y+step
            if 0 <= neighbor < len(distance):
                other = distance[neighbor]
                np.minimum(row, other+1, out=row)
                np.minimum(row[1:], other[:-1]+diagonal, out=row[1:])
                np.minimum(row[:-1], other[1:]+diagonal, out=row[:-1])
            row[:] = np.minimum.accumulate(row-x)+x
            row[:] = (np.minimum.accumulate((row+x)[::-1])[::-1]-x)
    return distance


# What the function does:
#     Distorts each targeted shared boundary once using multi-scale smooth noise.
# Inputs (if any):
#     land/labels: immutable baseline and object IDs; selected: IDs or None for all;
#     strength/scale: percent of shorter image side; detail: noise layers;
#     roughness: finer-layer weight multiplier; seed: deterministic noise seed.
# Outputs (if any):
#     A new Boolean land mask; outer frame and unrelated boundaries stay fixed.
def vary_contours(land, labels, selected, strength, scale, detail, roughness, seed):
    if not (0 <= strength <= 5 and .5 <= scale <= 50 and 1 <= detail <= 6 and 0 <= roughness <= 1):
        raise ValueError('Contour parameters are outside their supported ranges.')
    output = land.copy()
    if strength == 0 or selected == set():
        return output
    chosen = np.ones(land.shape, dtype=bool) if selected is None else np.isin(labels, list(selected))
    active = np.zeros(land.shape, dtype=bool)
    protected = np.zeros_like(active)
    # A shared edge is targeted if either adjoining object was selected. Adding
    # both IDs therefore does not double the displacement along that edge.
    for first, second in ((np.s_[:-1, :], np.s_[1:, :]), (np.s_[:, :-1], np.s_[:, 1:])):
        edge = land[first] != land[second]
        target = edge & (chosen[first] | chosen[second])
        fixed = edge & ~target
        active[first] |= target
        active[second] |= target
        protected[first] |= fixed
        protected[second] |= fixed
    if not active.any():
        return output
    distance = boundary_distance(active)
    # Nearest-boundary ownership prevents changes spilling onto another object's
    # unselected coast. Ties are protected, avoiding ambiguous junction edits.
    allowed = distance < boundary_distance(protected) if protected.any() else np.ones_like(active)
    del chosen, active, protected
    amplitude = strength*min(land.shape)/100
    wavelength = max(2., scale*min(land.shape)/100)
    rng = np.random.default_rng(seed)
    layers = []
    total = 0.
    for level in range(int(detail)):
        spacing = max(2., wavelength/2**level)
        weight = roughness**level
        grid = rng.uniform(-1, 1, (int(np.ceil(land.shape[0]/spacing))+2,
                                   int(np.ceil(land.shape[1]/spacing))+2)).astype(np.float32)
        layers.append((spacing, weight, grid))
        total += weight
    # Evaluate only short row batches to bound temporary memory at high resolution.
    for start in range(0, len(land), 64):
        stop = min(start+64, len(land))
        noise = np.zeros((stop-start, land.shape[1]), dtype=np.float32)
        for spacing, weight, grid in layers:
            xs = np.arange(land.shape[1])/spacing
            ys = np.arange(start, stop)/spacing
            ix, iy = xs.astype(int), ys.astype(int)
            tx, ty = xs-ix, ys-iy
            # Smoothstep interpolation gives continuous transitions between cells.
            tx, ty = tx*tx*(3-2*tx), ty*ty*(3-2*ty)
            a = grid[iy[:, None], ix]*(1-tx)+grid[iy[:, None], ix+1]*tx
            b = grid[iy[:, None]+1, ix]*(1-tx)+grid[iy[:, None]+1, ix+1]*tx
            noise += weight*(a*(1-ty[:, None])+b*ty[:, None])
        original = land[start:stop]
        signed = (distance[start:stop]+.5)*np.where(original, 1, -1)
        varied = signed+noise*(amplitude/total) > 0
        output[start:stop] = np.where(allowed[start:stop], varied, original)
    output[0], output[-1] = land[0], land[-1]
    output[:, 0], output[:, -1] = land[:, 0], land[:, -1]
    return output
