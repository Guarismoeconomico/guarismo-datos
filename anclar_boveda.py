#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
anclar_boveda.py — Anclaje de la bóveda (R2) en la cadena sellada.

POR QUÉ EXISTE (auditoría del 14-oct-2026, consulta 21.7 -> A)
    Hasta el 14-oct-2026 la raíz diaria (guarismo_sellador.py) sellaba SOLO las
    cabezas de guarismo_historico, es decir la cadena de la vitrina. Los objetos
    que la bóveda guarda en R2 (cuadros del INDEC, snapshots del BCRA y de
    datos.gob.ar, páginas y archivos de la mecánica D, Comtrade) no estaban
    cubiertos por ningún sello en Bitcoin. Este script los mete en la cadena.

QUÉ HACE
    1. Lista en R2 los objetos bajo `boveda/` (todos con --inicial; los nuevos
       desde el último anclaje en el modo diario). Los anclajes anteriores no
       se relistan: su sha256 ya está en la cadena.
    2. Baja cada uno y calcula su sha256 (no confía en metadatos ni en el etag).
    3. Escribe un OBJETO DE ANCLAJE en R2, con la lista completa:
           boveda/anclajes/AAAA/MM/anclaje_<sello>.json   (+ su .sha256 al lado)
    4. Inserta UNA fila en guarismo_historico, bucket `boveda`, con el sha256
       del anclaje, encadenada como las demás:
           hash_cadena = sha256(hash_cadena_previo + hash_contenido)
    El sellador suma la cabeza `boveda` a la raíz diaria (BUCKETS). Desde esa
    noche, cada sello cubre todo lo que la bóveda guardó hasta ese día.

MODOS
    (sin opción)  diario: desde el `hasta` del último anclaje. Si no hay nada
                  nuevo, no escribe nada y termina en 0.
    --inicial     TODO el prefijo, sin mirar el último anclaje. Es el sello
                  único de existencia de lo capturado antes de este script.
                  Se corre UNA vez. Si ya hay un anclaje, se niega.
    --seco        no escribe nada en R2 ni en Supabase: muestra qué anclaría y
                  con qué hashes. Siempre primero.

LO QUE NO HACE
    No toca los módulos de la bóveda, ni sellos/, ni la vitrina, ni el almacén
    reservado (guarismo-reservado: queda para una segunda vuelta con sus
    propias credenciales). No borra ni reescribe nada.

VARIABLES DE ENTORNO
    R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY   (obligatorias)
    R2_BUCKET                                             (default guarismo-crudo)
    SUPABASE_URL, SUPABASE_KEY (service_role)             (obligatorias salvo --seco)
    GITHUB_RUN_ID, GITHUB_SHA                             (trazabilidad)

CÓDIGO DE SALIDA
    0 anclaje escrito, o nada nuevo, o seco terminado.
    1 error: no se escribió nada, o se escribió el objeto en R2 pero NO la fila
      (el log lo dice; la próxima corrida diaria lo vuelve a cubrir, porque el
      anclaje huérfano es un objeto más bajo boveda/).
    2 configuración incompleta, o --inicial con un anclaje previo.
