#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
respaldo_s3.py — Respaldo incremental de `guarismo_crudo` a Cloudflare R2.

QUE HACE
    Copia a R2 las filas de guarismo_crudo que todavia no se respaldaron,
    en objetos NDJSON comprimidos (gzip), con su sha256 al lado.

QUE NO HACE
    NO borra nada de Supabase. Solo LEE.
    => Este respaldo NO libera espacio en Supabase. Es un problema aparte.

COMO SABE DONDE QUEDO
    Guarda el ultimo id respaldado en el propio bucket, en _estado/ultimo_id.txt.
    Si el proceso se corta, la proxima corrida retoma desde ahi. Nunca duplica.

ROBUSTEZ
    Las lecturas a Supabase reintentan con espera creciente ante errores
    transitorios (5xx / timeouts / conexion): el gateway puede devolver 521
    bajo carga sostenida en una instancia nano. Ademas se lee con pausa breve
    entre paginas para no ahogar al servidor.

VARIABLES DE ENTORNO (todas obligatorias salvo R2_BUCKET)
    SUPABASE_URL           https://xxxx.supabase.co
    SUPABASE_KEY           service_role (el mismo que usa el conector)
    R2_ENDPOINT            https://<account_id>.r2.cloudflarestorage.com   (SIN el bucket)
    R2_ACCESS_KEY_ID
    R2_SECRET_ACCESS_KEY
    R2_BUCKET              opcional, default "guarismo-crudo"

