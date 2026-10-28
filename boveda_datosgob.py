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
    (Desde la 21.8 tambien LEE sus propios snapshots anteriores bajo boveda/,
    solo para comparar. No los modifica.)

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

SERIE DEGRADADA — LA GUARDIA DE LA 21.8 (consulta 21.8 -> A, 14-oct-2026)
    El 13 y el 14-oct-2026 datos.gob devolvio la serie tcrm RECORTADA (5000
    puntos 2007->2010 un dia, 2000 puntos 1997->2002 al siguiente) y este
    modulo la archivo como OK. La unica guardia era "cero puntos = hueco".
    Verificado desde afuera ese mismo dia: la misma serie, en el mismo minuto,
    declaro count=10620 en una llamada y count=1687 en otra, y a start=2000
    devolvio una pagina vacia. Hipotesis (no regla): hay mas de un backend
    atras y cada pagina puede caer en uno distinto. El corte "pagina corta =
    fin de serie" toma esa pagina vacia como el final legitimo.

    Dos guardias, las dos baratas, y una serie es DEGRADADA si salta cualquiera:

      (1) CONTRA LA PROPIA FUENTE, sin estado: la API declara `count` (total de
          puntos de la serie). Si lo recibido no coincide, se baja la serie
          entera UNA vez mas (otra chance de caer en el backend sano) y si
          sigue sin cuadrar, degradada. El count NUNCA corta la paginacion:
          acusa, no manda (regla firme: ningun corte depende de un total
          declarado por la fuente).

      (2) CONTRA LA REFERENCIA, con memoria: se leen los ultimos snapshots de
          este mismo prefijo en R2 (VENTANA_REFERENCIA) y por serie se toma la
          referencia MAS LARGA que haya (mayor n, menor desde, mayor hasta).
          Si hoy llegan menos puntos, o la serie empieza despues, o termina
          antes, degradada. Se mira la ventana entera y no solo ayer porque
          ayer puede estar envenenado (el 14-oct lo esta: tcrm con 2000).
          Una linea no-OK (hueco o degradada) carga la referencia que uso, asi
          la memoria sobrevive una racha de dias malos mas larga que la ventana.

    Asimetrica a proposito: una serie que CRECE, o que el INDEC re-estima
    (mismo n, otro hash), es lo normal y no suena. Si una serie se achica de
    verdad (rebase, serie discontinuada), va a sonar todos los dias hasta que
    alguien lo mire. Es a proposito: mejor ruido que un recorte en silencio.

    Una serie degradada se escribe con n=0 y "error" como un hueco, mas la
    clave "degradada" (recibidos, declarados, rango, referencia, intentos) y
    lo recibido en "evidencia_parcial": aparte, nunca bajo "puntos" ni con
    sha256_puntos. La evidencia queda, el dato recortado no se hace pasar por
    serie completa. Codigo de salida: 0, como los huecos; el aviso va al log
    y al manifiesto.

    Si la referencia no se puede leer (R2 caido, prefijo vacio, credenciales),
    la guardia (2) se apaga por hoy CON AVISO en el log y en el manifiesto, y
    la (1) sigue funcionando sola.

DETERMINISMO
    gzip mtime=0 + json sort_keys=True: mismo contenido, mismo sha256. Misma
    regla que el sellador y que crudo_r2.py.

VARIABLES DE ENTORNO
    R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY   (obligatorias)
    R2_BUCKET                                             (opcional, default guarismo-crudo)

CODIGOS DE SALIDA
    0  todo bien (aunque alguna serie tenga hueco o este degradada)
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
from datetime import datetime, timedelta, timezone

import requests

for _f in (sys.stdout, sys.stderr):
    try:
        _f.reconfigure(encoding="utf-8")
    except Exception:
        pass

DG_API = "https://apis.datos.gob.ar/series/api/series"

# Guarismo se identifica en cada pedido. El conector ya lo hace ("Guarismo/1.0")
# y este modulo tiene que decir lo mismo: una sola identidad hacia las fuentes.
# Si un organismo se molesta por el volumen, que pueda escribir en vez de
# bloquear en silencio.
UA = {"User-Agent": "Guarismo/1.0 (+https://guarismo.com.ar; infoguarismo@gmail.com)"}
PAGINA = 1000          # maximo que acepta la API (default 100)
TIMEOUT = 60
PAUSA = 0.4            # cortesia entre llamadas
REINTENTOS = 3

