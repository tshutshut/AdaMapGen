"""Preset-driven maps made from curves and hierarchical irregular blobs."""
from dataclasses import dataclass
import numpy as np

MAP_TYPES = ('Single large island', 'Inland sea', 'Many archipelagos')


@dataclass(frozen=True)
class MapPreset:
    # Distances derive from mean adjacent root spacing measured along the curve.
    # Ratios after level 1 multiply the previous level's distance, not image size.
    closed: bool
    inverted: bool = False
    clusters: tuple = (8, 12)
    children_per_parent: tuple = (3, 5)
    child_angle_variation: float = 180.
    seed_jitter: float = .20
    open_end_margin: float = .08
    distance_ratios: tuple = (.24, .4)
    blob_radius_ratio: float = .30
    blob_shrink_ratios: tuple = (.55, .5)
    blob_size_variation: float = .15
    curve_scale: float = 1.
    curve_waviness: float = 1.

    # What the function does:
    #     Rejects settings that would reverse ranges or stop blobs shrinking.
    # Inputs (if any):
    #     None; checks the preset's immutable fields.
    # Outputs (if any):
    #     None; raises ValueError for unsupported combinations.
    def validate(self):
        for bounds, low, high in ((self.clusters, 3, 16), (self.children_per_parent, 1, 5)):
            if len(bounds) != 2 or not low <= bounds[0] <= bounds[1] <= high or any(int(v) != v for v in bounds):
                raise ValueError('Invalid seed or child count range.')
        if not 1 <= len(self.distance_ratios) <= 3 or len(self.distance_ratios) != len(self.blob_shrink_ratios):
            raise ValueError('Use one to three matching distance and size levels.')
        if not all(.05 <= v <= .9 for v in self.distance_ratios):
            raise ValueError('Distance ratios must be between 0.05 and 0.9.')
        if not all(.1 <= v <= .7 for v in self.blob_shrink_ratios):
            raise ValueError('Blob shrink ratios must be between 0.1 and 0.7.')
        for value, low, high in ((self.child_angle_variation, 0, 180),
                                  (self.seed_jitter, 0, .45), (self.open_end_margin, 0, .3),
                                  (self.blob_radius_ratio, .05, .5), (self.blob_size_variation, 0, .35),
                                  (self.curve_scale, .4, 1.2), (self.curve_waviness, 0, 1.5)):
            if not low <= value <= high:
                raise ValueError('Preset value outside its supported range.')
        # Maximum child multiplier is 0.7 * 1.35 = 0.945, always below 1.


PRESETS = {
    MAP_TYPES[0]: MapPreset(closed=True),
    MAP_TYPES[1]: MapPreset(closed=True, inverted=True),
    MAP_TYPES[2]: MapPreset(closed=False, clusters=(5, 9),
                          distance_ratios=(.45, .5), blob_radius_ratio=.24),
}


@dataclass
class MapGeometry:
    curve: np.ndarray
    centers: np.ndarray
    children: np.ndarray
    # Parent indices address centers followed by all descendants, in that order.
    parents: np.ndarray
    polar: np.ndarray
    radii: np.ndarray
    levels: np.ndarray
    closed: bool
    seed_locations: np.ndarray
    average_seed_distance: float
    # Per-blob orientation, elongation, four wave amplitudes, and four phases.
    # Keeping these with the geometry makes rasterization repeatable.
    blob_profiles: np.ndarray

    # What the function does:
    #     Exposes every seed in the same order as radii, levels, and parent IDs.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     Array of seed coordinates in image-edge units.
    @property
    def points(self):
        return np.concatenate((self.centers, self.children))


# What the function does:
#     Creates an open wandering curve or a closed irregular loop.
# Inputs (if any):
#     width/height: dimensions; rng: seeded generator; closed: loop flag.
# Outputs (if any):
#     512 ordered points; closed curves repeat the first point at the end.
def guiding_curve(width, height, rng, closed, scale=1., waviness=1.):
    t = np.linspace(0, 1, 512)
    phases = rng.uniform(0, 2*np.pi, 3)
    if closed:
        angle = t*2*np.pi
        radius = 1+waviness*(.13*np.sin(3*angle+phases[0])+.07*np.sin(5*angle+phases[1]))
        curve = np.column_stack((width*(.5+.27*radius*np.cos(angle)),
                                 height*(.5+.27*radius*np.sin(angle))))
        curve[-1] = curve[0]
    else:
        across = .5+waviness*(.15*np.sin(2*np.pi*t+phases[0])+.07*np.sin(4*np.pi*t+phases[1]))
        along = .15+.7*t
        curve = np.column_stack((along*width, across*height)) if width >= height else np.column_stack((across*width, along*height))
        if rng.random() < .5:
            curve[:, 0] = width-curve[:, 0]
    center = np.array([width/2, height/2])
    return center+(curve-center)*scale


