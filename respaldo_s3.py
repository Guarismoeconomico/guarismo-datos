#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
respaldo_s3.py — Respaldo incremental de `guarismo_crudo` a Cloudflare R2.

QUE HACE
    Copia a R2 las filas de guarismo_crudo que todavia no se respaldaron,
    en objetos NDJSON comprimidos (gzip), con su sha256 al lado.

QUE NO HACE
    NO borra nada de Supabase. La tabla esta protegida por el trigger
    trg_crudo_no_update (BEFORE DELETE / BEFORE UPDATE -> guarismo_crudo_solo_insert()),
    asi que ni siquiera podria. Este script solo LEE.
    => Este respaldo NO libera espacio en Supabase. Es un problema aparte.

COMO SABE DONDE QUEDO
    Guarda el ultimo id respaldado en el propio bucket, en _estado/ultimo_id.txt.
    No necesita ninguna tabla extra en Supabase. Si el objeto no existe, arranca de cero.

IDEMPOTENCIA
    El watermark se escribe DESPUES de subir cada objeto. Si el proceso se corta,
    la proxima corrida rehace el ultimo lote. Como la clave del objeto se deriva del
    rango de ids, se sobrescribe a si mismo: no quedan duplicados.

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
from datetime import datetime, timezone

import boto3
import requests
from botocore.config import Config
from botocore.exceptions import ClientError

# ---------------------------------------------------------------- configuracion

TABLA = "guarismo_crudo"
COLUMNAS = "id,capturado_en,commit_sha,url,status,fuente_date,cuerpo,bytes"

PAGINA = 200                            # filas por request a PostgREST
MAX_BYTES_OBJETO = 20 * 1024 * 1024     # ~20 MB sin comprimir por objeto
CLAVE_ESTADO = "_estado/ultimo_id.txt"
TIMEOUT = 120


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
    """Filas con id > desde_id, ordenadas ascendente."""
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
    r = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


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
