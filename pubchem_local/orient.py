"""
Standardised orientation of 2D structure layouts (used with the CDK depictions in pubchem_local.cdk).

CDK's structure diagram generator lays a molecule out in input (SMILES) atom order and then turns it to the widest
orientation, so close analogues (e.g. 2,5-dimethoxyphenethylamine and its 4-bromo derivative) can come out rotated or
mirrored relative to each other. After CDK has generated the coordinates, this module re-orients the main fragment with
deterministic rules anchored on its ring system:

1. The anchor is the largest ring system (ties: most aromatic atoms). Systems of more than two fused rings (steroids,
   morphinans, ...) are left in CDK's template-based orientation, which matches how chemists usually draw them.
2. Candidate transforms keep the layout's bond angles: mirror images, 180 degree rotations and, when the anchor consists
   of six-membered rings only, rotations in 60 degree steps.
3. The largest substituent of the anchor (by heavy atoms, then atomic number sum) points to the right.
4. The next largest substituent points up; on ties the one closest to the largest substituent (ring path distance) is
   used, so e.g. a 2-methoxy group is placed above the ring in every 2,5-dimethoxyphenyl analogue.
5. Wedge/hash bonds are re-derived from the stereo configuration afterwards, so mirroring never inverts stereocentres.

Molecules without rings keep CDK's own orientation. Other fragments (counter-ions, solvents) are laid out separately
and placed to the right of the main fragment.
"""
import math

BOND_LENGTH = 1.5  # CDK's default bond length in layout units
MAX_ANCHOR_RINGS = 2  # only re-orient around single rings and bicyclic systems (benzene, naphthalene, indole, ...)


def _coords(atom):
    p = atom.getPoint2d()
    return float(p.x), float(p.y)


def _anchor_ring_system(j, mol):
    search = j["RingSearch"](mol)
    systems = list(search.fusedRingFragments()) + list(search.isolatedRingFragments())
    if not systems:
        return None, None

    def key(system):
        aromatic = sum(1 for a in system.atoms() if a.isAromatic())
        return (system.getAtomCount(), aromatic)

    anchor = max(systems, key=key)
    rings = _rings_of(j, anchor)
    if len(rings) > MAX_ANCHOR_RINGS:
        # Larger fused systems (steroids, morphinans, ...) have conventional orientations that CDK's layout
        # templates already reproduce; re-orienting them makes them look unfamiliar.
        return None, None
    six_only = all(len(ring) == 6 for ring in rings)
    return set(int(mol.indexOf(a)) for a in anchor.atoms()), six_only


def _rings_of(j, system):
    """Smallest set of smallest rings of a ring system, as lists of atom indices (within the system)."""
    try:
        cycles = j["Cycles"].sssr(system).paths()
        return [list(path)[:-1] for path in cycles]
    except Exception:
        return [list(range(system.getAtomCount()))]


def _neighbours(mol):
    adj = {i: set() for i in range(mol.getAtomCount())}
    for bond in mol.bonds():
        a, b = int(mol.indexOf(bond.getBegin())), int(mol.indexOf(bond.getEnd()))
        adj[a].add(b)
        adj[b].add(a)
    return adj


def _substituents(mol, adj, anchor):
    """Groups of non-anchor atoms attached to the anchor: list of dicts(atoms, attach, size, weight)."""
    seen = set(anchor)
    groups = []
    for ring_atom in sorted(anchor):
        for start in sorted(adj[ring_atom]):
            if start in seen:
                continue
            atoms, stack = [], [start]
            seen.add(start)
            while stack:
                cur = stack.pop()
                atoms.append(cur)
                for nxt in adj[cur]:
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
            weight = sum(int(mol.getAtom(i).getAtomicNumber() or 0) for i in atoms)
            groups.append({"atoms": atoms, "attach": ring_atom, "size": len(atoms), "weight": weight})
    groups.sort(key=lambda g: (-g["size"], -g["weight"], g["attach"]))
    return groups


def _ring_distance(adj, anchor, a, b):
    """Shortest path length between two anchor atoms, travelling through the anchor only."""
    frontier, dist = [a], {a: 0}
    while frontier:
        nxt = []
        for cur in frontier:
            if cur == b:
                return dist[cur]
            for n in adj[cur]:
                if n in anchor and n not in dist:
                    dist[n] = dist[cur] + 1
                    nxt.append(n)
        frontier = nxt
    return 99