# 21.8: cuantas bajadas completas de una serie antes de declararla degradada,
# y cuantos snapshots anteriores se miran para armar la referencia.
INTENTOS_SERIE = 2
VENTANA_REFERENCIA = 7

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
            r = requests.get(DG_API, params=params, headers=UA, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            ultimo = e
            if intento < REINTENTOS - 1:
                time.sleep(1.5 * (intento + 1))
    raise ultimo


def bajar_serie(sid):
    """Baja la serie COMPLETA paginando con start/limit.

    Devuelve (puntos, meta, declarado). Los puntos vienen en orden ascendente
    de fecha. `declarado` es el `count` que la API dijo en la PRIMERA pagina
    (None si no lo dijo): se registra y se compara, nunca decide el corte.
    """
    puntos, meta, start, declarado = [], {}, 0, None
    while True:
        j = _pedir({"ids": sid, "limit": PAGINA, "start": start,
                    "sort": "asc", "metadata": "full", "format": "json"})
        if not meta:
            meta = next((m for m in (j.get("meta") or [])
                         if isinstance(m, dict) and (m.get("field") or m.get("dataset"))),
                        {})
        if declarado is None and isinstance(j.get("count"), int):
            declarado = j["count"]
        lote = j.get("data") or []
        puntos.extend([p[0], p[1]] for p in lote if len(p) >= 2)
        if len(lote) < PAGINA:
            break
        start += PAGINA
        time.sleep(PAUSA)
    return puntos, meta, declarado


def _hash_puntos(puntos):
    """Hash del contenido de la serie, estable e independiente del envoltorio."""
    crudo = json.dumps(puntos, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), default=str)
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 21.8 — referencia: lo mas largo que esta serie tuvo en los ultimos snapshots
# ---------------------------------------------------------------------------

def cliente_r2():
    """Cliente S3 contra R2. Lanza si faltan credenciales."""
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


def _mes_anterior(ahora):
    return ahora.replace(day=1) - timedelta(days=1)


def listar_snapshots(s3, bucket, ahora, cuantos):
    """Las claves de los ultimos `cuantos` snapshots (mes en curso + anterior).

    Las claves ordenan solas por nombre porque llevan el sello UTC fijo; se
    toman las ultimas. Los .sha256 no cuentan.
    """
    claves = []
    for mes in (_mes_anterior(ahora), ahora):
        prefijo = f"{PREFIJO}/{mes:%Y/%m}/snapshot_"
        token = None
        while True:
            args = {"Bucket": bucket, "Prefix": prefijo}
            if token:
                args["ContinuationToken"] = token
            r = s3.list_objects_v2(**args)
            for o in r.get("Contents") or []:
                if o["Key"].endswith(".ndjson.gz"):
                    claves.append(o["Key"])
            if not r.get("IsTruncated"):
                break
            token = r.get("NextContinuationToken")
    return sorted(set(claves))[-cuantos:]


def _mejor(a, b):
    """De dos referencias de la misma serie, la mas larga en cada dimension."""
    if a is None:
        return b
    if b is None:
        return a
    return {
        "n": max(a["n"], b["n"]),
        "desde": min(a["desde"], b["desde"]),
        "hasta": max(a["hasta"], b["hasta"]),
        # el objeto que aporto el n mas alto, para poder ir a mirarlo
        "objeto": a.get("objeto") if a["n"] >= b["n"] else b.get("objeto"),
    }


def leer_referencia(s3, bucket, ahora):
    """Por serie, la referencia mas larga de los ultimos snapshots.

    Devuelve (referencias, nota). `referencias` es {serie: {n, desde, hasta,
    objeto}}. Una linea OK aporta su propio rango; una linea no-OK aporta la
    referencia que ella misma cargo (la memoria viaja). Si no hay nada
    legible, devuelve ({}, motivo) y la guardia (2) queda apagada por hoy.
    """
    claves = listar_snapshots(s3, bucket, ahora, VENTANA_REFERENCIA)
    if not claves:
        return {}, "sin snapshots anteriores bajo " + PREFIJO
    refs, leidos = {}, 0
    for clave in claves:
        cuerpo = s3.get_object(Bucket=bucket, Key=clave)["Body"].read()
        for linea in gzip.decompress(cuerpo).decode("utf-8").splitlines():
            if not linea.strip():
                continue
            fila = json.loads(linea)
            if fila.get("_manifiesto"):
                continue
            serie = fila.get("serie")
            if not serie:
                continue
            if fila.get("n", 0) > 0 and fila.get("desde") and fila.get("hasta"):
                aporte = {"n": fila["n"], "desde": fila["desde"],
                          "hasta": fila["hasta"], "objeto": clave}
            elif isinstance(fila.get("referencia"), dict) and fila["referencia"].get("n"):
                aporte = dict(fila["referencia"])
            else:
                continue
            refs[serie] = _mejor(refs.get(serie), aporte)
        leidos += 1
    nota = f"{leidos} snapshots leidos ({claves[0].rsplit('/', 1)[-1]} … {claves[-1].rsplit('/', 1)[-1]})"
    return refs, nota


def _problema(puntos, declarado, ref):
    """Por que esta bajada NO es una serie completa. None si esta bien."""
    if not puntos:
        return None  # el vacio es hueco, no degradacion; lo trata capturar()
    n, desde, hasta = len(puntos), puntos[0][0], puntos[-1][0]
    if isinstance(declarado, int) and n != declarado:
        return f"recibi {n} puntos y la API declaro {declarado}"
    if ref:
        if n < ref["n"]:
            return f"recibi {n} puntos y la referencia tiene {ref['n']}"
        if desde > ref["desde"]:
            return f"la serie empieza en {desde} y la referencia en {ref['desde']}"
        if hasta < ref["hasta"]:
            return f"la serie termina en {hasta} y la referencia en {ref['hasta']}"
    return None


