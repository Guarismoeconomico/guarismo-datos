#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
boveda_indec.py — Captura los cuadros publicados del INDEC a la boveda.

POR QUE EXISTE
    El INDEC publica sus series como archivos Excel y los SOBRESCRIBE en cada
    publicacion, conservando el mismo nombre. La URL no cambia; el contenido
    si. Nadie guarda las versiones viejas.

    El EMAE es el caso extremo: la serie desestacionalizada se re-estima ENTERA
    con cada dato nuevo. La propia metodologia del INDEC lo declara (Metodologia
    INDEC N 20, seccion II.a): las mensualizaciones sufren modificaciones a
    medida que se agregan observaciones trimestrales.

    Lo que no se captura el dia que estaba, no se recupera nunca.

TRES PATRONES DE URL, NO UNO — verificado el 3-sep-2026
    (a) Fija de verdad: sh_emae_mensual_base2004.xls. El caso ideal.
    (b) Con el año adentro: sh_isac_2026.xls. Fija doce meses y despues rota.
        Se escribe {anio} y lo resuelve urls_candidatas() — automatico, para
        que nadie tenga que editar el diccionario cada enero.
    (c) Con la FECHA DE PUBLICACION adentro: ica_cuadros_20_07_26.xls, donde el
        20 es el dia en que salio el informe y cambia mes a mes. Esa URL no se
        puede construir. El ICA, el IPC mensual y los cuadros de supermercados
        caen aca y NO entran a este modulo: son mecanica D, scraping.

QUE HACE
    Baja cada archivo, lo hashea, y lo sube a R2 SOLO SI CAMBIO respecto de la
    ultima vez. Pero escribe un manifiesto TODOS LOS DIAS, haya cambio o no.

    Esa asimetria es a proposito. El manifiesto diario es lo que despues prueba
    "el 5 de septiembre el archivo todavia decia lo mismo" sin guardar treinta
    copias identicas de un Excel que se publica una vez por mes.

FORMATO
    boveda/indec/AAAA/MM/emae_mensual_20260901T221500Z.xls       (+ .sha256)
    boveda/indec/manifiestos/AAAA/MM/manifiesto_20260901T221500Z.json
    _estado/indec_hashes.json                                     (ultimo hash visto)

    Si _estado se pierde, la unica consecuencia es que se re-sube una copia.
    Nunca se pierde nada.

ATESTACION DE TIEMPO
    Se guardan los headers Date y Last-Modified del servidor del INDEC. El Date
    es una atestacion de tiempo de un TERCERO independiente: si el reloj del
    runner se corriera, el desfasaje queda documentado. Y el Last-Modified dice
    cuando el INDEC toco el archivo por ultima vez — dato notarial propio.

HUECOS, NO MENTIRAS
    Si un archivo falla, queda registrado en el manifiesto con su error y su
    fecha. Si fallan TODOS, el proceso termina en rojo y no sube nada.

VARIABLES DE ENTORNO
    R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY   (obligatorias)
    R2_BUCKET                                             (opcional, default guarismo-crudo)

CODIGOS DE SALIDA
    0  todo bien (aunque algun archivo tenga hueco)
    1  fallaron TODAS las descargas
    3  hay datos pero R2 fallo  <- corrida en ROJO a proposito