def _centroid(points):
    return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))


def orient_fragment(j, mol):
    """
    Re-orient a single connected fragment that already has 2D coordinates (in place).

    :return: the applied transform (mirrored, degrees) or None if the layout was kept
    """
    anchor, six_only = _anchor_ring_system(j, mol)
    if not anchor:
        return None
    adj = _neighbours(mol)
    groups = _substituents(mol, adj, anchor)
    if not groups:
        return None

    xy = [_coords(mol.getAtom(i)) for i in range(mol.getAtomCount())]
    cx, cy = _centroid([xy[i] for i in anchor])

    main = groups[0]
    second = None
    if len(groups) > 1:
        top = [g for g in groups[1:] if (g["size"], g["weight"]) == (groups[1]["size"], groups[1]["weight"])]
        second = min(top, key=lambda g: (_ring_distance(adj, anchor, main["attach"], g["attach"]), g["attach"]))

    def vec(group):
        gx, gy = _centroid([xy[i] for i in group["atoms"]])
        return gx - cx, gy - cy

    main_v = vec(main)
    second_v = vec(second) if second else None

    angles = range(0, 360, 60) if six_only else (0, 180)
    best, best_score = None, None
    for mirror in (False, True):
        for deg in angles:
            rad = math.radians(deg)
            cos_t, sin_t = math.cos(rad), math.sin(rad)

            def tf(v):
                x, y = v
                if mirror:
                    y = -y  # reflect across the x axis, then rotate
                return x * cos_t - y * sin_t, x * sin_t + y * cos_t

            mx, my = tf(main_v)
            norm = math.hypot(mx, my) or 1.0
            score = [round(mx / norm, 2)]  # main substituent to the right
            if second_v is not None:
                sx, sy = tf(second_v)
                score.append(round(sy / (math.hypot(sx, sy) or 1.0), 2))  # second substituent up
            score.append(round(my / norm, 2))  # then main substituent slightly up rather than down
            score.append(-int(mirror))  # finally prefer no mirroring, then the smallest rotation
            score.append(-deg)
            if best_score is None or score > best_score:
                best, best_score = (mirror, deg), score

    mirror, deg = best
    if not mirror and deg == 0:
        return None
    rad = math.radians(deg)
    cos_t, sin_t = math.cos(rad), math.sin(rad)
    for i in range(mol.getAtomCount()):
        x, y = xy[i][0] - cx, xy[i][1] - cy
        if mirror:
            y = -y
        mol.getAtom(i).setPoint2d(j["Point2d"](x * cos_t - y * sin_t + cx, x * sin_t + y * cos_t + cy))
    return mirror, deg


def layout(j, mol):
    """
    Generate 2D coordinates for 'mol' (all fragments) with CDK, orient the largest fragment and place the others to
    its right. Wedge bonds are assigned for the final coordinates.
    """
    fragments = list(j["ConnectivityChecker"].partitionIntoMolecules(mol).atomContainers())
    fragments.sort(key=lambda f: -f.getAtomCount())
    sdg = j["StructureDiagramGenerator"]()
    for frag in fragments:
        sdg.generateCoordinates(frag)
    transform = orient_fragment(j, fragments[0])

    def bounds(frag):
        pts = [_coords(a) for a in frag.atoms()]
        return min(p[0] for p in pts), max(p[0] for p in pts), min(p[1] for p in pts), max(p[1] for p in pts)

    _, right, low, high = bounds(fragments[0])
    mid_y = (low + high) / 2.0
    for frag in fragments[1:]:
        f_left, f_right, f_low, f_high = bounds(frag)
        dx = right + 1.5 * BOND_LENGTH - f_left
        dy = mid_y - (f_low + f_high) / 2.0
        for atom in frag.atoms():
            x, y = _coords(atom)
            atom.setPoint2d(j["Point2d"](x + dx, y + dy))
        right += 1.5 * BOND_LENGTH + (f_right - f_left)
    for frag in fragments:
        j["NonplanarBonds"].assign(frag)
    return transform
