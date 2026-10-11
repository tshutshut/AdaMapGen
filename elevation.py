"""Additive Gaussian mountain and valley profiles along fault segments."""
from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class ElevationSettings:
    base: float = 300.
    max_height: float = 1500.
    valley_depth: float = 400.
    peaks: int = 4
    count_variation: int = 1
    height_variation: float = .3
    width: float = 2.
    width_variation: float = .25
    steepness: float = 2.
    seed: int = 1
    ruggedness: float = .65


# What the function does:
#     Adds smooth peaks along convergent faults and subtracts divergent valleys.
# Inputs (if any):
#     land: Boolean mask; network: frozen faults and modes; settings: height controls.
# Outputs (if any):
#     Float32 heightmap in artistic elevation units; water stays zero and land >= 0.
def generate_elevation(land, network, settings):
    result = np.full(land.shape, settings.base, dtype=np.float32)
    width = settings.width*min(land.shape)/100
    for index, ((i, j), mode) in enumerate(zip(network.edges, network.modes)):
        if mode == 'Inactive':
            continue
        # Independent streams keep other faults stable when one mode changes.
        rng = np.random.default_rng(np.random.SeedSequence([settings.seed, index]))
        count = max(1, settings.peaks+int(rng.integers(-settings.count_variation, settings.count_variation+1)))
        a, b = network.points[[i, j]]
        delta = b-a
        length = np.linalg.norm(delta)
        if length < 1e-6:
            continue
        tangent = delta/length
        normal = np.array([-tangent[1], tangent[0]])
        spacing = length/count
        along_sigma = max(1., spacing*.65)
        limit = settings.max_height if mode == 'Convergence' else settings.valley_depth
        sign = 1 if mode == 'Convergence' else -1
        for peak in range(count):
            # Peak locations remain on the line. Slight positional jitter and
            # randomized widths/heights avoid an evenly spaced string of cones.
            fraction = (peak+.5+rng.uniform(-.25, .25))/count
            center = a+delta*fraction
            amplitude = limit*(1-settings.height_variation*rng.random())
            phases = rng.uniform(0, 2*np.pi, 5)
            across_sigma = max(.5, width*rng.uniform(1-settings.width_variation, 1+settings.width_variation))
            # Generalized Gaussian: exponent 2 gives a true Gaussian; higher
            # exponents give steeper flanks. Contributions sum before any clamp.
            cutoff = (2*np.log(1000))**(1/settings.steepness)
            extent = np.abs(tangent)*along_sigma*4+np.abs(normal)*across_sigma*cutoff
            left, top = np.maximum(0, np.floor(center-extent)).astype(int)
            right, bottom = np.minimum([land.shape[1], land.shape[0]], np.ceil(center+extent)).astype(int)
            if left >= right or top >= bottom:
                continue
            dx = np.arange(left, right)+.5-center[0]
            for start in range(top, bottom, 64):
                stop = min(start+64, bottom)
                dy = (np.arange(start, stop)+.5-center[1])[:, None]
                along = (dx*tangent[0]+dy*tangent[1])/along_sigma
                transverse = (dx*normal[0]+dy*normal[1])/across_sigma
                # A winding crest breaks the straight chain of round summits.
                # Broad bends carry smaller irregularities at several scales.
                bend = settings.ruggedness*(.35*np.sin(along*1.7+phases[0])+
                                            .15*np.sin(along*4.3+phases[1]))
                across = np.abs(transverse-bend)
                profile = np.exp(-.5*(along*along+across**settings.steepness))
                # Branching flank grooves follow oblique paths away from the
                # main crest. Multi-scale modulation makes spurs and saddles,
                # rather than adding uncorrelated speckle to the elevation.
                fine = (.55*np.sin(along*7.1+transverse*2.3+phases[2])+
                        .3*np.sin(along*13.7-transverse*4.1+phases[3])+
                        .15*np.sin(along*25.3+transverse*7.7+phases[4]))
                grooves = (.5+.5*np.sin(along*4.8+across*2.8+fine))**2
                flank = np.minimum(across, 1.)
                profile *= 1-settings.ruggedness*.8*grooves*flank
                profile *= 1-settings.ruggedness*.22*(.5+.5*fine)
                result[start:stop, left:right] += sign*amplitude*profile
    # Clamping happens only after summing all faults, so order does not cause
    # mountains and valleys to overwrite one another. Coastlines never change.
    np.maximum(result, 0, out=result)
    result[~land] = 0
    return result
