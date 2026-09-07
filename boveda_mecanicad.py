#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
boveda_mecanicad.py — Mecanica D: la pagina fija con listado.

POR QUE EXISTE
    Hay fuentes que no tienen URL resoluble. El resultado fiscal del SPN es el
    caso testigo: siete meses de 2026, cuatro convenciones de nombre y TRES
    formatos distintos.

        Enero    2026/02/sector_publico_base_caja_enero_2026.xlsx
        Febrero  2026/03/sector_publico_base_caja_febrero_2026.xlsx
        Marzo    marzo_26.xlsx                    <- sin carpeta de fecha
        Abril    2026/05/cuentas_publicas3.rar    <- RAR
        Mayo     mayo.zip
        Junio    junio.zip
        Julio    julio.zip

    No hay patron que resolver. Hay que leer la pagina.

    Pero NO es "scraping de listado de noticias con slug cambiante", que es lo
    que decia la lista de captura v2. Es mucho mas barato: UNA pagina fija por
    fuente, renderizada en el servidor, con el historico completo adentro.
    Verificado el 7-sep-2026 sobre las dos paginas reales.

LO QUE HACE DISTINTO A ESTE MODULO
    (1) ARCHIVA LA PAGINA, no solo los archivos.
        El CMS del Estado es Drupal. Cuando le suben un archivo con un nombre
        que ya existe, le pega un sufijo: _0, _1, _2. En la deuda trimestral,
        7 de 29 ediciones lo tienen. Eso significa que hay dos archivos en el
        servidor para el mismo trimestre, y SOLO LA PAGINA dice cual es el
        oficial.

        Sin la pagina archivada, dentro de dos años tenemos un binario sin
        cadena de custodia. Con ella, esta probado que el dia D la fuente
        declaraba que el archivo oficial del I-2024 era el que termina en _0.
        Cuesta 50 KB y se sube solo cuando cambia.

    (2) EL PERIODO SALE DE LA ETIQUETA, NUNCA DEL NOMBRE DEL ARCHIVO.
        "mayo.zip" y "junio.zip" no tienen año. "marzo_26.xlsx" no tiene
        carpeta. El unico lugar donde dice de que mes es el dato es el TEXTO
        DEL LINK, mas el año del encabezado que lo contiene.

        Un scraper que se queda con la URL y tira la etiqueta archiva mal.

    (3) SI LA ETIQUETA NO RESUELVE, SE CAPTURA IGUAL Y SE MARCA.
        En 2023 Hacienda escribio "Abil" en vez de "Abril". Un parser que
        adivina archiva ese mes con la etiqueta equivocada — el mismo modo de
        falla que _es_html() evito en septiembre: no un hueco, sino un dato
        falso con sello. Aca no se adivina: periodo=None, periodo_incierto=True
        y la etiqueta cruda queda en el manifiesto para que la vea un humano.

    (4) LA CADENCIA ES DIARIA, Y ESO ES A PROPOSITO.
        CORRECCION DEL 7-sep-2026: aca decia "la Secretaria de Finanzas NO
        publica calendario". Es FALSO, y se corrige donde estaba escrito.
        Finanzas SI publica calendario del reporte MENSUAL de deuda, con hora
        (16:00), colgado de la pagina madre datos-mensuales-de-la-deuda. Lo
        que NO tiene calendario publicado es el reporte TRIMESTRAL: la deuda
        del II trimestre 2026 tiene que aparecer a fin de septiembre y nadie
        prometio que dia. Mirando la pagina todos los dias, el archivo
        registra EL DIA EXACTO en que aparecio. Con cadencia trimestral, esa
        fecha se pierde para siempre.

        Y ahora hay las dos cosas, que es mejor todavia: para el mensual,
        prometido (el calendario, archivado) contra observado (el dia que el
        archivo cambia). Para el trimestral, solo observado.
        Es la serie de puntualidad del Tier 4, gratis.

        El costo son dos GET por dia. El hash decide si se guarda algo.

    (5) EL BACKFILL NO VA EN LA CORRIDA NORMAL.
        Las dos paginas juntas tienen ~200 archivos de casi una decada, con
        ZIP del SIGADE de tamaño desconocido. Eso es Clase B: se puede
        reconstruir cuando se quiera. Va en modo --backfill, que se corre a
        mano una vez, mirando el panel de R2.

MOTOR COMUN, ETIQUETADOR POR FUENTE
    Las dos paginas son server-rendered y se leen igual, pero la forma del
    periodo NO es la misma:

                     Hacienda                    Finanzas
        periodo      texto del link ("Enero")    primera celda ("I Trimestre")
        link dice    el periodo                  el PRODUCTO (Excel/Base/Informe)
        año          <h4>                        <h5>

    Por eso el motor baja, extrae los pares (texto, href) en orden y hashea;
    el etiquetador — quince lineas por fuente — decide que periodo es cada uno.
    Agregar la deuda MENSUAL despues es un etiquetador mas, no un modulo nuevo.

LO QUE NO HACE
    No descomprime los .zip ni los .rar del fiscal. Se archiva el objeto que
    publico el organismo, tal cual. Descomprimir seria producir un artefacto
    que la fuente nunca publico, y eso saca a Guarismo de la notaria. El dia
    que un informe necesite leer adentro, se descomprime desde el sellado.

    No entra a la subpagina /datos-anteriores de Finanzas (2017-2018). Anotado.
