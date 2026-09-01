#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
boveda_datosgob.py — Snapshot diario COMPLETO de las series de datos.gob.ar.

POR QUE EXISTE
    El conector baja estas mismas series con limit=1&sort=desc: solo el ultimo
    valor. Eso alimenta la VITRINA y esta bien para eso.

    La BOVEDA necesita otra cosa. El INDEC re-estima la serie desestacionalizada
    ENTERA con cada dato nuevo (filtros moviles + conciliacion trimestral con el
    PIB). Si solo guardamos el ultimo punto, registramos que salio el dato de
    julio pero NO registramos que los valores de 2024 cambiaron. La revision
    invisible vive justo ahi.

    Por eso este modulo baja la serie COMPLETA, todos los puntos, todos los dias.
    Y la API de datos.gob.ar SOBRESCRIBE con cada revision: no guarda vintages.
    Lo que no capturamos el dia que estaba, no se recupera nunca.

QUE NO HACE
    No toca Supabase. No toca la vitrina. No escribe en el prefijo crudo/.
    Solo LEE una API publica y escribe objetos nuevos bajo boveda/.

FORMATO
    Un objeto NDJSON comprimido por corrida, con su sidecar .sha256:

        boveda/datosgob/AAAA/MM/snapshot_20260901T221500Z_1234.ndjson.gz

    Linea 1  -> manifiesto de la corrida
    Linea N  -> una serie: metadata oficial + todos los puntos + su hash propio

    El hash POR SERIE es lo que despues permite responder "esta serie cambio
    respecto de ayer" sin descomprimir el archivo entero.

HUECOS, NO MENTIRAS
    Si una serie falla, se escribe igual con {"error": ...} y n=0. Queda el
    hueco registrado con su fecha. Nunca un dato viejo haciendose pasar por
    fresco. Si fallan TODAS, el proceso termina en rojo y no sube nada.

DETERMINISMO
    gzip mtime=0 + json sort_keys=True: mismo contenido, mismo sha256. Misma
    regla que el sellador y que crudo_r2.py.

VARIABLES DE ENTORNO
    R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY   (obligatorias)
    R2_BUCKET                                             (opcional, default guarismo-crudo)

CODIGOS DE SALIDA
    0  todo bien (aunque alguna serie tenga hueco)
    1  fallaron TODAS las series: no hay nada que archivar
    3  hay datos pero R2 fallo  <- corrida en ROJO a proposito
