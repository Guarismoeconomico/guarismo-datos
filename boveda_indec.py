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

CUATRO PATRONES DE URL — verificado el 3 y el 4-sep-2026
    (a) Fija de verdad: sh_emae_mensual_base2004.xls. El caso ideal.
    (b) Con el año adentro: sh_isac_2026.xls. Fija doce meses y despues rota.
        Se escribe {anio} y lo resuelve urls_candidatas() — automatico, para
        que nadie tenga que editar el diccionario cada enero.
    (c) Con el MES DE PUBLICACION trimestral: sh_oferta_demanda_06_26.xls.
        Se escribe {trim} y lo resuelve _trimestres_publicacion().
    (d) Con el DIA DE PUBLICACION adentro: ica_cuadros_20_08_26.xls, donde el
        20 es el dia en que salio el informe y cambia mes a mes.

    El patron (d) parecia imposible de cablear y por eso el ICA estaba anotado
    como mecanica D (scraping). No hace falta: se prueban los dias de la
    ventana de publicacion y el que no existe devuelve la pagina de error del
    INDEC, que _es_html() ya trata igual que un 404. Es el mismo truco que (c),
    con otra unidad de tiempo. Lo que SIGUE siendo mecanica D es lo que no
    tiene nombre predecible: el cuadro vivo del art. 15 del ICC, detras de
    bajarCuadroEstadistico.asp?idc=<hash>.

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

# Ventana de publicacion para el patron (d), el que lleva el DIA adentro.
# Verificado sobre 17 ediciones del ICA leyendole el pie a los propios
# informes: el dia observado va del 17 al 22, y ONCE de las diecisiete
# cayeron un 20. Se prueba primero el 20 y despues se baja de a uno, con
# margen de cinco dias para abajo y dos para arriba.
DIA_TIPICO = 20
DIA_DESDE = 12
DIA_HASTA = 24

