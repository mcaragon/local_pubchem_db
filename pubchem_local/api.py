"""
REST API and web UI.

Interactive API documentation is served at /docs (Swagger UI) and /redoc; the OpenAPI schema at /openapi.json.
"""
import hmac
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pubchem_local import __version__, config, jobs, pipeline
from pubchem_local.store import Store

STATIC_DIR = Path(__file__).with_name("static")

app = FastAPI(
    title="PubChem Local",
    version=__version__,
    description=(
        "Offline copy of PubChem. Search compounds by name, CAS number, CID, InChIKey, InChI, molecular formula or "
        "SMILES and retrieve computed properties, experimental physical properties (boiling point, melting point, "
        "density, flash point, ...) and GHS safety information.\n\n"
        "Physical property summary values are the median of all values reported by PubChem's sources at standard "
        "conditions; the individual source values are always included."
    ),
)
app.add_middleware(CORSMiddleware, allow_origins=config.cors_origins(), allow_methods=["*"], allow_headers=["*"])


def get_store():
    store = Store()
    try:
        yield store
    finally:
        store.close()


def require_admin(x_admin_token: Optional[str] = Header(default=None),
                  authorization: Optional[str] = Header(default=None)):
    token = config.admin_token()
    if not token:
        return
    supplied = x_admin_token or (authorization[7:] if authorization and authorization.startswith("Bearer ") else "")
    if not hmac.compare_digest(supplied or "", token):
        raise HTTPException(status_code=401, detail="Admin token required (X-Admin-Token header)")


def _cid_or_404(store, cid):
    data = store.compound(cid)
    if data is None:
        raise HTTPException(status_code=404, detail="Compound %d not found in the local database" % cid)
    return data


# ---------------------------------------------------------------------------------------------------- compounds

@app.get("/api/health", tags=["status"], summary="Service health and available datasets")
def health(store: Store = Depends(get_store)):
    return {"status": "ok", "version": __version__, "datasets": store.available, "counts": store.stats()}


@app.get("/api/search", tags=["compounds"], summary="Search compounds")
def search(q: str = Query(..., description="Name, CAS number, CID, InChIKey, InChI, molecular formula or SMILES"),
           limit: int = Query(20, ge=1, le=200), store: Store = Depends(get_store)):
    """Returns a ranked list of matching compounds. Exact identifier matches come first, then exact name matches,
    then names starting with the query."""
    return {"query": q, "results": store.search(q, limit=limit)}


@app.get("/api/autocomplete", tags=["compounds"], summary="Name suggestions for a prefix")
def autocomplete(q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=50),
                 store: Store = Depends(get_store)):
    return {"query": q, "suggestions": store.autocomplete(q, limit=limit)}


@app.get("/api/compound/{cid}", tags=["compounds"], summary="All data for a compound")
def compound(cid: int, store: Store = Depends(get_store)):
    return _cid_or_404(store, cid)


@app.get("/api/compound/{cid}/lab", tags=["lab"], summary="Compact lab notebook record for a compound")
def compound_lab(cid: int, store: Store = Depends(get_store)):
    data = store.lab_summary(cid)
    if data is None:
        raise HTTPException(status_code=404, detail="Compound %d not found" % cid)
    return data


@app.get("/api/compound/{cid}/safety-summary", tags=["compounds"],
         summary="Full Laboratory Chemical Safety Summary (all sections)")
def compound_lcss(cid: int, store: Store = Depends(get_store)):
    sections = store.lcss_sections(cid)
    if sections is None:
        raise HTTPException(status_code=404, detail="No laboratory safety summary for compound %d" % cid)
    return {"cid": cid, "title": store.title(cid), "sections": sections}


@app.get("/api/compound/{cid}/synonyms", tags=["compounds"], summary="Synonyms of a compound")
def compound_synonyms(cid: int, limit: int = Query(200, ge=1, le=5000), store: Store = Depends(get_store)):
    return {"cid": cid, "synonyms": store.synonyms(cid, limit=limit)}


@app.get("/api/compound/{cid}/structure.svg", tags=["compounds"], summary="2D structure depiction (SVG)",
         response_class=Response)
def compound_svg(cid: int, width: int = Query(300, ge=50, le=2000), height: int = Query(240, ge=50, le=2000),
                 theme: str = Query("light", pattern="^(light|dark)$"), store: Store = Depends(get_store)):
    svg = store.structure_svg(cid, width, height, dark=(theme == "dark"))
    if svg is None:
        raise HTTPException(status_code=404, detail="No structure available for compound %d" % cid)
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/lookup", tags=["lab"], summary="Resolve one identifier to the best matching compound")
def lookup(q: str = Query(..., description="Name, CAS number, CID, InChIKey, SMILES, ..."),
           full: bool = Query(False, description="Return the full compound record instead of the lab summary"),
           store: Store = Depends(get_store)):
    cid = store.resolve(q)
    if cid is None:
        raise HTTPException(status_code=404, detail="No compound found for '%s'" % q)
    return store.compound(cid) if full else store.lab_summary(cid)


class BatchRequest(BaseModel):
    identifiers: List[str] = Field(..., max_length=500,
                                   description="Names, CAS numbers, CIDs, InChIKeys or SMILES",
                                   json_schema_extra={"example": ["acetone", "64-17-5", "CC(=O)O", "180"]})


@app.post("/api/lab/batch", tags=["lab"], summary="Resolve many reagents at once")
def lab_batch(req: BatchRequest, store: Store = Depends(get_store)):
    """Resolve a list of reagents (as typed by a user) to compounds and return the lab summary for each.
    Unresolved identifiers are returned with `"found": false`."""
    results = []
    for ident in req.identifiers:
        cid = store.resolve(ident)
        if cid is None:
            results.append({"query": ident, "found": False})
        else:
            results.append(dict({"query": ident, "found": True}, **store.lab_summary(cid)))
    return {"results": results}


# ---------------------------------------------------------------------------------------------------- build

class BuildRequest(BaseModel):
    datasets: List[str] = Field(default_factory=lambda: ["lcss", "names"],
                                description="Any of: lcss, names, compounds")
    fresh: bool = Field(False, description="Discard a partially built compounds database")


@app.get("/api/build/status", tags=["build"], summary="Database and build status")
def build_status():
    status = jobs.current_status()
    return {"datasets": [pipeline.dataset_info(d) for d in config.DATASETS], "build": status,
            "admin_token_required": bool(config.admin_token())}


@app.post("/api/build/start", tags=["build"], summary="Start downloading and building databases",
          dependencies=[Depends(require_admin)])
def build_start(req: BuildRequest):
    try:
        pid = jobs.start_build(req.datasets, fresh=req.fresh)
    except RuntimeError as err:
        raise HTTPException(status_code=409, detail=str(err))
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))
    return {"started": True, "pid": pid}


@app.post("/api/build/cancel", tags=["build"], summary="Cancel the running build",
          dependencies=[Depends(require_admin)])
def build_cancel():
    return {"cancelled": jobs.cancel_build()}


# ---------------------------------------------------------------------------------------------------- web UI

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))
