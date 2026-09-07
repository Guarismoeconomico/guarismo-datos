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
        La Secretaria de Finanzas NO publica calendario. La deuda del II
        trimestre 2026 tiene que aparecer a fin de septiembre. Mirando la
        pagina todos los dias, el archivo registra EL DIA EXACTO en que
        aparecio. Con cadencia trimestral, esa fecha se pierde para siempre.
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


def _absoluta(href, base):
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
            etiqueta = b
            mes = MESES.get((etiqueta or "").strip().lower())
            salida.append({
                "familia": familia or "(sin familia)",
                "producto": "archivo",
                "etiqueta": etiqueta,
                "anio": anio,
                "orden": mes,
                "periodo": f"{anio}-{mes:02d}" if mes else None,
                "periodo_incierto": mes is None,
                "url": url,
                "ext": ext,
            })
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
            salida.append({
                "familia": "Deuda publica trimestral",
                "producto": (b or "archivo").strip(),
                "etiqueta": trim_txt,
                "anio": anio,
                "orden": tr,
                "periodo": f"{anio}-Q{tr}" if tr else None,
                "periodo_incierto": tr is None,
                "url": url,
                "ext": ext,
            })
    return salida


ETIQUETADORES = {"hacienda": etiquetar_hacienda, "finanzas": etiquetar_finanzas}


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
            solo = a.split("=", 1)[1]

    ahora = datetime.now(timezone.utc)
    run = re.sub(r"[^a-zA-Z0-9]+", "", os.getenv("GITHUB_RUN_ID", "local"))[:20]
    sello = f"{ahora:%Y%m%dT%H%M%SZ}_{run or 'local'}"
    bucket = os.getenv("R2_BUCKET") or BUCKET_DEFAULT

    fuentes = {k: v for k, v in FUENTES.items() if solo is None or k == solo}
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
        previo = (estado.get(cl_pag) or {}).get("sha256")
        mtime = modified_time(pagina)

        ent_pag = {
            "fuente": fuente, "tipo": "pagina", "url": cfg["url"],
            "descripcion": cfg["desc"], "organismo": cfg["organismo"],
            "capturado_utc": capturado, "bytes": len(pagina_b),
            "sha256": sha_pag, "http_date": headers.get("Date"),
            "last_modified": headers.get("Last-Modified"),
            "etag": headers.get("ETag"),
            "content_type": headers.get("Content-Type"),
            "article_modified_time": mtime,
            "cambio": sha_pag != previo,
        }
        if sha_pag != previo:
            ent_pag["objeto"] = guardar(s3, bucket, sello, ahora, cl_pag,
                                        pagina_b, "html", cfg["url"],
                                        capturado, sha_pag, seco)
            if not seco:
                estado[cl_pag] = {"sha256": sha_pag, "objeto": ent_pag["objeto"],
                                  "visto_utc": capturado}
            nuevos += 1
            print(f"   [mecD] {cl_pag:<30} {'NUEVO  →' if previo else 'PRIMERA→'} "
                  f"{len(pagina_b):>8} bytes  mod={mtime}")
        else:
            ent_pag["objeto"] = (estado.get(cl_pag) or {}).get("objeto")
            print(f"   [mecD] {cl_pag:<30} sin cambios {len(pagina_b):>8} bytes  mod={mtime}")
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
        print(f"⚠ {inciertos} etiqueta(s) sin resolver: la fuente cambio el texto "
              f"de un link. Revisar antes de que se pierda una edicion.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
