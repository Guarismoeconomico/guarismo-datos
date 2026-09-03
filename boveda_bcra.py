#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
boveda_bcra.py — Boveda del BCRA (mecanica C: API v4.0).

POR QUE EXISTE
    Las reservas internacionales diarias son PROVISORIAS hasta el balance
    semanal. El BCRA corrige el numero y no guarda la version anterior. Lo
    mismo pasa con la base monetaria y los agregados. Lo que no se captura el
    dia que estaba, no se recupera nunca.

QUE CAPTURA, Y POR QUE ASI
    (1) EL CATALOGO COMPLETO, todos los dias.
        Son ~1.610 variables. Cada entrada trae ultFechaInformada y
        ultValorInformado: o sea que el catalogo NO es un indice, es LA CABEZA
        DE LAS 1.610 SERIES. Dos requests preservan el provisorio diario de
        todo el BCRA. Ademas registra altas, bajas, renombres y cambios de
        primerFechaInformada (un rebase de serie se ve ahi y en ningun lado mas).

    (2) LA HISTORIA COMPLETA de las 31 variables de "Principales Variables".
        Bajar la historia entera de las 1.610 serian miles de requests y varios
        GB por año contra un limite de 10 GB. No se hace. Las 31 principales
        cubren reservas, base monetaria, CER, UVA, ICL, TAMAR, BADLAR y tipos
        de cambio: todo lo que se revisa y todo lo que tiene comprador.

TLS — LA DECISION DEL 2-sep-2026
    El conector de la VITRINA usa VERIFY_BCRA = False por una cadena de
    certificados historicamente mal armada. Se diagnostico el 2-sep-2026 desde
    el runner: el BCRA VERIFICA BIEN con certifi por defecto. No hay escalera,
    no hay excepcion, no hay verify=False en este archivo.

    Un archivo notarial no puede sellar datos recibidos por un canal no
    autenticado. Si algun dia la verificacion falla, queda un HUECO REGISTRADO
    — que es la conducta correcta.

    Ademas se registra el sha256 del certificado hoja observado. El certificado
    vigente vence el 25-feb-2027: cuando se renueve, la huella cambia y eso
    queda como hecho fechado en el archivo.

QUE NO HACE
    No toca Supabase. No toca la vitrina. No escribe en el prefijo crudo/.
    Solo LEE una API publica y escribe objetos nuevos bajo boveda/.

    Captura los ids 27, 28 y 29 (inflacion mensual, interanual y REM). ARCHIVAR
    NO ES PUBLICAR: nada de esto sale a ninguna pantalla como afirmacion propia
    de Guarismo. Regla intocable del proyecto.

FORMATO
    Un objeto NDJSON comprimido por corrida, con su sidecar .sha256:

        boveda/bcra/AAAA/MM/snapshot_20260902T221500Z_1234.ndjson.gz

    Linea 1    -> manifiesto de la corrida (incluye la huella TLS)
    Lineas N   -> tipo "catalogo": una por variable del catalogo
    Lineas M   -> tipo "serie":    una por serie completa, con su hash propio

    El hash POR SERIE es lo que despues permite responder "esta serie cambio
    respecto de ayer" sin descomprimir el archivo entero.

PAGINACION — EL FIX DEL 3-sep-2026
    El BCRA devuelve metadata.resultset.count distinto entre paginas y entre
    endpoints: no sirve como total. Cortar por aritmetica de count andaba de
    casualidad y podia truncar EN SILENCIO. Se corta por PAGINA CORTA (una
    pagina con menos elementos que el limit es la ultima) y si se agotan las
    paginas sin ver una corta, se lanza. El count se registra, no decide.

HUECOS, NO MENTIRAS
    Si una serie falla, se escribe igual con {"error": ...} y n=0. Queda el
    hueco registrado con su fecha. Si fallan TODAS, termina en rojo sin subir.
    Un truncamiento silencioso no seria un hueco: seria un archivo incompleto
    que parece completo. Por eso el corte de paginacion falla ruidoso.

DETERMINISMO
    gzip mtime=0 + json sort_keys=True: mismo contenido, mismo sha256. Misma
    regla que el sellador, que crudo_r2.py y que boveda_datosgob.py.