"""

import hashlib
import html as _html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

TIMEOUT = 120
REINTENTOS = 3
PAUSA = 1.0

BUCKET_DEFAULT = "guarismo-crudo"
PREFIJO = "boveda/mecanicad"
ESTADO = "_estado/mecanicad_hashes.json"

UA = {"User-Agent": "Guarismo/1.0 (+https://guarismo.com.ar; infoguarismo@gmail.com)"}

# Cuantas ediciones se capturan por familia/producto en la corrida normal.
# DOS, no una: el fiscal se revisa, y una correccion al mes anterior aparece
# como URL nueva (o con sufijo _1) en la misma pagina. Con una sola edicion
# esa revision no se ve.
VENTANA = 2

# Extensiones que se consideran "archivo publicado". El .rar esta porque
# Hacienda publico abril de 2026 en ese formato.
EXTENSIONES = ("xlsx", "xls", "zip", "rar", "pdf", "csv")

TIPOS = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "csv": "text/csv; charset=utf-8",
    "pdf": "application/pdf",
    "zip": "application/zip",
    "rar": "application/vnd.rar",
    "html": "text/html; charset=utf-8",
}

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

TRIMESTRES = {"i": 1, "ii": 2, "iii": 3, "iv": 4}

MES_NOMBRE = {v: k for k, v in MESES.items() if k != "setiembre"}

# "Julio 2026" en una celda. Se arma desde MESES para que agregar un nombre no
# requiera acordarse de tocar dos lugares. Ordenada por largo descendente para
# que el alternador no corte un nombre por la mitad.
MES_ANIO = re.compile(
    r"\b(" + "|".join(sorted(MESES, key=len, reverse=True)) +
    r")\s+((?:19|20)\d{2})\b", re.I)

# Erratas REALES de la fuente, verificadas a mano el 7-sep-2026. No es una
# heuristica ni una correccion ortografica automatica: es una lista corta,
# escrita por un humano, de textos que el organismo publico mal.
#
# Y aun asi NO alcanza sola. Un alias solo se aplica si el NOMBRE DEL ARCHIVO
# contiene el mes canonico: dos señales independientes, igual que _es_html().
# Si las dos no coinciden, la etiqueta queda incierta y no se archiva.
ALIAS_MES = {
    "abil": 4,   # sector_publico_base_caja_-_abril_23.xlsx (Hacienda, 2023)
}


# ---------------------------------------------------------------------------
# Fuentes
# ---------------------------------------------------------------------------

FUENTES = {
    "hacienda_fiscal": {
        "url": "https://www.argentina.gob.ar/economia/sechacienda/infoestadistica",
        "desc": "Secretaria de Hacienda — Sector Publico base caja e IMIG",
        "etiquetador": "hacienda",
        "organismo": "Secretaria de Hacienda",
    },
    "finanzas_deuda_trim": {
        "url": "https://www.argentina.gob.ar/economia/finanzas/datos-trimestrales-de-la-deuda",
        "desc": "Secretaria de Finanzas — datos trimestrales de la deuda publica",
        "etiquetador": "finanzas",
        "organismo": "Secretaria de Finanzas",
    },

    # --- Deuda MENSUAL. Reconocida el 7-sep-2026. Son TRES paginas. ---
    #
    # No es una pagina con dos tablas: son tres URLs distintas, y cada una se
    # fecha sola por separado. Las dos hijas se movieron el 18-ago-2026 con
    # trece minutos de diferencia; la madre, el 8-ene-2026 (cuando subieron el
    # calendario del año). Tres relojes independientes de la misma oficina.
    "finanzas_deuda_mens_informes": {
        # OJO: 55 ediciones, enero 2022 -> julio 2026, y adivinar el nombre es
        # imposible. Ocho llevan sufijo de Drupal (_0/_1/_2), dos tienen la
        # fecha de publicacion pegada al año (marzo-20231704, julio_20221608),
        # dos terminan en guion bajo colgando, y el separador mes/año cambia
        # dos veces dentro de 2022. Manda la pagina.
        "url": "https://www.argentina.gob.ar/economia/finanzas/datos-mensuales-de-la-deuda/informes-mensuales",
        "desc": "Secretaria de Finanzas — boletin mensual de deuda (informes)",
        "etiquetador": "deuda_mens_informes",
        "organismo": "Secretaria de Finanzas",
    },
    "finanzas_deuda_mens_datos": {
        # LA URL CANONICA. La que estaba anotada en la cola
        # (.../datos-mensuales-de-la-deuda/datos) REDIRIGE aca. Se cablea el
        # destino, no el salto.
        #
        # Y el hallazgo grueso: esta pagina tiene UNA SOLA FILA y la pisa cada
        # mes. Al reves que la trimestral, que lista las 29 ediciones. O sea:
        # el .xlsx mensual es Clase A o no es. Backfillearlo exigiria adivinar
        # URLs, y el archivo de julio 2026 ya viene con _0.
        "url": "https://www.argentina.gob.ar/economia/finanzas/datos-mensuales",
        "desc": "Secretaria de Finanzas — serie mensual de deuda (xlsx vigente)",
        "etiquetador": "deuda_mens_datos",
        "organismo": "Secretaria de Finanzas",
    },
    "finanzas_deuda_mens_calendario": {
        # La pagina madre. Existe por UNA sola cosa: cuelga de aca el
        # calendario_de_publicaciones_{anio}.pdf. Se entra por la pagina y no
        # por un {anio} adivinado, porque un patron estable hacia adelante no
        # es un patron para el backfill (leccion del calendario del INDEC, que
        # cambio de convencion tres veces).
        "url": "https://www.argentina.gob.ar/economia/finanzas/datos-mensuales-de-la-deuda",
        "desc": "Secretaria de Finanzas — calendario de publicaciones de deuda",
        "etiquetador": "deuda_mens_calendario",
        "organismo": "Secretaria de Finanzas",
    },
}


# ---------------------------------------------------------------------------
# Utilidades de texto
# ---------------------------------------------------------------------------

def _limpiar(fragmento):
    """Saca tags, resuelve entidades y normaliza espacios."""
    txt = re.sub(r"<[^>]+>", " ", fragmento or "")
    txt = _html.unescape(txt)
    txt = txt.replace("\xa0", " ")
    return re.sub(r"\s+", " ", txt).strip()


def _posible_resubida(url):
    """¿El nombre termina en el sufijo que agrega Drupal al resubir?

    OJO CON EL FALSO POSITIVO — lo encontro la prueba, no la lectura.
    Un `_\\d+` al final atrapa tambien el AÑO: presentacion_grafica_ivt_25.pdf,
    presentacion_grafica_it_2025.pdf. Marcar eso como resubida seria meter una
    afirmacion falsa en el manifiesto, que es peor que no marcar nada.

    Drupal cuenta desde 0 y de a uno, asi que el sufijo real es de UN digito.
    Con esa restriccion dan exactamente los 7 de 29 trimestres verificados a
    mano el 7-sep-2026.

    Sigue siendo una inferencia: el campo se llama "posible" y ningun informe
    debe afirmar la resubida sin cotejar contra la pagina archivada.
    """
    return bool(re.search(r"_\d\.[A-Za-z0-9]{1,5}$", url or ""))


def _ext_de(url):
    m = re.search(r"\.([A-Za-z0-9]{1,5})(?:$|[?#])", url or "")
    return m.group(1).lower() if m else ""


def modified_time(pagina):
    """El CMS fecha la pagina sola. Es la serie de puntualidad, gratis."""
    m = re.search(
        r'property=["\']article:modified_time["\'][^>]*content=["\']([^"\']+)',
        pagina, re.I)
    if not m:
        m = re.search(
            r'content=["\']([^"\']+)["\'][^>]*property=["\']article:modified_time',
            pagina, re.I)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Motor: la pagina en orden de lectura
# ---------------------------------------------------------------------------

EVENTO = re.compile(
    r"""(?P<h><h(?P<hn>[1-6])\b[^>]*>(?P<htxt>.*?)</h(?P=hn)>)
       |(?P<fila><tr\b[^>]*>)
       |(?P<celda><td\b[^>]*>(?P<ctxt>.*?)</td>)
       |(?P<link><a\b[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<ltxt>.*?)</a>)""",
    re.S | re.I | re.X,
)


def eventos(pagina):
    """Recorre la pagina en orden y emite encabezados, filas, celdas y links.

    Es un scanner, no un parser de arbol: no hace falta bajar BeautifulSoup
    para leer dos paginas de estructura conocida, y una dependencia menos es
    una cosa menos que se puede romper sola en el runner.
    """
    for m in EVENTO.finditer(pagina):
        if m.group("h"):
            yield ("h", int(m.group("hn")), _limpiar(m.group("htxt")))
        elif m.group("fila"):
            yield ("fila", None, None)
        elif m.group("celda"):
            # La celda se emite Y ADEMAS se destripa: en Finanzas el <a> vive
            # adentro del <td>, y el escaner consume la celda entera. Sin esto
            # la pagina de deuda devuelve CERO links. Lo encontro la prueba
            # contra el HTML real, no la lectura del codigo.
            yield ("celda", None, _limpiar(m.group("ctxt")))
            for a in re.finditer(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                                 m.group("ctxt"), re.S | re.I):
                yield ("link", _href(a.group(1)), _limpiar(a.group(2)))
        else:
            yield ("link", _href(m.group("href")), _limpiar(m.group("ltxt")))


def _href(crudo):
    """Normaliza un href. El "blank:#" de adelante lo pone el CMS solo."""
    return re.sub(r"^blank:#", "", _html.unescape(crudo or "").strip())


ESQUEMA_DOBLE = re.compile(r"^https?:?//https?:?//", re.I)


def _roto(href):
    """¿El href trae el esquema repetido? Es un defecto de la fuente, no nuestro.

    EL CASO REAL, y la correccion de lo que estaba anotado.
        El informe del IV trimestre 2019 de la Secretaria de Finanzas apunta a
        una URL rota desde 2019. Estaba anotado de memoria como
        "https://https://" — es FALSO. El href real es:

            https://https//www.argentina.gob.ar/sites/default/files/...

        Le falta el SEGUNDO dos puntos. Se ve en el HTML guardado y se ve en el
        error del runner, que dijo host='https' y path='//www.argentina...':
        requests leyo "https" como nombre de host, que es exactamente lo que
        pasa con un solo "//" y sin ":".

        Una regex escrita contra la version recordada no lo agarraba. Por eso
        la palabra de verificacion se cuenta de verdad y la URL se mira.

    Se normaliza para poder capturarlo, PERO queda constancia en el manifiesto
    de que la fuente lo publico roto. Arreglarlo en silencio seria borrar un
    hecho de la fuente, y esos hechos son el producto.
    """
    return bool(ESQUEMA_DOBLE.match(href or ""))


def _absoluta(href, base):
    # https://https//... y https://https://... -> https://...
    href = href or ""
    while ESQUEMA_DOBLE.match(href):
        href = re.sub(r"^https?:?//", "", href, count=1)
    href = re.sub(r"^(https?):?//", r"\1://", href, count=1)
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://www.argentina.gob.ar" + href
    return None


# ---------------------------------------------------------------------------
# Etiquetadores
# ---------------------------------------------------------------------------

def etiquetar_hacienda(pagina, base):
    """<h2> familia · <h4> año · el TEXTO DEL LINK es el mes."""
    familia, anio, salida = None, None, []
    for tipo, a, b in eventos(pagina):
        if tipo == "h":
            if a == 2:
                familia = b
            elif a == 4 and re.fullmatch(r"(19|20)\d{2}", b or ""):
                anio = int(b)
        elif tipo == "link":
            ext = _ext_de(a)
            if ext not in EXTENSIONES:
                continue
            url = _absoluta(a, base)
            if not url or anio is None:
                continue
            etiqueta = (b or "").strip()
            norm = etiqueta.lower()
            it = {
                "familia": familia or "(sin familia)",
                "producto": "archivo",
                "etiqueta": etiqueta,
                "anio": anio,
                "url": url,
                "ext": ext,
            }
            if _roto(a):
                it["href_roto_en_la_fuente"] = True

            mes = MESES.get(norm)

            # Errata conocida + confirmacion en el nombre del archivo.
            if mes is None and norm in ALIAS_MES:
                cand = ALIAS_MES[norm]
                if MES_NOMBRE[cand] in url.lower():
                    mes, it["etiqueta_corregida"] = cand, MES_NOMBRE[cand]

            # "Descargar anual 2018": no es un mes, es el agregado del año.
            # Se acepta solo si el año del texto coincide con el del <h4>.
            m_an = re.match(r"descargar\s+anual\s+((?:19|20)\d{2})", norm)
            if mes is None and m_an and int(m_an.group(1)) == anio:
                it.update(producto="anual", orden=13, periodo=f"{anio}-anual",
                          periodo_incierto=False)
                salida.append(it)
                continue

            it.update(orden=mes,
                      periodo=f"{anio}-{mes:02d}" if mes else None,
                      periodo_incierto=mes is None)
            salida.append(it)
    return salida


def etiquetar_finanzas(pagina, base):
    """<h5> año · primera celda de la fila = trimestre · el link es el PRODUCTO."""
    anio, trim_txt, primera, salida = None, None, True, []
    for tipo, a, b in eventos(pagina):
        if tipo == "h":
            if a == 5 and re.fullmatch(r"(19|20)\d{2}", b or ""):
                anio = int(b)
        elif tipo == "fila":
            trim_txt, primera = None, True
        elif tipo == "celda":
            if primera:
                trim_txt, primera = b, False
        elif tipo == "link":
            ext = _ext_de(a)
            if ext not in EXTENSIONES:
                continue
            url = _absoluta(a, base)
            if not url or anio is None:
                continue
            m = re.match(r"\s*(IV|III|II|I)\b", (trim_txt or ""), re.I)
            tr = TRIMESTRES.get(m.group(1).lower()) if m else None
            it = {
                "familia": "Deuda publica trimestral",
                "producto": (b or "archivo").strip(),
                "etiqueta": trim_txt,
                "anio": anio,
                "orden": tr,
                "periodo": f"{anio}-Q{tr}" if tr else None,
                "periodo_incierto": tr is None,
                "url": url,
                "ext": ext,
            }
            if _roto(a):
                it["href_roto_en_la_fuente"] = True
            salida.append(it)
    return salida


def _deuda_mensual(pagina, base, producto):
    """Motor de las DOS tablas mensuales de deuda. La primera celda es el periodo.

    Las dos paginas tienen la misma forma de tabla y una diferencia:

        Informes   "Julio 2026"                        -> 55 filas
        Datos      "Serie mensual 2019 - Julio 2026"   -> 1 fila, un RANGO

    La misma lectura sirve para las dos: se toma el ULTIMO par mes+año de la
    celda. En Informes hay uno solo. En Datos, el ultimo es el fin del rango,
    que es exactamente el dato que trae el archivo. El "2019" del arranque no
    matchea porque no lleva mes adelante.

    EL PRODUCTO NO SALE DE LA PAGINA, y esto es a proposito. En las dos
    tablas el texto del link dice "Descargar" y nada mas. Al reves que en la
    deuda trimestral, donde el link SI dice el producto (Base de Datos /
    Excel / Informe). Por eso lo fija la fuente. Inventar un producto a partir
    de "Descargar" seria escribir en el manifiesto algo que la fuente no dijo.
    """
    periodo_txt, primera, salida = None, True, []
    for tipo, a, b in eventos(pagina):
        if tipo == "fila":
            periodo_txt, primera = None, True
        elif tipo == "celda":
            if primera:
                periodo_txt, primera = b, False
        elif tipo == "link":
            ext = _ext_de(a)
            if ext not in EXTENSIONES:
                continue
            url = _absoluta(a, base)
            if not url:
                continue
            ms = MES_ANIO.findall(periodo_txt or "")
            mes = MESES.get(ms[-1][0].lower()) if ms else None
            anio = int(ms[-1][1]) if ms else None
            it = {
                "familia": "Deuda publica mensual",
                "producto": producto,
                "etiqueta": periodo_txt,
                "anio": anio,
                "orden": mes,
                "periodo": f"{anio}-{mes:02d}" if (anio and mes) else None,
                "periodo_incierto": not (anio and mes),
                "url": url,
                "ext": ext,
            }
            if _roto(a):
                it["href_roto_en_la_fuente"] = True
            salida.append(it)
    return salida


def etiquetar_deuda_mens_informes(pagina, base):
    """55 boletines en PDF, enero 2022 -> julio 2026."""
    return _deuda_mensual(pagina, base, "informe")


def etiquetar_deuda_mens_datos(pagina, base):
    """Una fila: el xlsx de la serie mensual. Se pisa todos los meses."""
    return _deuda_mensual(pagina, base, "datos")


def etiquetar_deuda_mens_calendario(pagina, base):
    """La pagina madre: un PDF suelto, sin tabla y SIN periodo que leer.

    El calendario de publicaciones no describe un mes: es el documento
    VIGENTE. Se reemplaza cuando cambia el año Y TAMBIEN cuando lo corrigen a
    mitad de camino — el INDEC hace exactamente eso con el suyo ("Actualizado
    al 20/05/2025"). Por eso el periodo es "vigente" y la clave es estable:
    cada version nueva entra como un objeto mas, con su sello y su hora, y la
    serie de objetos ES la historia de las correcciones.

    LA REGLA 2 SIGUE EN PIE. El periodo no se saca del nombre del archivo:
    aca sencillamente NO HAY periodo que leer, igual que en apendice6.xlsx.
    El año que trae el nombre se guarda aparte y rotulado como leido de la
    URL, porque es un hecho de la fuente y no una etiqueta que declaro.

    Cualquier OTRO archivo que aparezca en esta pagina cae como etiqueta sin
    resolver: no se archiva, pero suena la alarma y lo mira un humano. Es el
    comportamiento buscado, no un agujero.
    """
    salida = []
    for tipo, a, b in eventos(pagina):
        if tipo != "link":
            continue
        ext = _ext_de(a)
        if ext not in EXTENSIONES:
            continue
        url = _absoluta(a, base)
        if not url:
            continue
        nombre = url.rsplit("/", 1)[-1].lower()
        etiqueta = (b or "").strip()
        # Dos señales, como siempre: el nombre del archivo O el texto del link.
        es_cal = "calendario" in nombre or "calendario" in etiqueta.lower()
        it = {
            "familia": "Calendario de publicaciones",
            "producto": "calendario" if es_cal else (_slug(etiqueta, 12) or "archivo"),
            "etiqueta": etiqueta,
            "anio": None,
            "orden": None,
            "periodo": None,
            "periodo_incierto": True,
            "url": url,
            "ext": ext,
        }
        if es_cal:
            m = re.search(r"((?:19|20)\d{2})", nombre)
            it.update(periodo="vigente", periodo_incierto=False, orden=1,
                      anio=int(m.group(1)) if m else 0)
            if m:
                it["anio_en_el_nombre"] = int(m.group(1))
        if _roto(a):
            it["href_roto_en_la_fuente"] = True
        salida.append(it)
    return salida


ETIQUETADORES = {
    "hacienda": etiquetar_hacienda,
    "finanzas": etiquetar_finanzas,
    "deuda_mens_informes": etiquetar_deuda_mens_informes,
    "deuda_mens_datos": etiquetar_deuda_mens_datos,
    "deuda_mens_calendario": etiquetar_deuda_mens_calendario,
}


def seleccionar(items, ventana=VENTANA):
    """Las `ventana` ediciones mas nuevas de cada familia+producto.

    Lo incierto NUNCA entra en la corrida normal: no se puede ordenar algo
    cuyo periodo no se pudo leer, y meterlo "por las dudas" es exactamente
    como se archiva un mes con la etiqueta de otro. Queda listado en el
    manifiesto para que lo vea un humano, y lo levanta el backfill.
    """
    grupos = {}
    for it in items:
        if it["periodo_incierto"] or it["orden"] is None:
            continue
        grupos.setdefault((it["familia"], it["producto"]), []).append(it)
    elegidos = []
    for _, lista in sorted(grupos.items()):
        lista.sort(key=lambda x: (x["anio"], x["orden"]), reverse=True)
        elegidos.extend(lista[:ventana])
    return elegidos


def _slug(txt, n):
    return re.sub(r"[^a-z0-9]+", "", (txt or "").lower())[:n]


def clave_de(fuente, it):
    """Clave estable del objeto. El periodo va adentro: es lo que identifica.

    La FAMILIA tambien va: Hacienda publica dos familias en la misma pagina
    (base caja e IMIG) y las dos usan producto="archivo". Sin la familia, dos
    familias con el mismo periodo escriben sobre el mismo objeto.
    """
    fam = _slug(it.get("familia"), 14) or "sinfamilia"
    prod = _slug(it.get("producto"), 12) or "archivo"
    return f"{fuente}_{fam}_{it['periodo']}_{prod}"


# ---------------------------------------------------------------------------
# Red
# ---------------------------------------------------------------------------

class NoEsta(Exception):
    """El archivo no esta en esa URL. No tiene sentido reintentar."""


def _es_html(datos, headers):
    if "html" in (headers.get("Content-Type") or "").lower():
        return True
    cabeza = datos[:2048].lstrip().lower()
    return (cabeza.startswith(b"<!doctype")
            or cabeza.startswith(b"<html")
            or b"<html" in cabeza[:1024])


def bajar(url, ext="bin"):
    """Descarga binaria con reintentos. Devuelve (bytes, headers, url_final).

    Misma guarda que el modulo del INDEC: una pagina HTML donde esperabamos un
    archivo se trata IGUAL que un 404. Un 200 no prueba nada.
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
                    f"bytes en vez de un .{ext}. El archivo no esta publicado.")
            return r.content, dict(r.headers), r.url
        except NoEsta:
            raise
        except Exception as e:
            ultimo = e
            if intento < REINTENTOS - 1:
                time.sleep(2.0 * (intento + 1))
    raise ultimo


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