Uso:  python respaldo_s3.py
"""

import gzip
import hashlib
import io
import json
import os
import sys
import time
from datetime import datetime, timezone

import boto3
import requests
from botocore.config import Config
from botocore.exceptions import ClientError

# ---------------------------------------------------------------- configuracion

TABLA = "guarismo_crudo"
COLUMNAS = "id,capturado_en,commit_sha,url,status,fuente_date,cuerpo,bytes"

PAGINA = 100                            # filas por request (paginas mas livianas)
PAUSA_ENTRE_PAGINAS = 0.5               # segundos, para no ahogar al nano
MAX_BYTES_OBJETO = 20 * 1024 * 1024     # ~20 MB sin comprimir por objeto
CLAVE_ESTADO = "_estado/ultimo_id.txt"
TIMEOUT = 120

REINTENTOS = 6
ESPERAS = [5, 10, 20, 40, 60, 90]       # backoff entre reintentos


class _ErrorTransitorio(Exception):
    pass


def env(nombre, default=None):
    v = os.environ.get(nombre, default)
    if not v:
        print(f"[ERROR] Falta la variable de entorno {nombre}", file=sys.stderr)
        sys.exit(1)
    return v


SUPABASE_URL = env("SUPABASE_URL").rstrip("/")
SUPABASE_KEY = env("SUPABASE_KEY")
R2_ENDPOINT = env("R2_ENDPOINT").rstrip("/")
R2_KEY = env("R2_ACCESS_KEY_ID")
R2_SECRET = env("R2_SECRET_ACCESS_KEY")
BUCKET = os.environ.get("R2_BUCKET") or "guarismo-crudo"

s3 = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_KEY,
    aws_secret_access_key=R2_SECRET,
    region_name="auto",
    config=Config(
        signature_version="s3v4",
        retries={"max_attempts": 5, "mode": "standard"},
    ),
)


# ---------------------------------------------------------------- watermark

def leer_watermark():
    """Ultimo id respaldado. 0 si es la primera corrida."""
    try:
        r = s3.get_object(Bucket=BUCKET, Key=CLAVE_ESTADO)
        return int(r["Body"].read().decode("utf-8").strip())
    except ClientError as e:
        codigo = e.response.get("Error", {}).get("Code", "")
        if codigo in ("NoSuchKey", "404", "NotFound"):
            return 0
        raise


def escribir_watermark(ultimo_id):
    s3.put_object(
        Bucket=BUCKET,
        Key=CLAVE_ESTADO,
        Body=f"{ultimo_id}\n".encode("utf-8"),
        ContentType="text/plain; charset=utf-8",
    )


# ---------------------------------------------------------------- lectura

def bajar_pagina(desde_id):
    """Filas con id > desde_id, ascendente. Reintenta ante errores transitorios."""
    url = f"{SUPABASE_URL}/rest/v1/{TABLA}"
    params = {
        "select": COLUMNAS,
        "id": f"gt.{desde_id}",
        "order": "id.asc",
        "limit": str(PAGINA),
    }
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Accept": "application/json",
    }
    for intento in range(REINTENTOS):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
            if 400 <= r.status_code < 500:
                r.raise_for_status()          # error de config: no se reintenta
            if r.status_code >= 500:
                raise _ErrorTransitorio(f"HTTP {r.status_code} del servidor")
            return r.json()
        except (_ErrorTransitorio,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            if intento == REINTENTOS - 1:
                raise
            espera = ESPERAS[intento]
            print(f"  [aviso] lectura fallo ({e}); reintento {intento + 1}/{REINTENTOS - 1} en {espera}s...")
            time.sleep(espera)


# ---------------------------------------------------------------- escritura

def subir_lote(filas):
    """Sube un lote como NDJSON.gz + sidecar .sha256. Devuelve (clave, sha, bytes)."""
    id_desde, id_hasta = filas[0]["id"], filas[-1]["id"]

    buf = io.BytesIO()
    # mtime=0 => gzip determinista: el mismo lote produce siempre el mismo sha256.
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        for f in filas:
            linea = json.dumps(f, ensure_ascii=False, sort_keys=True) + "\n"
            gz.write(linea.encode("utf-8"))
    datos = buf.getvalue()

    sha = hashlib.sha256(datos).hexdigest()
    ahora = datetime.now(timezone.utc)
    nombre = f"crudo_{id_desde:012d}-{id_hasta:012d}.ndjson.gz"
    clave = f"crudo/{ahora:%Y/%m}/{nombre}"

    s3.put_object(
        Bucket=BUCKET,
        Key=clave,
        Body=datos,
        ContentType="application/gzip",
        Metadata={
            "sha256": sha,
            "filas": str(len(filas)),
            "id-desde": str(id_desde),
            "id-hasta": str(id_hasta),
            "respaldado-en": ahora.isoformat(),
        },
    )
    s3.put_object(
        Bucket=BUCKET,
        Key=clave + ".sha256",
        Body=f"{sha}  {nombre}\n".encode("utf-8"),
        ContentType="text/plain; charset=utf-8",
    )
    return clave, sha, len(datos)


# ---------------------------------------------------------------- principal

def main():
    print(f"[respaldo] bucket={BUCKET} tabla={TABLA}")

    wm = leer_watermark()
    print(f"[respaldo] watermark inicial: id > {wm}")

    lote = []
    bytes_lote = 0
    ultimo = wm
    total_filas = 0
    total_objetos = 0
    total_comprimido = 0

    def flush():
        nonlocal lote, bytes_lote, total_filas, total_objetos, total_comprimido
        if not lote:
            return
        clave, sha, comp = subir_lote(lote)
        escribir_watermark(lote[-1]["id"])   # solo despues de subir OK
        total_filas += len(lote)
        total_objetos += 1
        total_comprimido += comp
        print(f"  -> {clave}  ({len(lote)} filas, {comp/1024/1024:.2f} MB gz, sha {sha[:12]}...)")
        lote = []
        bytes_lote = 0

    while True:
        filas = bajar_pagina(ultimo)
        if not filas:
            break
        for f in filas:
            lote.append(f)
            bytes_lote += f.get("bytes") or len(f.get("cuerpo") or "")
            ultimo = f["id"]
        if bytes_lote >= MAX_BYTES_OBJETO:
            flush()
        time.sleep(PAUSA_ENTRE_PAGINAS)

    flush()

    if total_filas == 0:
        print("[respaldo] sin filas nuevas. Nada que hacer.")
    else:
        print(
            f"[respaldo] OK: {total_filas} filas nuevas en {total_objetos} objeto(s), "
            f"{total_comprimido/1024/1024:.2f} MB comprimidos. "
            f"Watermark final: {ultimo}"
        )


if __name__ == "__main__":
    main()