VARIABLES DE ENTORNO
    R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY   (obligatorias)
    R2_BUCKET                                             (opcional, default guarismo-crudo)

CODIGOS DE SALIDA
    0  todo bien (aunque alguna serie tenga hueco)
    1  fallo el catalogo Y todas las series: no hay nada que archivar
    3  hay datos pero R2 fallo  <- corrida en ROJO a proposito
"""

import gzip
import hashlib
import io
import json
import os
import re
import socket
import ssl
import sys
import time
import unicodedata
from datetime import datetime, timezone

import requests

for _f in (sys.stdout, sys.stderr):
    try:
        _f.reconfigure(encoding="utf-8")
    except Exception:
        pass

HOST = "api.bcra.gob.ar"
BASE = f"https://{HOST}/estadisticas/v4.0/monetarias"

# Una sola identidad hacia las fuentes, igual que el conector y las otras
# bovedas. Si al BCRA le molesta el volumen, que pueda escribir en vez de
# bloquear en silencio.
UA = {"User-Agent": "Guarismo/1.0 (+https://guarismo.com.ar; infoguarismo@gmail.com)"}

PAGINA = 1000          # se ajusta solo si la API devuelve un limit menor
TIMEOUT = 60
PAUSA = 0.35           # cortesia entre llamadas
REINTENTOS = 3
TOPE_PAGINAS = 60      # cinturon: 60.000 puntos por serie es mas que de sobra

BUCKET_DEFAULT = "guarismo-crudo"
PREFIJO = "boveda/bcra"

# Las 31 variables de la categoria "Principales Variables", verificadas contra
# el catalogo el 2-sep-2026. El id se fija ACA a proposito: el conector de la
# vitrina las resuelve por palabras y eso esta bien para la vitrina, pero la
# boveda no puede depender de un match difuso.
#
# La descripcion esperada NO es un candado: si cambia, se registra el hecho en
# la linea de la serie y se sigue. Un renombre del BCRA es dato, no error.
PRINCIPALES = {
    1:  ("reservas_internacionales",   "Reservas internacionales"),
    4:  ("tc_minorista_vendedor",      "Tipo de cambio minorista (promedio vendedor)"),
    5:  ("tc_mayorista_referencia",    "Tipo de cambio mayorista de referencia"),
    7:  ("tasa_badlar_priv_tna",       "Tasa de interés BADLAR de bancos privados"),
    8:  ("tasa_tm20_priv_tna",         "Tasa de interés TM20 de bancos privados"),
    11: ("tasa_baibar",                "Tasa de interés de préstamos entre entidades financiera privadas (BAIBAR)"),
    12: ("tasa_plazo_fijo_30d",        "Tasa de interés de depósitos a 30 días de plazo en entidades financieras"),
    13: ("tasa_adelantos_cc",          "Tasa de interés por adelantos en cuenta corriente"),
    14: ("tasa_prestamos_personales",  "Tasa de interés de préstamos personales"),
    15: ("base_monetaria",             "Base monetaria"),
    16: ("circulacion_monetaria",      "Circulación monetaria"),
    17: ("billetes_publico",           "Billetes y monedas en poder del público"),
    18: ("efectivo_entidades",         "Efectivo en entidades financieras."),
    19: ("dep_entidades_cc_bcra",      "Depósitos de las entidades financieras en cuenta corriente en el BCRA"),
    21: ("dep_efectivo_total",         "Depósitos en efectivo en las entidades financieras"),
    22: ("dep_cuenta_corriente",       "Depósitos en efectivo en las entidades financieras en cuentas corrientes(neto de utilización FUCO)"),
    23: ("dep_caja_ahorro",            "Depósitos en efectivo en las entidades financieras en cajas de ahorro"),
    24: ("dep_plazo",                  "Depósitos a plazo en efectivo en las entidades financieras (incluye inversiones y excluye CEDROs)"),
    25: ("m2_privado_var_ia",          "Variación interanual del promedio móvil de 30 días del M2 privado."),
    26: ("prestamos_sector_privado",   "Préstamos de las entidades financieras al sector privado"),
    27: ("ipc_mensual",                "Inflación mensual."),
    28: ("ipc_interanual",             "Inflación interanual."),
    29: ("rem_ipc_12m_mediana",        "Mediana de la variación interanual próximos 12 meses del índice de precios al consumidor del relevamiento de expectativas de mercado"),
    30: ("cer",                        "Coeficiente de estabilización de referencia (base 2.2.02=1)"),
    31: ("uva",                        "Unidad de valor adquisitivo (base 31.3.16=14.05)"),
    32: ("uvi",                        "Unidad de vivienda (base 31.3.16=14.05)"),
    35: ("tasa_badlar_priv_tea",       "Tasa de interés BADLAR de bancos privados"),
    40: ("icl",                        "Índice para Contratos de Locación (base 30.6.20=1)"),
    43: ("tasa_uso_justicia",          "Tasa de interés Comunicado P 14.290 (Uso de justicia)"),
    44: ("tasa_tamar_priv_tna",        "Tasa de interes TAMAR de bancos privados"),
    45: ("tasa_tamar_priv_tea",        "Tasa de interés TAMAR de bancos privados"),
}


def _norm(s):
    """Normaliza para comparar descripciones.

    El BCRA mete espacios duros (U+00A0) y espacios al final en varias
    descripciones. Comparar en crudo daria falsos 'cambio de descripcion'
    todos los dias.
    """
    s = unicodedata.normalize("NFKC", str(s or ""))
    return re.sub(r"\s+", " ", s).strip().casefold()


def huella_tls():
    """sha256 del certificado hoja que presenta el servidor, y su vencimiento.

    NO reemplaza a la verificacion: los pedidos van con verify por defecto.
    Esto es evidencia adicional. Si un dia cambia la huella, el archivo lo
    muestra con fecha.
    """
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((HOST, 443), timeout=30) as s:
            with ctx.wrap_socket(s, server_hostname=HOST) as ss:
                der = ss.getpeercert(binary_form=True)
                info = ss.getpeercert()
        return {
            "verificado": True,
            "cert_sha256": hashlib.sha256(der).hexdigest(),
            "not_after": (info or {}).get("notAfter"),
        }
    except Exception as e:
        return {"verificado": False, "error": f"{type(e).__name__}: {e}"}


def _pedir(url, params=None):
    """GET con reintentos y verificacion TLS PLENA. Nunca verify=False."""
    ultimo = None
    for intento in range(REINTENTOS):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            ultimo = e
            if intento < REINTENTOS - 1:
                time.sleep(1.5 * (intento + 1))
    raise ultimo


def _resultset(j):
    return ((j or {}).get("metadata") or {}).get("resultset") or {}


def _pagina_llena(rs):
    """Cuantos elementos tiene una pagina LLENA en esta respuesta.

    Es el menor entre el limit que pedimos y el limit que la API dice haber
    aplicado. Cubre las dos direcciones: si el BCRA recorta el limit (devuelve
    500 cuando pedimos 1000) no cortamos de mas; si declara un limit mayor que
    el pedido, tampoco.
    """
    declarado = int(rs.get("limit") or 0) or PAGINA
    return max(1, min(declarado, PAGINA))


def bajar_catalogo():
    """Catalogo COMPLETO, paginando por offset. Devuelve (filas, declarado_1a).

    CORTE POR PAGINA CORTA — fix del 3-sep-2026
        El BCRA devuelve metadata.resultset.count DISTINTO entre paginas: en la
        primera trae el total (1.610) y en la ultima trae el resto (610). Cortar
        por aritmetica de count funcionaba de casualidad y era una bomba de
        tiempo: el dia que la primera pagina declare el largo del lote en vez
        del total, el bucle corta en la pagina 1 y archiva 1.000 de 1.610 EN
        SILENCIO, sin hueco y sin error. Un snapshot truncado que parece
        completo es el peor modo de falla de un archivo notarial.

        Ahora el count NO decide nada: es solo una señal que se registra. El
        corte lo da la propia respuesta — una pagina mas corta que el limit es
        la ultima. Y si se agotan las paginas sin ver una corta, se LANZA:
        mejor un hueco registrado que un archivo truncado que parece entero.
    """
    filas, offset, declarado_1a = [], 0, None
    for pagina in range(TOPE_PAGINAS):
        j = _pedir(BASE, {"limit": PAGINA, "offset": offset})
        lote = j.get("results") or []
        rs = _resultset(j)
        if pagina == 0:
            declarado_1a = int(rs.get("count") or 0) or None
        filas.extend(lote)
        if len(lote) < _pagina_llena(rs):
            return filas, declarado_1a
        offset += len(lote)
        time.sleep(PAUSA)
    raise RuntimeError(
        f"catalogo: se agotaron las {TOPE_PAGINAS} paginas sin llegar a una "
        f"pagina corta ({len(filas)} filas bajadas). Posible truncamiento: no "
        f"se archiva como completo.")


def bajar_serie(idv):
    """Historia COMPLETA de una variable.

    El sobre es results[0]["detalle"], NO results directamente, y viene en
    orden descendente por fecha. Verificado sobre la respuesta real el
    2-sep-2026. Se ordena ascendente antes de guardar para que el hash no
    dependa del orden en que la API decida devolver.

    Corta por PAGINA CORTA, igual que el catalogo y por el mismo motivo: el
    count del BCRA no es confiable como total. Si se agotan las paginas sin
    pagina corta, lanza — y capturar() lo registra como HUECO de esa serie.
    """
    puntos, offset = [], 0
    for _ in range(TOPE_PAGINAS):
        j = _pedir(f"{BASE}/{idv}", {"limit": PAGINA, "offset": offset})
        res = j.get("results") or []
        lote = (res[0].get("detalle") or []) if res else []
        puntos.extend(lote)
        if len(lote) < _pagina_llena(_resultset(j)):
            puntos.sort(key=lambda p: str(p.get("fecha") or ""))
            return puntos
        offset += len(lote)
        time.sleep(PAUSA)
    raise RuntimeError(
        f"serie {idv}: se agotaron las {TOPE_PAGINAS} paginas sin llegar a una "
        f"pagina corta ({len(puntos)} puntos bajados). Posible truncamiento.")


def capturar():
    ahora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lineas, huecos, total_puntos = [], 0, 0

    # ---- 1. Catalogo -----------------------------------------------------
    print(f"[bcra] catalogo completo (cabeza de TODAS las series)")
    catalogo, cat_ok, cat_declarado = [], 0, None
    try:
        catalogo, cat_declarado = bajar_catalogo()
        cat_ok = len(catalogo)
        # El count se LOGUEA, no decide. Y es el de la PRIMERA pagina: el de la
        # ultima era el resto (610) y por eso el log viejo mentia.
        print(f"[bcra] catalogo: {cat_ok} variables "
              f"(la API declaro {cat_declarado} en la 1a pagina)")
        if cat_declarado and cat_declarado != cat_ok:
            print(f"   [bcra] AVISO — declarado {cat_declarado} ≠ bajado {cat_ok}. "
                  f"El count del BCRA no es confiable; manda lo bajado.")
        for v in catalogo:
            lineas.append({"tipo": "catalogo", "capturado_utc": ahora, **v})
    except Exception as e:
        huecos += 1
        lineas.append({"tipo": "catalogo", "capturado_utc": ahora,
                       "error": f"{type(e).__name__}: {e}"})
        print(f"[bcra] catalogo HUECO — {type(e).__name__}: {e}")

    por_id = {v.get("idVariable"): v for v in catalogo}

    # ---- 2. Historia completa de las Principales --------------------------
    print(f"[bcra] {len(PRINCIPALES)} series · historia COMPLETA")
    series_ok = 0
    for idv, (clave, desc_esperada) in PRINCIPALES.items():
        meta = por_id.get(idv) or {}
        fila = {
            "tipo": "serie",
            "serie": clave,
            "id_variable": idv,
            "capturado_utc": ahora,
            "descripcion_catalogo": meta.get("descripcion"),
            "categoria": meta.get("categoria"),
            "periodicidad": meta.get("periodicidad"),
            "unidad": meta.get("unidadExpresion"),
            "primera_fecha_informada": meta.get("primerFechaInformada"),
        }

        # Hechos sobre el id, no errores: se registran y se sigue.
        if not meta and catalogo:
            fila["ausente_del_catalogo"] = True
            print(f"   [bcra] {clave:<28} AVISO — id {idv} no esta en el catalogo")
        elif meta and _norm(meta.get("descripcion")) != _norm(desc_esperada):
            fila["descripcion_cambio"] = {"esperada": desc_esperada,
                                          "actual": meta.get("descripcion")}
            print(f"   [bcra] {clave:<28} AVISO — descripcion cambiada")

        try:
            puntos = bajar_serie(idv)
            cuerpo = json.dumps(puntos, ensure_ascii=False, sort_keys=True,
                                default=str).encode("utf-8")
            fila["n"] = len(puntos)
            fila["desde"] = puntos[0].get("fecha") if puntos else None
            fila["hasta"] = puntos[-1].get("fecha") if puntos else None
            fila["sha256"] = hashlib.sha256(cuerpo).hexdigest()
            fila["puntos"] = puntos
            total_puntos += len(puntos)
            series_ok += 1
            print(f"   [bcra] {clave:<28} {len(puntos):>6} pts  "
                  f"{fila['desde']} → {fila['hasta']}  {fila['sha256'][:12]}…")
        except Exception as e:
            fila["n"] = 0
            fila["puntos"] = []
            fila["error"] = f"{type(e).__name__}: {e}"
            huecos += 1
            print(f"   [bcra] {clave:<28} HUECO — {type(e).__name__}: {e}")

        lineas.append(fila)
        time.sleep(PAUSA)

    return lineas, cat_ok, series_ok, huecos, total_puntos, cat_declarado


def empaquetar(lineas, cat_ok, series_ok, huecos, total_puntos, tls,
               cat_declarado=None):
    """Arma el NDJSON gzipeado. Devuelve (bytes, sha256, manifiesto)."""
    ahora = datetime.now(timezone.utc)
    manifiesto = {
        "_manifiesto": True,
        "guarismo": "snapshot boveda · BCRA (catalogo completo + principales con historia)",
        "fuente": "api.bcra.gob.ar · Estadisticas monetarias v4.0",
        "capturado_utc": ahora.isoformat(timespec="seconds"),
        "tls": tls,
        "catalogo_variables": cat_ok,
        # Lo que la fuente DIJO que habia, en la primera pagina, contra lo que
        # efectivamente entregó. Medir a la fuente, no a la economia.
        "catalogo_declarado_1a_pagina": cat_declarado,
        "series_pedidas": len(PRINCIPALES),
        "series_ok": series_ok,
        "huecos": huecos,
        "puntos_totales": total_puntos,
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
            "origen": "boveda-bcra",
            "catalogo": str(manifiesto["catalogo_variables"]),
            "series-ok": str(manifiesto["series_ok"]),
            "huecos": str(manifiesto["huecos"]),
            "puntos": str(manifiesto["puntos_totales"]),
            "tls-verificado": str(manifiesto["tls"].get("verificado")),
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
    tls = huella_tls()
    if tls.get("verificado"):
        print(f"[bcra] TLS verificado · cert {tls['cert_sha256'][:16]}… "
              f"· vence {tls.get('not_after')}")
    else:
        print(f"[bcra] TLS: no se pudo leer el certificado — {tls.get('error')}")

    lineas, cat_ok, series_ok, huecos, total_puntos, cat_declarado = capturar()

    if cat_ok == 0 and series_ok == 0:
        print("✗ Fallo el catalogo Y todas las series. No hay nada que archivar.")
        return 1

    datos, sha, manifiesto = empaquetar(lineas, cat_ok, series_ok,
                                        huecos, total_puntos, tls, cat_declarado)
    print(f"[bcra] catalogo {cat_ok} vars · series {series_ok}/{len(PRINCIPALES)} · "
          f"{huecos} huecos · {total_puntos} puntos · {len(datos)/1024:.0f} KB gz")

    try:
        clave, bucket = subir_a_r2(datos, sha, manifiesto)
    except Exception as e:
        print(f"[bcra] R2 fallo: {type(e).__name__}: {e}")
        print("  NO se archivo el snapshot de hoy. Corrida en rojo a proposito.")
        return 3

    print(f"[bcra] R2 OK: {clave}")
    print(f"[bcra] sha256: {sha}")
    if huecos:
        print(f"⚠ {huecos} hueco(s) registrado(s). Revisar arriba cuales.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
