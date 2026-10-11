"""A planar, land-constrained fault graph for artistic tectonics."""
from dataclasses import dataclass
import numpy as np
from contours import boundary_distance


@dataclass
class FaultNetwork:
    points: np.ndarray
    coastal: np.ndarray
    edges: list
    modes: list


# What the function does:
#     Tests every grid interval traversed by a proposed fault for water.
# Inputs (if any):
#     land: Boolean mask; a/b: endpoints in image-edge coordinates.
# Outputs (if any):
#     True only when the entire segment stays on land, including corner contacts.
def segment_on_land(land, a, b):
    delta = b-a
    times = [0., 1.]
    for axis in (0, 1):
        if abs(delta[axis]) > 1e-12:
            boundaries = np.arange(np.ceil(min(a[axis], b[axis])), np.floor(max(a[axis], b[axis]))+1)
            times.extend(((boundaries-a[axis])/delta[axis]).tolist())
    times = np.unique(np.clip(times, 0, 1))
    samples = np.r_[times, (times[:-1]+times[1:])/2]
    points = a+samples[:, None]*delta
    # Tiny offsets include both sides of exact grid-edge/corner contacts.
    for dx, dy in ((-1e-7, -1e-7), (-1e-7, 1e-7), (1e-7, -1e-7), (1e-7, 1e-7)):
        x = np.floor(points[:, 0]+dx).astype(int)
        y = np.floor(points[:, 1]+dy).astype(int)
        if np.any(x < 0) or np.any(y < 0) or np.any(x >= land.shape[1]) or np.any(y >= land.shape[0]):
            return False
        if not land[y, x].all():
            return False
    return True


# What the function does:
#     Rejects crossing or overlapping segments while allowing shared junctions.
# Inputs (if any):
#     points: coordinates; first/second: pairs of node indices.
# Outputs (if any):
#     True when the two segments conflict.
def edges_conflict(points, first, second):
    a, b = points[list(first)]
    c, d = points[list(second)]

    # What the function does:
    #     Computes signed planar area to determine which side a point lies on.
    # Inputs (if any):
    #     u/v: two vectors.
    # Outputs (if any):
    #     Scalar cross product.
    def cross(u, v):
        return u[0]*v[1]-u[1]*v[0]

    shared = set(first) & set(second)
    if shared:
        common = next(iter(shared))
        u = points[next(i for i in first if i != common)]-points[common]
        v = points[next(i for i in second if i != common)]-points[common]
        return abs(cross(u, v)) < 1e-8 and np.dot(u, v) > 0
    if np.any(np.maximum(np.minimum(a, b), np.minimum(c, d)) > np.minimum(np.maximum(a, b), np.maximum(c, d))+1e-8):
        return False
    return cross(b-a, c-a)*cross(b-a, d-a) <= 1e-8 and cross(d-c, a-c)*cross(d-c, b-c) <= 1e-8


# What the function does:
#     Places spaced interior/coastal points and builds a short non-crossing graph.
# Inputs (if any):
#     land/labels: committed map and region IDs; counts: requested junctions/endpoints;
#     spacing: minimum separation as percent of shorter side; seed: repeatable randomness.
# Outputs (if any):
#     FaultNetwork; impossible connections and placements are omitted rather than forced.
def generate_faults(land, labels, count=20, endpoints=12, spacing=4., seed=1):
    rng = np.random.default_rng(seed)
    padded = np.pad(land, 1)
    coast = land & ~(padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:])
    distance = boundary_distance(coast)
    separation = spacing*min(land.shape)/100
    points, coastal = [], []
    # Random candidate pools keep memory bounded. Clearance weighting favors
    # broad interior areas; minimum spacing prevents crowded junctions.
    xs = rng.integers(0, land.shape[1], 12000)
    ys = rng.integers(0, land.shape[0], 12000)
    valid = land[ys, xs] & (distance[ys, xs] >= max(1, separation*.4))
    candidates = np.column_stack((xs[valid]+.5, ys[valid]+.5))
    scores = distance[ys[valid], xs[valid]]*rng.uniform(.2, 1, len(candidates))
    for point in candidates[np.argsort(-scores)]:
        if len(points) >= count:
            break
        if not points or np.min(np.linalg.norm(np.array(points)-point, axis=1)) >= separation:
            points.append(point)
            coastal.append(False)
    coast_indices = np.flatnonzero(coast)
    rng.shuffle(coast_indices)
    added = 0
    for index in coast_indices[:12000]:
        if added >= endpoints:
            break
        y, x = divmod(int(index), land.shape[1])
        point = np.array([x+.5, y+.5])
        if not points or np.min(np.linalg.norm(np.array(points)-point, axis=1)) >= separation:
            points.append(point)
            coastal.append(True)
            added += 1
    points = np.array(points, dtype=float).reshape(-1, 2)
    coastal = np.array(coastal, dtype=bool)
    edges, candidates = [], set()
    if len(points) > 1:
        distances = np.linalg.norm(points[:, None]-points[None, :], axis=2)
        for i in range(len(points)):
            for j in np.argsort(distances[i])[1:9]:
                candidates.add(tuple(sorted((i, int(j)))))
        degree = np.zeros(len(points), dtype=int)
        for i, j in sorted(candidates, key=lambda pair: (distances[pair], pair)):
            if degree[i] >= (1 if coastal[i] else 3) or degree[j] >= (1 if coastal[j] else 3):
                continue
            a, b = points[[i, j]]
            if labels[int(a[1]), int(a[0])] != labels[int(b[1]), int(b[0])]:
                continue
            if not segment_on_land(land, a, b) or any(edges_conflict(points, (i, j), edge) for edge in edges):
                continue
            edges.append((i, j))
            degree[i] += 1
            degree[j] += 1
    return FaultNetwork(points, coastal, edges, ['Convergence']*len(edges))
