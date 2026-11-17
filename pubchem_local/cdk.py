"""
Structure depictions with the CDK (Chemistry Development Kit) DepictionGenerator, the engine behind CDK Depict.

The CDK is a Java library; it is loaded into this Python process with JPype. The jars are found in CDK_CLASSPATH
(the Docker image sets /opt/cdk/lib), or in ./java/lib of a source checkout. If Java, JPype or the jars are missing,
available() returns False and callers fall back to the RDKit renderer in pubchem_local.depict.
"""
import glob
import io
import os
import threading
from pathlib import Path

try:
    import jpype
except ImportError:  # pragma: no cover
    jpype = None

STYLES = ("cow", "bow", "wob", "cob")  # colour/black on white, white/colour on black (as in CDK Depict)
ABBREVIATIONS = ("off", "on")
ANNOTATIONS = ("none", "number", "cip")
FORMATS = ("svg", "png")
ORIENTATIONS = ("standard", "cdk")  # see pubchem_local.orient

_lock = threading.Lock()
_state = {"started": False, "error": None}
_java = {}


class DepictError(ValueError):
    """Invalid input (e.g. unparsable SMILES)."""


def _classpath():
    candidates = []
    if os.environ.get("CDK_CLASSPATH"):
        candidates.append(os.environ["CDK_CLASSPATH"])
    candidates.append(str(Path(__file__).resolve().parent.parent / "java" / "lib"))
    for folder in candidates:
        jars = sorted(glob.glob(os.path.join(folder, "*.jar")))
        if jars:
            return jars
    return []


def _start():
    """Start the JVM once (thread-safe). Returns True if the CDK can be used."""
    if _state["started"]:
        return True
    if _state["error"]:
        return False
    with _lock:
        if _state["started"]:
            return True
        try:
            if jpype is None:
                raise RuntimeError("JPype is not installed")
            jars = _classpath()
            if not jars:
                raise RuntimeError("CDK jars not found (set CDK_CLASSPATH)")
            if not jpype.isJVMStarted():
                jpype.startJVM("-Djava.awt.headless=true", "-Xmx%s" % os.environ.get("CDK_MAX_HEAP", "256m"),
                               classpath=jars, convertStrings=True)
            # Classes are loaded by name: "from java.awt import ..." would resolve to the repository's java/ folder.
            J = jpype.JClass
            Color = J("java.awt.Color")
            ByteArrayOutputStream = J("java.io.ByteArrayOutputStream")
            ImageIO = J("javax.imageio.ImageIO")
            CDKConstants = J("org.openscience.cdk.CDKConstants")
            Abbreviations = J("org.openscience.cdk.depict.Abbreviations")
            Depiction = J("org.openscience.cdk.depict.Depiction")
            DepictionGenerator = J("org.openscience.cdk.depict.DepictionGenerator")
            CIPTool = J("org.openscience.cdk.geometry.cip.CIPTool")
            CDK2DAtomColors = J("org.openscience.cdk.renderer.color.CDK2DAtomColors")
            UniColor = J("org.openscience.cdk.renderer.color.UniColor")
            StandardGenerator = J("org.openscience.cdk.renderer.generators.standard.StandardGenerator")
            SilentChemObjectBuilder = J("org.openscience.cdk.silent.SilentChemObjectBuilder")
            SmilesParser = J("org.openscience.cdk.smiles.SmilesParser")
            StructureDiagramGenerator = J("org.openscience.cdk.layout.StructureDiagramGenerator")
            NonplanarBonds = J("org.openscience.cdk.layout.NonplanarBonds")
            RingSearch = J("org.openscience.cdk.ringsearch.RingSearch")
            Cycles = J("org.openscience.cdk.graph.Cycles")
            ConnectivityChecker = J("org.openscience.cdk.graph.ConnectivityChecker")
            Point2d = J("javax.vecmath.Point2d")

            abbreviations = Abbreviations()
            abbreviations.loadFromFile("/org/openscience/cdk/depict/obabel_superatoms.smi")

            @jpype.JImplements("org.openscience.cdk.renderer.color.IAtomColorer")
            class ColourOnBlack(object):
                """CDK's element colours with white carbon/hydrogen, for dark backgrounds (CDK Depict 'cob')."""
                def __init__(self):
                    self._base = CDK2DAtomColors()

                @jpype.JOverride
                def getAtomColor(self, atom, default=None):
                    num = atom.getAtomicNumber()
                    if num is None or int(num) in (1, 6):
                        return Color.WHITE
                    return self._base.getAtomColor(atom) if default is None else self._base.getAtomColor(atom, default)

            _java.update(locals())
            # warm up: the first depiction loads fonts and classes (~1 s)
            DepictionGenerator().depict(SmilesParser(SilentChemObjectBuilder.getInstance()).parseSmiles("CCO"))
            _state["started"] = True
            return True
        except Exception as err:  # Java missing, jars missing, ...
            _state["error"] = str(err)
            return False