def capturar(refs=None):
    """Recorre las series. Devuelve (lineas, ok, huecos, degradadas, total_puntos)."""
    refs = refs or {}
    lineas, ok, huecos, degradadas, total = [], 0, 0, 0, 0
    for clave, sid in sorted(SERIES.items()):
        capturado = datetime.now(timezone.utc).isoformat(timespec="seconds")
        ref = refs.get(clave)
        try:
            puntos, meta, declarado, motivo, intentos = [], {}, None, None, 0
            while intentos < INTENTOS_SERIE:
                intentos += 1
                puntos, meta, declarado = bajar_serie(sid)
                motivo = _problema(puntos, declarado, ref)
                if motivo is None:
                    break
                if intentos < INTENTOS_SERIE:
                    time.sleep(PAUSA * 2)
            if not puntos:
                raise ValueError("la API respondio sin datos")
            if motivo is not None:
                fila = {
                    "serie": clave,
                    "id": sid,
                    "capturado_utc": capturado,
                    "n": 0,
                    "error": f"serie degradada: {motivo}",
                    "degradada": {
                        "recibidos": len(puntos),
                        "declarados": declarado,
                        "desde_recibido": puntos[0][0],
                        "hasta_recibido": puntos[-1][0],
                        "intentos": intentos,
                        "sha256_evidencia": _hash_puntos(puntos),
                    },
                    "referencia": ref,
                    "evidencia_parcial": puntos,
                    "puntos": [],
                }
                degradadas += 1
                print(f"   [boveda] {clave:<20} DEGRADADA — {motivo}"
                      + (f" · ref {ref['n']} pts {ref['desde']} → {ref['hasta']}" if ref else ""))
            else:
                fila = {
                    "serie": clave,
                    "id": sid,
                    "capturado_utc": capturado,
                    "n": len(puntos),
                    "desde": puntos[0][0],
                    "hasta": puntos[-1][0],
                    "declarado": declarado,
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
                "referencia": ref,
                "puntos": [],
            }
            huecos += 1
            print(f"   [boveda] {clave:<20} HUECO — {type(e).__name__}: {e}")
        lineas.append(fila)
        time.sleep(PAUSA)
    return lineas, ok, huecos, degradadas, total


def empaquetar(lineas, ok, huecos, total, degradadas=0, referencia_nota=None):
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
        "degradadas": degradadas,
        "guardia_referencia": referencia_nota,
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


def subir_a_r2(datos, sha, manifiesto, s3=None):
    """Sube el snapshot con su sidecar .sha256. Cualquier problema lanza."""
    if s3 is None:
        s3 = cliente_r2()

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
            "degradadas": str(manifiesto["degradadas"]),
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

    # --- 21.8: la referencia, antes de pedirle nada a la fuente ------------
    s3, refs, nota = None, {}, None
    try:
        s3 = cliente_r2()
        refs, nota = leer_referencia(s3, os.getenv("R2_BUCKET") or BUCKET_DEFAULT,
                                     datetime.now(timezone.utc))
        if refs:
            print(f"[boveda] referencia: {len(refs)} series · {nota}")
        else:
            nota = f"guardia de referencia APAGADA hoy: {nota}"
            print(f"⚠ [boveda] {nota}")
    except Exception as e:
        nota = f"guardia de referencia APAGADA hoy: {type(e).__name__}: {e}"
        print(f"⚠ [boveda] {nota}")

    lineas, ok, huecos, degradadas, total = capturar(refs)

    if ok == 0:
        print("✗ Fallaron TODAS las series. No hay nada que archivar.")
        return 1

    datos, sha, manifiesto = empaquetar(lineas, ok, huecos, total, degradadas, nota)
    print(f"[boveda] {ok}/{len(SERIES)} series OK · {huecos} huecos · "
          f"{degradadas} degradadas · {total} puntos · {len(datos)/1024:.0f} KB gz")

    try:
        clave, bucket = subir_a_r2(datos, sha, manifiesto, s3)
    except Exception as e:
        print(f"[boveda] R2 fallo: {type(e).__name__}: {e}")
        print("  NO se archivo el snapshot de hoy. Corrida en rojo a proposito.")
        return 3

    print(f"[boveda] R2 OK: {clave}")
    print(f"[boveda] sha256: {sha}")
    if huecos:
        print(f"⚠ {huecos} serie(s) con hueco registrado. Revisar arriba cuales.")
    if degradadas:
        print(f"⚠ {degradadas} serie(s) DEGRADADA(S): la fuente devolvio menos de lo "
              f"que declara o de lo que tenia. Lo recibido quedo como evidencia aparte.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