"""

import hashlib
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

TIMEOUT = 120
REINTENTOS = 3
PAUSA = 1.0

BUCKET_DEFAULT = "guarismo-crudo"
PREFIJO = "boveda/indec"
ESTADO = "_estado/indec_hashes.json"

UA = {"User-Agent": "Guarismo/1.0 (+https://guarismo.com.ar; infoguarismo@gmail.com)"}

# URLs verificadas sobre informes de prensa oficiales. El hash aleatorio del
# INDEC afecta a los PDF de prensa (uploads/informesdeprensa/), NO a estos
# cuadros. Pero hay DOS patrones distintos y conviene no confundirlos:
#
#   (a) URL fija de verdad, sin fecha en el nombre. Se sobrescribe en cada
#       publicacion conservando el nombre. Es el caso ideal.
#   (b) URL con el AÑO adentro (sh_isac_2026.xls). Fija durante doce meses y
#       despues rota. Se escribe {anio} y lo resuelve urls_candidatas().
#
# Hay un tercer patron que NO entra aca: los cuadros del ICA llevan el DIA DE
# PUBLICACION en el nombre (ica_cuadros_20_07_26.xls, y el 20 cambia mes a mes
# porque el ICA sale entre el 18 y el 22). Esa URL no se puede construir: hay
# que ir a buscarla. Es mecanica D — scraping de listado — y no este modulo.
#
# NOTA SOBRE EL IPC: se captura y se hashea. ARCHIVAR NO ES PUBLICAR. Nada de
# esto sale a ninguna pantalla como afirmacion propia de Guarismo. Misma regla
# intocable que los ids 27/28/29 de boveda_bcra.py.
ARCHIVOS = {
    "emae_mensual": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/economia/sh_emae_mensual_base2004.xls",
        "ext": "xls",
        "desc": "EMAE. Numeros indice base 2004=100 y variaciones. Serie original, "
                "desestacionalizada y tendencia-ciclo.",
    },
    "emae_actividad": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/economia/sh_emae_actividad_base2004.xls",
        "ext": "xls",
        "desc": "EMAE por sector de actividad. Base 2004=100 y variaciones.",
    },
    "emae_metodologia": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/economia/metodologia_emae_ago_16.pdf",
        "ext": "pdf",
        "desc": "Metodologia INDEC N 20 — EMAE base 2004, agosto 2016. ISSN 2545-7179.",
    },

    # --- Tier 1: desestacionalizadas que se re-estiman con cada dato ------
    # Patron (b): el año va adentro del nombre.
    "ipi_manufacturero": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/economia/sh_ipi_manufacturero_{anio}.xls",
        "ext": "xls",
        "desc": "IPI manufacturero. Serie original, desestacionalizada y tendencia-ciclo, "
                "base 2004=100, nivel general y divisiones, desde enero 2016.",
    },
    "isac": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/economia/sh_isac_{anio}.xls",
        "ext": "xls",
        "desc": "ISAC. Indicador sintetico de la actividad de la construccion. Serie "
                "original, desestacionalizada y tendencia-ciclo, desde enero 2012.",
    },
    "ipi_metodologia": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/economia/metodologia_ipi_manufacturero_2019.pdf",
        "ext": "pdf",
        "desc": "Metodologia del IPI manufacturero, 2019. Documenta X-11 de X-13ARIMA-SEATS "
                "y el metodo H13 para tendencia-ciclo.",
    },

    # --- Tier 1: PIB trimestral. La serie del cupon PBI -------------------
    # Patron (c) predecible: el mes de PUBLICACION (03/06/09/12) va adentro.
    "pib_oferta_demanda": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/economia/sh_oferta_demanda_{trim}.xls",
        "ext": "xls",
        "desc": "PIB. Series trimestrales de oferta y demanda globales, serie original, "
                "desde 2004. Informe de avance del nivel de actividad.",
    },
    "pib_oferta_demanda_desest": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/economia/sh_oferta_demanda_desest_{trim}.xls",
        "ext": "xls",
        "desc": "PIB. Series trimestrales DESESTACIONALIZADAS de oferta y demanda "
                "globales. Es la que se re-estima entera con cada publicacion: la mas "
                "irrecuperable de las dos.",
    },

    # --- Tier 4: cuadros completos del IPC, todos con URL fija de verdad --
    "ipc_aperturas": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/economia/sh_ipc_aperturas.xls",
        "ext": "xls",
        "desc": "IPC. Series de las principales aperturas regionales desde diciembre 2016.",
    },
    "ipc_precios_promedio": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/economia/sh_ipc_precios_promedio.xls",
        "ext": "xls",
        "desc": "IPC. Serie de precios promedio.",
    },
    "ipc_serie_aperturas": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/economia/serie_ipc_aperturas.csv",
        "ext": "csv",
        "desc": "IPC. Serie historica de aperturas, formato csv.",
    },
    "ipc_serie_divisiones": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/economia/serie_ipc_divisiones.csv",
        "ext": "csv",
        "desc": "IPC. Serie historica por division, formato csv.",
    },
    "ipc_serie_metadatos": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/economia/serie_ipc_metadatos.txt",
        "ext": "txt",
        "desc": "IPC. Metadatos de las series. Un cambio aca es un cambio de definicion: "
                "vale tanto como el dato.",
    },

    # --- Sector externo ---------------------------------------------------
    "bdp_servicios_pais": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/economia/Base_servicios_internacionales_trim_pais.csv",
        "ext": "csv",
        "desc": "Balanza de pagos. Servicios trimestrales por rubro y pais. "
                "URL verificada sobre informe de 2025 — confirmar en el primer manifiesto.",
    },
    "comex_metodologia_precios": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/economia/metodologia_preciosycantidades.pdf",
        "ext": "pdf",
        "desc": "Metodologia de indices de precios y cantidades del comercio exterior. "
                "URL citada en informes del ICA — confirmar en el primer manifiesto.",
    },
}

# Tipo de contenido por extension. Se declara al subir a R2 para que el objeto
# se pueda abrir despues sin adivinar.
TIPOS = {
    "pdf": "application/pdf",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
}


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


class NoEsta(Exception):
    """404 — el archivo no esta en esa URL. No tiene sentido reintentar."""


def bajar(url):
    """Descarga binaria con reintentos. Devuelve (bytes, headers).

    Un 404 corta de inmediato: reintentarlo es tiempo perdido, y ademas es la
    señal que necesita urls_candidatas() para pasar al año anterior.
    """
    ultimo = None
    for intento in range(REINTENTOS):
        try:
            r = requests.get(url, headers=UA, timeout=TIMEOUT)
            if r.status_code == 404:
                raise NoEsta(f"404 — no existe {url}")
            r.raise_for_status()
            if not r.content:
                raise ValueError("respuesta vacia")
            return r.content, dict(r.headers)
        except NoEsta:
            raise
        except Exception as e:
            ultimo = e
            if intento < REINTENTOS - 1:
                time.sleep(2.0 * (intento + 1))
    raise ultimo


def _trimestres_publicacion(hoy=None):
    """Los dos ultimos meses de publicacion trimestral, como (mm, aa).

    El INDEC publica el Informe de avance del nivel de actividad en marzo,
    junio, septiembre y diciembre, cerca del dia 20. El nombre del cuadro lleva
    el MES DE PUBLICACION, no el trimestre del dato: el 1er trimestre de 2026
    salio como sh_oferta_demanda_06_26.xls. Verificado sobre cinco ediciones
    (12_25, 03_26, 06_26, 06_25, 06_24) el 3-sep-2026.

    Se devuelven DOS candidatos a proposito: durante las tres primeras semanas
    del mes de publicacion el archivo nuevo todavia no existe, y el vigente es
    el del trimestre anterior. Sin el segundo candidato quedaria un hueco
    inventado tres semanas por trimestre.
    """
    hoy = hoy or datetime.now(timezone.utc)
    anio, mes = hoy.year, (hoy.month // 3) * 3
    if mes == 0:                      # enero y febrero miran a diciembre
        anio, mes = anio - 1, 12
    salida = []
    for _ in range(2):
        salida.append((mes, anio % 100))
        mes -= 3
        if mes == 0:
            anio, mes = anio - 1, 12
    return salida


def urls_candidatas(url):
    """Resuelve {anio} o {trim} en la URL. Devuelve los candidatos, en orden.

    POR QUE
        Varios cuadros del INDEC llevan una fecha en el nombre:
          {anio} → sh_isac_2026.xls           (fija doce meses, rota en enero)
          {trim} → sh_oferta_demanda_06_26.xls (mes de publicacion trimestral)
        Dejar esas fechas escritas a mano significaria que el modulo empieza a
        devolver 404 hasta que alguien se acuerde de editarlo. Eso es
        mantenimiento manual, y en este proyecto lo que no es automatico no va.

        En los dos casos se prueba el periodo corriente y, si no esta, el
        anterior. Eso cubre la ventana real entre que el periodo arranca y que
        el INDEC efectivamente publica.

    Lo que NO se puede resolver asi son las URLs con el DIA de publicacion
    adentro (ica_cuadros_20_07_26.xls): esas hay que ir a buscarlas.
    """
    if "{trim}" in url:
        return [url.replace("{trim}", f"{mm:02d}_{aa:02d}")
                for mm, aa in _trimestres_publicacion()]
    if "{anio}" not in url:
        return [url]
    anio = datetime.now(timezone.utc).year
    return [url.replace("{anio}", str(anio)),
            url.replace("{anio}", str(anio - 1))]


def bajar_resolviendo(url_cfg):
    """Baja probando los candidatos. Devuelve (bytes, headers, url_usada)."""
    ultimo = None
    for u in urls_candidatas(url_cfg):
        try:
            datos, headers = bajar(u)
            return datos, headers, u
        except Exception as e:
            ultimo = e
    raise ultimo


def leer_estado(s3, bucket):
    """Ultimo hash visto de cada archivo. Si no existe, arranca vacio."""
    try:
        obj = s3.get_object(Bucket=bucket, Key=ESTADO)
        return json.loads(obj["Body"].read().decode("utf-8"))
    except Exception as e:
        print(f"[indec] sin estado previo ({type(e).__name__}) — se trata todo como nuevo")
        return {}


def main():
    ahora = datetime.now(timezone.utc)
    run = re.sub(r"[^a-zA-Z0-9]+", "", os.getenv("GITHUB_RUN_ID", "local"))[:20]
    sello = f"{ahora:%Y%m%dT%H%M%SZ}_{run or 'local'}"
    bucket = os.getenv("R2_BUCKET") or BUCKET_DEFAULT

    print(f"[indec] {len(ARCHIVOS)} archivos · captura de cuadros publicados")

    try:
        s3 = _cliente()
    except Exception as e:
        print(f"[indec] R2 fallo (cliente): {type(e).__name__}: {e}")
        return 3

    estado = leer_estado(s3, bucket)
    entradas, ok, nuevos, huecos = [], 0, 0, 0

    for clave, cfg in sorted(ARCHIVOS.items()):
        capturado = datetime.now(timezone.utc).isoformat(timespec="seconds")
        url_usada = cfg["url"]
        try:
            datos, headers, url_usada = bajar_resolviendo(cfg["url"])
            sha = hashlib.sha256(datos).hexdigest()
            previo = (estado.get(clave) or {}).get("sha256")
            cambio = (sha != previo)

            entrada = {
                "archivo": clave,
                # La URL efectivamente descargada, que es lo que importa
                # notarialmente. Si la plantilla tenia año, queda la resuelta.
                "url": url_usada,
                "descripcion": cfg["desc"],
                "capturado_utc": capturado,
                "bytes": len(datos),
                "sha256": sha,
                # Atestacion de tiempo de un tercero independiente.
                "http_date": headers.get("Date"),
                "last_modified": headers.get("Last-Modified"),
                "etag": headers.get("ETag"),
                # Se REGISTRA, no decide nada. Si un dia el INDEC devuelve una
                # pagina de error con 200, el content-type lo delata y queda
                # la evidencia fechada en el manifiesto.
                "content_type": headers.get("Content-Type"),
                "cambio": cambio,
            }
            if url_usada != cfg["url"]:
                entrada["url_plantilla"] = cfg["url"]

            if cambio:
                nombre = f"{clave}_{sello}.{cfg['ext']}"
                obj = f"{PREFIJO}/{ahora:%Y/%m}/{nombre}"
                s3.put_object(
                    Bucket=bucket, Key=obj, Body=datos,
                    ContentType=TIPOS.get(cfg["ext"], "application/octet-stream"),
                    Metadata={
                        "sha256": sha,
                        "origen": "boveda-indec",
                        "url": url_usada[:900],
                        "capturado-utc": capturado,
                    },
                )
                s3.put_object(
                    Bucket=bucket, Key=obj + ".sha256",
                    Body=f"{sha}  {nombre}\n".encode("utf-8"),
                    ContentType="text/plain; charset=utf-8",
                )
                entrada["objeto"] = obj
                estado[clave] = {"sha256": sha, "objeto": obj, "visto_utc": capturado}
                nuevos += 1
                marca = "NUEVO  →" if previo else "PRIMERA→"
                print(f"   [indec] {clave:<26} {marca} {len(datos):>8} bytes  {sha[:12]}…")
            else:
                # Sin cambios: no se re-sube el binario, pero el manifiesto de
                # hoy deja constancia de que seguia diciendo lo mismo.
                entrada["objeto"] = (estado.get(clave) or {}).get("objeto")
                print(f"   [indec] {clave:<26} sin cambios {len(datos):>8} bytes  {sha[:12]}…")

            ok += 1
        except Exception as e:
            entrada = {
                "archivo": clave,
                "url": url_usada,
                "capturado_utc": capturado,
                "error": f"{type(e).__name__}: {e}",
            }
            huecos += 1
            print(f"   [indec] {clave:<26} HUECO — {type(e).__name__}: {e}")

        entradas.append(entrada)
        time.sleep(PAUSA)

    if ok == 0:
        print("✗ Fallaron TODAS las descargas. No hay nada que archivar.")
        return 1

    manifiesto = {
        "guarismo": "manifiesto de captura · cuadros publicados del INDEC",
        "capturado_utc": ahora.isoformat(timespec="seconds"),
        "fuente": "INDEC · indec.gob.ar",
        "archivos_pedidos": len(ARCHIVOS),
        "ok": ok,
        "nuevos": nuevos,
        "huecos": huecos,
        "run_id": os.getenv("GITHUB_RUN_ID", "local"),
        "commit": os.getenv("GITHUB_SHA", "local"),
        "entradas": entradas,
    }

    try:
        clave_man = f"{PREFIJO}/manifiestos/{ahora:%Y/%m}/manifiesto_{sello}.json"
        s3.put_object(
            Bucket=bucket, Key=clave_man,
            Body=json.dumps(manifiesto, ensure_ascii=False, sort_keys=True,
                            indent=1, default=str).encode("utf-8"),
            ContentType="application/json; charset=utf-8",
        )
        s3.put_object(
            Bucket=bucket, Key=ESTADO,
            Body=json.dumps(estado, ensure_ascii=False, sort_keys=True,
                            indent=1, default=str).encode("utf-8"),
            ContentType="application/json; charset=utf-8",
        )
    except Exception as e:
        print(f"[indec] R2 fallo (manifiesto/estado): {type(e).__name__}: {e}")
        return 3

    print(f"[indec] {ok}/{len(ARCHIVOS)} OK · {nuevos} nuevos · {huecos} huecos")
    print(f"[indec] manifiesto: {clave_man}")
    if huecos:
        print(f"⚠ {huecos} archivo(s) con hueco registrado. Revisar arriba cuales.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