"""

import datetime as dt
import hashlib
import json
import os
import re
import sys
import time

import requests

PREFIJO = "boveda/"
PREFIJO_ANCLAJES = "boveda/anclajes/"
BUCKET_HISTORICO = "boveda"
BUCKET_DEFAULT = "guarismo-crudo"
TIMEOUT = 60
PAUSA_LISTADO = 0.2


# --- canónico: idéntico a guarismo_conector.py ------------------------------

def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def _sha_texto(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# --- R2 ---------------------------------------------------------------------

def cliente_r2():
    import boto3
    from botocore.config import Config
    endpoint = os.environ["R2_ENDPOINT"]
    return boto3.client(
        "s3",
        endpoint_url=endpoint.rstrip("/"),
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(signature_version="s3v4",
                      retries={"max_attempts": 5, "mode": "standard"}),
    )


def listar(s3, bucket: str, prefijo: str) -> list:
    """Todos los objetos bajo el prefijo, paginando. Sin filtros: el filtro por
    fecha se hace después, sobre lo listado, para que el log diga cuántos vio."""
    out, token = [], None
    while True:
        kw = {"Bucket": bucket, "Prefix": prefijo, "MaxKeys": 1000}
        if token:
            kw["ContinuationToken"] = token
        r = s3.list_objects_v2(**kw)
        for o in r.get("Contents", []) or []:
            lm = o["LastModified"]
            if lm.tzinfo is None:
                lm = lm.replace(tzinfo=dt.timezone.utc)
            out.append({
                "clave": o["Key"],
                "bytes": int(o["Size"]),
                "etag": (o.get("ETag") or "").strip('"'),
                "last_modified": lm.astimezone(dt.timezone.utc)
                                   .isoformat(timespec="seconds"),
            })
        if not r.get("IsTruncated"):
            break
        token = r.get("NextContinuationToken")
        time.sleep(PAUSA_LISTADO)
    # Los anclajes anteriores no se vuelven a anclar: su sha256 ya está en la
    # fila de la cadena que los creó. Así un día sin capturas da "nada nuevo".
    out = [e for e in out if not e["clave"].startswith(PREFIJO_ANCLAJES)]
    out.sort(key=lambda e: e["clave"])
    return out


def sha_objeto(s3, bucket: str, clave: str) -> str:
    """sha256 del objeto completo, bajándolo por partes. Nunca confía en el etag
    (en multipart no es el md5) ni en los metadatos (los puso otro proceso)."""
    h = hashlib.sha256()
    cuerpo = s3.get_object(Bucket=bucket, Key=clave)["Body"]
    for parte in iter(lambda: cuerpo.read(1024 * 1024), b""):
        h.update(parte)
    return h.hexdigest()


def leer_json_r2(s3, bucket: str, clave: str) -> dict:
    cuerpo = s3.get_object(Bucket=bucket, Key=clave)["Body"].read()
    return json.loads(cuerpo.decode("utf-8"))


# --- Supabase ---------------------------------------------------------------

def _cab(key: str) -> dict:
    return {"apikey": key, "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"}


def ultima_fila(url: str, key: str):
    """Última fila del bucket `boveda` en guarismo_historico, o None."""
    r = requests.get(
        f"{url}/rest/v1/guarismo_historico",
        params={"bucket": f"eq.{BUCKET_HISTORICO}",
                "select": "id,capturado_en,contenido,hash_contenido,hash_cadena",
                "order": "id.desc", "limit": "1"},
        headers=_cab(key), timeout=TIMEOUT)
    r.raise_for_status()
    filas = r.json() or []
    return filas[0] if filas else None


def insertar_fila(url: str, key: str, fila: dict) -> None:
    r = requests.post(f"{url}/rest/v1/guarismo_historico",
                      headers=_cab(key), json=fila, timeout=TIMEOUT)
    r.raise_for_status()


# --- programa ---------------------------------------------------------------

def main(argv) -> int:
    seco = "--seco" in argv
    inicial = "--inicial" in argv
    desconocidas = [a for a in argv if a not in ("--seco", "--inicial")]
    if desconocidas:
        print(f"✗ opción desconocida: {desconocidas}. Uso: [--seco] [--inicial]")
        return 2

    faltan = [v for v in ("R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
              if not os.getenv(v)]
    if faltan:
        print(f"✗ faltan variables de R2: {faltan}")
        return 2
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
    if not seco and not (url and key):
        print("✗ faltan SUPABASE_URL / SUPABASE_KEY (obligatorias fuera de --seco)")
        return 2

    bucket = os.getenv("R2_BUCKET") or BUCKET_DEFAULT
    ahora = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    run = re.sub(r"[^a-zA-Z0-9]+", "", os.getenv("GITHUB_RUN_ID", "local"))[:20] or "local"
    sello = f"{ahora:%Y%m%dT%H%M%SZ}_{run}"
    modo = "inicial" if inicial else "diario"

    print("=" * 62)
    print(f"  GUARISMO — Anclaje de la bóveda · modo {modo}"
          + (" · SECO" if seco else ""))
    print("=" * 62)
    print(f"   bucket R2: {bucket} · prefijo: {PREFIJO} · sello: {sello}")

    # --- el último anclaje: de dónde seguir -------------------------------
    previo = None
    if url and key:
        try:
            previo = ultima_fila(url, key)
        except Exception as e:
            print(f"✗ no pude leer guarismo_historico: {e}")
            return 1
    elif seco:
        print("   [seco] sin SUPABASE_URL/KEY: se asume que no hay anclaje previo.")

    desde = None
    claves_previas = set()
    if previo:
        cont_prev = json.loads(previo["contenido"])
        desde = cont_prev.get("hasta")
        print(f"   último anclaje: id={previo['id']} · {cont_prev.get('tipo')} · "
              f"hasta {desde} · cadena {previo['hash_cadena'][:12]}…")
        if inicial:
            print("✗ --inicial se corre UNA sola vez y ya hay un anclaje. "
                  "Para seguir, correr en modo diario.")
            return 2
    else:
        print("   sin anclaje previo en guarismo_historico.")
        if not inicial:
            print("✗ el modo diario necesita un anclaje previo. Correr --inicial "
                  "primero (en seco, y después en serio).")
            return 2

    s3 = cliente_r2()

    # --- qué hay en R2 ----------------------------------------------------
    t0 = time.time()
    todos = listar(s3, bucket, PREFIJO)
    print(f"   listados: {len(todos)} objetos bajo {PREFIJO} "
          f"({sum(o['bytes'] for o in todos)/1e6:.1f} MB) en {time.time()-t0:.0f} s")

    if inicial:
        objetos = todos
    else:
        # Los objetos ya cubiertos por el anclaje anterior se excluyen por clave,
        # no solo por fecha: así una hora igual al `hasta` no se cuenta dos veces
        # ni se pierde.
        clave_prev = cont_prev.get("anclaje")
        try:
            anterior = leer_json_r2(s3, bucket, clave_prev)
            claves_previas = {o["clave"] for o in anterior.get("objetos", [])}
            print(f"   anclaje anterior leído: {clave_prev} · "
                  f"{len(claves_previas)} objetos")
        except Exception as e:
            print(f"✗ no pude leer el anclaje anterior {clave_prev}: {e}")
            return 1
        objetos = [o for o in todos
                   if o["last_modified"] >= desde and o["clave"] not in claves_previas]

    if not objetos:
        print("   nada nuevo que anclar. No se escribe nada.")
        print("=" * 62 + "\n  ✓ listo · 0 objetos\n" + "=" * 62)
        return 0

    # --- sha256 de cada uno, bajándolos -----------------------------------
    t0 = time.time()
    total = 0
    for i, o in enumerate(objetos, 1):
        o["sha256"] = sha_objeto(s3, bucket, o["clave"])
        total += o["bytes"]
        if i <= 10 or i % 200 == 0 or i == len(objetos):
            print(f"   {i:>5}/{len(objetos)}  {o['sha256'][:12]}…  "
                  f"{o['bytes']:>10}  {o['clave']}")
    print(f"   sha256 calculados: {len(objetos)} objetos · {total/1e6:.1f} MB "
          f"· {time.time()-t0:.0f} s")

    hasta = max(o["last_modified"] for o in objetos)
    clave_anclaje = f"{PREFIJO_ANCLAJES}{ahora:%Y/%m}/anclaje_{sello}.json"

    anclaje = {
        "guarismo": "anclaje de la bóveda en la cadena sellada",
        "tipo": modo,
        "bucket_r2": bucket,
        "prefijo": PREFIJO,
        "desde": desde,
        "hasta": hasta,
        "generado_utc": ahora.isoformat(timespec="seconds"),
        "run_id": os.getenv("GITHUB_RUN_ID", "local"),
        "commit": os.getenv("GITHUB_SHA", "local"),
        "n_objetos": len(objetos),
        "bytes_total": total,
        "sha256": "sha256 del contenido completo de cada objeto, calculado al bajarlo",
        "objetos": objetos,
    }
    texto_anclaje = _canon(anclaje) + "\n"
    sha_anclaje = _sha_texto(texto_anclaje)

    # La fila es chica: apunta al anclaje y lleva su sha. El detalle vive en R2.
    contenido = _canon({
        "guarismo": "anclaje de la bóveda",
        "tipo": modo,
        "anclaje": clave_anclaje,
        "sha256_anclaje": sha_anclaje,
        "n_objetos": len(objetos),
        "bytes_total": total,
        "desde": desde,
        "hasta": hasta,
        "run_id": os.getenv("GITHUB_RUN_ID", "local"),
        "commit": os.getenv("GITHUB_SHA", "local"),
    })
    h_cont = _sha_texto(contenido)
    h_prev = previo["hash_cadena"] if previo else None
    h_cad = _sha_texto((h_prev or "") + h_cont)

    print(f"\n▸ Anclaje: {clave_anclaje}")
    print(f"   objetos {len(objetos)} · {total/1e6:.1f} MB · desde {desde} · hasta {hasta}")
    print(f"   sha256 del anclaje: {sha_anclaje}")
    print(f"▸ Fila en guarismo_historico · bucket '{BUCKET_HISTORICO}'")
    print(f"   hash_contenido: {h_cont}")
    print(f"   hash_previo:    {h_prev}")
    print(f"   hash_cadena:    {h_cad}")

    if seco:
        print("\n   SECO: no se escribió nada en R2 ni en Supabase.")
        print("=" * 62 + f"\n  ✓ seco · {len(objetos)} objetos se anclarían\n" + "=" * 62)
        return 0

    # --- escribir: primero R2 (objeto + sidecar), después la fila --------------
    try:
        s3.put_object(Bucket=bucket, Key=clave_anclaje,
                      Body=texto_anclaje.encode("utf-8"),
                      ContentType="application/json",
                      Metadata={"sha256": sha_anclaje, "origen": "anclar-boveda",
                                "tipo": modo})
        s3.put_object(Bucket=bucket, Key=clave_anclaje + ".sha256",
                      Body=f"{sha_anclaje}  {os.path.basename(clave_anclaje)}\n".encode("utf-8"),
                      ContentType="text/plain")
        print("   R2: anclaje y sidecar escritos.")
    except Exception as e:
        print(f"✗ no pude escribir el anclaje en R2: {e}. No se tocó Supabase.")
        return 1

    try:
        insertar_fila(url, key, {
            "bucket": BUCKET_HISTORICO,
            "capturado_en": ahora.isoformat(timespec="seconds"),
            "contenido": contenido,
            "hash_contenido": h_cont,
            "hash_previo": h_prev,
            "hash_cadena": h_cad,
        })
        print(f"   Supabase: fila insertada en '{BUCKET_HISTORICO}' · {h_cad[:12]}…")
    except Exception as e:
        print(f"✗ el anclaje quedó en R2 pero la fila NO se insertó: {e}")
        print("   Revisar a mano. La próxima corrida diaria vuelve a cubrir estos "
              "objetos (y el anclaje huérfano).")
        return 1

    print("=" * 62 + f"\n  ✓ listo · {len(objetos)} objetos anclados\n" + "=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
