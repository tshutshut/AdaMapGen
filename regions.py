"""Land/sea region objects extracted losslessly from the painted grid."""
from dataclasses import dataclass, field
from collections import deque
import numpy as np

# Areas are measured in original map cells, not scaled preview pixels.
MIN_REGION_AREA = 5


# What the function does:
#     Removes tiny land/sea regions and rebuilds their ownership and nesting.
# Inputs (if any):
#     land: map snapshot; previous: optional old objects for ID matching.
# Outputs (if any):
#     Clean RegionMap and packed edits that preserve cleanup on regeneration.
def build_clean_regions(land, previous=None):
    original = land.copy()
    result = build_regions(land, previous)
    while True:
        candidates = [obj for obj in result.objects.values()
                      if obj.area < MIN_REGION_AREA and obj.neighbors]
        if not candidates:
            break
        # Handle one region at a time: adjacent tiny regions must merge rather
        # than simultaneously exchange colors forever. Each pass reduces the
        # number of objects. The sole region of a tiny map cannot be removed.
        obj = min(candidates, key=lambda item: (item.area, item.id))
        land[result.labels == obj.id] = obj.kind == 'sea'
        result = build_regions(land, result)
    edits = []
    changed = original != land
    for value in (False, True):
        mask = changed & (land == value)
        if mask.any():
            edits.append({'mask': np.packbits(mask), 'shape': mask.shape, 'value': value})
    return result, edits


@dataclass
class Region:
    id: int
    kind: str
    area: int
    bounds: tuple
    parent: int | None = None
    level: int = 0
    touches_border: bool = False
    neighbors: set = field(default_factory=set)
    contours: list = field(default_factory=list)


@dataclass
class RegionMap:
    labels: np.ndarray
    objects: dict
    next_id: int

    # What the function does:
    #     Renders the region partition without overwriting enclosed objects.
    # Inputs (if any):
    #     None; reads region IDs and land/sea types.
    # Outputs (if any):
    #     A Boolean land mask identical to the analyzed grid.
    def render(self):
        palette = np.zeros(self.next_id, dtype=bool)
        for region in self.objects.values():
            palette[region.id] = region.kind == 'land'
        return palette[self.labels]


# What the function does:
#     Traces ordered closed pixel-edge contours for one region in its bounding box.
# Inputs (if any):
#     mask: local region mask; x/y: its map offset.
# Outputs (if any):
#     A list of closed arrays of (x, y) boundary points, including enclosed boundaries.
def trace_contours(mask, x, y):
    p = np.pad(mask, 1)
    edges = {}
    # Directed boundaries keep the occupied cell to the right.
    for boundary, start, end in (
            (mask & ~p[:-2, 1:-1], (0, 0), (1, 0)),
            (mask & ~p[1:-1, 2:], (1, 0), (1, 1)),
            (mask & ~p[2:, 1:-1], (1, 1), (0, 1)),
            (mask & ~p[1:-1, :-2], (0, 1), (0, 0))):
        for yy, xx in zip(*np.where(boundary)):
            a = (int(xx)+x+start[0], int(yy)+y+start[1])
            b = (int(xx)+x+end[0], int(yy)+y+end[1])
            edges.setdefault(a, []).append(b)
    loops = []
    directions = {(1, 0): 0, (0, 1): 1, (-1, 0): 2, (0, -1): 3}
    while edges:
        origin = next(iter(edges))
        current = origin
        loop = [origin]
        previous_direction = None
        while True:
            options = edges[current]
            choice = 0
            if previous_direction is not None and len(options) > 1:
                priorities = []
                for target in options:
                    direction = directions[(target[0]-current[0], target[1]-current[1])]
                    priorities.append({1: 0, 0: 1, 3: 2, 2: 3}[(direction-previous_direction) % 4])
                choice = int(np.argmin(priorities))
            target = options.pop(choice)
            if not options:
                del edges[current]
            previous_direction = directions[(target[0]-current[0], target[1]-current[1])]
            current = target
            loop.append(current)
            if current == origin:
                break
        loops.append(np.array(loop, dtype=np.int32))
    return loops


