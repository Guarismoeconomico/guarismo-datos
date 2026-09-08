#!/usr/bin/env python3
"""
GUARISMO — Boveda, MECANICA F: fuentes privadas, almacen separado.

QUE ES
    Captura las Estimaciones Nacionales de Produccion de GEA (Bolsa de
    Comercio de Rosario). Fuente PRIVADA: se captura, NO se republica.

POR QUE UN MODULO APARTE Y NO UNA FUENTE MAS DE LA MECANICA D
    Por el ALMACEN, no por la mecanica. Este modulo escribe en el bucket
    `guarismo-reservado` con credenciales propias (R2_RESERVADO_*) que NO
    alcanzan `guarismo-crudo`, y las de la boveda publica no alcanzan este.
    La separacion es de credencial, no de convencion: un bug aca no puede
    ensuciar el archivo publicable ni al reves.

    Decision firme del proyecto: separacion por ALMACEN, no por flag.

EL GATILLO, Y POR QUE NO ES EL DE LA MECANICA D
    En argentina.gob.ar manda `article:modified_time`. En BCR ese campo esta
    MUERTO en las paginas de indice (medido el 8-sep-2026):

        /estimaciones               -> 30-abr-2019   (muestra el informe de hoy)
        /estimaciones-anteriores    -> 08-may-2019   (listado que crece)
        nodo del informe 197        -> 12-ago-2026   <- VIVO

    O sea: los INDICES estan congelados y los NODOS se fechan bien. Un
    gatillo por modified_time del listado no sonaria NUNCA.

    Salida: se mira el listado TODOS LOS DIAS y se compara la lista de slugs
    contra el estado. Es 1 request por dia. Mismo criterio que Comtrade: si
    la fuente no da una senal de cambio confiable, se mira diario y el cambio
    lo fechamos nosotros. Eso es Clase A.

    NO se gatilla por hash de la pagina. Se gatilla por APARICION DE CLAVE,
    que es lo que la mecanica D hizo siempre.

DOS CLASES DE NOVEDAD, Y NO SON LO MISMO
    NUEVO    slug que no estaba en el estado          -> la fuente PUBLICO
    EDITADO  modified_time movido en un slug conocido -> la fuente EDITO algo
                                                         que ya habia publicado

    La segunda es revision silenciosa, fechada por la propia fuente. Es el
    producto de Guarismo y viene gratis en el <head> de cada nodo.

LO QUE SE ARCHIVA CUANDO HAY NOVEDAD
    1. la pagina del listado   (prueba que informe estaba listado ese dia)
    2. el nodo del informe     (prueba que decia la pagina y su modified_time)
    3. el PDF                  (el documento)
    La pagina se archiva junto al archivo: la pagina prueba cual era el
    documento oficial en la fecha D.

LO QUE NO HACE
    No republica nada. El bucket es de captura. Publicar cualquier cosa de
    aca requiere permiso escrito de la fuente (mail pendiente).

Uso:
    python boveda_privadas.py [--seco] [--backfill] [--paginas=N]

    --seco      no escribe un byte en R2. Se estrena SIEMPRE en seco.
    --backfill  recorre el listado completo (Clase B). No va en la diaria.
    --paginas=N tope de paginas del backfill (default: todas las que haya).
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone, timedelta

import requests

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

TIMEOUT = 120
REINTENTOS = 3
PAUSA = 1.0

BUCKET_DEFAULT = "guarismo-reservado"
PREFIJO = "boveda/privadas"
ESTADO = "_estado/privadas_estado.json"

UA = {"User-Agent": "Guarismo/1.0 (+https://guarismo.com.ar; infoguarismo@gmail.com)"}

ARG = timezone(timedelta(hours=-3))

HOST = "https://www.bcr.com.ar"
LISTADO = (HOST + "/es/mercados/gea/estimaciones-nacionales-de-produccion"
                  "/estimaciones-anteriores")

# Tope duro del backfill. Medido el 8-sep-2026: el pager llegaba a page=34
# (35 paginas x 6 entradas ~= 210 informes). El tope es una red de seguridad
# contra un pager infinito, no una afirmacion sobre cuantas paginas hay.
TOPE_PAGINAS = 60

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

# ---------------------------------------------------------------------------
# Parser del listado — funciones puras, probadas sin red
# ---------------------------------------------------------------------------

ITEM = re.compile(r'<article\b[^>]*class="[^"]*\bc2\b[^"]*"[^>]*>(.*?)</article>',
                  re.S | re.I)
TITULO = re.compile(
    r'<a\b[^>]*class="[^"]*\bc2-title\b[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.S | re.I)
FECHA = re.compile(r'<span\b[^>]*class="[^"]*\bc2-date\b[^"]*"[^>]*>(.*?)</span>',
                   re.S | re.I)
FECHA_TXT = re.compile(
    r'(\d{1,2})\s+de\s+([A-Za-z\u00c0-\u017f]+)\s+de\s+(\d{4})', re.I)

PDF_HREF = re.compile(r'href="([^"]*/sites/default/files/[^"]*\.pdf)"', re.I)

META = (r'<meta[^>]*(?:property|name)="{k}"[^>]*content="([^"]*)"'
        r'|<meta[^>]*content="([^"]*)"[^>]*(?:property|name)="{k}"')


def _texto(frag):
    import html as _h
    return " ".join(_h.unescape(re.sub(r"<[^>]+>", " ", frag or "")).split())


def _sin_tildes(p):
    p = unicodedata.normalize("NFKD", (p or "").lower())
    return "".join(c for c in p if not unicodedata.combining(c))


def fecha_es(txt):
    """'12 de Agosto de 2026' -> '2026-08-12'. None si no se entiende.

    NO ADIVINA. Si la fuente cambia de formato (p.ej. a 12/08/2026) esto
    devuelve None y el item queda registrado como omitido con su motivo.
    Un hueco fechado vale mas que una fecha inventada.
    """
    m = FECHA_TXT.search(txt or "")
    if not m:
        return None
    dia, mes, anio = int(m.group(1)), _sin_tildes(m.group(2)), int(m.group(3))
    if mes not in MESES:
        return None
    mm = MESES[mes]
    if not (1 <= dia <= 31 and 2000 <= anio <= 2100):
        return None
    try:
        datetime(anio, mm, dia)
    except ValueError:
        return None
    return f"{anio:04d}-{mm:02d}-{dia:02d}"


def entradas(pagina):
    """Items del listado. Lo que no se entiende va None, nunca inventado."""
    out = []
    for bloque in ITEM.findall(pagina or ""):
        mt = TITULO.search(bloque)
        if not mt:
            continue
        href = _texto(mt.group(1)) or mt.group(1).strip()
        if "/estimaciones/" not in href:
            continue
        mf = FECHA.search(bloque)
        crudo = _texto(mf.group(1)) if mf else None
        out.append({
            "href": absoluta(href),
            "slug": href.rstrip("/").rsplit("/", 1)[-1],
            "titulo": _texto(mt.group(2)) or None,
            "fecha_txt": crudo,
            "fecha": fecha_es(crudo),
        })
    return out


def absoluta(href):
    href = (href or "").strip()
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return HOST + href
    return href


def meta_de(pagina, clave):
    m = re.search(META.format(k=re.escape(clave)), pagina or "", re.I)
    if not m:
        return None
    return m.group(1) or m.group(2)


def modified_time(pagina):
    """El nodo SI se fecha. Se registra crudo y, si es epoch, tambien legible.

    En BCR el campo viene como timestamp Unix, no ISO. Se guarda tal cual
    para no perder el original, y se agrega la lectura al lado.
    """
    return meta_de(pagina, "article:modified_time")


def epoch_a_iso(valor):
    """1786571271 -> '2026-08-12T18:47:51-03:00'. None si no es epoch."""
    v = (valor or "").strip()
    if not re.fullmatch(r"\d{9,11}", v):
        return None
    try:
        return datetime.fromtimestamp(int(v), ARG).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def pdfs_de(nodo):
    """PDFs del nodo, absolutos y sin duplicados, en orden de aparicion.

    El nodo linkea el mismo PDF dos veces (relativo y absoluto): medido en el
    informe 197. Se deduplica por URL absoluta.
    """
    vistos, out = set(), []
    for href in PDF_HREF.findall(nodo or ""):
        u = absoluta(href)
        if u not in vistos:
            vistos.add(u)
            out.append(u)
    return out


def hay_siguiente(pagina, actual):
    """True si el pager ofrece una pagina posterior a la actual."""
    for n in re.findall(r'href="[^"]*[?&]page=(\d+)"', pagina or ""):
        if int(n) > actual:
            return True
    return False


# ---------------------------------------------------------------------------
# Guardas de canal y de tipo
# ---------------------------------------------------------------------------

class NoEsta(Exception):
    pass


class CanalInseguro(Exception):
    pass


def _exigir_https(url, donde):
    """La boveda NUNCA sella por un canal no autenticado. Decision firme.

    Va aca por la misma razon que en la mecanica D: la regla es sobre el
    CANAL, asi que vive en el unico punto por el que pasa todo lo que se
    sella, no en el que arma las URLs.

    OJO CON BCR: el canonical y el og:url de este sitio se declaran en
    http:// plano (medido el 8-sep-2026) aunque la pagina se sirva por
    https. Nunca seguir el canonical: se usan las URLs del pager y de los
    href, que vienen relativas y las absolutiza absoluta() sobre HOST, que
    es https por construccion.
    """
    if not (url or "").lower().startswith("https://"):
        raise CanalInseguro(
            f"{donde} en canal no autenticado: {url!r}. La boveda no sella por "
            f"HTTP plano — queda hueco fechado, no se baja.")


def _es_html(datos, headers):
    if "html" in (headers.get("Content-Type") or "").lower():
        return True
    cabeza = (datos or b"")[:2048].lstrip().lower()
    return (cabeza.startswith(b"<!doctype") or cabeza.startswith(b"<html")
            or b"<html" in cabeza[:1024])


def _es_pdf(datos, headers):
    """Un PDF de verdad arranca con %PDF-. El status 200 no prueba nada."""
    if not (datos or b"").startswith(b"%PDF-"):
        return False
    ct = (headers.get("Content-Type") or "").lower()
    return ("pdf" in ct) or (ct == "") or ("octet-stream" in ct)


def bajar(url, espero="bin"):
    """Descarga con reintentos. Devuelve (bytes, headers, url_final).

    Nunca archivar lo que no se pudo identificar como lo que se pedia. Un
    dato falso con sello es infinitamente peor que un hueco.
    """
    _exigir_https(url, "URL pedida")

    ultimo = None
    for intento in range(REINTENTOS):
        try:
            r = requests.get(url, headers=UA, timeout=TIMEOUT)

            # La cadena ENTERA, no solo el destino: un https que rebota por
            # http y vuelve a https igual viajo sin autenticar en el medio.
            for salto in list(r.history) + [r]:
                _exigir_https(getattr(salto, "url", None), "salto de redireccion")

            if r.status_code == 404:
                raise NoEsta(f"404 — no existe {url}")
            r.raise_for_status()
            if not r.content:
                raise ValueError("respuesta vacia")

            if espero == "html" and not _es_html(r.content, r.headers):
                raise NoEsta(f"esperaba HTML y vinieron {len(r.content)} bytes "
                             f"que no lo son")
            if espero == "pdf" and not _es_pdf(r.content, r.headers):
                que = "una pagina HTML" if _es_html(r.content, r.headers) else "otra cosa"
                raise NoEsta(f"esperaba un PDF y el servidor devolvio {que} de "
                             f"{len(r.content)} bytes. El archivo no esta publicado.")
            return r.content, dict(r.headers), r.url
        except (NoEsta, CanalInseguro):
            raise
        except Exception as e:
            ultimo = e
            if intento < REINTENTOS - 1:
                time.sleep(2.0 * (intento + 1))
    raise ultimo


# ---------------------------------------------------------------------------
# R2 — bucket reservado, credenciales propias
# ---------------------------------------------------------------------------

def _cliente():
    import boto3
    from botocore.config import Config

    endpoint = os.getenv("R2_ENDPOINT")
    key = os.getenv("R2_RESERVADO_ACCESS_KEY_ID")
    secret = os.getenv("R2_RESERVADO_SECRET_ACCESS_KEY")
    if not (endpoint and key and secret):
        raise RuntimeError(
            "faltan R2_ENDPOINT / R2_RESERVADO_ACCESS_KEY_ID / "
            "R2_RESERVADO_SECRET_ACCESS_KEY. Este modulo NO usa las "
            "credenciales de guarismo-crudo a proposito.")

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
    """Estado previo. {} SOLO si el objeto todavia no existe.

    POR QUE NO ALCANZA UN except Exception: return {}
        Asi estaba escrito y trataba igual dos cosas que no se parecen:

          NoSuchKey     -> primera corrida. {} es la respuesta correcta.
          AccessDenied  -> no tengo permiso para LEER el estado.

        En el segundo caso, devolver {} hace que el modulo diga "sin estado
        previo", re-archive todo como PRIMERA y despues PISE el estado bueno
        con uno reconstruido a ciegas. Un error de credenciales terminaria
        en un log que se ve perfecto.

        Paso el 8-sep-2026: con el token inactivo, el log dijo "sin estado
        previo — todo lo que se vea sera PRIMERA" sin que eso fuera cierto.
        Un cero sin motivo escrito es indistinguible de un bug.

    Por eso cualquier error que NO sea "todavia no existe" ROMPE la corrida.
    Es preferible un workflow en rojo a un archivo reconstruido de prepo.
    """
    from botocore.exceptions import ClientError
    try:
        obj = s3.get_object(Bucket=bucket, Key=ESTADO)
        return json.loads(obj["Body"].read().decode("utf-8"))
    except ClientError as e:
        cod = (e.response.get("Error") or {}).get("Code")
        if cod in ("NoSuchKey", "404", "NotFound"):
            return {}
        raise RuntimeError(
            f"no se pudo LEER {ESTADO} en {bucket}: {cod}. No es que no "
            f"exista: no se pudo leer. La corrida se detiene para no "
            f"reconstruir el estado a ciegas.") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"{ESTADO} existe pero no es JSON valido: {e}. Se detiene: "
            f"seguir significaria pisarlo con uno nuevo.") from e


def escribir_estado(s3, bucket, estado):
    s3.put_object(Bucket=bucket, Key=ESTADO,
                  Body=json.dumps(estado, ensure_ascii=False,
                                  indent=1).encode("utf-8"),
                  ContentType="application/json")


def sha256(datos):
    return hashlib.sha256(datos).hexdigest()


def subir(s3, bucket, clave, datos, tipo):
    s3.put_object(Bucket=bucket, Key=clave, Body=datos, ContentType=tipo)


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------

def url_pagina(n):
    return LISTADO if n == 0 else f"{LISTADO}?page={n}"


def recorrer(paginas, log):
    """Lee el listado. Devuelve (items, paginas_crudas, omitidas, repetidos).

    Nunca corta por un total declarado por la fuente: corta porque el pager
    dejo de ofrecer una pagina posterior, o por el tope duro.

    POR QUE SE CUENTAN LOS REPETIDOS
        El backfill del 8-sep-2026 leyo 205 posiciones (34 paginas de 6 + 1)
        y quedaron 199 informes unicos: SEIS entradas repetidas que este
        bucle salteaba en silencio. Se verifico que no hubo perdida —cero
        meses faltantes en los 80 de 2020-2026— pero el modulo no lo decia.

        Un manifiesto tiene que contar lo que OMITIO. Si manana el listado
        devolviera la misma entrada doscientas veces, el log de antes habria
        dicho "199 informes" con la misma tranquilidad de siempre.

        Hipotesis NO verificada de por que se repiten: la paginacion de
        Drupal reordenando entre requests cuando hay empates de fecha. Con
        este registro, el proximo backfill dice en que pagina paso.
    """
    items, crudas, omitidas, repetidos = [], {}, [], []
    vistos = {}
    n = 0
    while n < min(paginas, TOPE_PAGINAS):
        url = url_pagina(n)
        try:
            datos, _h, _u = bajar(url, espero="html")
        except Exception as e:
            omitidas.append({"pagina": n, "url": url,
                             "motivo": f"{type(e).__name__}: {e}"})
            log(f"  [pagina {n}] HUECO — {type(e).__name__}: {e}")
            break
        pagina = datos.decode("utf-8", errors="replace")
        crudas[n] = datos
        nuevas = entradas(pagina)
        if not nuevas:
            omitidas.append({"pagina": n, "url": url,
                             "motivo": "cero entradas parseadas"})
            log(f"  [pagina {n}] CERO entradas — el listado cambio de forma")
            break
        rep_aca = 0
        for it in nuevas:
            if it["slug"] in vistos:
                repetidos.append({"slug": it["slug"], "pagina": n,
                                  "visto_antes_en_pagina": vistos[it["slug"]],
                                  "fecha": it["fecha"]})
                rep_aca += 1
                continue
            vistos[it["slug"]] = n
            items.append(it)
        log(f"  [pagina {n}] {len(nuevas)} entradas"
            + (f" · {rep_aca} REPETIDA(S)" if rep_aca else ""))
        if not hay_siguiente(pagina, n):
            break
        n += 1
        time.sleep(PAUSA)
    return items, crudas, omitidas, repetidos


def clave_objeto(fecha, slug, que, ext):
    """boveda/privadas/bcr/AAAA/MM/AAAA-MM-DD_slug__que.ext"""
    per = fecha or "sin-fecha"
    anio, mes = (per[:4], per[5:7]) if fecha else ("sin-fecha", "sin-fecha")
    return f"{PREFIJO}/bcr/{anio}/{mes}/{per}_{slug}__{que}.{ext}"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seco", action="store_true",
                    help="no escribe un byte en R2")
    ap.add_argument("--backfill", action="store_true",
                    help="recorre el listado completo (Clase B)")
    ap.add_argument("--paginas", type=int, default=None,
                    help="tope de paginas a recorrer")
    ap.add_argument("--bucket", default=os.getenv("R2_BUCKET_RESERVADO",
                                                  BUCKET_DEFAULT))
    a = ap.parse_args(argv)

    salida = []

    def log(txt):
        print(txt, flush=True)
        salida.append(txt)

    ahora = datetime.now(ARG)
    sello = ahora.isoformat()
    paginas = a.paginas if a.paginas else (TOPE_PAGINAS if a.backfill else 1)

    log(f"[privadas] BCR/GEA · {sello} · bucket={a.bucket} · "
        f"{'BACKFILL' if a.backfill else 'diaria'} · paginas<={paginas}"
        f"{' · SECO' if a.seco else ''}")

    s3 = None if a.seco else _cliente()
    estado = {} if a.seco else leer_estado(s3, a.bucket)
    if not estado:
        log("  sin estado previo — todo lo que se vea sera PRIMERA")
    conocidos = estado.get("bcr", {})
    # Se fija ANTES del bucle a proposito: `conocidos` se llena adentro, asi
    # que preguntarle ahi si esta vacio da PRIMERA solo para el primer item y
    # NUEVO para el resto. El conteo de `primera` es la alarma de "se perdio
    # el estado": si miente, la alarma miente.
    habia_estado = bool(conocidos)

    items, crudas, omitidas, repetidos = recorrer(paginas, log)
    log(f"  {len(items)} informe(s) en el listado"
        + (f" · {len(repetidos)} entrada(s) repetida(s) en el listado"
           if repetidos else ""))

    primeras, nuevos, editados, huecos = [], [], [], []
    manifiesto_items = []

    for it in items:
        slug = it["slug"]
        previo = conocidos.get(slug)

        if it["fecha"] is None:
            huecos.append({"slug": slug, "motivo":
                           f"fecha no interpretable: {it['fecha_txt']!r}"})
            log(f"  [{slug}] HUECO — fecha no interpretable: {it['fecha_txt']!r}")
            continue

        # Nodo: es donde vive el modified_time vivo y el link al PDF.
        try:
            datos_nodo, _h, url_nodo = bajar(it["href"], espero="html")
        except Exception as e:
            huecos.append({"slug": slug, "url": it["href"],
                           "motivo": f"{type(e).__name__}: {e}"})
            log(f"  [{slug}] HUECO — nodo: {type(e).__name__}: {e}")
            continue

        nodo = datos_nodo.decode("utf-8", errors="replace")
        mt = modified_time(nodo)
        mt_iso = epoch_a_iso(mt)
        pub = meta_de(nodo, "article:published_time")
        pdfs = pdfs_de(nodo)

        ficha = {
            "slug": slug, "titulo": it["titulo"], "url": it["href"],
            "fecha_listado": it["fecha"], "fecha_listado_txt": it["fecha_txt"],
            "published_time": pub,
            "modified_time": mt, "modified_time_iso": mt_iso,
            "pdfs": pdfs, "sha_nodo": sha256(datos_nodo),
        }

        if previo is None:
            veredicto = "NUEVO" if habia_estado else "PRIMERA"
        elif previo.get("modified_time") != mt:
            veredicto = "EDITADO"
        else:
            veredicto = "sin cambios"
        ficha["veredicto"] = veredicto

        if veredicto == "EDITADO":
            ficha["modified_time_previo"] = previo.get("modified_time")
            ficha["modified_time_previo_iso"] = epoch_a_iso(
                previo.get("modified_time"))
            editados.append(slug)
            log(f"  [{slug}] EDITADO — modified_time {previo.get('modified_time')}"
                f" -> {mt} ({mt_iso})")
        elif veredicto in ("NUEVO", "PRIMERA"):
            (nuevos if veredicto == "NUEVO" else primeras).append(slug)
            log(f"  [{slug}] {veredicto} — {it['fecha']} · {len(pdfs)} pdf(s)")

        if not pdfs:
            huecos.append({"slug": slug, "motivo": "el nodo no linkea ningun PDF"})
            log(f"  [{slug}] OJO — el nodo no linkea ningun PDF")

        manifiesto_items.append(ficha)

        if veredicto == "sin cambios":
            continue

        # Escritura: nodo + PDFs. El listado se escribe una vez por corrida
        # con novedad, mas abajo.
        if not a.seco:
            subir(s3, a.bucket, clave_objeto(it["fecha"], slug, "nodo", "html"),
                  datos_nodo, "text/html; charset=utf-8")

        for i, pu in enumerate(pdfs):
            try:
                datos_pdf, hpdf, updf = bajar(pu, espero="pdf")
            except Exception as e:
                huecos.append({"slug": slug, "url": pu,
                               "motivo": f"{type(e).__name__}: {e}"})
                log(f"  [{slug}] HUECO — pdf: {type(e).__name__}: {e}")
                continue
            que = "informe" if i == 0 else f"informe_{i + 1}"
            sha = sha256(datos_pdf)
            ficha.setdefault("archivos", []).append({
                "que": que, "url": pu, "url_final": updf,
                "bytes": len(datos_pdf), "sha256": sha,
                "last_modified": hpdf.get("Last-Modified"),
                "etag": hpdf.get("ETag"),
            })
            if not a.seco:
                subir(s3, a.bucket,
                      clave_objeto(it["fecha"], slug, que, "pdf"),
                      datos_pdf, "application/pdf")
            log(f"  [{slug}] {que}: {len(datos_pdf):,} bytes · sha {sha[:12]}")
            time.sleep(PAUSA)

        conocidos[slug] = {
            "modified_time": mt, "fecha_listado": it["fecha"],
            "sha_nodo": ficha["sha_nodo"], "visto": sello,
        }

    # El listado se archiva junto con lo que apunta: prueba que informe estaba
    # listado ese dia. Solo si hubo novedad, para no escribir todos los dias.
    if (primeras or nuevos or editados) and not a.seco:
        for n, datos in crudas.items():
            subir(s3, a.bucket,
                  f"{PREFIJO}/bcr/_listado/{ahora:%Y/%m/%d}_page{n}.html",
                  datos, "text/html; charset=utf-8")

    manifiesto = {
        "modulo": "boveda_privadas", "fuente": "bcr_gea_estimaciones",
        "capturado_arg": sello,
        "run_id": os.getenv("GITHUB_RUN_ID"),
        "modo": "backfill" if a.backfill else "diaria",
        "seco": a.seco,
        "paginas_leidas": sorted(crudas.keys()),
        "paginas_omitidas": omitidas,
        "repetidos_en_listado": repetidos,
        "informes_vistos": len(items),
        "primeras": primeras, "nuevos": nuevos,
        "editados": editados,
        "huecos": huecos,
        "items": manifiesto_items,
        "nota_licencia": ("Fuente privada. Capturado, NO publicable sin "
                          "permiso escrito de la Bolsa de Comercio de Rosario."),
    }

    if not a.seco:
        cuerpo = json.dumps(manifiesto, ensure_ascii=False, indent=1).encode("utf-8")
        subir(s3, a.bucket,
              f"{PREFIJO}/bcr/_manifiestos/{ahora:%Y/%m/%d_%H%M%S}.json",
              cuerpo, "application/json")
        estado["bcr"] = conocidos
        estado["ultima_corrida"] = sello
        escribir_estado(s3, a.bucket, estado)

    log(f"[privadas] {len(items)} informes · {len(primeras)} primera · "
        f"{len(nuevos)} nuevos · {len(editados)} editados · "
        f"{len(huecos)} huecos · {len(omitidas)} paginas omitidas · "
        f"{len(repetidos)} repetidos")

    # Siempre 0: un hueco aislado NO es una emergencia y no debe tumbar la
    # corrida diaria — queda fechado en el manifiesto, que es su lugar. Lo que
    # sí importa es que el hueco este CONTADO y con su motivo escrito.
    return 0


if __name__ == "__main__":
    sys.exit(main())
