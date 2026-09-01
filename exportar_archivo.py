#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Guarismo — Exportar el archivo sellado y respaldarlo en R2
===========================================================

Baja guarismo_historico completo, lo escribe a un JSON y lo sube a Cloudflare
R2 bajo el prefijo archivo/. GitHub Actions ademas lo sube como artifact, que
queda como red SECUNDARIA (expira a los 90 dias; R2 no).

    python exportar_archivo.py

    -> respaldo/archivo-YYYY-MM-DD.json          (local / artifact)
    -> archivo/AAAA/MM/sellado_<ts>_<run>.json.gz  + .sha256   (R2, permanente)

Solo LEE de Supabase (clave publishable). No toca ninguna fila.

DETERMINISMO
    gzip con mtime=0 y json con sort_keys=True: el mismo contenido produce
    siempre el mismo sha256. Misma regla que el sellador y que crudo_r2.py.

VARIABLES DE ENTORNO
    SUPABASE_ANON                                        (obligatoria)
    SUPABASE_URL                                         (opcional)
    R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY  (obligatorias)
    R2_BUCKET                                            (opcional, default guarismo-crudo)

CODIGOS DE SALIDA
    0  todo bien          2  falta SUPABASE_ANON
    1  no bajo el archivo 3  el JSON quedo, pero R2 fallo  <- corrida en ROJO a proposito
"""

import datetime as dt
import gzip
import hashlib
import io
import json
import os
import pathlib
import re
import sys

import requests

for _f in (sys.stdout, sys.stderr):
    try:
        _f.reconfigure(encoding="utf-8")
    except Exception:
        pass

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://rudepkizcatkhqprqjfw.supabase.co")
# Publishable/anon: read-only, publica. En Actions la inyecta el workflow.
ANON = os.getenv("SUPABASE_ANON", "PEGAR_ACA_LA_CLAVE_PUBLISHABLE")

TABLA = "guarismo_historico"
PAGINA = 1000
TIMEOUT = 60

BUCKET_DEFAULT = "guarismo-crudo"
PREFIJO = "archivo"          # separado de crudo/ y de _estado/


def bajar_todo():
    headers = {"apikey": ANON, "Authorization": f"Bearer {ANON}"}
    filas, desde = [], 0
    while True:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{TABLA}",
            params={"select": "*", "order": "id.asc"},
            headers={**headers, "Range": f"{desde}-{desde + PAGINA - 1}"},
            timeout=TIMEOUT)
        r.raise_for_status()
        lote = r.json()
        filas.extend(lote)
        if len(lote) < PAGINA:
            return filas
        desde += PAGINA


def _slug(texto, largo=20):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", str(texto or "local")).strip("-").lower()
    return (s or "local")[:largo]


def subir_a_r2(texto_json):
    """Sube el JSON gzipeado a R2 con su sidecar .sha256.

    Devuelve (clave, sha256, bytes_gz). Cualquier problema lanza excepcion:
    aca NO hay red de seguridad, tiene que verse en rojo.
    """
    # boto3 se importa adentro: si falta, el export local igual funciona.
    import boto3
    from botocore.config import Config

    endpoint = os.getenv("R2_ENDPOINT")
    key = os.getenv("R2_ACCESS_KEY_ID")
    secret = os.getenv("R2_SECRET_ACCESS_KEY")
    if not (endpoint and key and secret):
        raise RuntimeError("faltan R2_ENDPOINT / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY")

    s3 = boto3.client(
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

    buf = io.BytesIO()
    # mtime=0 => gzip determinista (mismo contenido, mismo sha256).
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(texto_json.encode("utf-8"))
    datos = buf.getvalue()

    sha = hashlib.sha256(datos).hexdigest()
    ahora = dt.datetime.now(dt.timezone.utc)
    run = _slug(os.getenv("GITHUB_RUN_ID", "local"))
    nombre = f"sellado_{ahora:%Y%m%dT%H%M%SZ}_{run}.json.gz"
    clave = f"{PREFIJO}/{ahora:%Y/%m}/{nombre}"
    bucket = os.getenv("R2_BUCKET") or BUCKET_DEFAULT

    s3.put_object(
        Bucket=bucket,
        Key=clave,
        Body=datos,
        ContentType="application/gzip",
        Metadata={
            "sha256": sha,
            "origen": "exportador",
            "tabla": TABLA,
            "generado-utc": ahora.isoformat(timespec="seconds"),
        },
    )
    s3.put_object(
        Bucket=bucket,
        Key=clave + ".sha256",
        Body=f"{sha}  {nombre}\n".encode("utf-8"),
        ContentType="text/plain; charset=utf-8",
    )
    return clave, sha, len(datos)


def main():
    if ANON.startswith("PEGAR"):
        print("✗ Falta SUPABASE_ANON.")
        return 2

    try:
        filas = bajar_todo()
    except Exception as e:
        print(f"✗ No se pudo bajar el archivo: {e}")
        return 1

    ahora = dt.datetime.now(dt.timezone.utc)
    doc = {
        "guarismo": "respaldo del archivo sellado",
        "generado_utc": ahora.isoformat(timespec="seconds"),
        "tabla": TABLA,
        "filas": len(filas),
        "datos": filas,
    }
    # Una sola serializacion: lo que se guarda local es lo mismo que se hashea.
    texto = json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True, default=str)

    destino = pathlib.Path("respaldo")
    destino.mkdir(exist_ok=True)
    ruta = destino / f"archivo-{ahora:%Y-%m-%d}.json"
    ruta.write_text(texto, encoding="utf-8")

    mb = ruta.stat().st_size / 1e6
    print(f"✓ {len(filas)} filas · {mb:.2f} MB · {ruta}")

    # Resumen por bucket, para verlo de un vistazo en el log
    por_bucket = {}
    for f in filas:
        por_bucket[f.get("bucket", "?")] = por_bucket.get(f.get("bucket", "?"), 0) + 1
    for b, n in sorted(por_bucket.items()):
        print(f"   {b:<12} {n} capturas")

    try:
        clave, sha, n = subir_a_r2(texto)
        print(f"[archivo] R2 OK: {clave} ({n/1024:.0f} KB gz)")
        print(f"[archivo] sha256: {sha}")
    except Exception as e:
        print(f"[archivo] R2 fallo: {type(e).__name__}: {e}")
        print("  El JSON quedo en respaldo/ y el artifact lo salva, pero el")
        print("  respaldo permanente NO se escribio. Corrida en rojo a proposito.")
        return 3

    return 0


if __name__ == "__main__":
    sys.exit(main())