# What the function does:
#     Labels edge-connected land and sea, tracks IDs, and derives nesting levels.
# Inputs (if any):
#     land: Boolean image; previous: optional region map for overlap-based ID matching.
# Outputs (if any):
#     RegionMap with cell ownership, contours, adjacency, parent, and nesting level.
def build_regions(land, previous=None):
    height, width = land.shape
    runs, parents, contacts = [], [], []
    last = []

    # What the function does:
    #     Resolves a run's union-find representative with path compression.
    # Inputs (if any):
    #     index: run index.
    # Outputs (if any):
    #     Representative run index.
    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for y, row in enumerate(land):
        cuts = np.concatenate(([0], np.flatnonzero(row[1:] != row[:-1])+1, [width]))
        current = []
        for left, right in zip(cuts[:-1], cuts[1:]):
            index = len(runs)
            runs.append((y, int(left), int(right), bool(row[left])))
            parents.append(index)
            current.append(index)
            if len(current) > 1:
                contacts.append((current[-2], index))
        i = j = 0
        while i < len(last) and j < len(current):
            a, b = last[i], current[j]
            ra, rb = runs[a], runs[b]
            if min(ra[2], rb[2]) > max(ra[1], rb[1]):
                if ra[3] == rb[3]:
                    parents[find(b)] = find(a)
                else:
                    contacts.append((a, b))
            if ra[2] <= rb[2]:
                i += 1
            else:
                j += 1
        last = current
    roots = [find(i) for i in range(len(runs))]
    unique = sorted(set(roots))
    dense = {root: i for i, root in enumerate(unique)}
    labels = np.empty(land.shape, dtype=np.int32)
    kinds = {dense[root]: runs[root][3] for root in unique}
    for index, (y, left, right, _) in enumerate(runs):
        labels[y, left:right] = dense[roots[index]]
    # Match strongest overlaps first: a split's largest matching child keeps
    # the old ID; a merge keeps its strongest matching predecessor's ID.
    mapping = {}
    used = set()
    next_id = previous.next_id if previous is not None else 1
    if previous is not None and previous.labels.shape == land.shape:
        scores = {}
        for start in range(0, height, 64):
            pairs = labels[start:start+64].astype(np.int64)*next_id+previous.labels[start:start+64]
            values, counts = np.unique(pairs, return_counts=True)
            for value, count in zip(values, counts):
                new, old = divmod(int(value), next_id)
                if old in previous.objects and kinds[new] == (previous.objects[old].kind == 'land'):
                    scores[new, old] = scores.get((new, old), 0)+int(count)
        for (new, old), score in sorted(scores.items(), key=lambda item: (-item[1], item[0])):
            if new not in mapping and old not in used:
                mapping[new] = old
                used.add(old)
    for new in kinds:
        if new not in mapping:
            mapping[new] = next_id
            next_id += 1
    lookup = np.array([mapping[i] for i in range(len(unique))], dtype=np.int32)
    labels = lookup[labels]
    objects = {}
    for index, (y, left, right, kind) in enumerate(runs):
        identity = mapping[dense[roots[index]]]
        border = y in (0, height-1) or left == 0 or right == width
        if identity not in objects:
            objects[identity] = Region(identity, 'land' if kind else 'sea', 0, (left, y, right, y+1))
        obj = objects[identity]
        x0, y0, x1, y1 = obj.bounds
        obj.bounds = (min(x0, left), min(y0, y), max(x1, right), max(y1, y+1))
        obj.area += right-left
        obj.touches_border |= border
    for a, b in contacts:
        a, b = mapping[dense[roots[a]]], mapping[dense[roots[b]]]
        if a != b:
            objects[a].neighbors.add(b)
            objects[b].neighbors.add(a)
    # All border-touching regions are roots beneath an implicit exterior.
    # Breadth-first levels also handle multiple roots when a channel reaches
    # the frame. Corner-only contacts never join two objects.
    queue = deque(sorted(i for i, obj in objects.items() if obj.touches_border))
    visited = set(queue)
    while queue:
        parent = objects[queue.popleft()]
        for child_id in sorted(parent.neighbors):
            if child_id not in visited:
                child = objects[child_id]
                child.parent, child.level = parent.id, parent.level+1
                visited.add(child_id)
                queue.append(child_id)
    for obj in objects.values():
        x0, y0, x1, y1 = obj.bounds
        obj.contours = trace_contours(labels[y0:y1, x0:x1] == obj.id, x0, y0)
    return RegionMap(labels, objects, next_id)
