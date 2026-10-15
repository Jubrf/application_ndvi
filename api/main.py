"""
API de la version React (v5) : sert l'interface (dossier web/) et les calculs du moteur Python.

Étape 1 : rouvrir une analyse enregistrée (.zip), l'interpréter avec les réglages de
l'interface, exporter (Excel, enregistrement, cartes KML / HTML). Pas d'appel Earth Engine.

Accès protégé par mot de passe (variable d'environnement APP_PASSWORD, authentification
HTTP du navigateur). Lancement local :
    APP_PASSWORD=... uvicorn api.main:app --reload
"""
import base64
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api.engine import Store
from ndvi_app.config import APP_VERSION

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_UPLOAD = 60 * 1024 * 1024
API_VERSION = "v5.0-etape1"

app = FastAPI(title="NDVI parcellaire", docs_url=None, redoc_url=None, openapi_url=None)
store = Store()
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


# ---------------------------------------------------------------- interface
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
