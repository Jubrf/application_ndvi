"""
API de la version React (v5) : sert l'interface (dossier web/) et les calculs du moteur Python.

Rouvrir une analyse enregistrée (.zip), l'interpréter avec les réglages de l'interface,
exporter (Excel, enregistrement, cartes KML / HTML) ; avec Earth Engine (v5.1) : nouvelle
analyse et complément en tâche de fond, météo, vignettes, analyse à une date.

Accès protégé par mot de passe (variable d'environnement APP_PASSWORD, authentification
HTTP du navigateur). Lancement local :
    APP_PASSWORD=... uvicorn api.main:app --reload
"""
import base64
import datetime
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api import gee as gee_conn
from api import projects
from api.engine import Store
from ndvi_app.config import APP_VERSION

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_UPLOAD = 60 * 1024 * 1024
API_VERSION = "v5.1"

app = FastAPI(title="NDVI parcellaire", docs_url=None, redoc_url=None, openapi_url=None)
store = Store()
parcels = projects.ParcelStore()
jobs = projects.Jobs()
app.add_middleware(GZipMiddleware, minimum_size=2000)


# ---------------------------------------------------------------- accès
@app.middleware("http")
async def password_gate(request: Request, call_next):
    if request.url.path == "/healthz":
        return await call_next(request)
    expected = os.environ.get("APP_PASSWORD", "")
    if not expected:
        return PlainTextResponse("Accès fermé : la variable APP_PASSWORD n'est pas définie sur le serveur.",
                                 status_code=503)
    auth = request.headers.get("authorization", "")
    ok = False
    if auth.lower().startswith("basic "):
        try:
            _, _, pwd = base64.b64decode(auth[6:]).decode("utf-8").partition(":")
            ok = secrets.compare_digest(pwd.encode("utf-8"), expected.encode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            ok = False
    if not ok:
        return PlainTextResponse("Mot de passe requis.", status_code=401,
                                 headers={"WWW-Authenticate": 'Basic realm="NDVI parcellaire", charset="UTF-8"'})
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ---------------------------------------------------------------- modèles
class Settings(BaseModel):
    low: float = Field(ge=0, le=1)
    high: float = Field(ge=0, le=1)
    min_clear: int = Field(ge=0, le=100)
    min_pixels: int = Field(ge=1, le=100000)
    min_weight: float = Field(ge=0, le=1)
    smooth_days: int = Field(ge=1, le=60)
    grey: list[float] | None = None
    uncertain_days: int = Field(15, ge=5, le=90)   # courbe incertaine au-delà (absent : 15)

    def as_dict(self):
        d = self.model_dump()
        if d["grey"]:
            if len(d["grey"]) != 2:
                raise HTTPException(422, "zone grise : deux bornes attendues.")
            d["grey"] = sorted(d["grey"])
        if d["low"] >= d["high"]:
            raise HTTPException(422, "le seuil sol nu / couvert doit être inférieur au seuil "
                                     "couvert bien développé.")
        return d


class MapRequest(BaseModel):
    settings: Settings
    day: str
    format: str = "kml"


def _get(key):
    a = store.get(key)
    if a is None:
        # serveur redémarré ou analyse évincée : l'interface renvoie le fichier
        raise HTTPException(404, "analyse-inconnue")
    return a


def _file(data, name, mime):
    return Response(data, media_type=mime,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------------------------------------------------------------- routes
@app.get("/api/version")
def version():
    return {"api": API_VERSION, "engine": APP_VERSION}


@app.post("/api/analyses")
async def open_analysis(file: UploadFile = File(...), name: str = Form(None)):
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "fichier trop volumineux.")
    try:
        a = store.open(data, name or file.filename or "analyse.zip")
    except ValueError as e:
        raise HTTPException(400, f"Fichier non reconnu : {e}")
    except Exception as e:  # fichier abîmé ou d'un autre format
        raise HTTPException(400, f"Fichier illisible : {type(e).__name__} — {e}")
    return {"info": a.info(), "geometry": a.geometry()}


@app.post("/api/analyses/{key}/results")
def results(key: str, settings: Settings):
    return JSONResponse(_get(key).payload(settings.as_dict()))


@app.post("/api/analyses/{key}/excel")
def excel(key: str, settings: Settings):
    a = _get(key)
    return _file(a.excel(settings.as_dict()), f"ndvi_temporel_{a.p_start}_{a.p_end}.xlsx",
                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.post("/api/analyses/{key}/save")
def save(key: str, settings: Settings):
    a = _get(key)
    return _file(a.session_zip(settings.as_dict()), f"analyse_ndvi_{a.p_start}_{a.p_end}.zip",
                 "application/zip")


@app.post("/api/analyses/{key}/map")
def map_file(key: str, req: MapRequest):
    a = _get(key)
    if req.format not in ("kml", "html"):
        raise HTTPException(422, "format kml ou html.")
    data = a.map_file(req.settings.as_dict(), req.day, req.format)
    if req.format == "kml":
        return _file(data, f"phases_ndvi_{req.day}.kml", "application/vnd.google-earth.kml+xml")
    return _file(data, f"phases_ndvi_{req.day}.html", "text/html")


# ---------------------------------------------------------------- Earth Engine (étape 2)
class Calc(BaseModel):
    buffer_m: int = Field(10, ge=0, le=50)
    cs_threshold: float = Field(0.60, ge=0.3, le=0.95)
    cloud_buffer_m: int = Field(20, ge=0, le=200)
    iqr_k: float = Field(1.5, ge=0.5, le=5)


class Source(BaseModel):
    """Parcelles sur lesquelles travailler : analyse ouverte ou fichier de parcelles chargé."""
    type: str                      # "analysis" | "parcels"
    key: str
    calc: Calc | None = None       # paramètres de calcul (fichier de parcelles seulement)


def _src(source: Source):
    """(objet parcelles, params_t) : paramètres verrouillés de l'analyse, ou ceux demandés."""
    if source.type == "analysis":
        a = _get(source.key)
        return a, a.params_t
    ps = parcels.get(source.key)
    if ps is None:
        raise HTTPException(404, "parcelles-inconnues")
    return ps, projects.calc_params((source.calc or Calc()).model_dump())


def _gee_call(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except gee_conn.GeeUnavailable as e:
        raise HTTPException(503, f"Earth Engine indisponible : {e}")
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Erreur Earth Engine : {type(e).__name__} — {str(e)[:400]}")


@app.get("/api/gee")
def gee_status(retry: bool = False):
    ok, msg = gee_conn.connect(force=retry)
    return {"configured": gee_conn.configured(), "ok": ok, "message": msg}


@app.post("/api/parcels")
async def load_parcels(file: UploadFile = File(...), name: str = Form(None),
                       buffer_m: int = Form(10), id_field: str = Form(None)):
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "fichier trop volumineux.")
    try:
        ps = parcels.load(data, name or file.filename or "parcelles.zip", buffer_m, id_field or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"info": ps.info(), "geometry": ps.geometry()}


class DatesRequest(BaseModel):
    source: Source
    start: str
    end: str
    min_clear: int = 50


@app.post("/api/dates")
def dates(req: DatesRequest):
    src, params_t = _src(req.source)
    found = _gee_call(projects.list_dates, src, req.start, req.end, params_t)
    out = {"dates": [projects.date_entry(d, req.min_clear) for d in found]}
    if req.source.type == "analysis":
        out["extension"] = projects.extension_dates(src, found, req.min_clear)
    return out


class RunRequest(BaseModel):
    source: Source
    start: str
    end: str
    dates: list[str]
    settings: Settings | None = None


@app.post("/api/jobs")
def start_job(req: RunRequest):
    if not req.dates:
        raise HTTPException(422, "aucune date choisie.")
    ok, msg = gee_conn.connect()
    if not ok:
        raise HTTPException(503, f"Earth Engine indisponible : {msg}")
    src, params_t = _src(req.source)
    s = req.settings.as_dict() if req.settings else projects.default_interpretation()
    p0, p1 = datetime.date.fromisoformat(req.start), datetime.date.fromisoformat(req.end)
    if req.source.type == "analysis":
        # Compléter : mois cherchés librement ; la période devient l'enveloppe des deux
        done = {d for d, _ in src.session["raws"]}
        if any(d in done for d in req.dates):
            raise HTTPException(422, "certaines dates sont déjà dans l'analyse.")
        period = (min(p0, src.p_start), max(p1, src.p_end))
        job = jobs.start("compléter", len(req.dates),
                         lambda j: projects.run_extend(j, src, period, req.dates, s))
    else:
        job = jobs.start("nouvelle", len(req.dates),
                         lambda j: projects.run_new(j, src, (p0, p1), req.dates, params_t, s))
    return job.view()


def _job(jid):
    job = jobs.get(jid)
    if job is None:
        raise HTTPException(404, "calcul-inconnu")
    return job


@app.get("/api/jobs/{jid}")
def job_status(jid: str):
    job = _job(jid)
    out = job.view()
    if job.state == "done" and job.result:
        a = store.open(*job.result)
        out["analysis"] = {"info": a.info(), "geometry": a.geometry()}
    return out


@app.post("/api/jobs/{jid}/cancel")
def job_cancel(jid: str):
    _job(jid).cancel = True
    return {"ok": True}


@app.get("/api/analyses/{key}/file")
def analysis_file(key: str):
    a = _get(key)
    return _file(a.data, a.file_name, "application/zip")


class WeatherRequest(BaseModel):
    settings: Settings
    refresh: bool = False


@app.post("/api/analyses/{key}/weather")
def analysis_weather(key: str, req: WeatherRequest):
    """Météo (manquante ou actualisée) : nouveau fichier d'analyse avec la météo."""
    a = _get(key)
    ok, msg = gee_conn.connect()
    if not ok:
        raise HTTPException(503, f"Earth Engine indisponible : {msg}")
    data, w = projects.with_weather(a, req.settings.as_dict(), refresh=req.refresh)
    if w.get("error"):
        raise HTTPException(502, f"Météo ERA5-Land indisponible : {w['error']}")
    b = store.open(data, a.file_name)
    return {"info": b.info(), "geometry": b.geometry()}


class ThumbRequest(BaseModel):
    source: Source
    pid: str
    date: str
    mode: str = "naturel"
    mask: bool = True


@app.post("/api/thumbnail")
def thumbnail(req: ThumbRequest):
    src, params_t = _src(req.source)
    if req.pid not in src.ids:
        raise HTTPException(404, "parcelle inconnue.")
    return {"url": _gee_call(projects.thumbnail, src, params_t, req.pid, req.date, req.mode, req.mask)}


class OneDateRequest(BaseModel):
    source: Source
    date: str
    settings: Settings
    format: str | None = None


def _clean(v):
    if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
        return None
    if hasattr(v, "item"):
        v = v.item()
        return _clean(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    return v


@app.post("/api/onedate")
def one_date(req: OneDateRequest):
    src, params_t = _src(req.source)
    s = req.settings.as_dict()
    df_os, df_all, wx = _gee_call(projects.one_date, src, params_t, req.date, s)
    rows = [{k: _clean(v) for k, v in r.items()} for r in df_os.to_dict("records")]
    grey = tuple(s["grey"]) if s.get("grey") else None
    return {"date": req.date, "columns": list(df_os.columns), "rows": rows,
            "help": projects.column_help(s["low"], s["high"], grey),
            "weather": None if wx.get("error") else bool(wx.get("days")),
            "weather_error": wx.get("error")}


@app.post("/api/onedate/file")
def one_date_file(req: OneDateRequest):
    src, params_t = _src(req.source)
    s = req.settings.as_dict()
    if req.format == "xlsx":
        data = _gee_call(projects.one_date_excel, src, params_t, req.date, s)
        return _file(data, f"ndvi_{req.date}.xlsx",
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if req.format not in ("kml", "html"):
        raise HTTPException(422, "format xlsx, kml ou html.")
    data = _gee_call(projects.one_date_map, src, params_t, req.date, s, req.format)
    return _file(data, f"ndvi_{req.date}.{req.format}",
                 "application/vnd.google-earth.kml+xml" if req.format == "kml" else "text/html")


# ---------------------------------------------------------------- interface
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
