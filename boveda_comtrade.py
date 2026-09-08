#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
boveda_comtrade.py — Capa de ALARMA de UN Comtrade.

POR QUE EXISTE
    La ONU lo declara por escrito: mantiene UNA SOLA VERSION de cada dataset.
    Cuando un pais corrige lo que declaro, la version corregida REEMPLAZA a la
    anterior y no queda archivo de la previa. Lo que no se captura el dia que
    estaba, no se recupera nunca en ningun lado del mundo.

    Pero la misma fuente que no guarda lo que reviso, SI publica cuando lo
    reviso. El endpoint getDa devuelve, por dataset:

        firstReleased    — cuando salio por primera vez
        lastReleased     — cuando salio la ultima version
        datasetChecksum  — el hash que la propia ONU le pone al dataset
        totalRecords     — cuantos registros tiene

    Eso es el registro de revisiones, escrito por la fuente. Es exactamente
    "medir a las fuentes" y no cuesta bajar los datos.

EL LIMITE QUE JUSTIFICA MIRAR TODOS LOS DIAS
    getDa da DOS fechas: la primera y la ultima. Si un dataset se reviso cinco
    veces, las tres del medio NO EXISTEN en la respuesta de hoy: quedaron
    tapadas por el ultimo lastReleased.

    Por eso:
      - El backfill de getDa es Clase B: reconstruye primera y ultima.
      - Mirar todos los dias es Clase A: cada cambio de lastReleased o de
        datasetChecksum queda fechado por nosotros, incluidos los que despues
        seran tapados por una revision posterior.
    Las dos cuentas no se suman.

QUE NO HACE ESTA VERSION
    No baja el dato espejo. Eso es la capa 2 y va cuando esta capa diga que
    algo cambio. Misma arquitectura que la mecanica D: la pagina es la alarma,
    los archivos se bajan cuando la alarma suena.

    No toca Supabase. No toca la vitrina. No escribe en crudo/.

MEDIDO CONTRA LA FUENTE (sonda del 8-sep-2026, no inferido)
    - getDa PELADO da 404. La firma con path /C/{freq}/HS es la que anda.
    - publishedDateFrom/To filtra por lastReleased, no por firstReleased:
      Brasil 202301 (firstReleased 2023-03-09) entro en un rango que arranca
      el 2026-01-01 porque su lastReleased es 2026-06-19.
    - La API NO devuelve ningun header de cuota. La cuota no es descubrible:
      por eso este modulo lleva su propio presupuesto y es conservador.
    - Los archivos de referencia de paises son publicos, sin clave.

VARIABLES DE ENTORNO
    COMTRADE_KEY                                          (obligatoria)
    R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY   (obligatorias)
    R2_BUCKET                                             (opcional, default guarismo-crudo)

FORMATO
    Un objeto NDJSON comprimido por corrida, con su sidecar .sha256:

        boveda/comtrade/AAAA/MM/alarma_20260908T221500Z_1234.ndjson.gz

    Linea 1  -> manifiesto de la corrida
    Linea N  -> un dataset: los campos de la fuente + el veredicto de cambio

    Estado:
        _estado/comtrade_estado.json    (ultimo lastReleased+checksum visto)

    Si _estado se pierde, la unica consecuencia es que una corrida marca todo
    como PRIMERA. No es un bug: es el estado vacio.

USO
    python boveda_comtrade.py           corrida normal
    python boveda_comtrade.py --seco    no escribe un byte en ningun lado