# URLs verificadas sobre informes de prensa oficiales. El hash aleatorio del
# INDEC afecta a los PDF de prensa (uploads/informesdeprensa/), NO a estos
# cuadros. Pero hay CUATRO patrones distintos y conviene no confundirlos:
#
#   (a) URL fija de verdad, sin fecha en el nombre. Se sobrescribe en cada
#       publicacion conservando el nombre. Es el caso ideal.
#   (b) URL con el AÑO adentro (sh_isac_2026.xls). Fija durante doce meses y
#       despues rota. Se escribe {anio} y lo resuelve urls_candidatas().
#   (c) URL con el MES de publicacion trimestral (sh_oferta_demanda_06_26.xls).
#       Se escribe {trim}.
#   (d) URL con el DIA de publicacion (ica_cuadros_20_08_26.xls). Se escribe
#       {dia} y lo resuelve _dias_publicacion() probando la ventana.
#
# Lo que NO entra aca sigue siendo lo que no tiene nombre predecible: el cuadro
# vivo del art. 15 del ICC, detras de bajarCuadroEstadistico.asp?idc=<hash>.
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
    "comex_metodologia": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/economia/metodologia_comex.pdf",
        "ext": "pdf",
        "desc": "Metodologia del intercambio comercial argentino. URL fija citada por "
                "el propio INDEC como el enlace metodologico del comercio exterior. "
                "Patron (a).",
    },

    # --- Tier 1: ICA, el patron (d) --------------------------------------
    # El ICA es mensual y sale entre el 17 y el 22. Su URL lleva la FECHA DE
    # PUBLICACION adentro; la resuelve _dias_publicacion() probando la ventana
    # del mes corriente y, si todavia no salio, la del mes anterior. Esa caida
    # es la misma que hace _trimestres_publicacion() con el PIB, y por el mismo
    # motivo: sin ella quedaria un hueco inventado dos semanas por mes.
    #
    # POR QUE SE BAJA TODOS LOS DIAS Y NO UNA VEZ POR MES: el informe de mayo
    # 2026 se difundio el 18/06/2026 y el propio PDF declara "Fecha de
    # actualizacion: 24/6/2026" — seis dias despues. Si el INDEC retoca el
    # cuadro conservando el nombre, la captura diaria lo ve como NUEVO. Ese par
    # antes/despues es exactamente el producto.
    #
    # LA FUENTE DECLARA SU PROVISORIEDAD CON NUMERO, cada mes: en la edicion de
    # mayo 2026, el 12,7% de la documentacion aduanera oficializada del mes
    # seguia pendiente al cierre del informe, y los exportadores pueden
    # corregir valores entre 60 y 180 dias despues del embarque. Ese porcentaje
    # cambia edicion a edicion: es serie propia sin interpretar nada.
    "ica_cuadros": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/economia/ica_cuadros_{dia}.xls",
        "ext": "xls",
        "desc": "ICA. Cuadros del informe tecnico y complementarios del intercambio "
                "comercial argentino de bienes (40 cuadros). Patron (d): el dia de "
                "publicacion va adentro del nombre.",
    },
    "ica_anexo_cuadros": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/economia/ica_anexo_cuadros_{dia}.xls",
        "ext": "xls",
        "desc": "ICA. Anexo con el detalle de las series que componen el informe. "
                "Verificado en las ediciones de feb, abr, jun y jul de 2026, y en "
                "2024 y 2025. OJO: el informe de mayo 2026 apunta dos veces al "
                "ica_cuadros y no nombra el anexo — si ese mes no existe, queda un "
                "hueco fechado, que es el registro correcto.",
    },

    # --- Tier 2: redeterminacion de obra publica (el comprador #2) --------
    # Regimen vigente: Dec. 490/2023, que sustituyo al 691/2016, que a su vez
    # reemplazo la metodologia del 1295/2002. El INDEC sigue rotulando sus
    # cuadros "Dec. 1295/02" porque la estructura de indices es la de 2002.
    # En las tres versiones los precios de referencia son los del INDEC.
    #
    # El ICC nace provisorio TODOS los meses: la nota al pie de cada informe
    # declara que capataz y seguro ART entran despues del cierre mensual, lo
    # que incide en la provisoriedad del capitulo Mano de obra y, por arrastre,
    # del nivel general. Ese par provisorio/definitivo es el producto.
    #
    # OJO: el cuadro VIVO del art. 15 vive detras de un link dinamico
    # (bajarCuadroEstadistico.asp?idc=<hash>) y NO se puede cablear aca.
    # Va por mecanica D. Los 9 archivos SH-ICC-* de serie historica terminan
    # en octubre de 2015 — son backfill Clase B, bloque aparte.
    "sipm_series": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/economia/series_sipm_dic2015.xls",
        "ext": "xls",
        "desc": "SIPM. Series completas de IPIM, IPIB e IPP desde diciembre 2015. El "
                "IPIB es el insumo del articulo 15 del regimen de redeterminacion de "
                "obra publica. URL fija citada al pie de cada informe del SIPM.",
    },
    "sipm_metodologia": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/economia/metodologia1_8_sipm.pdf",
        "ext": "pdf",
        "desc": "Metodologia del SIPM. Documenta la formula Laspeyres de ponderaciones "
                "fijas base 1993 y el origen de los ponderadores (Censo Nacional "
                "Economico 1994).",
    },
    "icc_metodologia": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/economia/metodologia_icc.pdf",
        "ext": "pdf",
        "desc": "Metodologia del ICC en el Gran Buenos Aires, base 1993=100. Es la "
                "unica URL fija que el informe del ICC publica: los cuadros del "
                "regimen de redeterminacion no tienen URL directa.",
    },

    # --- Tier 2: salarios -------------------------------------------------
    # ATENCION: estos NO viven en /ftp/cuadros/economia/ sino en
    # /ftp/cuadros/sociedad/. Es el unico bloque fuera de economia.
    "salarios_indice": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/sociedad/indice_salarios.csv",
        "ext": "csv",
        "desc": "Indice de salarios. Serie historica desde octubre 2015, formato csv. "
                "El componente privado NO registrado se estima y se revisa: es la "
                "parte con vintage real de toda la familia laboral.",
    },
    "salarios_variacion": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/sociedad/variacion_indice_salarios.csv",
        "ext": "csv",
        "desc": "Indice de salarios. Variaciones porcentuales por sector, serie "
                "historica en csv.",
    },
    "salarios_metadatos": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/sociedad/metadatos_series_salarios.txt",
        "ext": "txt",
        "desc": "Indice de salarios. Metadatos de las series. Gemelo de "
                "ipc_serie_metadatos: un cambio aca es un cambio de definicion, "
                "no de dato.",
    },
    "salarios_cvs_diarios": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/sociedad/sh_cvs_diarios_{anio}.xls",
        "ext": "xls",
        "desc": "Coeficiente de variacion salarial, serie DIARIA, base 31 de octubre "
                "2016=100. Patron (b): el anio va adentro del nombre, lo resuelve "
                "urls_candidatas() con caida al anio anterior.",
    },
    "salarios_metodologia": {
        "url": "https://www.indec.gob.ar/ftp/cuadros/sociedad/cvs_metodologia.pdf",
        "ext": "pdf",
        "desc": "Metodologia del coeficiente de variacion salarial. Declara el rezago "
                "de cinco meses del indice mensual construido a partir de la EPH.",
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
    """El archivo no esta en esa URL. No tiene sentido reintentar."""


def _es_html(datos, headers):
    """¿La respuesta es una pagina web en vez del archivo que pedimos?

    EL CASO REAL — verificado en el navegador el 3-sep-2026
        Se pidio sh_oferta_demanda_09_26.xls, un cuadro que el INDEC todavia no
        publico. El servidor NO devolvio 404: REDIRIGIO a
        indec.gob.ar/indec/web/Error-Default y sirvio esa pagina con estado 2xx.
        La pagina dice "ERROR 404" en el texto, pero el codigo HTTP no.

        Resultado: el modulo bajo 37 KB de HTML, los hasheo y los archivo como
        si fueran una planilla. Los dos cuadros del PIB quedaron con IDENTICO
        sha256, que es imposible entre el original y el desestacionalizado.
        Es el peor modo de falla de un archivo notarial: no un hueco, sino un
        dato falso con fecha y sello.

        Y no afectaba solo al PIB. Sin esta guarda, TODA la resolucion de fecha
        ({anio} y {trim}) era decorativa: el candidato inexistente nunca
        fallaba, asi que nunca se caia al periodo anterior.

    Se detecta por dos vias independientes — el Content-Type declarado y la
    cabeza del contenido — porque cualquiera de las dos puede mentir sola.
    """
    if "html" in (headers.get("Content-Type") or "").lower():
        return True
    cabeza = datos[:2048].lstrip().lower()
    return (cabeza.startswith(b"<!doctype")
            or cabeza.startswith(b"<html")
            or b"<html" in cabeza[:1024])


def bajar(url, ext="bin"):
    """Descarga binaria con reintentos. Devuelve (bytes, headers, url_final).

    Un 404 corta de inmediato: reintentarlo es tiempo perdido, y ademas es la
    señal que necesita urls_candidatas() para pasar al periodo anterior. Una
    pagina HTML donde esperabamos un archivo se trata IGUAL que un 404, porque
    es lo que el INDEC devuelve en vez de un 404.
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
            if ext != "html" and _es_html(r.content, r.headers):
                raise NoEsta(
                    f"el servidor devolvio una pagina HTML de {len(r.content)} "
                    f"bytes en vez de un .{ext} (destino final: {r.url}). "
                    f"El archivo no esta publicado.")
            return r.content, dict(r.headers), r.url
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


def _dias_publicacion(hoy=None):
    """Los dias candidatos de publicacion, como (dd, mm, aa), en orden.

    El ICA sale una vez por mes, entre el 17 y el 22, y su nombre de archivo
    lleva ese dia adentro. No hay forma de saber cual es sin preguntar: se
    prueban todos los de la ventana y el que no existe devuelve la pagina de
    error del INDEC, que bajar() ya trata igual que un 404.

    ORDEN, y no es capricho: primero el 20, porque once de las diecisiete
    ediciones verificadas cayeron ese dia. Con eso, veintiseis dias de cada
    treintaiuno se resuelven en UN solo pedido. Despues se baja de a uno desde
    el tope. Medido sobre un mes completo: 2,2 pedidos por dia en promedio,
    peor dia 9 (la vispera de la publicacion, cuando el mes corriente todavia
    no tiene nada y hay que caer al anterior).

    TOPE DEL MES CORRIENTE = el dia de hoy. Un archivo fechado el 22 no puede
    existir el 20: pedirlo seria gastar un pedido en algo imposible.

    EL MES ANTERIOR SIEMPRE VA, y es la pieza que evita el hueco inventado.
    Del 1 al 16 el archivo del mes todavia no existe y el vigente es el del mes
    pasado: sin ese segundo bloque el modulo registraria un hueco todos los
    dias durante media vida. Es la misma decision, por el mismo motivo, que
    _trimestres_publicacion() toma para el PIB.
    """
    hoy = hoy or datetime.now(timezone.utc)

    def bloque(anio, mes, tope):
        dias = []
        if DIA_DESDE <= DIA_TIPICO <= tope:
            dias.append(DIA_TIPICO)
        dias += [d for d in range(tope, DIA_DESDE - 1, -1) if d != DIA_TIPICO]
        return [(d, mes, anio % 100) for d in dias]

    salida = []
    if hoy.day >= DIA_DESDE:
        salida += bloque(hoy.year, hoy.month, min(DIA_HASTA, hoy.day))
    pa, pm = (hoy.year - 1, 12) if hoy.month == 1 else (hoy.year, hoy.month - 1)
    salida += bloque(pa, pm, DIA_HASTA)
    return salida


def urls_candidatas(url):
    """Resuelve {anio}, {trim} o {dia} en la URL. Los candidatos, en orden.

    POR QUE
        Varios cuadros del INDEC llevan una fecha en el nombre:
          {anio} → sh_isac_2026.xls            (fija doce meses, rota en enero)
          {trim} → sh_oferta_demanda_06_26.xls (mes de publicacion trimestral)
          {dia}  → ica_cuadros_20_08_26.xls    (dia de publicacion mensual)
        Dejar esas fechas escritas a mano significaria que el modulo empieza a
        devolver 404 hasta que alguien se acuerde de editarlo. Eso es
        mantenimiento manual, y en este proyecto lo que no es automatico no va.

        En los tres casos se prueba el periodo corriente y, si no esta, el
        anterior. Eso cubre la ventana real entre que el periodo arranca y que
        el INDEC efectivamente publica.

    Lo unico que NO se resuelve asi es lo que no tiene nombre predecible: el
    cuadro vivo del art. 15 del ICC vive detras de un link dinamico con hash.
    """
    if "{trim}" in url:
        return [url.replace("{trim}", f"{mm:02d}_{aa:02d}")
                for mm, aa in _trimestres_publicacion()]
    if "{dia}" in url:
        return [url.replace("{dia}", f"{dd:02d}_{mm:02d}_{aa:02d}")
                for dd, mm, aa in _dias_publicacion()]
    if "{anio}" not in url:
        return [url]
    anio = datetime.now(timezone.utc).year
    return [url.replace("{anio}", str(anio)),
            url.replace("{anio}", str(anio - 1))]


def bajar_resolviendo(cfg):
    """Baja probando los candidatos. Devuelve (bytes, headers, pedida, final).

    'pedida' es la URL que resolvimos del diccionario; 'final' es donde termino
    el pedido despues de redirects. Se registran las dos porque si el INDEC
    redirige, eso es un hecho de la fuente y va al manifiesto.
    """
    ext = cfg.get("ext", "bin")
    ultimo = None
    for u in urls_candidatas(cfg["url"]):
        try:
            datos, headers, final = bajar(u, ext)
            return datos, headers, u, final
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
            datos, headers, url_usada, url_final = bajar_resolviendo(cfg)
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
            if url_final and url_final != url_usada:
                # El INDEC redirigio. Es un hecho de la fuente: se registra.
                entrada["url_final"] = url_final

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