# What the function does:
#     Places seeds along a curve and recursively creates smaller child blobs.
# Inputs (if any):
#     width/height: dimensions; rng: seeded generator; preset: construction settings.
# Outputs (if any):
#     Complete geometry with parent links, levels, sizes, and polar offsets.
def build_geometry(width, height, rng, preset):
    preset.validate()
    curve = guiding_curve(width, height, rng, preset.closed, preset.curve_scale, preset.curve_waviness)
    distance = np.r_[0, np.cumsum(np.linalg.norm(np.diff(curve, axis=0), axis=1))]
    count = int(rng.integers(preset.clusters[0], preset.clusters[1]+1))
    length = distance[-1]
    if preset.closed:
        spacing = length/count
        # Wrap the last-to-first gap too; a random phase avoids a fixed start.
        locations = np.sort((np.arange(count)*spacing+rng.uniform(0, spacing)+
                             rng.uniform(-preset.seed_jitter, preset.seed_jitter, count)*spacing) % length)
        average = np.diff(np.r_[locations, locations[0]+length]).mean()
    else:
        margin = preset.open_end_margin*length
        spacing = (length-2*margin)/(count-1)
        locations = np.linspace(margin, length-margin, count)
        # Limit endpoint offsets so every seed stays on the open curve.
        jitter = min(preset.seed_jitter*spacing, margin)
        locations += rng.uniform(-jitter, jitter, count)
        average = np.diff(locations).mean()
    centers = np.column_stack([np.interp(locations, distance, curve[:, axis]) for axis in (0, 1)])
    points = list(centers)
    variation = preset.blob_size_variation
    radii = list(average*preset.blob_radius_ratio*rng.uniform(1-variation, 1+variation, count))
    levels = [0]*count
    parents, polar = [], []
    frontier = list(range(count))
    reach = average
    for level, distance_ratio in enumerate(preset.distance_ratios, start=1):
        # All children at a level share a distance scale, while each direction
        # is independently uniform over a full circle. Levels progressively shrink.
        reach *= distance_ratio
        next_frontier = []
        for parent in frontier:
            amount = int(rng.integers(preset.children_per_parent[0], preset.children_per_parent[1]+1))
            rotation = None
            for child_index in range(amount):
                radius = radii[parent]*preset.blob_shrink_ratios[level-1]*rng.uniform(1-variation, 1+variation)
                angle = rng.uniform(0, 2*np.pi)
                # Keep the original random draws, so this setting does not
                # reshuffle blob sizes or counts. Zero produces evenly spaced
                # directions with a random group rotation; 180 is fully random.
                if rotation is None:
                    rotation = angle
                if preset.child_angle_variation < 180:
                    regular = rotation+child_index*2*np.pi/amount
                    offset = (angle-regular+np.pi) % (2*np.pi)-np.pi
                    angle = (regular+offset*preset.child_angle_variation/180) % (2*np.pi)
                point = points[parent]+reach*np.array([np.cos(angle), np.sin(angle)])
                next_frontier.append(len(points))
                points.append(point)
                radii.append(radius)
                levels.append(level)
                parents.append(parent)
                polar.append((reach, angle))
        frontier = next_frontier
    # Sample shapes only after the entire seed tree is built, so adding shape
    # variation does not change the existing centers, hierarchy, or size ratios.
    profiles = np.empty((len(points), 10))
    profiles[:, 0] = rng.uniform(0, 2*np.pi, len(points))
    profiles[:, 1] = rng.uniform(1.05, 1.65, len(points))
    waves = rng.uniform(.4, 1, (len(points), 4))/np.arange(2, 6)
    profiles[:, 2:6] = waves/waves.sum(axis=1, keepdims=True)*rng.uniform(.15, .30, (len(points), 1))
    profiles[:, 6:] = rng.uniform(0, 2*np.pi, (len(points), 4))
    return MapGeometry(curve, centers, np.array(points[count:]), np.array(parents),
                       np.array(polar), np.array(radii), np.array(levels), preset.closed,
                       locations, float(average), profiles)