"""

import gzip
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

BASE = "https://comtradeapi.un.org"
PREFIJO = "boveda/comtrade"
ESTADO = "_estado/comtrade_estado.json"
BUCKET_DEFAULT = "guarismo-crudo"

TIMEOUT = 90
PAUSA = 1.5

# Presupuesto duro de llamadas. La API no declara su cuota en los headers
# (medido: ni un x-ratelimit en toda la sonda), asi que el limite es nuestro.
MAX_LLAMADAS = 20

# Desde donde se pide. Cubre toda la historia: el filtro es por lastReleased
# y no existe dataset sin lastReleased.
DESDE = "2000-01-01"

# Los declarantes que se vigilan.
#
# Argentina esta en la lista A PROPOSITO: lo que Argentina le declara a la ONU
# tambien se sobrescribe, y comparar eso contra lo que publico el INDEC es un
# derivado propio. No es el espejo, es el reflejo de casa.
#
# Los codigos son M49. Se verifican contra el archivo de referencia publico de
# la propia fuente en cada corrida: si la fuente cambia un codigo, suena.
REPORTERS = {
    "32": "Argentina",
    "76": "Brasil",
    "156": "China",
    "842": "EEUU",
    "356": "India",
    "704": "Vietnam",
}

FRECUENCIAS = ["M", "A"]      # mensual y anual
CLASIFICACION = "HS"

REF_REPORTERS = "/files/v1/app/reference/Reporters.json"

_llamadas = 0


# ----------------------------------------------------------------------------
# Red
# ----------------------------------------------------------------------------

def _clave():
    k = os.getenv("COMTRADE_KEY", "").strip()
    if not k:
        raise RuntimeError("falta COMTRADE_KEY")
    return k


def pedir(ruta, params=None, con_clave=True):
    """Una llamada GET. Devuelve el JSON. Lanza si algo no cierra.

    La clave viaja SIEMPRE en header, jamas en la URL: una URL con la clave
    adentro termina en logs, en historiales y en manifiestos.
    """
    global _llamadas
    if _llamadas >= MAX_LLAMADAS:
        raise RuntimeError(f"presupuesto de {MAX_LLAMADAS} llamadas agotado")
    _llamadas += 1

    url = BASE + ruta
    if not url.startswith("https://"):
        raise RuntimeError(f"canal inseguro: {url}")

    headers = {"Accept": "application/json"}
    if con_clave:
        headers["Ocp-Apim-Subscription-Key"] = _clave()

    r = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)

    # Las redirecciones se miran enteras: un https que rebota por http deja
    # los bytes viajando sin autenticar en el medio.
    for h in r.history:
        if not h.url.startswith("https://"):
            raise RuntimeError(f"redireccion por canal inseguro: {h.url}")

    if r.status_code == 429:
        raise RuntimeError("429 — cuota agotada (la fuente no la declara en headers)")
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")

    ct = (r.headers.get("Content-Type") or "").lower()
    if "json" not in ct:
        raise RuntimeError(f"content-type inesperado: {ct or '(vacio)'}")

    datos = r.json()
    if datos.get("error"):
        raise RuntimeError(f"la fuente devolvio error: {datos['error']}")

    time.sleep(PAUSA)
    return datos


# ----------------------------------------------------------------------------
# Verificacion de codigos contra la fuente
# ----------------------------------------------------------------------------

def verificar_codigos():
    """Contrasta los codigos cableados contra el archivo publico de la fuente.

    No aborta la corrida: reporta. Un codigo que dejo de existir es un hecho
    de la fuente, y se archiva como tal.
    """
    try:
        ref = pedir(REF_REPORTERS, con_clave=False)
    except Exception as e:
        print(f"[comtrade] referencia de reporters no disponible ({e}) — sin verificar")
        return {}

    filas = ref.get("results") or ref.get("data") or []
    porcodigo = {}
    for f in filas:
        c = str(f.get("id") or f.get("reporterCode") or "")
        if c:
            porcodigo[c] = f.get("text") or f.get("reporterDesc") or ""

    veredicto = {}
    for cod, nombre in REPORTERS.items():
        oficial = porcodigo.get(cod)
        if oficial is None:
            veredicto[cod] = "NO ESTA EN LA REFERENCIA"
            print(f"[comtrade] ⚠ {cod} ({nombre}): no figura en la referencia oficial")
        else:
            veredicto[cod] = oficial
    print(f"[comtrade] codigos verificados contra la fuente: {len(porcodigo)} reporters en la referencia")
    return veredicto


# ----------------------------------------------------------------------------
# Captura
# ----------------------------------------------------------------------------

def _hoy():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def capturar_alarma():
    """getDa por reporter y frecuencia.

    Devuelve (datasets, huecos, celdas). `celdas` es la grilla COMPLETA
    reporter x frecuencia con el resultado de cada una, incluidas las que
    devolvieron cero.

    POR QUE LA GRILLA Y NO SOLO EL TOTAL
      Un reporter que responde 200 con lista vacia NO es un hueco: la llamada
      salio bien. Pero tampoco es normal, y hoy se suma al total y desaparece.
      Si Argentina pasa de 200 datasets a 0, el total baja y nadie sabe cual
      se cayo. Un manifiesto tiene que contar lo que OMITIO.
    """
    datasets = []
    huecos = []
    celdas = []
    hoy = _hoy()

    for cod, nombre in REPORTERS.items():
        for freq in FRECUENCIAS:
            etiqueta = f"{nombre}/{freq}"
            ruta = f"/data/v1/getDa/C/{freq}/{CLASIFICACION}"
            params = {
                "reporterCode": cod,
                "publishedDateFrom": DESDE,
                "publishedDateTo": hoy,
            }
            try:
                res = pedir(ruta, params)
            except Exception as e:
                print(f"[comtrade] HUECO — {etiqueta}: {e}")
                huecos.append({"reporter": cod, "nombre": nombre,
                               "freq": freq, "error": str(e)})
                celdas.append({"reporter": cod, "nombre": nombre, "freq": freq,
                               "datasets": None, "resultado": "error",
                               "error": str(e)})
                continue

            filas = res.get("data") or []
            for f in filas:
                f["_reporterNombre"] = nombre
                datasets.append(f)
            celdas.append({"reporter": cod, "nombre": nombre, "freq": freq,
                           "datasets": len(filas),
                           "resultado": "ok" if filas else "cero",
                           "error": None})
            print(f"[comtrade] {etiqueta:18s} {len(filas):5d} datasets"
                  + ("   <- CERO" if not filas else ""))

    return datasets, huecos, celdas


def capturar_liveupdate():
    """Las publicaciones mas recientes de TODO el sistema, no solo las nuestras.

    Es la serie de puntualidad del organismo entero, y cuesta una llamada.
    """
    try:
        res = pedir("/data/v1/getLiveUpdate")
    except Exception as e:
        print(f"[comtrade] HUECO — getLiveUpdate: {e}")
        return None, {"que": "getLiveUpdate", "error": str(e)}
    filas = res.get("data") or []
    print(f"[comtrade] getLiveUpdate      {len(filas):5d} publicaciones recientes")
    return filas, None


# ----------------------------------------------------------------------------
# El cero y el guion gritan
# ----------------------------------------------------------------------------

def gritar_ceros(celdas):
    """Separa la grilla en las que dieron cero y las que fallaron.

    NO rompe la corrida: un cero puede ser legitimo (un reporter puede no
    tener datasets en esta clasificacion). Pero tiene que VERSE, en el log y
    en el manifiesto, o es indistinguible de una caida silenciosa.
    """
    en_cero = [c for c in celdas if c["resultado"] == "cero"]
    con_error = [c for c in celdas if c["resultado"] == "error"]

    for c in con_error:
        print(f"[comtrade] !! SIN RESPUESTA  {c['nombre']}/{c['freq']} — "
              f"{c['error']}")
    for c in en_cero:
        print(f"[comtrade] !! CERO DATASETS  {c['nombre']}/{c['freq']} — "
              f"la llamada salio bien y la fuente no devolvio nada")

    if not en_cero and not con_error:
        print(f"[comtrade] grilla completa: {len(celdas)}/{len(celdas)} "
              f"celdas con datos")
    else:
        print(f"[comtrade] grilla: {len(celdas) - len(en_cero) - len(con_error)}"
              f"/{len(celdas)} con datos · {len(en_cero)} en cero · "
              f"{len(con_error)} con error")

    return {"celdas": celdas, "en_cero": en_cero, "con_error": con_error}


# ----------------------------------------------------------------------------
# Veredicto de cambio
# ----------------------------------------------------------------------------

def _firma(d):
    """Lo que define 'esto cambio'. NUNCA es solo el checksum de la fuente."""
    return {
        "lastReleased": d.get("lastReleased"),
        "datasetChecksum": d.get("datasetChecksum"),
        "totalRecords": d.get("totalRecords"),
    }


def evaluar(datasets, estado):
    """Marca cada dataset como PRIMERA, NUEVO o sin cambios.

    Se compara la firma completa, no un campo suelto: si cambia el checksum
    pero no el lastReleased, eso tambien es un hecho y hay que verlo.
    """
    nuevo_estado = {}
    conteo = {"PRIMERA": 0, "NUEVO": 0, "igual": 0}

    for d in datasets:
        clave = str(d.get("datasetCode"))
        firma = _firma(d)
        previo = estado.get(clave)

        if previo is None:
            d["_veredicto"] = "PRIMERA"
            d["_motivo"] = "sin registro previo en la boveda"
            conteo["PRIMERA"] += 1
        elif previo != firma:
            cambios = [k for k in firma if previo.get(k) != firma.get(k)]
            d["_veredicto"] = "NUEVO"
            d["_motivo"] = "cambio en: " + ", ".join(cambios)
            d["_previo"] = previo
            conteo["NUEVO"] += 1
        else:
            d["_veredicto"] = "igual"
            d["_motivo"] = ""
            conteo["igual"] += 1

        nuevo_estado[clave] = firma

    return nuevo_estado, conteo


# ----------------------------------------------------------------------------
# Empaquetado
# ----------------------------------------------------------------------------

def empaquetar(datasets, live, huecos, codigos, conteo, grilla):
    ahora = datetime.now(timezone.utc)
    manifiesto = {
        "_tipo": "manifiesto",
        "modulo": "boveda-comtrade",
        "capa": "alarma",
        "capturado_utc": ahora.isoformat(),
        "fuente": BASE,
        "clasificacion": CLASIFICACION,
        "frecuencias": FRECUENCIAS,
        "reporters": REPORTERS,
        "codigos_verificados": codigos,
        "publishedDateFrom": DESDE,
        "publishedDateTo": _hoy(),
        "datasets_total": len(datasets),
        "primera": conteo["PRIMERA"],
        "nuevos": conteo["NUEVO"],
        "sin_cambios": conteo["igual"],
        "huecos": huecos,
        "grilla_celdas": grilla["celdas"],
        "reporters_en_cero": grilla["en_cero"],
        "reporters_con_error": grilla["con_error"],
        "liveupdate_registros": len(live) if live is not None else None,
        "llamadas_gastadas": _llamadas,
        "presupuesto": MAX_LLAMADAS,
        "run_id": os.getenv("GITHUB_RUN_ID", "local"),
        "nota_clase": ("getDa entrega solo firstReleased y lastReleased: las "
                       "revisiones intermedias solo existen si se mira todos "
                       "los dias. El backfill de este endpoint es Clase B."),
    }

    lineas = [json.dumps(manifiesto, ensure_ascii=False)]
    for d in datasets:
        lineas.append(json.dumps({"_tipo": "dataset", **d}, ensure_ascii=False))
    if live is not None:
        for f in live:
            lineas.append(json.dumps({"_tipo": "liveupdate", **f}, ensure_ascii=False))

    crudo = ("\n".join(lineas) + "\n").encode("utf-8")
    datos = gzip.compress(crudo, 9)
    sha = hashlib.sha256(datos).hexdigest()
    return datos, sha, manifiesto


# ----------------------------------------------------------------------------
# R2
# ----------------------------------------------------------------------------

def _cliente():
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
        config=Config(signature_version="s3v4",
                      retries={"max_attempts": 5, "mode": "standard"}),
    )


# Los unicos codigos que significan "todavia no existe", que es CORRECTO en
# la primera corrida. Cualquier otra cosa significa "no pude leer".
NO_EXISTE = ("NoSuchKey", "NoSuchBucket", "404", "NotFound")


def leer_estado(s3, bucket):
    """Lee el estado. Distingue "no existe" de "no puedo leer".

    POR QUE ESTO ABORTA LA CORRIDA
      Un `except Exception: return {}` trata igual NoSuchKey (primera corrida,
      correcto) y AccessDenied (no tengo permiso). El segundo caso haria decir
      "sin estado previo", marcar TODOS los datasets como PRIMERA y despues
      PISAR el estado bueno con uno reconstruido a ciegas.

      Y en este modulo duele el doble: `0 primera` es justamente la prueba de
      que el estado se leyo. Mejor un workflow en rojo que un archivo que
      miente en verde.
    """
    try:
        obj = s3.get_object(Bucket=bucket, Key=ESTADO)
        return json.loads(obj["Body"].read().decode("utf-8"))
    except Exception as e:
        codigo = type(e).__name__
        try:
            codigo = e.response["Error"]["Code"]          # botocore ClientError
        except Exception:
            pass
        if codigo in NO_EXISTE or type(e).__name__ in NO_EXISTE:
            print(f"[comtrade] sin estado previo ({codigo}) — todo cuenta como PRIMERA")
            return {}
        raise RuntimeError(
            f"no se pudo LEER el estado ({codigo}). La credencial responde pero "
            f"no entrega {ESTADO}. Se aborta a proposito: seguir marcaria todo "
            f"como PRIMERA y pisaria el estado bueno."
        ) from e


def subir(s3, bucket, datos, sha, manifiesto, nuevo_estado):
    ahora = datetime.now(timezone.utc)
    run = re.sub(r"[^a-zA-Z0-9]+", "", os.getenv("GITHUB_RUN_ID", "local"))[:20] or "local"
    nombre = f"alarma_{ahora:%Y%m%dT%H%M%SZ}_{run}.ndjson.gz"
    clave = f"{PREFIJO}/{ahora:%Y/%m}/{nombre}"

    s3.put_object(
        Bucket=bucket, Key=clave, Body=datos, ContentType="application/gzip",
        Metadata={
            "sha256": sha,
            "origen": "boveda-comtrade",
            "capa": "alarma",
            "datasets": str(manifiesto["datasets_total"]),
            "nuevos": str(manifiesto["nuevos"]),
            "huecos": str(len(manifiesto["huecos"])),
            "en-cero": str(len(manifiesto["reporters_en_cero"])),
            "capturado-utc": manifiesto["capturado_utc"],
        },
    )
    s3.put_object(
        Bucket=bucket, Key=clave + ".sha256",
        Body=f"{sha}  {nombre}\n".encode("utf-8"),
        ContentType="text/plain; charset=utf-8",
    )
    # El estado se escribe DESPUES del objeto: si la subida falla, el estado
    # viejo sigue en pie y la proxima corrida vuelve a detectar el cambio.
    s3.put_object(
        Bucket=bucket, Key=ESTADO,
        Body=json.dumps(nuevo_estado, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json; charset=utf-8",
    )
    return clave


# ----------------------------------------------------------------------------

def main():
    seco = "--seco" in sys.argv

    print(f"[comtrade] {len(REPORTERS)} reporters × {len(FRECUENCIAS)} frecuencias · capa de ALARMA")
    if seco:
        print("[comtrade] ⚠ MODO SECO: no se escribe NADA en R2. Solo se informa.")

    bucket = os.getenv("R2_BUCKET") or BUCKET_DEFAULT
    s3 = None
    estado = {}
    if not seco:
        try:
            s3 = _cliente()
            estado = leer_estado(s3, bucket)
        except Exception as e:
            print(f"✗ R2 no disponible: {e}")
            return 1
    else:
        # En seco tambien se lee el estado si se puede: sin el, el seco dice
        # "todo PRIMERA" y no informa nada util.
        try:
            s3ro = _cliente()
            estado = leer_estado(s3ro, bucket)
        except Exception as e:
            print(f"[comtrade] seco sin estado ({type(e).__name__}) — todo dira PRIMERA")

    codigos = verificar_codigos()
    datasets, huecos, celdas = capturar_alarma()
    live, hueco_live = capturar_liveupdate()
    if hueco_live:
        huecos.append(hueco_live)

    if not datasets:
        print("✗ Ningun dataset. No hay nada que archivar.")
        return 1

    nuevo_estado, conteo = evaluar(datasets, estado)

    # Los cambios se listan uno por uno: son el producto del modulo.
    for d in datasets:
        if d["_veredicto"] == "NUEVO":
            print(f"[comtrade] NUEVO  {d['_reporterNombre']:10s} {d.get('freqCode')} "
                  f"{d.get('period')}  {d['_motivo']}  "
                  f"first={d.get('firstReleased')} last={d.get('lastReleased')}")

    grilla = gritar_ceros(celdas)
    datos, sha, manifiesto = empaquetar(datasets, live, huecos, codigos,
                                        conteo, grilla)

    print(f"[comtrade] {len(datasets)} datasets · {conteo['PRIMERA']} primera · "
          f"{conteo['NUEVO']} nuevos · {conteo['igual']} sin cambios · "
          f"{len(huecos)} huecos · {len(datos)//1024} KB gz")
    print(f"[comtrade] llamadas: {_llamadas} de {MAX_LLAMADAS}")

    if seco:
        print("[comtrade] SECO · no se escribio nada. Si el log dice lo esperado, correr sin --seco.")
        return 0

    try:
        clave = subir(s3, bucket, datos, sha, manifiesto, nuevo_estado)
    except Exception as e:
        print(f"✗ R2 fallo al subir: {e}")
        return 1

    print(f"[comtrade] R2 OK: {clave}")
    print(f"[comtrade] sha256: {sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
