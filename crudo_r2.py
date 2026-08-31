#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
crudo_r2.py — Escribe el espejo crudo DIRECTO a Cloudflare R2.

POR QUE EXISTE
    Antes cada corrida del conector insertaba sus respuestas crudas en la tabla
    guarismo_crudo de Supabase, y de ahi respaldo_s3.py las copiaba a R2. Eso
    hacia crecer Supabase ~17 MB por dia contra un limite de 500 MB.
    Ahora el conector escribe a R2 en el momento. Supabase queda como red de
    seguridad: solo recibe filas cuando R2 falla.

FORMATO
    Un objeto NDJSON comprimido (gzip) por corrida, con su sidecar .sha256.
    Mismo formato y misma carpeta que los objetos de respaldo_s3.py, para que
    un solo lector pueda leer todo el archivo historico.

    respaldo_s3.py  ->  crudo/AAAA/MM/crudo_000000050614-000000050813.ndjson.gz
    este archivo    ->  crudo/AAAA/MM/vivo_20260831T221547Z_intradia_1234.ndjson.gz

    El prefijo del nombre (crudo_ vs vivo_) dice de donde vino cada objeto.

DETERMINISMO
    gzip con mtime=0 y json con sort_keys=True: el mismo contenido produce
    siempre el mismo sha256. Es la misma regla que usa el sellador.

VARIABLES DE ENTORNO
    R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY  (obligatorias)
    R2_BUCKET                                            (opcional, default guarismo-crudo)

    Si falta alguna, subir() lanza excepcion y el conector cae a Supabase.
"""

import gzip
import hashlib
import io
import json
import os
import re
from datetime import datetime, timezone

BUCKET_DEFAULT = "guarismo-crudo"


def _cliente():
    """Cliente S3 apuntando a R2. Lanza excepcion si falta configuracion."""
    import boto3
    from botocore.config import Config

    endpoint = os.getenv("R2_ENDPOINT")
    key = os.getenv("R2_ACCESS_KEY_ID")
    secret = os.getenv("R2_SECRET_ACCESS_KEY")
    if not (endpoint and key and secret):
        raise RuntimeError("faltan R2_ENDPOINT / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY")

    return boto3.client(
        "s3",
        endpoint_url=endpoint.rstrip("/"),
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 5, "mode": "standard"},
        ),
    )


def _slug(texto, largo=24):
    """Convierte un nombre de workflow en algo apto para una clave de objeto."""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", str(texto or "local")).strip("-").lower()
    return (s or "local")[:largo]


def subir(espejo, capturado_en, commit, job):
    """Sube el espejo completo como UN objeto. Devuelve (clave, sha256, bytes_gz).

    Cualquier problema lanza excepcion: el conector la atrapa y cae a Supabase.
    """
    if not espejo:
        raise ValueError("espejo vacio")

    bucket = os.getenv("R2_BUCKET") or BUCKET_DEFAULT
    s3 = _cliente()

    filas = [{
        "capturado_en": capturado_en,
        "commit_sha": commit,
        "url": e["url"],
        "status": e["status"],
        "fuente_date": e["fuente_date"],
        "cuerpo": e["cuerpo"],
        "bytes": e["bytes"],
    } for e in espejo]

    buf = io.BytesIO()
    # mtime=0 => gzip determinista (mismo contenido, mismo sha256).
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        for f in filas:
            linea = json.dumps(f, ensure_ascii=False, sort_keys=True) + "\n"
            gz.write(linea.encode("utf-8"))
    datos = buf.getvalue()

    sha = hashlib.sha256(datos).hexdigest()
    ahora = datetime.now(timezone.utc)
    run = _slug(os.getenv("GITHUB_RUN_ID", "local"), 20)
    nombre = f"vivo_{ahora:%Y%m%dT%H%M%SZ}_{_slug(job)}_{run}.ndjson.gz"
    clave = f"crudo/{ahora:%Y/%m}/{nombre}"

    s3.put_object(
        Bucket=bucket,
        Key=clave,
        Body=datos,
        ContentType="application/gzip",
        Metadata={
            "sha256": sha,
            "filas": str(len(filas)),
            "origen": "conector",
            "commit": str(commit)[:40],
            "capturado-en": str(capturado_en),
        },
    )
    s3.put_object(
        Bucket=bucket,
        Key=clave + ".sha256",
        Body=f"{sha}  {nombre}\n".encode("utf-8"),
        ContentType="text/plain; charset=utf-8",
    )
    return clave, sha, len(datos)
