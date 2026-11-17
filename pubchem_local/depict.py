"""
2D structure depictions with RDKit, styled to resemble CDK Depict's "colour on white" renderings:

- CoordGen layout, rotated so the molecule's long axis is horizontal (chains drawn left to right)
- all bonds black (RDKit normally splits bonds to heteroatoms into two colours); only atom labels are coloured
- CDK's Jmol-based element colours (a few near-invisible ones darkened for readability)
- bond line width ~1/26 of the bond length, round line caps, CDK-like label size and double bond spacing
"""
import re

from rdkit import Chem
from rdkit.Chem import rdDepictor
from rdkit.Chem.Draw import rdMolDraw2D

rdDepictor.SetPreferCoordGen(True)

# CDK2DAtomColors (Jmol palette), as rendered by CDK 2.9 Depict. Boron (#FFB5B5) and silicon (#F0C8A0) are darkened:
# they are nearly invisible on white and in print.
CDK_COLORS = {
    7: "#3050F8",   # N
    8: "#FF0D0D",   # O
    9: "#90E050",   # F
    11: "#AB5CF2",  # Na
    15: "#FF8000",  # P
    16: "#C6C62C",  # S
    17: "#1FF01F",  # Cl
    34: "#FFA100",  # Se
    35: "#A62929",  # Br
    53: "#940094",  # I
    5: "#E07070",   # B (CDK: #FFB5B5)
    14: "#C09060",  # Si (CDK: #F0C8A0)
}
DARK_COLORS = {**CDK_COLORS, 7: "#7B93FF", 53: "#D070D0", 35: "#D07060", 11: "#C79BF7"}

BOND_WIDTH_FRACTION = 0.038  # CDK: 2.08 px line width for a 54 px bond
LABEL_SCALE = 0.62
_BOND_RE = re.compile(r"<path class='bond-[^>]*>")
_BOND_ID_RE = re.compile(r"<path class='(bond-\d+) ")
_WIDTH_RE = re.compile(r"stroke-width:[0-9.]+px")
HASH_MARKS = 8  # CDK draws hashed wedges with few, bold dashes; RDKit with many thin ones
_COLOR_RE = re.compile(r"(stroke|fill):#[0-9A-Fa-f]{6}")


def _rgb(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) / 255.0 for i in (1, 3, 5))


def _drawer(mol, width, height, dark, line_width=None):
    drawer = rdMolDraw2D.MolDraw2DSVG(width, height)
    opts = drawer.drawOptions()
    opts.clearBackground = False
    default = (0.9, 0.9, 0.9) if dark else (0.0, 0.0, 0.0)
    palette = {-1: default, 1: default, 6: default}
    palette.update({k: _rgb(v) for k, v in (DARK_COLORS if dark else CDK_COLORS).items()})
    opts.setAtomPalette(palette)
    opts.baseFontSize = LABEL_SCALE
    opts.padding = 0.06
    opts.additionalAtomLabelPadding = 0.1
    opts.multipleBondOffset = 0.16
    opts.scaleBondWidth = False
    opts.prepareMolsBeforeDrawing = False  # already prepared in depict_svg (re-preparing would add chiral Hs)
    if line_width is not None:
        opts.bondLineWidth = line_width
    drawer.DrawMolecule(mol)
    drawer.FinishDrawing()
    return drawer


def _bond_length_px(drawer, mol):
    bond = mol.GetBondWithIdx(0)
    p1 = drawer.GetDrawCoords(bond.GetBeginAtomIdx())
    p2 = drawer.GetDrawCoords(bond.GetEndAtomIdx())
    return ((p1.x - p2.x) ** 2 + (p1.y - p2.y) ** 2) ** 0.5


def depict_svg(mol, width=300, height=240, dark=False):
    """:return: SVG text, or None if the molecule cannot be drawn"""
    mol = Chem.Mol(mol)
    rdDepictor.Compute2DCoords(mol, canonOrient=True)
    # Wedges are derived from the final coordinates. No explicit H labels on stereocentres (as in CDK Depict).
    mol = rdMolDraw2D.PrepareMolForDrawing(mol, addChiralHs=False)

    # First pass only to learn the drawing scale; the second pass draws with a proportional line width so that
    # RDKit spaces hashed wedges and double bonds for that width itself.
    line_width = 2.0
    if mol.GetNumBonds():
        line_width = min(max(_bond_length_px(_drawer(mol, width, height, dark), mol) * BOND_WIDTH_FRACTION, 0.8), 4.0)
    svg = _drawer(mol, width, height, dark, line_width=line_width).GetDrawingText()

    bond_color = "#E6E6E6" if dark else "#000000"

    def _bond(match):
        element = _COLOR_RE.sub(lambda m: "%s:%s" % (m.group(1), bond_color), match.group(0))
        return element.replace("stroke-linecap:butt", "stroke-linecap:round")

    svg = _BOND_RE.sub(_bond, svg)
    return _thin_hashes(svg, line_width)


def _thin_hashes(svg, line_width):
    """Keep ~HASH_MARKS evenly spaced dashes of each hashed wedge (one <path> per dash) at the normal line width."""
    lines = svg.split("\n")
    groups = {}
    for i, line in enumerate(lines):
        m = _BOND_ID_RE.match(line)
        if m:
            groups.setdefault(m.group(1), []).append(i)
    drop = set()
    for idxs in groups.values():
        if len(idxs) <= HASH_MARKS:  # plain, double/triple or already sparse bonds
            continue
        last = len(idxs) - 1  # dashes are ordered from the stereocentre to the wide end; keep both ends
        keep = {idxs[int(round(k * last / float(HASH_MARKS - 1)))] for k in range(HASH_MARKS)}
        for i in idxs:
            if i in keep:
                lines[i] = _WIDTH_RE.sub("stroke-width:%.2fpx" % line_width, lines[i])
            else:
                drop.add(i)
    return "\n".join(line for i, line in enumerate(lines) if i not in drop)