# What the function does:
#     Paints a randomly oriented oval with smooth asymmetric lobes and indentations.
# Inputs (if any):
#     mask: destination; center: seed coordinates; radius: nominal size;
#     profile: stored orientation, elongation, angular amplitudes, and phases.
# Outputs (if any):
#     None; adds one connected continuous shape to the mask in bounded row batches.
def paint_blob(mask, center, radius, profile):
    x, y = center
    orientation, elongation = profile[:2]
    amplitudes, phases = profile[2:6], profile[6:]
    # Reciprocal axis scaling preserves the base oval's area. Bounded waves
    # keep its radial boundary positive, avoiding self-intersecting outlines.
    rx, ry = radius*np.sqrt(elongation), radius/np.sqrt(elongation)
    extent = max(rx, ry)*(1+amplitudes.sum())
    left, right = max(0, int(np.floor(x-extent))), min(mask.shape[1], int(np.ceil(x+extent)))
    top, bottom = max(0, int(np.floor(y-extent))), min(mask.shape[0], int(np.ceil(y+extent)))
    if left >= right or top >= bottom:
        return
    cosine, sine = np.cos(orientation), np.sin(orientation)
    dx = np.arange(left, right)+.5-x
    for start in range(top, bottom, 64):
        stop = min(start+64, bottom)
        dy = (np.arange(start, stop)+.5-y)[:, None]
        u, v = (cosine*dx+sine*dy)/rx, (-sine*dx+cosine*dy)/ry
        angle = np.arctan2(v, u)
        edge = np.ones_like(angle)
        for frequency, amplitude, phase in zip(range(2, 6), amplitudes, phases):
            edge += amplitude*np.sin(frequency*angle+phase)
        mask[start:stop, left:right] |= u*u+v*v <= edge*edge


# What the function does:
#     Fills a closed curve using pixel-center scanline intersections.
# Inputs (if any):
#     mask: destination Boolean image; curve: closed polygon.
# Outputs (if any):
#     None; fills the interior in place without full-image temporary arrays.
def fill_curve(mask, curve):
    a, b = curve[:-1], curve[1:]
    for y in range(max(0, int(curve[:, 1].min())), min(mask.shape[0], int(np.ceil(curve[:, 1].max())))):
        scan = y+.5
        crossing = (a[:, 1] > scan) != (b[:, 1] > scan)
        starts, ends = a[crossing], b[crossing]
        xs = np.sort(starts[:, 0]+(scan-starts[:, 1])*(ends[:, 0]-starts[:, 0])/(ends[:, 1]-starts[:, 1]))
        for left, right in zip(xs[::2], xs[1::2]):
            mask[y, max(0, int(np.ceil(left-.5))):min(mask.shape[1], int(np.ceil(right-.5)))] = True


# What the function does:
#     Combines the filled interior and hierarchical blobs into a silhouette.
# Inputs (if any):
#     width/height: dimensions; geometry: curve and blob tree.
# Outputs (if any):
#     Boolean foreground mask, before optional land/sea inversion.
def rasterize_geometry(width, height, geometry):
    mask = np.zeros((height, width), dtype=bool)
    if geometry.closed:
        fill_curve(mask, geometry.curve)
    for center, radius, level, profile in zip(geometry.points, geometry.radii, geometry.levels, geometry.blob_profiles):
        # Open-curve roots organize clusters without painting connecting land.
        if level == 0 and not geometry.closed:
            continue
        paint_blob(mask, center, radius, profile)
    return mask


# What the function does:
#     Generates every map through one preset-driven curve/blob pipeline.
# Inputs (if any):
#     kind: preset; width/height: dimensions; seed: random seed;
#     turns: clockwise quarter turns; with_geometry: include construction data.
# Outputs (if any):
#     Boolean land mask, optionally paired with its rotated construction tree.
def generate_map(kind, width, height, seed, turns=0, with_geometry=False, preset=None):
    if kind not in PRESETS:
        raise ValueError('Select a supported map type.')
    if not 16 <= width <= 5120 or not 16 <= height <= 5120:
        raise ValueError('Map dimensions must be between 16 and 5120 cells.')
    preset = PRESETS[kind] if preset is None else preset
    if (preset.closed, preset.inverted) != (PRESETS[kind].closed, PRESETS[kind].inverted):
        raise ValueError('Curve closure and inversion must match the selected map type.')
    geometry = build_geometry(width, height, np.random.default_rng(seed), preset)
    mask = rasterize_geometry(width, height, geometry)
    if preset.inverted:
        mask = ~mask
    mask = np.rot90(mask, -turns % 4).copy()
    for _ in range(turns % 4):
        for name in ('curve', 'centers', 'children'):
            points = getattr(geometry, name)
            setattr(geometry, name, np.column_stack((height-points[:, 1], points[:, 0])))
        width, height = height, width
    geometry.polar[:, 1] = (geometry.polar[:, 1]+turns*np.pi/2) % (2*np.pi)
    geometry.blob_profiles[:, 0] = (geometry.blob_profiles[:, 0]+turns*np.pi/2) % (2*np.pi)
    return (mask, geometry) if with_geometry else mask