def available():
    return _start()


def engine():
    """Which renderer structure images use: "cdk", "rdkit" (CDK unavailable) or "starting"."""
    if _state["started"]:
        return "cdk"
    return "rdkit" if _state["error"] else "starting"


def unavailable_reason():
    return _state["error"]


def depict(smiles, fmt="svg", width=None, height=None, style="cow", abbr="off", annotate="none", zoom=None,
           title=None, orient="standard"):
    """
    Render a SMILES string.

    :param width, height: image size in px; omit both for an automatic size (scaled by zoom)
    :param style: cow (colour on white), bow (black on white), wob (white on black), cob (colour on black)
    :param abbr: "on" to contract common groups (Ph, OMe, Boc, ...), "off" to draw every atom
    :param annotate: "none", "number" (atom numbers) or "cip" (R/S, E/Z labels)
    :param orient: "standard" (ring-anchored orientation, see pubchem_local.orient) or "cdk" (CDK's own layout)
    :return: (bytes, media type)
    """
    if not _start():
        raise RuntimeError("CDK unavailable: %s" % _state["error"])
    j = _java
    if (style not in STYLES or abbr not in ABBREVIATIONS or annotate not in ANNOTATIONS or fmt not in FORMATS
            or orient not in ORIENTATIONS):
        raise DepictError("invalid option")

    try:
        mol = j["SmilesParser"](j["SilentChemObjectBuilder"].getInstance()).parseSmiles(smiles)
    except Exception as err:
        raise DepictError("Could not parse SMILES: %s" % getattr(err, "getMessage", lambda: err)())

    if orient == "standard" and mol.getAtomCount():
        try:
            from pubchem_local import orient as orientation
            orientation.layout(j, mol)
        except Exception:
            for atom in mol.atoms():  # let CDK lay the molecule out itself
                atom.setPoint2d(None)

    if annotate == "cip":
        j["CIPTool"].label(mol)
        label_key = j["StandardGenerator"].ANNOTATION_LABEL
        cip_key = j["CDKConstants"].CIP_DESCRIPTOR
        for item in list(mol.atoms()) + list(mol.bonds()):
            label = item.getProperty(cip_key)
            if label is not None and str(label) not in ("NONE", "UNKNOWN", "ns"):
                item.setProperty(label_key, "(%s)" % label)
    if abbr == "on":
        j["abbreviations"].apply(mol)
    if title:
        mol.setTitle(title)

    gen = j["DepictionGenerator"]()
    if style == "cow":
        gen = gen.withAtomColors()
    elif style == "wob":
        gen = gen.withAtomColors(j["UniColor"](j["Color"].WHITE)).withBackgroundColor(j["Color"].BLACK)
    elif style == "cob":
        gen = gen.withAtomColors(j["ColourOnBlack"]()).withBackgroundColor(j["Color"].BLACK)
    foreground = j["Color"].WHITE if style in ("wob", "cob") else j["Color"].BLACK
    gen = gen.withTitleColor(foreground)  # CDK's default title colour is red
    if style in ("wob", "cob"):
        gen = gen.withAnnotationColor(j["Color"].WHITE)
    if annotate == "number":
        gen = gen.withAtomNumbers()
    if title:
        gen = gen.withMolTitle()
    if width and height:
        gen = gen.withSize(float(width), float(height))
        gen = gen.withZoom(float(zoom)) if zoom else gen.withFillToFit()
        gen = gen.withMargin(8.0)  # the default margin lets fill-to-fit drawings touch the image edge
    else:
        gen = gen.withZoom(float(zoom or 1.3))

    depiction = gen.depict(mol)
    if fmt == "svg":
        return str(depiction.toSvgStr(j["Depiction"].UNITS_PX)).encode("utf-8"), "image/svg+xml"
    buffer = j["ByteArrayOutputStream"]()
    j["ImageIO"].write(depiction.toImg(), "png", buffer)
    return bytes(buffer.toByteArray()), "image/png"