"""

import gzip
import hashlib
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

for _f in (sys.stdout, sys.stderr):
    try:
        _f.reconfigure(encoding="utf-8")
    except Exception:
        pass

DG_API = "https://apis.datos.gob.ar/series/api/series"
PAGINA = 1000          # maximo que acepta la API (default 100)
TIMEOUT = 60
PAUSA = 0.4            # cortesia entre llamadas
REINTENTOS = 3

BUCKET_DEFAULT = "guarismo-crudo"
PREFIJO = "boveda/datosgob"

# Los mismos IDs que ya usa el conector, verificados en produccion.
# La lista vive ACA y no se importa del conector a proposito: la boveda y la
# vitrina eligen series con criterios distintos y no tienen que moverse juntas.
SERIES = {
    "tcrm":               "116.4_TCRZE_2015_D_36_4",
    "saldo_comercial":    "74.3_ISC_0_M_19",
    "emae_var_mensual":   "143.3_ICE_SER_VM_2004_A_34",
    "emae_transporte":    "11.3_EMC_2004_M_25",
    "emae_admin_pub":     "11.3_C_2004_M_60",
    "emae_salud":         "11.3_HR_2004_M_24",
    "emae_inmobiliario":  "11.3_SEGA_2004_M_48",
    "emae_minas":         "11.3_ISD_2004_M_26",
    "emae_sector_39":     "11.3_ISOM_2004_M_39",
    "emae_desest":        "143.3_NO_PR_2004_A_31",
    "emae_tendencia":     "143.3_NO_PR_2004_A_28",
    "demanda_elec_total": "367.3_DEMANDA_TOTAL__13",
    "demanda_elec_resid": "367.3_DEMANDA_REIAL__19",
    "recaudacion_total":  "172.3_TL_RECAION_M_0_0_17",
    "ripte":              "158.1_REPTE_0_0_5",
    "desocupacion":       "42.3_EPH_PUNTUATAL_0_M_30",
}


def _campo(d, *rutas):
    """La API anida en 'field' y 'dataset'. Tolera que cambien los nombres."""
    for ruta in rutas:
        v = d
        for tramo in ruta.split("."):
            if not isinstance(v, dict):
                v = None
                break
            v = v.get(tramo)
        if v not in (None, ""):
            return v
    return None


def _pedir(params):
    """GET con reintentos. Devuelve el JSON o lanza la ultima excepcion."""
    ultimo = None
    for intento in range(REINTENTOS):
        try:
            r = requests.get(DG_API, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            ultimo = e
            if intento < REINTENTOS - 1:
                time.sleep(1.5 * (intento + 1))
    raise ultimo


def bajar_serie(sid):
    """Baja la serie COMPLETA paginando con start/limit.

    Devuelve (puntos, meta). Los puntos vienen en orden ascendente de fecha.
    """
    puntos, meta, start = [], {}, 0
    while True:
        j = _pedir({"ids": sid, "limit": PAGINA, "start": start,
                    "sort": "asc", "metadata": "full", "format": "json"})
        if not meta:
            meta = next((m for m in (j.get("meta") or [])
                         if isinstance(m, dict) and (m.get("field") or m.get("dataset"))),
                        {})
        lote = j.get("data") or []
        puntos.extend([p[0], p[1]] for p in lote if len(p) >= 2)
        if len(lote) < PAGINA:
            break
        start += PAGINA
        time.sleep(PAUSA)
    return puntos, meta


def _hash_puntos(puntos):
    """Hash del contenido de la serie, estable e independiente del envoltorio."""
    crudo = json.dumps(puntos, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), default=str)
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()


def capturar():
    """Recorre las series. Devuelve (lineas, ok, huecos, total_puntos)."""
    lineas, ok, huecos, total = [], 0, 0, 0
    for clave, sid in sorted(SERIES.items()):
        capturado = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            puntos, meta = bajar_serie(sid)
            if not puntos:
                raise ValueError("la API respondio sin datos")
            fila = {
                "serie": clave,
                "id": sid,
                "capturado_utc": capturado,
                "n": len(puntos),
                "desde": puntos[0][0],
                "hasta": puntos[-1][0],
                "sha256_puntos": _hash_puntos(puntos),
                # La metadata NO se hardcodea: si el INDEC cambia el nombre, la
                # unidad o la base, el archivo lo registra. Tambien es revision.
                "descripcion": _campo(meta, "field.description", "dataset.title"),
                "unidades": _campo(meta, "field.units"),
                "frecuencia": _campo(meta, "field.frequency", "distribution.frequency"),
                "fuente": _campo(meta, "dataset.source", "dataset.publisher.name"),
                "dataset": _campo(meta, "dataset.title"),
                "puntos": puntos,
            }
            ok += 1
            total += len(puntos)
            print(f"   [boveda] {clave:<20} {len(puntos):>6} pts  "
                  f"{puntos[0][0]} → {puntos[-1][0]}  {fila['sha256_puntos'][:12]}…")
        except Exception as e:
            fila = {
                "serie": clave,
                "id": sid,
                "capturado_utc": capturado,
                "n": 0,
                "error": f"{type(e).__name__}: {e}",
                "puntos": [],
            }
            huecos += 1
            print(f"   [boveda] {clave:<20} HUECO — {type(e).__name__}: {e}")
        lineas.append(fila)
        time.sleep(PAUSA)
    return lineas, ok, huecos, total


def empaquetar(lineas, ok, huecos, total):
    """Arma el NDJSON gzipeado. Devuelve (bytes, sha256, manifiesto)."""
    ahora = datetime.now(timezone.utc)
    manifiesto = {
        "_manifiesto": True,
        "guarismo": "snapshot boveda · series completas",
        "fuente": "apis.datos.gob.ar · Series de Tiempo APN",
        "licencia": "Creative Commons Attribution 4.0",
        "capturado_utc": ahora.isoformat(timespec="seconds"),
        "series_pedidas": len(SERIES),
        "series_ok": ok,
        "huecos": huecos,
        "puntos_totales": total,
        "run_id": os.getenv("GITHUB_RUN_ID", "local"),
        "commit": os.getenv("GITHUB_SHA", "local"),
    }

    buf = io.BytesIO()
    # mtime=0 => gzip determinista (mismo contenido, mismo sha256).
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        for fila in [manifiesto] + lineas:
            gz.write((json.dumps(fila, ensure_ascii=False, sort_keys=True,
                                 default=str) + "\n").encode("utf-8"))
    datos = buf.getvalue()
    return datos, hashlib.sha256(datos).hexdigest(), manifiesto


def _slug(texto, largo=20):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", str(texto or "local")).strip("-").lower()
    return (s or "local")[:largo]


def subir_a_r2(datos, sha, manifiesto):
    """Sube el snapshot con su sidecar .sha256. Cualquier problema lanza."""
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
        config=Config(signature_version="s3v4",
                      retries={"max_attempts": 5, "mode": "standard"}),
    )

    ahora = datetime.now(timezone.utc)
    run = _slug(os.getenv("GITHUB_RUN_ID", "local"))
    nombre = f"snapshot_{ahora:%Y%m%dT%H%M%SZ}_{run}.ndjson.gz"
    clave = f"{PREFIJO}/{ahora:%Y/%m}/{nombre}"
    bucket = os.getenv("R2_BUCKET") or BUCKET_DEFAULT

    s3.put_object(
        Bucket=bucket, Key=clave, Body=datos, ContentType="application/gzip",
        Metadata={
            "sha256": sha,
            "origen": "boveda-datosgob",
            "series-ok": str(manifiesto["series_ok"]),
            "huecos": str(manifiesto["huecos"]),
            "puntos": str(manifiesto["puntos_totales"]),
            "capturado-utc": manifiesto["capturado_utc"],
        },
    )
    s3.put_object(
        Bucket=bucket, Key=clave + ".sha256",
        Body=f"{sha}  {nombre}\n".encode("utf-8"),
        ContentType="text/plain; charset=utf-8",
    )
    return clave, bucket


def main():
    print(f"[boveda] {len(SERIES)} series · snapshot COMPLETO (no el ultimo valor)")
    lineas, ok, huecos, total = capturar()

    if ok == 0:
        print("✗ Fallaron TODAS las series. No hay nada que archivar.")
        return 1

    datos, sha, manifiesto = empaquetar(lineas, ok, huecos, total)
    print(f"[boveda] {ok}/{len(SERIES)} series OK · {huecos} huecos · "
          f"{total} puntos · {len(datos)/1024:.0f} KB gz")

    try:
        clave, bucket = subir_a_r2(datos, sha, manifiesto)
    except Exception as e:
        print(f"[boveda] R2 fallo: {type(e).__name__}: {e}")
        print("  NO se archivo el snapshot de hoy. Corrida en rojo a proposito.")
        return 3

    print(f"[boveda] R2 OK: {clave}")
    print(f"[boveda] sha256: {sha}")
    if huecos:
        print(f"⚠ {huecos} serie(s) con hueco registrado. Revisar arriba cuales.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