def leer_estado(s3, bucket):
    try:
        obj = s3.get_object(Bucket=bucket, Key=ESTADO)
        return json.loads(obj["Body"].read().decode("utf-8"))
    except Exception as e:
        print(f"[mecD] sin estado previo ({type(e).__name__}) — se trata todo como nuevo")
        return {}


# ---------------------------------------------------------------------------
# Corrida
# ---------------------------------------------------------------------------

def guardar(s3, bucket, sello, ahora, clave, datos, ext, url, capturado, sha,
            seco=False):
    nombre = f"{clave}_{sello}.{ext}"
    obj = f"{PREFIJO}/{ahora:%Y/%m}/{nombre}"
    if seco:
        return obj + "  (SECO: no escrito)"
    s3.put_object(
        Bucket=bucket, Key=obj, Body=datos,
        ContentType=TIPOS.get(ext, "application/octet-stream"),
        Metadata={"sha256": sha, "origen": "boveda-mecanicad",
                  "url": url[:900], "capturado-utc": capturado},
    )
    s3.put_object(
        Bucket=bucket, Key=obj + ".sha256",
        Body=f"{sha}  {nombre}\n".encode("utf-8"),
        ContentType="text/plain; charset=utf-8",
    )
    return obj


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    backfill = "--backfill" in argv
    # --seco: baja y hashea todo, pero NO escribe un solo byte en R2.
    # La primera corrida de un modulo nuevo va siempre en seco: el log dice
    # exactamente que se habria archivado, y recien despues se suelta.
    seco = "--seco" in argv
    solo = None
    for a in argv:
        if a.startswith("--fuente="):
            # Acepta lista: --fuente=a,b,c. Con un solo nombre se comporta
            # igual que antes. Hace falta porque un backfill pelado rebaja
            # las 204 ediciones ya selladas de Hacienda y Finanzas trimestral
            # para nada.
            solo = {s.strip() for s in a.split("=", 1)[1].split(",") if s.strip()}

    ahora = datetime.now(timezone.utc)
    run = re.sub(r"[^a-zA-Z0-9]+", "", os.getenv("GITHUB_RUN_ID", "local"))[:20]
    sello = f"{ahora:%Y%m%dT%H%M%SZ}_{run or 'local'}"
    bucket = os.getenv("R2_BUCKET") or BUCKET_DEFAULT

    if solo is not None:
        # Un typo en --fuente= dejaba cero fuentes, y el modulo terminaba
        # diciendo "Fallaron TODAS las descargas". Es un diagnostico FALSO: no
        # fallo nada, no se pidio nada. Se aborta y se dice cual fue el error.
        desconocidas = sorted(solo - set(FUENTES))
        if desconocidas:
            print(f"[mecD] fuente(s) inexistente(s): {', '.join(desconocidas)}")
            print(f"[mecD] disponibles: {', '.join(sorted(FUENTES))}")
            return 2

    fuentes = {k: v for k, v in FUENTES.items() if solo is None or k in solo}
    modo = "BACKFILL (historico completo)" if backfill else f"normal (ventana {VENTANA})"
    print(f"[mecD] {len(fuentes)} fuente(s) · modo {modo}")
    if backfill:
        print("[mecD] ⚠ backfill: se bajan TODAS las ediciones listadas.")
    if seco:
        print("[mecD] ⚠ MODO SECO: no se escribe NADA en R2. Solo se informa.")

    try:
        s3 = _cliente()
    except Exception as e:
        print(f"[mecD] R2 fallo (cliente): {type(e).__name__}: {e}")
        return 3

    estado = leer_estado(s3, bucket)
    entradas, ok, nuevos, huecos, inciertos = [], 0, 0, 0, 0

    for fuente, cfg in sorted(fuentes.items()):
        capturado = datetime.now(timezone.utc).isoformat(timespec="seconds")

        # --- 1. la pagina, que es la prueba de que URL era la oficial hoy ---
        try:
            pagina_b, headers, url_final = bajar(cfg["url"], "html")
        except Exception as e:
            huecos += 1
            entradas.append({"fuente": fuente, "url": cfg["url"],
                             "capturado_utc": capturado,
                             "error": f"{type(e).__name__}: {e}"})
            print(f"   [mecD] {fuente:<22} HUECO (pagina) — {type(e).__name__}: {e}")
            continue

        pagina = pagina_b.decode("utf-8", errors="replace")
        sha_pag = hashlib.sha256(pagina_b).hexdigest()
        cl_pag = f"{fuente}_pagina"
        prev = estado.get(cl_pag) or {}
        previo = prev.get("sha256")
        mtime = modified_time(pagina)

        # LA PAGINA NO SE GATILLA POR HASH — descubierto el 7-sep-2026.
        #
        # Dos capturas separadas por NUEVE MINUTOS dieron el mismo tamaño
        # exacto (50.794 y 45.258 bytes) y sha256 distinto, con el
        # article:modified_time sin moverse. Las paginas de argentina.gob.ar
        # reserializan en cada render.
        #
        # Gatillar por hash tendria dos consecuencias, y la segunda es grave:
        #   1. se archivaria la pagina todos los dias, para siempre;
        #   2. la serie de PUNTUALIDAD quedaria envenenada. Si "la pagina
        #      cambio" pasa todos los dias, el dia que Finanzas publique la
        #      deuda del II-2026 ese cambio no se distingue del ruido. Se
        #      pierde justo lo que el modulo venia a medir.
        #
        # Es la regla que ya estaba escrita para el icc_op_art15: el hash solo
        # no declara revision. Manda lo que el CMS DECLARA. El hash se sigue
        # registrando siempre, y la reserializacion queda anotada como lo que
        # es: una conducta medida de la fuente.
        prev_mt = prev.get("article_modified_time")
        if previo is None:
            cambio_pag, motivo = True, "primera captura"
        elif mtime:
            cambio_pag = (mtime != prev_mt)
            motivo = "article_modified_time"
        else:
            # Sin fecha declarada no queda otra que el hash, y se dice.
            cambio_pag = (sha_pag != previo)
            motivo = "hash (la pagina no declara modified_time)"
        reserializa = (sha_pag != previo) and not cambio_pag

        ent_pag = {
            "fuente": fuente, "tipo": "pagina", "url": cfg["url"],
            "descripcion": cfg["desc"], "organismo": cfg["organismo"],
            "capturado_utc": capturado, "bytes": len(pagina_b),
            "sha256": sha_pag, "http_date": headers.get("Date"),
            "last_modified": headers.get("Last-Modified"),
            "etag": headers.get("ETag"),
            "content_type": headers.get("Content-Type"),
            "article_modified_time": mtime,
            "cambio": cambio_pag,
            "motivo_cambio": motivo,
            # El binario es distinto pero la fuente no declaro publicacion:
            # es reserializacion, no revision. Se mide, no se archiva.
            "reserializa": reserializa,
        }
        if cambio_pag:
            ent_pag["objeto"] = guardar(s3, bucket, sello, ahora, cl_pag,
                                        pagina_b, "html", cfg["url"],
                                        capturado, sha_pag, seco)
            if not seco:
                estado[cl_pag] = {"sha256": sha_pag, "objeto": ent_pag["objeto"],
                                  "article_modified_time": mtime,
                                  "visto_utc": capturado}
            nuevos += 1
            print(f"   [mecD] {cl_pag:<30} {'NUEVO  →' if previo else 'PRIMERA→'} "
                  f"{len(pagina_b):>8} bytes  mod={mtime}  ({motivo})")
        else:
            ent_pag["objeto"] = prev.get("objeto")
            extra = "  [reserializa: mismo mod, otro hash]" if reserializa else ""
            print(f"   [mecD] {cl_pag:<30} sin cambios {len(pagina_b):>8} bytes  "
                  f"mod={mtime}{extra}")
        ok += 1
        entradas.append(ent_pag)

        # --- 2. los archivos ---
        items = ETIQUETADORES[cfg["etiquetador"]](pagina, cfg["url"])
        raros = [i for i in items if i["periodo_incierto"]]
        inciertos += len(raros)
        for r in raros:
            entradas.append({
                "fuente": fuente, "tipo": "etiqueta_no_resuelta",
                "etiqueta_cruda": r["etiqueta"], "anio": r["anio"],
                "url": r["url"], "capturado_utc": capturado,
                "nota": "no se pudo leer el periodo desde la etiqueta; NO se archivo",
            })
            print(f"   [mecD] {fuente:<22} etiqueta sin resolver: "
                  f"{r['etiqueta']!r} ({r['anio']}) — {r['url'].rsplit('/', 1)[-1]}")

        elegidos = ([i for i in items if not i["periodo_incierto"]]
                    if backfill else seleccionar(items))
        print(f"   [mecD] {fuente:<22} {len(items)} links · "
              f"{len(raros)} sin resolver · {len(elegidos)} a capturar")

        for it in elegidos:
            capturado = datetime.now(timezone.utc).isoformat(timespec="seconds")
            clave = clave_de(fuente, it)
            try:
                datos, h2, ufin = bajar(it["url"], it["ext"])
                sha = hashlib.sha256(datos).hexdigest()
                previo = (estado.get(clave) or {}).get("sha256")
                cambio = sha != previo

                entrada = {
                    "fuente": fuente, "tipo": "archivo", "archivo": clave,
                    "familia": it["familia"], "producto": it["producto"],
                    "periodo": it["periodo"], "etiqueta_fuente": it["etiqueta"],
                    "url": it["url"], "capturado_utc": capturado,
                    "bytes": len(datos), "sha256": sha,
                    "http_date": h2.get("Date"),
                    "last_modified": h2.get("Last-Modified"),
                    "etag": h2.get("ETag"),
                    "content_type": h2.get("Content-Type"),
                    # Posible huella de resubida del CMS: Drupal agrega _0,
                    # _1, _2... cuando le suben un archivo con un nombre que
                    # ya existia. Es INFERIDO, no declarado, y por eso el
                    # campo se llama "posible".
                    "posible_resubida": _posible_resubida(it["url"]),
                    "cambio": cambio,
                }

                # DEFECTO CORREGIDO EL 7-sep-2026.
                #
                # Los etiquetadores marcaban href_roto_en_la_fuente (el link
                # del IV trimestre 2019, roto desde 2019) y etiqueta_corregida
                # (el "Abil" de Hacienda) sobre el item... y main() armaba el
                # manifiesto campo por campo, sin copiarlos. Los dos hechos se
                # descubrian, se marcaban y se TIRABAN antes de escribir el
                # JSON. El manifiesto no los tenia nunca.
                #
                # Son hechos de la fuente, que es literalmente el producto.
                for extra in ("href_roto_en_la_fuente", "etiqueta_corregida",
                              "anio_en_el_nombre"):
                    if extra in it:
                        entrada[extra] = it[extra]
                if ufin and ufin != it["url"]:
                    entrada["url_final"] = ufin

                if cambio:
                    entrada["objeto"] = guardar(s3, bucket, sello, ahora, clave,
                                                datos, it["ext"], it["url"],
                                                capturado, sha, seco)
                    if not seco:
                        estado[clave] = {"sha256": sha, "objeto": entrada["objeto"],
                                         "visto_utc": capturado}
                    nuevos += 1
                    marca = "NUEVO  →" if previo else "PRIMERA→"
                    print(f"   [mecD] {clave:<30} {marca} {len(datos):>9} bytes  {sha[:12]}…")
                else:
                    entrada["objeto"] = (estado.get(clave) or {}).get("objeto")
                    print(f"   [mecD] {clave:<30} sin cambios {len(datos):>9} bytes  {sha[:12]}…")
                ok += 1
            except Exception as e:
                entrada = {"fuente": fuente, "tipo": "archivo", "archivo": clave,
                           "periodo": it["periodo"], "url": it["url"],
                           "capturado_utc": capturado,
                           "error": f"{type(e).__name__}: {e}"}
                huecos += 1
                print(f"   [mecD] {clave:<30} HUECO — {type(e).__name__}: {e}")

            entradas.append(entrada)
            time.sleep(PAUSA)

    if ok == 0:
        print("✗ Fallaron TODAS las descargas. No hay nada que archivar.")
        return 1

    manifiesto = {
        "guarismo": "manifiesto de captura · mecanica D (pagina fija con listado)",
        "capturado_utc": ahora.isoformat(timespec="seconds"),
        "modo": "backfill" if backfill else "normal",
        "ventana": None if backfill else VENTANA,
        "fuentes": sorted(fuentes),
        "ok": ok, "nuevos": nuevos, "huecos": huecos,
        "etiquetas_sin_resolver": inciertos,
        "run_id": os.getenv("GITHUB_RUN_ID", "local"),
        "commit": os.getenv("GITHUB_SHA", "local"),
        "entradas": entradas,
    }

    if seco:
        print(f"[mecD] SECO · {ok} habrian quedado OK · {nuevos} se habrian "
              f"archivado · {huecos} huecos · {inciertos} sin resolver")
        print("[mecD] no se escribio nada. Si el log de arriba dice lo esperado, "
              "correr sin --seco.")
        return 0

    try:
        clave_man = f"{PREFIJO}/manifiestos/{ahora:%Y/%m}/manifiesto_{sello}.json"
        s3.put_object(Bucket=bucket, Key=clave_man,
                      Body=json.dumps(manifiesto, ensure_ascii=False,
                                      sort_keys=True, indent=1,
                                      default=str).encode("utf-8"),
                      ContentType="application/json; charset=utf-8")
        s3.put_object(Bucket=bucket, Key=ESTADO,
                      Body=json.dumps(estado, ensure_ascii=False, sort_keys=True,
                                      indent=1, default=str).encode("utf-8"),
                      ContentType="application/json; charset=utf-8")
    except Exception as e:
        print(f"[mecD] R2 fallo (manifiesto/estado): {type(e).__name__}: {e}")
        return 3

    print(f"[mecD] {ok} OK · {nuevos} nuevos · {huecos} huecos · "
          f"{inciertos} etiquetas sin resolver")
    print(f"[mecD] manifiesto: {clave_man}")
    if huecos:
        print(f"⚠ {huecos} hueco(s) registrado(s). Revisar arriba cuales.")
    if inciertos:
        print(f"⚠ {inciertos} etiqueta(s) sin resolver. Las erratas conocidas al "
              f"7-sep-2026 ('Abil', 'Descargar anual') YA se resuelven solas: si "
              f"esto suena, la fuente escribio algo nuevo. Mirar los renglones "
              f"'etiqueta sin resolver' de arriba antes de que se pierda una edicion.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
