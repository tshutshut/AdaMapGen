"""Elevation bands and labelled index contours for the visible map viewport."""
import numpy as np

# Color stops run from low to high elevation. Palettes are deliberately light
# enough to retain dark contour labels and distinguish nearby terrain bands.
PALETTES = {
    'Terrain': ((91, 156, 100), (170, 193, 109), (224, 206, 145), (172, 135, 107), (248, 246, 237)),
    'Alpine': ((102, 168, 145), (174, 203, 158), (194, 177, 147), (177, 183, 190), (250, 252, 255)),
    'Sunset': ((130, 118, 181), (196, 133, 178), (233, 163, 146), (245, 204, 145), (255, 246, 205)),
    'Ocean tones': ((89, 143, 184), (101, 183, 197), (160, 213, 205), (207, 234, 218), (245, 252, 234)),
    'Grayscale': ((140, 140, 140), (170, 170, 170), (200, 200, 200), (225, 225, 225), (250, 250, 250)),
}


# What the function does:
#     Converts visible elevations into discrete bands, fine contours, and index lines.
# Inputs (if any):
#     heights: sampled viewport elevations; land: matching mask; interval: height step;
#     palette: named low-to-high color gradient.
# Outputs (if any):
#     RGB pixels and sparse (x, y, elevation) labels for every fifth contour.
def contour_colors(heights, land, interval, palette='Terrain'):
    interval = max(1., float(interval))
    bands = np.floor(heights/interval).astype(np.int32)
    # Quantize elevation first: each level has one color. The fixed mapping
    # stays stable when zooming, panning, or editing other parts of the map.
    stops = np.array(PALETTES[palette], dtype=float)
    positions = np.linspace(0, 1, len(stops))
    height_fraction = 1-np.exp(-(bands*interval)/1800)
    pixels = np.stack([np.interp(height_fraction, positions, stops[:, channel])
                       for channel in range(3)], axis=-1).round().astype(np.uint8)
    minor = np.zeros(land.shape, dtype=bool)
    major = np.zeros_like(minor)
    levels = np.zeros(land.shape, dtype=np.float32)
    for a, b in ((np.s_[:-1, :], np.s_[1:, :]), (np.s_[:, :-1], np.s_[:, 1:])):
        valid = land[a] & land[b]
        crossed = valid & (bands[a] != bands[b])
        index = valid & (bands[a]//5 != bands[b]//5)
        minor[a] |= crossed
        major[a] |= index
        threshold = (np.minimum(bands[a], bands[b])//5+1)*5*interval
        levels[a] = np.where(index, threshold, levels[a])
    pixels[minor] = (pixels[minor].astype(float)*.65).astype(np.uint8)
    pixels[major] = [45, 40, 35]
    # Spaced labels avoid filling steep regions with unreadable numbers.
    labels = []
    for y in range(40, land.shape[0]-20, 130):
        for x in range(40, land.shape[1]-20, 150):
            yy, xx = np.where(major[y-20:y+20, x-20:x+20])
            if len(xx):
                index = np.argmin((xx-20)**2+(yy-20)**2)
                px, py = x-20+int(xx[index]), y-20+int(yy[index])
                labels.append((px, py, int(levels[py, px])))
    return pixels, labels
