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

    (6) GATILLO POR LISTADO — SAGyP, 15-sep-2026.
        El listado de Estimaciones Agricolas no es Drupal: es PHP y NO declara
        article:modified_time. Y muestra UN MES POR VEZ (?mes=AAAA-MM).

        Dos consecuencias, las dos resueltas por configuracion de la fuente
        ("gatillo": "listado" y "paginas_por_mes": N), sin tocar a las
        otras siete:

          - la pagina se gatilla por la FIRMA DEL LISTADO: el sha256 de los
            pares (url, etiqueta) que devuelve el etiquetador. Cambia cuando
            aparece o desaparece una clave, no cuando el servidor reserializa.
            Es la aparicion de clave de boveda_privadas.py con BCR.
          - se miran el mes en curso y el anterior, cada uno con su propia
            clave de estado. Sin eso, el 1ro de cada mes el listado "cambia"
            porque cambio el mes, no porque alguien publico.

        Fuentes de una sola pagina y sin gatillo declarado: todo como antes.

    (7) _absoluta() RESUELVE CONTRA EL HOST DE LA PAGINA.
        Hasta el 15-sep-2026 pegaba "https://www.argentina.gob.ar" a todo href
        que empezara con "/", y el parametro `base` no se usaba. Con siete
        fuentes de argentina.gob.ar no se notaba. Con SAGyP habria armado
        https://www.argentina.gob.ar/sitio/areas/estimaciones/... — una URL de
        otro organismo. Lo encontro la lectura antes de cablear, no un hueco.

    (8) METADATOS DE R2 EN ASCII — BCRA, 15-sep-2026.
        R2 (S3) rechaza metadatos con caracteres no-ASCII: botocore corta con
        ParamValidationError antes de mandar nada. Probado con un stub, sin red.
        El caso real: ITCMSerie_metodología_abr_16.xls, con tilde en la URL.
        Sin esto, ese archivo quedaria HUECO para siempre con un error que no
        dice nada de la fuente. guardar() codifica la URL de los metadatos solo
        si hace falta: las URLs ASCII de siempre pasan intactas. El manifiesto
        sigue guardando la URL tal cual la publico la fuente.

    (9) LICITACIONES — 17-sep-2026.
        Dos paginas de Finanzas: colocaciones de deuda y el cronograma 2026.

        COLOCACIONES TIENE VENTANA MOVIL, y eso la vuelve IRRECUPERABLE:
          08-sep-2026  filas 31-07-2026 y 30-04-2026 (colocaciones_31-5-26.xlsx)
          17-sep-2026  filas 31-08-2026 (..._preliminar.xlsx) y 31-07-2026
        La pagina se modifico el 11-sep y el corte de abril DESAPARECIO del
        listado. Los años cerrados quedan con un solo corte, al 31-12. Cada
        corte parcial vive unas semanas. Un caso: HIPOTESIS de ventana de dos.

    (10) EL NOMBRE DEL ARCHIVO NO DECIDE EL PERIODO — NI PARA VETARLO.
        Regla 2 completa: el periodo sale de la PAGINA. Si el nombre del
        archivo trae otra fecha, se ANOTA (conflicto_celda_vs_nombre), suena
        en el log y el archivo SE GUARDA con la fecha de la pagina.
        Si se contradicen DOS TEXTOS DE LA PAGINA (celda contra etiqueta),
        sigue siendo incierto, como en Datos Anteriores.
        Por que: lo incierto no se captura en NINGUN modo — tampoco en
        backfill —, y en una pagina con ventana movil eso es perdida segura.
        El caso real fue la fila 30-04-2026 con colocaciones_31-5-26.xlsx.

    (11) UN ETIQUETADOR QUE FALLA NO TIRA ABAJO A LAS DEMAS FUENTES.
        Hasta el 17-sep-2026, una excepcion en el etiquetador de una fuente
        cortaba la corrida entera de la mecanica D. Ahora esa fuente queda
        HUECO (etiquetador) y las otras siguen. La pagina se archiva igual por
        su modified_time; las de gatillo por listado no, porque su gatillo ES
        la lista.

    (12) NOTICIAS DE FINANZAS — 17-sep-2026. Tres cosas nuevas, todas por
        configuracion de la fuente:

        paginas_fijas: N   el listado se pagina con ?page=N. Se miran las N
                           primeras, cada una con su clave de estado.
        captura: "nuevas"  una noticia no se revisa: nace y queda. Se baja lo
                           que TODAVIA NO ESTA en el estado, no una ventana.
                           Bajar las dos ultimas todos los dias seria gastar
                           pedidos contra el Estado para traer lo mismo.
        it["id"]           la identidad de una noticia es su slug, no su
                           fecha: hay varias del mismo tipo el mismo dia. El
                           id entra en la clave sin pisar el periodo.

        EL LISTADO TRAE LA HORA: <time datetime='2026-09-11 17:18:06'>. La
        fecha sale de ahi (es dato de la pagina, no del nombre) y la hora
        queda aparte, en hora_en_la_pagina. Con eso la puntualidad del Tesoro
        se mide con hora y no solo con dia.

        LO QUE NO HACE: no entra adentro de cada noticia. Los PDF colgados de
        una noticia son otro nivel y van en otra vuelta; la noticia archivada
        prueba cuales colgaba.

    (13) WASDE DEL USDA POR ESMIS — 22-sep-2026. La primera fuente de afuera.
        La "otra puerta" que estaba anotada (Cornell) REDIRIGE a ESMIS
        (esmis.nal.usda.gov, Drupal 11). Verificado el 21-sep-2026.

        NO ES MECANICA B. La ruta lleva un numero de publicacion que no se
        adivina: release-files/796054/wasde0926.pdf. Es un listado.

        LA PAGINA, TAL CUAL ESTA AL 22-sep-2026 (HTML crudo, no el fetch)
            - NO declara article:modified_time -> gatillo por listado.
            - 10 publicaciones por pagina (?page=N), cada una en 4 formatos:
              pdf, txt, xls, xml. Se mira SOLO la primera pagina: una
              publicacion nueva aparece arriba.
            - Una tabla "Latest Release" REPITE la ultima publicacion. El link
              repetido cuenta una vez (se queda el de la tabla principal, que
              va primero en la pagina).
            - Mayo y junio 2026 figuran SOLO como wasde0526v2 y wasde0626v2:
              el USDA los republico. Si la v1 sigue en el servidor es
              HIPOTESIS; no se adivina su URL.

        DE DONDE SALE CADA COSA
            periodo    la fecha del <time datetime> de la CELDA de fecha. Nunca
                       del nombre del archivo (wasde0926 no dice el dia).
            producto   el formato, del TEXTO DE LA ETIQUETA ("Sep 11 2026 - pdf").
                       Si no coincide con la extension de la URL: incierto.
            id         {numero}_{nombre}, p. ej. 796054_wasde0926. Una v2 tiene
                       otro numero y otro nombre: es un objeto NUEVO, con su
                       clave. La v1 no se pisa nunca.
            hora       NO SE USA. La pagina dice 12:00:00Z y el USDA publica a
                       las 12:00 de Washington: la fuente rotula mal la zona. La
                       hora se guarda CRUDA en datetime_en_la_pagina, sin
                       interpretar. Una hora mal rotulada no es dato.
            Si la fecha que repite el propio link no es la de su celda, dos
            textos de la pagina se contradicen: incierto, no se archiva, suena.

        CAPTURA POR APARICION ("captura": "nuevas"), como las noticias: se baja
        lo que todavia no esta en el estado. Lo ya archivado no se vuelve a
        pedir todos los dias.

        EL PUNTO CIEGO, ESCRITO: si el USDA REEMPLAZA un archivo EN LA MISMA URL,
        esto no lo ve. La clave ya esta en el estado y no se baja de nuevo. La
        v2 de mayo y junio se ve porque vino con URL nueva. Si algun dia hace
        falta vigilar reemplazos en el lugar, es otra decision.

        txt y xml van en la lista PROPIA del WASDE (WASDE_EXTENSIONES), no en
        EXTENSIONES: las otras catorce fuentes no ven nada distinto. En TIPOS
        se suman los dos content-types, que solo usa esta fuente.

        La primera corrida baja las 10 ediciones listadas: 40 archivos, CLASE B
        (reconstruidas, no vistas al salir). La primera Clase A del WASDE es la
        del viernes 9-oct-2026, 12:00 de Washington.

    (14) LA PAGINA DE CADA EDICION DEL WASDE — consulta 17.1 -> A, 22-sep-2026.
        Cada fila del listado tiene un link "View" a la pagina de esa edicion.
        AHI, y no en el listado, el USDA escribe sus correcciones. El caso que
        lo destapo, verificado sobre el HTML crudo el 22-sep-2026:
            /2026-05-12-0 (Release Abstract): el 13-may-2026 republico el WASDE
            de mayo para corregir la tabla de trigo por clase (671-11): trigo
            blanco importado 25/26, 5 -> 7 millones de bushels; total 123 ->
            125; y las correcciones consecuentes en oferta y stocks finales.
        El listado sigue fechando esa edicion el 12-may. La fecha de la
        correccion SOLO existe en la nota. Junio (v2) no trae nota.

        LA URL SALE DEL LISTADO, NUNCA SE ARMA. Mayo es /2026-05-12-0 (sufijo
        de Drupal); /2026-05-12 da "Page Not Found". Armarla a mano fallo el
        mismo dia que se descubrio.

        GATILLO POR CONTENIDO VISIBLE, NO POR APARICION.
            La nota de mayo es del 13 y la edicion del 12: una captura por
            aparicion la habria perdido. Se bajan las 10 paginas de edicion
            todos los dias ("revisar_contenido") y se archiva una solo si cambia
            su FIRMA DE CONTENIDO: el texto visible de <main> + sus links. Asi
            una nota agregada o editada se ve, y un servidor que reserializa
            atributos no dispara nada. Sin <main> no hay firma: se compara por
            hash y el manifiesto lo dice.

        Las paginas de edicion NO entran en la firma del listado: tienen su
        propio gatillo. Si entraran, el dia del estreno el listado "cambiaria"
        sin que el USDA publique nada, y eso envenena la puntualidad.

        EN EL LOG: "50 links · 0 sin resolver · 10 a capturar" es el regimen
        normal (40 archivos + 10 ediciones), con las 10 "sin cambios". Una
        edicion en NUEVO es una pagina de edicion que el USDA toco.

    (15) EL REM, DESDE LA PAGINA — consulta 19.1 -> B, 25-sep-2026.
        El 24-sep-2026 a las 12:27 el BCRA RENOMBRO los siete archivos del REM
        sin tocar una letra del texto de la pagina. La pagina archivada lo
        probo sola: 7 links distintos, mismo contenido visible.
            antes   relevamiento-expectativas-mercado-ago-2026.pdf
                    tablas-relevamiento-expectativas-mercado-ago-2026.xlsx
                    {listado,ranking,historico,metodologia,consideraciones}-
                    relevamiento-expectativas-mercado.{pdf,xlsx}
            ahora   relevamiento-expectativas-mercado-2026-08.pdf
                    relevamiento-expectativas-mercado-tablas-2026-08.xlsx
                    relevamiento-expectativas-mercado-{listado,...}.{pdf,xlsx}
        La mecanica B ARMABA esas URLs con {mes3}. Era la cuarta vez que la
        URL del REM cambiaba. Con el patron viejo, el 6-oct los cinco de URL
        fija habrian quedado en "sin cambios" para siempre: perdida silenciosa
        de algo irrecuperable.

        LA URL SALE DEL LINK, NUNCA SE ARMA. La seccion "Archivos" es un
        bloque de lineas separadas por <br>: "ETIQUETA | <a>PDF</a> | <a>XLSX</a>".
            periodo   de la ETIQUETA DE LA FILA ("REM AGOSTO 2026"). Nunca del
                      nombre: si el nombre trae otra fecha, se anota y suena
                      (regla 10), y se guarda con la de la pagina.
            producto  de la etiqueta de la fila + el formato del link:
                      fila con mes y año -> informe (PDF) / tablas (XLSX).
                      Las otras cinco -> "vigente", clave estable, como el
                      cronograma: el BCRA las reescribe en el lugar cada mes.
            formato   el TEXTO del link ("PDF") tiene que coincidir con la
                      extension de la URL. Si no: incierto, no se archiva, suena.
            Una fila que no se reconoce queda incierta. Nunca se archiva lo que
            no se identifico.

        LOS SIETE SE BAJAN TODOS LOS DIAS (seleccion normal, sin "nuevas"):
        hoy los baja la mecanica B desde las URLs viejas, y el hash decide si
        se guarda algo. Las entradas rem_* de la B NO se tocan en este cambio:
        mientras las URLs viejas respondan, son la vigilancia de lo deslistado.
        El dia que den HUECO, queda registrado que el BCRA las saco.

        La primera corrida da 7 PRIMERA: la edicion de agosto, por la URL
        nueva. Si los bytes son los mismos que tiene la B por la URL vieja, el
        manifiesto de las dos lo muestra (mismo sha).

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
from urllib.parse import quote
from datetime import datetime, timedelta, timezone

import requests

TIMEOUT = 120
REINTENTOS = 3
PAUSA = 1.0

BUCKET_DEFAULT = "guarismo-crudo"
PREFIJO = "boveda/mecanicad"
ESTADO = "_estado/mecanicad_hashes.json"

UA = {"User-Agent": "Guarismo/1.0 (+https://guarismo.com.ar; infoguarismo@gmail.com)"}

# Toda hora lleva zona. El mes "en curso" de un listado mensual es el mes de
# Buenos Aires, no el de UTC: la corrida de las 19:15 ARG ya es 22:15 UTC.
ARG = timezone(timedelta(hours=-3), "ART")

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
    # Solo los usa el WASDE (13). No estan en EXTENSIONES a proposito.
    "txt": "text/plain",
    "xml": "application/xml",
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
    # --- Estructura financiera de titulos publicos. Reconocida el 7-sep-2026. ---
    #
    # LA MISMA FALLA QUE LA DEUDA MENSUAL, EN OTRO LADO: la pagina muestra el
    # archivo VIGENTE y nada mas. No hay historico, no hay subpagina de
    # anteriores. Cada mes el anterior deja de estar linkeado.
    #
    # Y lo que se pierde es grueso: es el detalle instrumento por instrumento
    # de cada bono y letra vigente del Estado Nacional. Una foto fechada de eso
    # es la clase de documento que se pide en un juicio. Sin captura el dia de
    # publicacion, la foto de este mes no existe mas.
    #
    # Cuatro archivos, tres formas distintas de nombrarse. Ninguno declara su
    # periodo en la pagina: los cuatro van como "vigente".
    "finanzas_titulos_estructura": {
        "url": "https://www.argentina.gob.ar/economia/finanzas/estructura-financiera-de-titulos-publicos",
        "desc": "Secretaria de Finanzas — estructura financiera de titulos publicos, cupones y coeficientes PG",
        "etiquetador": "titulos_estructura",
        "organismo": "Secretaria de Finanzas",
    },
    # --- Datos Anteriores de la deuda trimestral. Reconocida el 7-sep-2026,
    # --- cableada el 7-sep-2026. 102 archivos, 2007-2018.
    #
    # CADENCIA "archivo", Y ES LA PRIMERA DEL MODULO.
    #   Esta pagina no publica: guarda. Lo ultimo que subio es de 2018 y lo
    #   vigente vive en la pagina madre. Correr seleccionar() aca da NUEVE
    #   archivos por dia PARA SIEMPRE — medido, no estimado: dos de 2018 por
    #   cada uno de los tres productos vivos, mas DOS INFORMES DE 2015 y el
    #   avance preliminar de 2016, que entran por ser lo mas nuevo de
    #   productos discontinuados. Nueve objetos que no pueden cambiar nunca.
    #
    #   Con cadencia "archivo" la pagina se mira igual todos los dias — su
    #   article:modified_time es la alarma, y es la unica que importa: se
    #   movio el 26-ago-2026 y nadie sabe que cambio, porque no estaba bajo
    #   captura. Los archivos van solo con --backfill.
    #
    #   OJO: esto NO arregla el congelado de 42,7 MB de titulos_estructura.
    #   Ese es un ITEM adentro de una pagina viva, no una pagina archivo.
    #   Sigue pendiente y sigue necesitando su propia medicion.
    "finanzas_deuda_trim_ant": {
        "url": "https://www.argentina.gob.ar/economia/finanzas/datos-trimestrales-de-la-deuda/datos-anteriores",
        "desc": "Secretaria de Finanzas — deuda publica trimestral, ediciones anteriores (2007-2018)",
        "etiquetador": "deuda_ant",
        "organismo": "Secretaria de Finanzas",
        "cadencia": "archivo",
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

    # --- SAGyP, Estimaciones Agricolas. Cableada el 15-sep-2026. ---
    #
    # PRIMERA FUENTE DEL MODULO FUERA DE argentina.gob.ar, y primera sin
    # article:modified_time. Por eso trae dos claves que nadie mas usa:
    #
    #   gatillo="listado"    la pagina se archiva cuando cambia la LISTA de
    #                        informes, no su hash. Ver (6) arriba.
    #   paginas_por_mes=2    el listado muestra un mes por vez; se miran el
    #                        mes en curso y el anterior. Ver paginas_de().
    #
    # POR QUE ACA Y NO EN LA MECANICA B: publica en jueves sin dia fijo del
    # mes (tercero o cuarto) y el nombre del archivo cambia de mayuscula de un
    # mes a otro ("Informe mensual" en julio 2026, "Informe Mensual" en
    # agosto). Adivinar URLs serian ~25 pedidos por dia y fallaria igual.
    #
    # LA FECHA PROMETIDA ESTA ADENTRO DEL PDF: el mensual del 20-ago-2026
    # anuncia el proximo para el 17-sep-2026. Esa es la alarma.
    #
    # Estatal -> redistribuible -> guarismo-crudo, como las otras siete.
    "sagyp_estimaciones": {
        "url": "https://www.magyp.gob.ar/sitio/areas/estimaciones/estimaciones/informes/",
        "desc": "SAGyP — Estimaciones Agricolas, informes mensual y semanal",
        "etiquetador": "sagyp",
        "organismo": "Secretaria de Agricultura, Ganaderia y Pesca",
        "gatillo": "listado",
        "paginas_por_mes": 2,
    },

    # --- BCRA. Tres paginas de WordPress. Reconocidas el 15-sep-2026. ---
    #
    # Las tres declaran article:modified_time, asi que el gatillo es el de
    # siempre. Sus links empiezan con "/": sin el arreglo (7) de _absoluta()
    # se habrian armado sobre argentina.gob.ar.
    "bcra_calendario_informes": {
        # LA FECHA PROMETIDA DEL BCRA, escrita por la fuente. El propio sitio
        # dice que la pagina se actualiza cuando se confirman o REPROGRAMAN
        # fechas: cada version que se edita se pierde. No tiene archivos: la
        # pagina ES el objeto. Creada 22-abr-2026, modificada 06-may-2026.
        "url": "https://www.bcra.gob.ar/calendario-de-informes/",
        "desc": "BCRA — calendario de informes (fechas previstas de publicacion)",
        "etiquetador": "solo_pagina",
        "organismo": "Banco Central de la Republica Argentina",
    },
    "bcra_rem": {
        # La pagina del REM se REESCRIBE cada mes: resumen ejecutivo nuevo y
        # links a la edicion nueva. Desde el 25-sep-2026 los siete archivos
        # salen de ACA, del link de la pagina (ver (15)): el 24-sep el BCRA
        # los renombro y la mecanica B los armaba. Las rem_* de la B quedan
        # como vigilancia de las URLs viejas.
        "url": "https://www.bcra.gob.ar/relevamiento-expectativas-mercado-rem/",
        "desc": "BCRA — pagina del REM (resumen ejecutivo y archivos vigentes)",
        "etiquetador": "rem",
        "organismo": "Banco Central de la Republica Argentina",
    },
    "bcra_itcrm_metodologias_ant": {
        # Las metodologias VIEJAS del ITCRM, que el BCRA publica el mismo.
        # Historia cerrada (2016 y 2017): cadencia archivo, como Datos
        # Anteriores. La pagina se mira todos los dias; los 4 archivos van
        # solo con --backfill (Clase B).
        "url": "https://www.bcra.gob.ar/metodologias-anteriores-itcrm/",
        "desc": "BCRA — metodologias anteriores del ITCRM (PDF y serie)",
        "etiquetador": "itcrm_metodologias",
        "organismo": "Banco Central de la Republica Argentina",
        "cadencia": "archivo",
    },

    # --- Finanzas, Licitaciones. Reconocidas el 8 y el 17-sep-2026. ---
    #
    # COLOCACIONES: compilado anual (31-12 de cada año cerrado) + cortes
    # parciales del año en curso, que ROTAN. Ver (9) arriba. Book node 79900,
    # slug viejo sin guiones. Ventana 2 por producto: los dos cortes parciales
    # vigentes y los dos ultimos cierres anuales, todos los dias.
    "finanzas_colocaciones": {
        "url": "https://www.argentina.gob.ar/economia/finanzas/deudapublica/colocacionesdedeuda",
        "desc": "Finanzas — colocaciones de deuda (cierres anuales y cortes parciales del año)",
        "etiquetador": "colocaciones",
        "organismo": "Secretaria de Finanzas",
    },
    # CRONOGRAMA 2026: la promesa escrita de cada llamado y licitacion. El PDF
    # se REEMPLAZA en el lugar (el de 2025 vivia en /2022/06/ con fecha
    # interna de enero de 2025) y la pagina no mueve su modified_time: la
    # unica señal es el hash del archivo, que se baja todos los dias.
    # La pagina es por año: cuando exista la de 2027 se suma como otra fuente.
    # NOTICIAS: el listado de Finanzas, que es donde salen el llamado y el
    # resultado de cada licitacion. El cronograma dice lo PROMETIDO; esto es
    # lo OBSERVADO, con hora. Sin article:modified_time -> gatillo por listado,
    # como SAGyP. Cada pagina MUESTRA 16 noticias, pero ?page=1 no empieza en
    # la 17: medido el 17-sep-2026, las paginas 0 y 1 comparten 9 y entre las
    # dos se ven 23 distintas. El paginador avanza de a 7 (hipotesis de un
    # caso). Con dos paginas alcanza de sobra para no perder nada entre
    # corridas diarias, y lo repetido cuenta una vez.
    "finanzas_noticias": {
        "url": "https://www.argentina.gob.ar/economia/finanzas/noticias",
        "desc": "Finanzas — listado de noticias (llamados y resultados de licitacion)",
        "etiquetador": "noticias_finanzas",
        "organismo": "Secretaria de Finanzas",
        "gatillo": "listado",
        "paginas_fijas": 2,
        "captura": "nuevas",
    },
    "finanzas_licit_cronograma": {
        "url": "https://www.argentina.gob.ar/economia/finanzas/licitaciones-de-letras-y-bonos-del-tesoro/cronograma-2026",
        "desc": "Finanzas — cronograma 2026 de llamados y licitaciones del Tesoro",
        "etiquetador": "cronograma_licitaciones",
        "organismo": "Secretaria de Finanzas",
    },

    # --- USDA, WASDE. Reconocido el 21-sep-2026 sobre el HTML crudo. ---
    #
    # La primera fuente que no es argentina. Ver (13) arriba. Dominio publico
    # (obra del gobierno de EE.UU.) -> guarismo-crudo, como las otras.
    # Una sola pagina: sin paginas_fijas, la clave es usda_wasde_pagina.
    "usda_wasde": {
        "url": "https://esmis.nal.usda.gov/publication/world-agricultural-supply-and-demand-estimates",
        "desc": "USDA — World Agricultural Supply and Demand Estimates (WASDE), via ESMIS",
        "etiquetador": "wasde",
        "organismo": "USDA — World Agricultural Outlook Board",
        "gatillo": "listado",
        "captura": "nuevas",
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
        # Contra el HOST DE LA PAGINA. Ver (7) en el encabezado. Sin base se
        # mantiene el comportamiento historico, que es el de las siete
        # fuentes de argentina.gob.ar.
        mb = HOST.match(base or "")
        host = mb.group(1) if mb else "www.argentina.gob.ar"
        return "https://" + host + href
    return None


HOST = re.compile(r"^https?://([^/]+)", re.I)


def _a_https_mismo_host(url, base):
    """HTTP plano -> HTTPS cuando el host es EL MISMO que el de la pagina.

    LA DECISION FIRME: la boveda nunca sella por un canal no autenticado.
    Y _absoluta() NO alcanzaba: devuelve el http:// tal cual, sin mirarlo.
    Verificado leyendo el codigo el 7-sep-2026, no recordado.

    EL CASO REAL, uno entre 102 en Datos Anteriores:
        http://www.argentina.gob.ar/sites/default/files/
        presentacion_grafica_de_la_deuda_31-12-2016.pdf
    Los otros 101 son https al mismo host y al mismo directorio.

    Se sube el esquema y QUEDA ESCRITO en el manifiesto. Arreglarlo en
    silencio seria borrar un hecho de la fuente, que es el producto.

    Si el host fuera OTRO no se toca: eso no es una errata de tipeo, es un
    link a un tercero, y subirle el esquema seria inventar una URL. En ese
    caso queda como esta y muere en el candado de bajar() -> _exigir_https(),
    que lo deja como hueco fechado con el motivo escrito.

    Las dos piezas son hermanas y hacen falta las dos: esta CORRIGE y deja
    constancia; aquella PROHIBE y no corrige nada.
    """
    if not (url or "").lower().startswith("http://"):
        return url, False
    mu, mb = HOST.match(url), HOST.match(base or "")
    if not mu or not mb or mu.group(1).lower() != mb.group(1).lower():
        return url, False
    return "https://" + url[len("http://"):], True


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


def etiquetar_titulos_estructura(pagina, base):
    """Un link suelto arriba + una tabla de tres filas abajo. Ninguno trae periodo.

    LA PAGINA, TAL CUAL ESTA AL 7-sep-2026

        <a> suelto      estructura_financiera_titulos_publicos_31-07-26.xlsx
        <h4> Cupones, precios tecnicos y coeficientes PG
        tabla  "Coeficientes de pago de PG - Desde enero de 2019 en adelante"
               "Coeficientes de pago de PG - Desde enero de 2018 hasta enero de 2019"
               "Cupones Titulos Publicos Nacionales"

    POR QUE LOS CUATRO SON "vigente"

        Ninguna etiqueta dice un periodo. Las dos de PG dicen una COBERTURA
        ("desde enero de 2019 en adelante"), que no es lo mismo: describen
        desde cuando aplica el archivo, no de que mes es el dato.

        El de estructura trae 31-07-26 en el NOMBRE. Es tentador usarlo de
        periodo y esta prohibido: la pagina se modifico el 25-ago-2026 y nadie
        declaro si ese numero es la fecha del dato o la de publicacion. Es el
        mismo reconocimiento pendiente que tuvo el {mes} del INDEC, donde la
        respuesta resulto ser "mes de publicacion" y no la que parecia. La
        fecha queda en fecha_en_el_nombre, rotulada como leida de la URL.

        Con periodo "vigente" y clave estable, cada edicion nueva entra como
        objeto nuevo con su sello: la SERIE DE OBJETOS es la serie de
        vintages. No se pierde nada por no ponerle nombre al periodo.

    LA COLISION QUE HABRIA HABIDO, y como se evita
        Las dos filas de PG empiezan igual. Con producto = slug(etiqueta, 12)
        las dos daban "coeficientes" y la segunda pisaba a la primera — el
        mismo modo de falla que las dos familias de Hacienda en la 7a sesion.
        Por eso el producto sale de los AÑOS de la etiqueta: pg2019 y
        pg2018a2019. Si una fila de PG no trae año, no se adivina: alarma.

    LO QUE NO SE ARCHIVA, SUENA
        Cualquier fila que no sea coeficientes ni cupones, y cualquier link
        suelto cuyo nombre no diga "estructura", cae como etiqueta sin
        resolver. Si Finanzas agrega un archivo, lo mira un humano.
    """
    etiqueta, primera, salida = None, True, []
    for tipo, a, b in eventos(pagina):
        if tipo in ("h", "fila"):
            # Un encabezado o una fila nueva cortan el contexto. Sin esto, un
            # link suelto DESPUES de la tabla se quedaria con la etiqueta de
            # la ultima fila, que es archivar un archivo con el nombre de otro.
            etiqueta, primera = None, True
        elif tipo == "celda":
            if primera:
                etiqueta, primera = b, False
        elif tipo == "link":
            ext = _ext_de(a)
            if ext not in EXTENSIONES:
                continue
            url = _absoluta(a, base)
            if not url:
                continue
            nombre = url.rsplit("/", 1)[-1].lower()
            # LA ETIQUETA SE CONSUME, y esto lo encontro la prueba.
            #
            # eventos() no emite cierres: no hay evento </tr> ni </table>. Un
            # link suelto DESPUES de la tabla se quedaba con la etiqueta de la
            # ultima fila ("Cupones Titulos Publicos Nacionales") y, como esa
            # etiqueta contiene "cupon", lo archivaba como cupones. Un archivo
            # con el nombre de otro, sellado. Resetear en "h" y "fila" llega
            # tarde, y agregarle cierres al scanner tocaria las seis fuentes.
            #
            # En esta pagina cada fila tiene exactamente UN link (verificado
            # sobre las cuatro). Si algun dia una fila trae dos, el segundo cae
            # como etiqueta sin resolver y suena la alarma: se pierde una
            # captura, no se archiva una mentira.
            # Se consume el ARRASTRE, no el dato: `etiq` sigue viajando al
            # manifiesto. Borrar la etiqueta del item seria tirar la evidencia
            # de que fue la fuente la que puso ese texto.
            etiq, etiqueta = etiqueta, None
            norm = (etiq or "").lower()
            anios = re.findall(r"(?:19|20)\d{2}", norm)

            if "coeficiente" in norm:
                # El producto sale de los años de la ETIQUETA, no del nombre.
                fam, prod = ("Coeficientes PG",
                             "pg" + "a".join(anios)) if anios else (None, None)
            elif "cupon" in norm or "cupón" in norm:
                fam, prod = "Titulos publicos", "cupones"
            elif etiq is None and "estructura" in nombre:
                # Link suelto: dos señales. Sin etiqueta Y el nombre lo dice.
                fam, prod = "Titulos publicos", "estructura"
            else:
                fam, prod = None, None

            it = {
                "familia": fam or "(sin familia)",
                "producto": prod or "archivo",
                "etiqueta": etiq,
                "anio": None,
                "orden": None,
                "periodo": None,
                "periodo_incierto": True,
                "url": url,
                "ext": ext,
            }
            if fam:
                it.update(periodo="vigente", periodo_incierto=False,
                          orden=1, anio=0)
                m = re.search(r"(\d{2}-\d{2}-\d{2,4})", nombre)
                if m:
                    it["fecha_en_el_nombre"] = m.group(1)
            if _roto(a):
                it["href_roto_en_la_fuente"] = True
            salida.append(it)
    return salida


FECHA_CELDA = re.compile(r"^\s*(\d{2})-(\d{2})-((?:19|20)\d{2})\s*$")

# Cierre de trimestre -> numero de trimestre. Un mes que no sea 3/6/9/12 no es
# un cierre y no se adivina: queda incierto.
CIERRE_TRIM = {3: 1, 6: 2, 9: 3, 12: 4}

# "IV Trimestre" / "I Semestre" dentro del texto del producto.
UNIDAD = re.compile(r"\b(IV|III|II|I)\b\s*(trimestre|semestre)", re.I)


def _producto_deuda_ant(txt):
    """El producto CANONICO, no el texto crudo. Devuelve (producto, canonico?).

    LO ENCONTRO EL TEST, NO LA LECTURA — y es el mismo modo de falla que el
    slug de los coeficientes PG, dado vuelta.
        La celda dice "Datos Deuda Publica IV Trimestre 2018". Si eso se usa
        como producto, el PERIODO viaja adentro del nombre del producto y cada
        fila se vuelve su propio grupo: seleccionar() devolvia 89 de 102 en vez
        de 9, y la ventana dejaba de agrupar nada.

    El periodo ya vive en su campo. El producto tiene que ser lo que se repite
    a lo largo de los años, o la serie no se puede seguir.

    Es una lista corta escrita a mano, como ALIAS_MES, no una heuristica.
    Clasifica las 102 filas reales del 7-sep-2026, con cero sin reconocer.

    Y de yapa CORRIGE UNA DERIVA DE LA FUENTE: entre 2007 y 2018 el mismo
    producto se escribio "Base de Datos de Deuda Publica" y "Base de dato de
    Deuda Publica". Sin canonizar serian dos series distintas para el mismo
    objeto. El texto crudo de la fuente se conserva entero en `etiqueta`.
    """
    e = (txt or "").strip().lower()
    if "avance preliminar" in e:    return "avance preliminar", True
    if e.startswith("presentaci"):  return "presentacion grafica", True
    if e.startswith("base de dat"): return "base de datos", True
    if e.startswith("informe de"):  return "informe", True
    if e.startswith("datos deuda"): return "datos", True
    # Producto nuevo o reescrito: NO se fuerza a ninguno de los cinco. Se pasa
    # el texto tal cual y se marca, que es lo mismo que hace el modulo con una
    # etiqueta de periodo que no resuelve.
    return (txt or "").strip() or "archivo", False


def etiquetar_deuda_ant(pagina, base):
    """Datos Anteriores (2007-2018): la FECHA en la primera celda, el PRODUCTO
    en la segunda. AL REVES que la trimestral vigente.

    POR QUE NO SIRVE etiquetar_finanzas()
        En la pagina vigente el año vive en un <h5>, la primera celda es el
        trimestre y el PRODUCTO es el texto del link. Aca el texto del link es
        "Descargar" en las 102 filas: no dice nada.

        Y hay 16 filas donde la etiqueta del producto NO declara periodo —
        quince "Informe de Deuda Publica" a secas, identicas entre si, mas un
        "Datos Deuda Publica" pelado. Con el etiquetador vigente esas 16
        quedarian periodo_incierto y NUNCA entrarian a una corrida normal.
        Contado sobre las 102 filas reales el 7-sep-2026.

    DE DONDE SALE EL PERIODO
        De la PRIMERA CELDA, que trae dd-mm-aaaa en las 102 de 102, y cuyo mes
        es cierre de trimestre en las 102 de 102. Sigue valiendo la regla del
        modulo: el periodo sale de la PAGINA, nunca del nombre del archivo.
        La primera celda es texto de la pagina.

    LAS DOS SEÑALES
        Medido: la etiqueta declara periodo en 86 de 102, y en las 86 COINCIDE
        con la celda. Cero contradicciones. Las seis que dicen "Semestre"
        tampoco contradicen: "I Semestre 2015" cae el 30-06-2015, que es Q2.
        La fuente no se equivoco — cambio la UNIDAD, y eso se guarda en
        unidad_declarada en vez de tirarse.

        La regla queda escrita para el futuro, aunque hoy no dispare: si la
        etiqueta declara un periodo que NO es el de la celda, hay conflicto
        entre dos señales independientes y el item queda INCIERTO. No se
        archiva con el periodo de una de las dos: eso seria elegir a dedo.

    EL LINK SUELTO
        eventos() no emite cierres: no hay evento </tr> ni </table>. Un link
        despues de la tabla se quedaria con la fecha y el producto de la
        ultima fila — el bug que el test encontro en titulos_estructura.
        Aca se CONSUME la fila al emitir: una fila vale por un link. Un link
        con extension de archivo y sin fila detras sale como incierto, para
        que lo vea un humano, no se descarta en silencio.
    """
    celdas, salida = [], []
    for tipo, a, b in eventos(pagina):
        if tipo == "fila":
            celdas = []
        elif tipo == "celda":
            celdas.append(b or "")
        elif tipo == "link":
            ext = _ext_de(a)
            if ext not in EXTENSIONES:
                continue
            url = _absoluta(a, base)
            if not url:
                continue
            url, esquema = _a_https_mismo_host(url, base)

            m = FECHA_CELDA.match(celdas[0]) if celdas else None
            crudo = celdas[1].strip() if len(celdas) > 1 else ""
            producto, canonico = _producto_deuda_ant(crudo)

            if not m:
                # Sin fecha en la primera celda no hay periodo posible. No se
                # adivina y no se tira: queda para que lo mire un humano.
                salida.append({
                    "familia": "Deuda publica trimestral", "producto": producto,
                    "etiqueta": crudo or None,
                    "anio": None, "orden": None, "periodo": None,
                    "periodo_incierto": True, "url": url, "ext": ext,
                })
                continue

            dia, mes, anio = int(m.group(1)), int(m.group(2)), int(m.group(3))
            tr = CIERRE_TRIM.get(mes)

            it = {
                "familia": "Deuda publica trimestral",
                "producto": producto,
                # La etiqueta es el texto CRUDO del producto tal como lo
                # escribio la fuente. Es la evidencia: ahi se ve la deriva de
                # "Base de Datos" a "Base de dato" y el "Semestre" de 2014-15.
                # La celda de fecha va aparte, en fecha_en_la_celda.
                "etiqueta": crudo or None,
                "anio": anio,
                "orden": tr,
                "periodo": f"{anio}-Q{tr}" if tr else None,
                "periodo_incierto": tr is None,
                "url": url,
                "ext": ext,
                "fecha_en_la_celda": f"{anio:04d}-{mes:02d}-{dia:02d}",
            }

            if not canonico:
                # No es un hueco: se captura igual. Pero queda dicho, porque un
                # producto nuevo en una pagina congelada en 2018 es noticia.
                it["producto_no_canonico"] = True

            u = UNIDAD.search(crudo)
            if u:
                n = TRIMESTRES[u.group(1).lower()]
                unidad = u.group(2).lower()
                # I Semestre -> Q2, II Semestre -> Q4.
                q = n if unidad == "trimestre" else n * 2
                it["unidad_declarada"] = unidad
                if tr is not None and q != tr:
                    # Dos señales independientes en conflicto. No se elige.
                    it["periodo"] = None
                    it["orden"] = None
                    it["periodo_incierto"] = True
                    it["conflicto_celda_vs_etiqueta"] = (
                        f"celda={anio}-Q{tr} · etiqueta={u.group(0)}")

            if _roto(a):
                it["href_roto_en_la_fuente"] = True
            if esquema:
                # La fuente lo publico en HTTP plano. Lo subimos nosotros.
                # No es url_final: eso es una redireccion de la fuente.
                it["esquema_corregido_por_guarismo"] = "http->https"
            salida.append(it)
            celdas = []          # la fila se consume: un link por fila
    return salida


# Solo cuenta lo que vive en el archivo de informes. Un PDF del menu o del pie
# no es un informe y no tiene que sonar como etiqueta sin resolver.
SAGYP_ARCHIVOS = "/_archivos/estimaciones/"

SAGYP_EVENTO = re.compile(
    r"""(?P<agenda>\bspan1\b[^>]*>\s*(?P<dia>\d{1,2})\s*</div>\s*
            <div\b[^>]*\bspan2\b[^>]*>\s*(?P<mesn>[^<]+?)\s*</div>)
       |(?P<link><a\b[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<ltxt>.*?)</a>)""",
    re.S | re.I | re.X,
)

SAGYP_PRODUCTO = re.compile(r"\binforme\s+(mensual|semanal)\b", re.I)
SAGYP_FECHA = re.compile(
    r"\bal\s+(\d{1,2})[_/.\-](\d{1,2})[_/.\-]((?:19|20)\d{2})\b", re.I)


def etiquetar_sagyp(pagina, base):
    """Agenda de jueves · el TEXTO DEL LINK dice producto y fecha.

    LA PAGINA, TAL CUAL ESTA AL 15-sep-2026 (?mes=2026-08)
        Jueves 20 Agosto
            <a> Informe Mensual al 20_08_2026
            <a> Informe Semanal al 20_08_2026
        + un <input name="mes" value="2026-08"> que dice de que mes es la hoja.

    DE DONDE SALE CADA COSA
        periodo    la fecha del TEXTO DEL LINK ("al 20_08_2026"). Es el corte
                   que declara la fuente. Nunca del nombre del archivo.
        producto   "mensual" o "semanal", del texto del link, en minuscula.
                   La fuente cambio la mayuscula entre julio y agosto de 2026;
                   sin normalizar serian dos series. El texto crudo queda
                   entero en `etiqueta`.
        agenda     el dia y mes del bloque ("Jueves 20 Agosto") + el año de
                   la hoja. Es la fecha en que la AGENDA ubica el informe.

    ETIQUETA Y AGENDA SON DOS COSAS, Y SI DIFIEREN NO ES UN ERROR
        "al 20_08" es el corte del dato; el bloque es el dia de la agenda. En
        las seis entradas vistas coinciden. Si un dia no coinciden, eso es un
        HECHO de puntualidad, no una etiqueta rota: se archiva igual y queda
        agenda_distinta_de_etiqueta=True en el manifiesto.

    LO QUE NO RESUELVE, SUENA
        Un informe que no diga mensual/semanal, o sin fecha legible, queda
        incierto: no se archiva y aparece en el log para que lo mire un humano.
    """
    m_hoja = re.search(r'name="mes"\s+value="((?:19|20)\d{2})-(\d{2})"', pagina)
    anio_hoja = int(m_hoja.group(1)) if m_hoja else None
    agenda, salida = None, []
    for m in SAGYP_EVENTO.finditer(pagina):
        if m.group("agenda"):
            mes = MESES.get(_limpiar(m.group("mesn")).lower())
            dia = int(m.group("dia"))
            agenda = (f"{anio_hoja:04d}-{mes:02d}-{dia:02d}"
                      if (anio_hoja and mes) else None)
            continue
        a = _href(m.group("href"))
        ext = _ext_de(a)
        if ext not in EXTENSIONES or SAGYP_ARCHIVOS not in a:
            continue
        url = _absoluta(a, base)
        if not url:
            continue
        url, esquema = _a_https_mismo_host(url, base)
        etiqueta = _limpiar(m.group("ltxt"))

        mp = SAGYP_PRODUCTO.search(etiqueta)
        mf = SAGYP_FECHA.search(etiqueta)
        fecha = None
        if mf:
            try:
                fecha = datetime(int(mf.group(3)), int(mf.group(2)),
                                 int(mf.group(1)))
            except ValueError:
                fecha = None

        it = {
            "familia": "Estimaciones agricolas",
            "producto": mp.group(1).lower() if mp else "archivo",
            "etiqueta": etiqueta,
            "anio": fecha.year if fecha else anio_hoja,
            "orden": None,
            "periodo": None,
            "periodo_incierto": True,
            "url": url,
            "ext": ext,
            "fecha_en_la_agenda": agenda,
        }
        if mp and fecha:
            periodo = f"{fecha:%Y-%m-%d}"
            it.update(orden=fecha.month * 100 + fecha.day, periodo=periodo,
                      periodo_incierto=False)
            if agenda and agenda != periodo:
                it["agenda_distinta_de_etiqueta"] = True
        if _roto(a):
            it["href_roto_en_la_fuente"] = True
        if esquema:
            it["esquema_corregido_por_guarismo"] = "http->https"
        salida.append(it)
    return salida


def etiquetar_solo_pagina(pagina, base):
    """La pagina es el objeto. No hay archivos que capturar desde aca.

    Para el calendario, porque no linkea ninguno. (Hasta el 25-sep-2026 la
    usaba tambien la pagina del REM; desde entonces tiene etiquetador propio,
    ver (15).)
    """
    return []


MES3 = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
        "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}


def etiquetar_itcrm_metodologias(pagina, base):
    """<h4> año · el TEXTO DEL LINK es el mes · la EXTENSION dice el producto.

    LA PAGINA, TAL CUAL ESTA AL 15-sep-2026
        <h4>2017</h4>  Marzo (PDF) · Marzo (XLS)
        <h4>2016</h4>  Abril (PDF) · Abril (XLS)

    El producto sale de la extension y no de la etiqueta, porque las dos
    etiquetas de cada año son identicas ("Marzo" y "Marzo"). Con un solo
    producto, el PDF y la planilla del mismo mes escribirian sobre la misma
    clave. La extension no es un periodo: la regla 2 sigue en pie.

    DOS SEÑALES
        El nombre trae _mar_17 / _abr_16. No es fuente del periodo y tampoco
        lo veta: si lo contradice, se anota y suena (regla 10). Hoy coinciden
        los cuatro.
    """
    anio, salida = None, []
    for tipo, a, b in eventos(pagina):
        if tipo == "h":
            anio = int(b) if (a == 4 and re.fullmatch(r"(19|20)\d{2}", b or "")) else None
            continue
        if tipo != "link":
            continue
        ext = _ext_de(a)
        if ext not in EXTENSIONES:
            continue
        url = _absoluta(a, base)
        if not url:
            continue
        url, esquema = _a_https_mismo_host(url, base)
        etiqueta = (b or "").strip()
        mes = MESES.get(etiqueta.lower())
        producto = "metodologia" if ext == "pdf" else ("serie" if ext in ("xls", "xlsx", "csv") else ext)
        ok = bool(anio and mes)
        it = {
            "familia": "Metodologias anteriores ITCRM",
            "producto": producto,
            "etiqueta": etiqueta,
            "anio": anio,
            "orden": mes if ok else None,
            "periodo": f"{anio}-{mes:02d}" if ok else None,
            "periodo_incierto": not ok,
            "url": url,
            "ext": ext,
        }
        nombre = url.rsplit("/", 1)[-1].lower()
        mn = re.search(r"_([a-z]{3})_(\d{2})\.[a-z0-9]+$", nombre)
        if mn:
            it["fecha_en_el_nombre"] = f"{mn.group(1)}_{mn.group(2)}"
            if ok and (MES3.get(mn.group(1)) != mes or 2000 + int(mn.group(2)) != anio):
                # Regla (10): el nombre no veta. Se anota, suena y se guarda
                # con el periodo de la pagina.
                it["conflicto_celda_vs_nombre"] = (
                    f"pagina={anio}-{mes:02d} · nombre={mn.group(1)}_{mn.group(2)}")
        if not url.isascii():
            # Hecho de la fuente: publico el nombre con tilde. Ver (8).
            it["url_no_ascii"] = True
        if _roto(a):
            it["href_roto_en_la_fuente"] = True
        if esquema:
            it["esquema_corregido_por_guarismo"] = "http->https"
        salida.append(it)
    return salida


REM_CORTE = re.compile(r"<br\s*/?>|</?(?:div|p|li|tr|td|h[1-6])\b[^>]*>", re.I)
REM_LINK = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
REM_FIJOS = (
    # (palabra en la etiqueta de la fila, producto). El orden importa:
    # "Metodologia Ranking" tiene que ganar antes que "Ranking".
    ("metodolog", "metodologia"),
    ("consideraci", "consideraciones"),
    ("participante", "participantes"),
    ("listado", "participantes"),
    ("hist", "historico"),
    ("ranking", "ranking"),
)
REM_NOMBRE_FECHA = re.compile(r"-((?:19|20)\d{2})-(\d{2})\.[a-z0-9]+$", re.I)


def etiquetar_rem(pagina, base):
    """La seccion Archivos del REM: una LINEA por fila, separada por <br>.

    LA PAGINA, TAL CUAL ESTA AL 24-sep-2026 (HTML crudo, guardado en R2)
        REM AGOSTO 2026 | <a>PDF</a> | <a>XLSX</a><br />
        REM Consideraciones | <a>PDF</a><br />
        REM Listado de Participantes | <a>PDF</a><br />
        REM Metodología Ranking | <a>PDF</a><br />
        REM Ranking | <a>XLSX</a><br />
        REM Resultados históricos | <a>XLSX</a>

    La etiqueta de la fila es el texto antes del primer "|". Ver (15).
    """
    salida, vistos = [], set()
    for trozo in REM_CORTE.split(pagina):
        links = [m for m in REM_LINK.finditer(trozo)
                 if _ext_de(_href(m.group(1))) in EXTENSIONES]
        if not links:
            continue
        cabeza = _limpiar(trozo[:links[0].start()])
        fila = cabeza.split("|", 1)[0].strip() if "|" in cabeza else ""
        mf = MES_ANIO.search(fila)
        for m in links:
            crudo = _href(m.group(1))
            ext = _ext_de(crudo)
            url = _absoluta(crudo, base)
            if not url or url in vistos:
                continue
            vistos.add(url)
            url, esquema = _a_https_mismo_host(url, base)
            formato = _limpiar(m.group(2))
            it = {
                "familia": "REM", "producto": None,
                "etiqueta": f"{fila} | {formato}" if fila else formato,
                "anio": None, "orden": None, "periodo": None,
                "periodo_incierto": True, "url": url, "ext": ext,
            }
            formato_ok = formato.lower() == ext
            if not formato_ok:
                it["formato_distinto_de_la_extension"] = f"link={formato!r} · url=.{ext}"
            elif mf:
                anio, mes = int(mf.group(2)), MESES[mf.group(1).lower()]
                prod = {"pdf": "informe", "xlsx": "tablas", "xls": "tablas"}.get(ext)
                if prod:
                    it.update(producto=prod, anio=anio, orden=mes,
                              periodo=f"{anio}-{mes:02d}", periodo_incierto=False)
                    nombre = url.rsplit("/", 1)[-1]
                    mn = REM_NOMBRE_FECHA.search(nombre)
                    if mn:
                        it["fecha_en_el_nombre"] = f"{mn.group(1)}-{mn.group(2)}"
                        if (int(mn.group(1)), int(mn.group(2))) != (anio, mes):
                            # Regla (10): el nombre no veta. Se anota y suena.
                            it["conflicto_celda_vs_nombre"] = (
                                f"pagina={anio}-{mes:02d} · nombre={mn.group(1)}-{mn.group(2)}")
            else:
                baja = fila.lower()
                prod = next((p for clave, p in REM_FIJOS if clave in baja), None)
                if prod:
                    it.update(producto=prod, anio=0, orden=1, periodo="vigente",
                              periodo_incierto=False)
            if it["producto"] is None:
                it["producto"] = _slug(fila, 12) or "archivo"
            if _roto(m.group(1)):
                it["href_roto_en_la_fuente"] = True
            if esquema:
                it["esquema_corregido_por_guarismo"] = "http->https"
            salida.append(it)
    return salida


COLOC_FECHA = re.compile(r"^\s*(\d{1,2})-(\d{1,2})-((?:19|20)\d{2})\s*$")
COLOC_NOMBRE = re.compile(r"(?<!\d)(\d{1,2})[-_](\d{1,2})[-_]((?:19|20)?\d{2})(?!\d)")
ANIO_TXT = re.compile(r"\b((?:19|20)\d{2})\b")


def etiquetar_colocaciones(pagina, base):
    """Colocaciones de deuda: FECHA en la primera celda, AÑO en la segunda.

    LA PAGINA, TAL CUAL ESTA AL 17-sep-2026
        31-08-2026 | Operaciones en el año 2026 | colocaciones_31-08-26_preliminar.xlsx
        31-07-2026 | Operaciones en el año 2026 | colocaciones_31-07-26_1.xlsx
        31-12-2025 | Operaciones en el año 2025 | colocaciones_31-12-2025_0.xlsx
        ... un cierre por año hasta 2020

    DE DONDE SALE CADA COSA
        periodo    la fecha de la PRIMERA CELDA (texto de la pagina).
        producto   "anual" si la celda es 31-12; si no, "parcial". Asi la
                   ventana guarda los dos cortes vivos Y los dos ultimos
                   cierres, y un corte parcial reemplazado (preliminar ->
                   definitivo) cae sobre la MISMA clave: es una revision.
        estado     "preliminar" si el NOMBRE lo dice. Es un hecho de la
                   fuente, no un periodo: va a estado_en_el_nombre.

    DOS SEÑALES DE LA PAGINA
        La segunda celda dice "Operaciones en el año AAAA". Si no es el año de
        la celda, hay conflicto entre dos textos de la pagina: INCIERTO.

    EL NOMBRE DEL ARCHIVO (regla 10)
        Trae una fecha en cinco escrituras distintas (31-07-26, 31-12-2025,
        31_12_2021, coloc_31_12_2020_ ...). Si contradice a la celda, se
        anota en conflicto_celda_vs_nombre y el archivo SE GUARDA igual.

    Una fila vale por un link (se consume al emitir), y el mismo link
    repetido cuenta una vez.
    """
    celdas, salida, vistos = [], [], set()
    for tipo, a, b in eventos(pagina):
        if tipo == "fila":
            celdas = []
            continue
        if tipo == "celda":
            celdas.append(b or "")
            continue
        if tipo != "link":
            continue
        ext = _ext_de(a)
        if ext not in EXTENSIONES:
            continue
        url = _absoluta(a, base)
        if not url or url in vistos:
            continue
        vistos.add(url)
        url, esquema = _a_https_mismo_host(url, base)
        fila, celdas = celdas, []
        desc = fila[1].strip() if len(fila) > 1 else ""
        it = {
            "familia": "Colocaciones de deuda", "producto": "archivo",
            "etiqueta": desc or None, "anio": None, "orden": None,
            "periodo": None, "periodo_incierto": True, "url": url, "ext": ext,
        }
        m = COLOC_FECHA.match(fila[0]) if fila else None
        fecha = None
        if m:
            try:
                fecha = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                fecha = None
        if fecha:
            it["fecha_en_la_celda"] = f"{fecha:%Y-%m-%d}"
            it.update(producto="anual" if (fecha.month, fecha.day) == (12, 31) else "parcial",
                      anio=fecha.year, orden=fecha.month * 100 + fecha.day,
                      periodo=f"{fecha:%Y-%m-%d}", periodo_incierto=False)
            ma = ANIO_TXT.search(desc)
            if ma and int(ma.group(1)) != fecha.year:
                it.update(periodo=None, orden=None, periodo_incierto=True,
                          conflicto_celda_vs_etiqueta=(
                              f"celda={fecha:%Y-%m-%d} · descripcion={ma.group(1)}"))
        nombre = url.rsplit("/", 1)[-1]
        mn = COLOC_NOMBRE.search(nombre)
        if mn:
            it["fecha_en_el_nombre"] = mn.group(0)
            if fecha and not it["periodo_incierto"]:
                yy = int(mn.group(3))
                yy = yy + 2000 if yy < 100 else yy
                if (int(mn.group(1)), int(mn.group(2)), yy) != (fecha.day, fecha.month, fecha.year):
                    it["conflicto_celda_vs_nombre"] = (
                        f"celda={fecha:%Y-%m-%d} · nombre={mn.group(0)}")
        if re.search(r"preliminar", nombre, re.I):
            it["estado_en_el_nombre"] = "preliminar"
        if _roto(a):
            it["href_roto_en_la_fuente"] = True
        if esquema:
            it["esquema_corregido_por_guarismo"] = "http->https"
        salida.append(it)
    return salida


def etiquetar_cronograma_licitaciones(pagina, base):
    """El cronograma del año: un PDF VIGENTE, sin periodo que leer.

    Mismo criterio que el calendario de publicaciones de Finanzas: periodo
    "vigente" y clave estable, porque el archivo se REEMPLAZA en el lugar. La
    serie de objetos es la historia de las versiones. El año sale del titulo
    de la pagina ("Cronograma 2026") y va a anio_de_la_pagina.

    Se reconoce por el texto del link ("Descargar cronograma") o por el
    nombre (hoy calendario_prensa_0.pdf). Cualquier otro archivo que aparezca
    queda incierto y suena.
    """
    mt = re.search(r"<title>\s*Cronograma\s+((?:19|20)\d{2})", pagina, re.I)
    anio_pag = int(mt.group(1)) if mt else None
    salida, vistos = [], set()
    for tipo, a, b in eventos(pagina):
        if tipo != "link":
            continue
        ext = _ext_de(a)
        if ext not in EXTENSIONES:
            continue
        url = _absoluta(a, base)
        if not url or url in vistos:
            continue
        vistos.add(url)
        url, esquema = _a_https_mismo_host(url, base)
        etiqueta = (b or "").strip()
        nombre = url.rsplit("/", 1)[-1].lower()
        es = ("cronograma" in etiqueta.lower() or "cronograma" in nombre
              or "calendario" in nombre)
        it = {
            "familia": "Cronograma de licitaciones",
            "producto": "cronograma" if es else (_slug(etiqueta, 12) or "archivo"),
            "etiqueta": etiqueta, "anio": None, "orden": None, "periodo": None,
            "periodo_incierto": True, "url": url, "ext": ext,
        }
        if es:
            it.update(periodo="vigente", periodo_incierto=False, orden=1,
                      anio=anio_pag or 0)
            if anio_pag:
                it["anio_de_la_pagina"] = anio_pag
        if _roto(a):
            it["href_roto_en_la_fuente"] = True
        if esquema:
            it["esquema_corregido_por_guarismo"] = "http->https"
        salida.append(it)
    return salida


NOTICIA = re.compile(
    r"<time\b[^>]*datetime=['\"](\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})['\"]", re.I)
NOTICIA_LINK = re.compile(
    r'<a\b[^>]*href="((?:https://www\.argentina\.gob\.ar)?/noticias/[^"#?]+)"(.*?)</a>',
    re.S | re.I)
NOTICIA_TITULO = re.compile(r"<h3[^>]*>(.*?)</h3>", re.S | re.I)
NOTICIA_TIPO = (
    ("llamado", re.compile(r"\bllamado\b", re.I)),
    ("resultado", re.compile(r"\bresultado\b", re.I)),
    ("conversion", re.compile(r"\bconversi[oó]n\b|\bcanje\b", re.I)),
    ("precio", re.compile(r"\bcomunicaci[oó]n del precio\b", re.I)),
)


def etiquetar_noticias_finanzas(pagina, base):
    """El listado de noticias: cada tarjeta trae FECHA CON HORA, titulo y slug.

    LA TARJETA, TAL CUAL ESTA AL 17-sep-2026
        <a href="/noticias/resultado-de-la-licitacion-...-12">
          <time datetime='2026-09-11 17:18:06'>11 de septiembre de 2026</time>
          <h3>Resultado de la licitacion por efectivo ...</h3>

    DE DONDE SALE CADA COSA
        periodo   la fecha del atributo datetime: dato de la pagina, legible
                  por maquina, no el nombre de la URL.
        hora      hora_en_la_pagina. Es lo que permite medir puntualidad del
                  Tesoro con hora y no solo con dia.
        producto  llamado / resultado / conversion / precio / otra, leido del
                  TITULO. Lo que no es de licitacion entra como "otra": el
                  listado es la evidencia de todo lo que publico Finanzas.
        id        el slug. Hay varias noticias del mismo tipo el mismo dia,
                  asi que el periodo no alcanza para identificar.

    UNA NOTICIA ES UN OBJETO HTML: no tiene extension, y se archiva la pagina
    entera. Los PDF que cuelguen de adentro son otro nivel.

    Sin fecha legible queda incierto: no se archiva y suena.
    """
    salida, vistos = [], set()
    for m in NOTICIA_LINK.finditer(pagina):
        href, cuerpo = m.group(1), m.group(2)
        url = _absoluta(_href(href), base)
        if not url:
            continue
        url, esquema = _a_https_mismo_host(url, base)
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        if slug in vistos:
            continue
        vistos.add(slug)
        mt = NOTICIA.search(cuerpo)
        mh = NOTICIA_TITULO.search(cuerpo)
        titulo = _limpiar(mh.group(1)) if mh else ""
        producto = "otra"
        for nombre, rx in NOTICIA_TIPO:
            if rx.search(titulo):
                producto = nombre
                break
        it = {
            "familia": "Noticias de Finanzas",
            "producto": producto,
            "etiqueta": titulo or None,
            "id": slug,
            "anio": None, "orden": None, "periodo": None, "periodo_incierto": True,
            "url": url, "ext": "html",
        }
        if mt:
            anio, mes, dia = (int(x) for x in mt.group(1).split("-"))
            it.update(anio=anio, orden=mes * 100 + dia, periodo=mt.group(1),
                      periodo_incierto=False,
                      hora_en_la_pagina=f"{mt.group(1)}T{mt.group(2)}")
        if _roto(href):
            it["href_roto_en_la_fuente"] = True
        if esquema:
            it["esquema_corregido_por_guarismo"] = "http->https"
        salida.append(it)
    return salida


# --- WASDE (13) ---
WASDE_EXTENSIONES = ("pdf", "txt", "xls", "xml")
WASDE_FILA = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
WASDE_CELDA_FECHA = re.compile(
    r"<td\b[^>]*class=\"[^\"]*views-field-release-date[^\"]*\"[^>]*>(.*?)</td>",
    re.S | re.I)
WASDE_TIME = re.compile(r"<time\b[^>]*datetime=\"([^\"]+)\"", re.I)
WASDE_LINK = re.compile(
    r'<a\b[^>]*href="([^"]*/release-files/(\d+)/([^"/?#]+))"[^>]*>(.*?)</a>',
    re.S | re.I)
WASDE_FECHA = re.compile(r"^((?:19|20)\d{2})-(\d{2})-(\d{2})")
# El link "View" de cada fila: la pagina de la edicion (14).
WASDE_VIEW = re.compile(
    r'<a\b[^>]*href="([^"]*/publication/[^"]+)"[^>]*>\s*View\s*</a>', re.I)


def etiquetar_wasde(pagina, base):
    """ESMIS: una fila por publicacion · <time> en la celda · 4 formatos.

    Ver (13) en el encabezado. Todo lo que no resuelve queda incierto: no se
    archiva y suena. Un link de release-files que aparezca FUERA de una fila
    con celda de fecha tambien suena: no se identifico, no se archiva.
    """
    salida, vistos = [], set()

    def _item(href, numero, nombre, ltxt, dt_celda, dt_link):
        a = _href(href)
        url = _absoluta(a, base)
        if not url:
            return
        url, esquema = _a_https_mismo_host(url, base)
        if url in vistos:
            return
        vistos.add(url)
        ext = _ext_de(url)
        etiqueta = _limpiar(ltxt)
        prod = etiqueta.rsplit(" - ", 1)[-1].strip().lower() if etiqueta else ""
        raiz = nombre.rsplit(".", 1)[0]
        it = {
            "familia": "WASDE",
            "producto": prod if prod in WASDE_EXTENSIONES else "archivo",
            "etiqueta": etiqueta or None,
            "id": f"{numero}_{raiz}",
            "anio": None, "orden": None, "periodo": None,
            "periodo_incierto": True,
            "url": url, "ext": ext,
        }
        if dt_celda:
            it["datetime_en_la_pagina"] = dt_celda
        mf = WASDE_FECHA.match(dt_celda or "")
        fecha = None
        if mf:
            try:
                fecha = datetime(int(mf.group(1)), int(mf.group(2)),
                                 int(mf.group(3)))
            except ValueError:
                fecha = None
        if dt_link and dt_celda and dt_link != dt_celda:
            it["conflicto_celda_vs_etiqueta"] = f"celda {dt_celda} · link {dt_link}"
        if (fecha and ext in WASDE_EXTENSIONES and prod == ext
                and "conflicto_celda_vs_etiqueta" not in it):
            it.update(anio=fecha.year, orden=fecha.month * 100 + fecha.day,
                      periodo=f"{fecha:%Y-%m-%d}", periodo_incierto=False)
        if _roto(a):
            it["href_roto_en_la_fuente"] = True
        if esquema:
            it["esquema_corregido_por_guarismo"] = "http->https"
        salida.append(it)

    def _edicion(href, dt_celda):
        """La pagina de la edicion (14). Se revisa por contenido, todos los dias."""
        a = _href(href)
        url = _absoluta(a, base)
        if not url:
            return
        url, esquema = _a_https_mismo_host(url, base)
        if url in vistos:
            return
        vistos.add(url)
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        it = {
            "familia": "WASDE", "producto": "edicion", "etiqueta": slug,
            "id": slug, "anio": None, "orden": None, "periodo": None,
            "periodo_incierto": True, "url": url, "ext": "html",
            "revisar_contenido": True,
        }
        if dt_celda:
            it["datetime_en_la_pagina"] = dt_celda
        mf = WASDE_FECHA.match(dt_celda or "")
        if mf:
            try:
                f = datetime(int(mf.group(1)), int(mf.group(2)), int(mf.group(3)))
                it.update(anio=f.year, orden=f.month * 100 + f.day,
                          periodo=f"{f:%Y-%m-%d}", periodo_incierto=False)
            except ValueError:
                pass
        if esquema:
            it["esquema_corregido_por_guarismo"] = "http->https"
        salida.append(it)

    for fila in WASDE_FILA.finditer(pagina):
        cuerpo = fila.group(1)
        mc = WASDE_CELDA_FECHA.search(cuerpo)
        if not mc:
            continue
        mt = WASDE_TIME.search(mc.group(1))
        dt_celda = mt.group(1) if mt else None
        for ml in WASDE_LINK.finditer(cuerpo):
            mtl = WASDE_TIME.search(ml.group(4))
            _item(ml.group(1), ml.group(2), ml.group(3), ml.group(4),
                  dt_celda, mtl.group(1) if mtl else None)
        mv = WASDE_VIEW.search(cuerpo)
        if mv:
            _edicion(mv.group(1), dt_celda)

    # Lo que quedo afuera de una fila con fecha: se lista incierto.
    for ml in WASDE_LINK.finditer(pagina):
        _item(ml.group(1), ml.group(2), ml.group(3), ml.group(4), None, None)
    return salida


ETIQUETADORES = {
    "hacienda": etiquetar_hacienda,
    "finanzas": etiquetar_finanzas,
    "deuda_mens_informes": etiquetar_deuda_mens_informes,
    "deuda_mens_datos": etiquetar_deuda_mens_datos,
    "deuda_mens_calendario": etiquetar_deuda_mens_calendario,
    "titulos_estructura": etiquetar_titulos_estructura,
    "deuda_ant": etiquetar_deuda_ant,
    "sagyp": etiquetar_sagyp,
    "solo_pagina": etiquetar_solo_pagina,
    "rem": etiquetar_rem,
    "itcrm_metodologias": etiquetar_itcrm_metodologias,
    "colocaciones": etiquetar_colocaciones,
    "cronograma_licitaciones": etiquetar_cronograma_licitaciones,
    "noticias_finanzas": etiquetar_noticias_finanzas,
    "wasde": etiquetar_wasde,
}


def seleccionar(items, ventana=VENTANA):
    """Las `ventana` ediciones mas nuevas de cada familia+producto.

    Lo incierto NUNCA entra: no se puede ordenar algo cuyo periodo no se pudo
    leer, y meterlo "por las dudas" es exactamente como se archiva un mes con
    la etiqueta de otro. Queda listado en el manifiesto para que lo vea un
    humano. OJO (corregido el 17-sep-2026): el backfill TAMPOCO lo levanta;
    este comentario decia que si. Por eso existe la regla (10).
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


def paginas_de(fuente, cfg, ahora):
    """Las paginas a mirar de una fuente: [(clave_estado, url, mes)].

    Siete de ocho fuentes son UNA pagina y devuelven exactamente lo de antes:
    la clave f"{fuente}_pagina" y la URL de FUENTES.

    Con paginas_por_mes=N se miran los ultimos N meses, en hora de Buenos
    Aires, cada uno con SU clave. Asi el cambio de mes no se confunde con una
    publicacion, y un reemplazo del informe del mes pasado se sigue viendo.
    El mes sale del reloj de la corrida: se resuelve solo, nadie lo toca.
    """
    fijas = cfg.get("paginas_fijas")
    if fijas:
        # ?page=0 no existe: la primera es la URL pelada.
        return [(f"{fuente}_pagina_{i}",
                 cfg["url"] if i == 0 else f"{cfg['url']}?page={i}", None)
                for i in range(int(fijas))]
    n = cfg.get("paginas_por_mes")
    if not n:
        return [(f"{fuente}_pagina", cfg["url"], None)]
    hoy = ahora.astimezone(ARG)
    anio, mes, salida = hoy.year, hoy.month, []
    for _ in range(int(n)):
        m = f"{anio:04d}-{mes:02d}"
        salida.append((f"{fuente}_pagina_{m}", f"{cfg['url']}?mes={m}", m))
        anio, mes = (anio, mes - 1) if mes > 1 else (anio - 1, 12)
    return salida


def firma_listado(items):
    """sha256 de la LISTA publicada: pares (url, etiqueta), ordenados.

    Incluye los inciertos: un informe nuevo que no resuelve tambien es un
    cambio del listado, y tiene que quedar la pagina de ese dia.
    """
    # Lo que se revisa por contenido (14) tiene gatillo propio y NO entra: si
    # entrara, el listado "cambiaria" el dia que se suma sin que nadie publique.
    pares = sorted(f"{i.get('url')}\t{i.get('etiqueta')}" for i in items
                   if not i.get("revisar_contenido"))
    return hashlib.sha256("\n".join(pares).encode("utf-8")).hexdigest()


def firma_contenido(datos):
    """sha256 del texto visible de <main> + sus links. None si no hay <main>.

    Ver (14). Los atributos que el servidor reescribe en cada render (ids de
    Drupal) quedan afuera; una nota nueva o editada, adentro.
    """
    txt = datos.decode("utf-8", errors="replace")
    m = re.search(r"<main\b.*?</main>", txt, re.S | re.I)
    if not m:
        return None
    cuerpo = re.sub(r"<(script|style)\b.*?</\1>", " ", m.group(0), flags=re.S | re.I)
    hrefs = sorted(set(_href(h) for h in re.findall(r'href="([^"]+)"', cuerpo)))
    firma = _limpiar(cuerpo) + "\n" + "\n".join(hrefs)
    return hashlib.sha256(firma.encode("utf-8")).hexdigest()


def _sin_repetidos(items):
    """El mismo link listado dos veces cuenta una. Solo para fuentes de varias
    paginas: en las de una pagina no se toca nada de lo que ya andaba."""
    vistos, salida = set(), []
    for it in items:
        k = (it.get("url"), it.get("etiqueta"))
        if k not in vistos:
            vistos.add(k)
            salida.append(it)
    return salida


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
    clave = f"{fuente}_{fam}_{it['periodo']}_{prod}"
    # Cuando la fuente publica varios documentos del mismo tipo el mismo dia
    # (las noticias), el periodo no alcanza para identificar: se agrega el id
    # que declara la fuente, que es su slug. Ninguna otra fuente lo usa.
    if it.get("id"):
        clave += "_" + _slug(it["id"], 28)
    return clave


# ---------------------------------------------------------------------------
# Red
# ---------------------------------------------------------------------------

class NoEsta(Exception):
    """El archivo no esta en esa URL. No tiene sentido reintentar."""


class CanalInseguro(Exception):
    """Se pidio sellar algo por un canal no autenticado. No se reintenta."""


def _exigir_https(url, donde):
    """La boveda NUNCA sella por un canal no autenticado. Decision firme.

    POR QUE ACA Y NO EN _absoluta()
        _absoluta() arma URLs; este modulo SELLA. La regla es sobre el canal,
        asi que vive en el unico punto por el que pasan las siete fuentes.
        Puesta en el armador seria saltéable: cualquier etiquetador futuro que
        devuelva una URL sin pasar por ahi se la saltea sin querer.

    POR QUE NO CORRIGE
        Corregir es tarea del etiquetador, que es el unico que puede dejar el
        hecho escrito en el item (esquema_corregido_por_guarismo). Si _bajar_
        subiera el esquema en silencio, la boveda archivaria una URL que la
        fuente nunca publico, sin constancia. Aca solo se dice que no.

        Consecuencia buscada: un http:// a OTRO host, que el etiquetador no
        toca a proposito, muere aca y queda HUECO FECHADO CON EL MOTIVO. Que
        es exactamente lo que tiene que pasar.

    ESTADO AL 7-sep-2026 (contado, no recordado)
        Las siete paginas de FUENTES son https. Y sobre las tres grandes
        —Hacienda 117 links, deuda trimestral vigente 87, informes mensuales
        55— la busqueda de "http://www.argentina.gob.ar" en el HTML dio CERO
        en las tres. El unico caso del modulo era el de Datos Anteriores, uno
        entre 102, y lo corrige su etiquetador antes de llegar hasta aca.

        O sea: hoy este candado no deberia dispararse nunca. Va igual. Una
        regla que solo se cumple porque nadie la probo no es una regla.
    """
    if not (url or "").lower().startswith("https://"):
        raise CanalInseguro(
            f"{donde} en canal no autenticado: {url!r}. La boveda no sella por "
            f"HTTP plano — queda hueco fechado, no se baja.")


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

    Y la guarda de canal: nada que no sea https se baja, ni al pedir ni
    despues de las redirecciones. Ver _exigir_https().
    """
    # Antes del primer byte y FUERA del bucle: un esquema mal no se reintenta,
    # igual que un 404. Reintentar tres veces algo que esta prohibido solo
    # ensucia el log y demora la corrida.
    _exigir_https(url, "URL pedida")

    ultimo = None
    for intento in range(REINTENTOS):
        try:
            r = requests.get(url, headers=UA, timeout=TIMEOUT)

            # LA CADENA ENTERA, NO SOLO EL DESTINO.
            #   requests sigue las redirecciones solo. Un https que rebota por
            #   http y vuelve a https termina con r.url en https, y los bytes
            #   igual viajaron sin autenticar en el medio. Se mira cada salto.
            #   Ya paso una vez en este modulo que una URL anotada redirigiera
            #   a otra (.../datos-mensuales-de-la-deuda/datos), asi que las
            #   redirecciones aca son la norma, no la excepcion.
            for salto in list(r.history) + [r]:
                _exigir_https(getattr(salto, "url", None), "salto de redireccion")

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
        except (NoEsta, CanalInseguro):
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


# Los unicos codigos que significan "todavia no existe", que es CORRECTO en la
# primera corrida. Cualquier otra cosa significa "no pude leer".
NO_EXISTE = ("NoSuchKey", "NoSuchBucket", "404", "NotFound")


def leer_estado(s3, bucket):
    """Ultimo hash visto de cada archivo. Distingue "no existe" de "no puedo leer".

    POR QUE ESTO ABORTA LA CORRIDA
      Un `except Exception: return {}` trata igual dos cosas que no se
      parecen:

        NoSuchKey     -> primera corrida. {} es la respuesta correcta.
        AccessDenied  -> la credencial es valida pero no puede LEER el estado.

      En el segundo caso, devolver {} hace que el modulo trate los
      27 objetos como nuevos, los RE-ARCHIVE, y despues pise el estado
      bueno con uno reconstruido a ciegas.

      Y en este modulo eso es peor que perder bytes: el log diria
      "27 nuevos", que en la tabla de vigilancia significa
      "UN ORGANISMO PUBLICO". Un hipo de credencial se disfrazaria de evento
      de publicacion masiva. Mejor un workflow en rojo que una noticia falsa.

      Paso de verdad el 8-sep-2026 en boveda_privadas.py, con un token cuya
      fecha de INICIO todavia no habia llegado.
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
            print(f"[mecD] sin estado previo ({codigo}) — se trata todo como nuevo")
            return {}
        raise RuntimeError(
            f"no se pudo LEER el estado ({codigo}). La credencial responde pero "
            f"no entrega {ESTADO}. Se aborta a proposito: seguir re-archivaria "
            f"todo y pisaria el estado bueno."
        ) from e


# ---------------------------------------------------------------------------
# Corrida
# ---------------------------------------------------------------------------

def _url_ascii(url):
    """La URL para los METADATOS de R2, que solo aceptan ASCII. Ver (8).

    Si ya es ASCII se devuelve identica: las ocho fuentes anteriores al
    15-sep-2026 no cambian ni un byte. Si no, se codifica en UTF-8 con %,
    que es exactamente lo que manda requests al pedirla.
    """
    url = url or ""
    if url.isascii():
        return url
    return quote(url, safe=":/?#[]@!$&'()*+,;=%~")


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
                  "url": _url_ascii(url)[:900], "capturado-utc": capturado},
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

    try:
        estado = leer_estado(s3, bucket)
    except Exception as e:
        print(f"[mecD] R2 fallo (estado): {e}")
        return 3
    entradas, ok, nuevos, huecos, inciertos = [], 0, 0, 0, 0

    for fuente, cfg in sorted(fuentes.items()):
        # --- 1. la(s) pagina(s), que prueban que URL era la oficial hoy ---
        #
        # Siete de ocho fuentes son UNA pagina: este bucle da una sola vuelta
        # y hace exactamente lo de antes. SAGyP son dos (mes en curso y mes
        # anterior), cada una con su clave de estado. Ver paginas_de().
        items, paginas_ok, ent_pag = [], 0, None
        for cl_pag, url_pag, mes_pag in paginas_de(fuente, cfg, ahora):
            capturado = datetime.now(timezone.utc).isoformat(timespec="seconds")
            try:
                pagina_b, headers, url_final = bajar(url_pag, "html")
            except Exception as e:
                huecos += 1
                entradas.append({"fuente": fuente, "url": url_pag,
                                 "capturado_utc": capturado,
                                 "error": f"{type(e).__name__}: {e}"})
                print(f"   [mecD] {fuente:<22} HUECO (pagina) — {type(e).__name__}: {e}")
                continue

            pagina = pagina_b.decode("utf-8", errors="replace")
            # El etiquetador corre ANTES de decidir si la pagina cambio: el
            # gatillo por listado necesita la lista. Es una funcion pura de la
            # pagina; correrla antes no cambia nada para las otras fuentes.
            try:
                items_pag = ETIQUETADORES[cfg["etiquetador"]](pagina, url_pag)
                error_etq = None
            except Exception as e:
                # Ver (11): una fuente rota no tira abajo a las demas.
                items_pag, error_etq = [], f"{type(e).__name__}: {e}"
                huecos += 1
                print(f"   [mecD] {fuente:<22} HUECO (etiquetador) — {error_etq}")
                if cfg.get("gatillo") == "listado":
                    entradas.append({"fuente": fuente, "url": url_pag,
                                     "capturado_utc": capturado,
                                     "error": f"etiquetador: {error_etq}"})
                    continue
            sha_pag = hashlib.sha256(pagina_b).hexdigest()
            prev = estado.get(cl_pag) or {}
            previo = prev.get("sha256")
            mtime = modified_time(pagina)
            firma = (firma_listado(items_pag)
                     if cfg.get("gatillo") == "listado" else None)

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
            #
            # GATILLO POR LISTADO (15-sep-2026): si la fuente lo declara, manda
            # la firma de la lista publicada, no el hash ni el modified_time.
            # Un servidor que reserializa no la mueve; un informe nuevo, si.
            prev_mt = prev.get("article_modified_time")
            if previo is None:
                cambio_pag, motivo = True, "primera captura"
            elif firma is not None:
                cambio_pag = (firma != prev.get("firma_listado"))
                motivo = "listado (la pagina no declara modified_time)"
            elif mtime:
                cambio_pag = (mtime != prev_mt)
                motivo = "article_modified_time"
            else:
                # Sin fecha declarada no queda otra que el hash, y se dice.
                cambio_pag = (sha_pag != previo)
                motivo = "hash (la pagina no declara modified_time)"
            reserializa = (sha_pag != previo) and not cambio_pag

            ent_pag = {
                "fuente": fuente, "tipo": "pagina", "url": url_pag,
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
            if error_etq:
                ent_pag["error_etiquetador"] = error_etq
            if firma is not None:
                ent_pag["firma_listado"] = firma
                ent_pag["mes_listado"] = mes_pag
                ent_pag["claves_listadas"] = len(items_pag)
            if cambio_pag:
                ent_pag["objeto"] = guardar(s3, bucket, sello, ahora, cl_pag,
                                            pagina_b, "html", url_pag,
                                            capturado, sha_pag, seco)
                if not seco:
                    estado[cl_pag] = {"sha256": sha_pag, "objeto": ent_pag["objeto"],
                                      "article_modified_time": mtime,
                                      "visto_utc": capturado}
                    if firma is not None:
                        estado[cl_pag]["firma_listado"] = firma
                nuevos += 1
                if firma is not None:
                    print(f"   [mecD] {cl_pag:<30} {'NUEVO  →' if previo else 'PRIMERA→'} "
                          f"{len(pagina_b):>8} bytes  claves={len(items_pag)}  ({motivo})")
                else:
                    print(f"   [mecD] {cl_pag:<30} {'NUEVO  →' if previo else 'PRIMERA→'} "
                          f"{len(pagina_b):>8} bytes  mod={mtime}  ({motivo})")
            else:
                ent_pag["objeto"] = prev.get("objeto")
                if firma is not None:
                    extra = "  [reserializa: mismo listado, otro hash]" if reserializa else ""
                    print(f"   [mecD] {cl_pag:<30} sin cambios {len(pagina_b):>8} bytes  "
                          f"claves={len(items_pag)}{extra}")
                else:
                    extra = "  [reserializa: mismo mod, otro hash]" if reserializa else ""
                    print(f"   [mecD] {cl_pag:<30} sin cambios {len(pagina_b):>8} bytes  "
                          f"mod={mtime}{extra}")
            ok += 1
            entradas.append(ent_pag)
            items.extend(items_pag)
            paginas_ok += 1

        if not paginas_ok:
            continue
        if cfg.get("paginas_por_mes") or cfg.get("paginas_fijas"):
            # La misma noticia puede aparecer en dos paginas del listado si
            # publican algo entre un pedido y el otro. Cuenta una vez.
            items = _sin_repetidos(items)

        # --- 2. los archivos ---
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
        for r in items:
            if r.get("conflicto_celda_vs_nombre") and not r["periodo_incierto"]:
                print(f"   [mecD] {fuente:<22} ⚠ la fecha del nombre no es la de la "
                      f"pagina ({r['conflicto_celda_vs_nombre']}): se guarda con la "
                      f"de la pagina — {r['url'].rsplit('/', 1)[-1]}")

        # CADENCIA "archivo": la pagina se mira todos los dias, los archivos
        # NO. Una pagina que solo guarda historia cerrada no tiene ediciones
        # nuevas que ventanear; correr seleccionar() ahi baja los mismos
        # objetos todos los dias, para siempre, contra un servidor del Estado.
        #
        # El etiquetador se corre IGUAL: si la fuente escribe una etiqueta
        # nueva que no resuelve, eso tiene que sonar el mismo dia.
        #
        # Y se DICE en el log y se ESCRIBE en el manifiesto. Un "0 a capturar"
        # sin explicacion es indistinguible de un bug, y un manifiesto que no
        # cuenta lo que omitio no prueba que la omision fue deliberada.
        archivo_cerrado = cfg.get("cadencia") == "archivo" and not backfill
        if archivo_cerrado:
            elegidos = []
            # ent_pag ya esta en `entradas`, pero es el mismo objeto: lo que se
            # agregue aca viaja al manifiesto.
            ent_pag["cadencia"] = "archivo"
            ent_pag["archivos_listados"] = len(items)
            ent_pag["archivos_omitidos_en_normal"] = len(items)
        elif cfg.get("captura") == "nuevas" and not backfill:
            # Una noticia no se revisa: nace y queda. Se baja lo que todavia
            # no esta en el estado; el resto ya esta archivado con su sello.
            # Las paginas de edicion del WASDE (14) se revisan todos los dias.
            elegidos = [i for i in items if not i["periodo_incierto"]
                        and (i.get("revisar_contenido")
                             or clave_de(fuente, i) not in estado)]
        else:
            elegidos = ([i for i in items if not i["periodo_incierto"]]
                        if backfill else seleccionar(items))
        nota = "  [cadencia archivo: los archivos van solo con --backfill]" \
            if archivo_cerrado else ""
        print(f"   [mecD] {fuente:<22} {len(items)} links · "
              f"{len(raros)} sin resolver · {len(elegidos)} a capturar{nota}")

        for it in elegidos:
            capturado = datetime.now(timezone.utc).isoformat(timespec="seconds")
            clave = clave_de(fuente, it)
            try:
                datos, h2, ufin = bajar(it["url"], it["ext"])
                sha = hashlib.sha256(datos).hexdigest()
                previo = (estado.get(clave) or {}).get("sha256")
                cambio = sha != previo
                firma_c = None
                if it.get("revisar_contenido"):
                    # (14): manda el contenido visible, no el hash.
                    firma_c = firma_contenido(datos)
                    if firma_c is not None and previo is not None:
                        cambio = firma_c != (estado.get(clave) or {}).get("firma_contenido")

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
                              "anio_en_el_nombre", "fecha_en_el_nombre",
                              "esquema_corregido_por_guarismo",
                              "unidad_declarada", "fecha_en_la_celda", "producto_no_canonico",
                              "conflicto_celda_vs_etiqueta",
                              "fecha_en_la_agenda", "agenda_distinta_de_etiqueta",
                              "url_no_ascii", "conflicto_celda_vs_nombre",
                              "estado_en_el_nombre", "anio_de_la_pagina",
                              "hora_en_la_pagina", "id", "datetime_en_la_pagina"):
                    if extra in it:
                        entrada[extra] = it[extra]
                if ufin and ufin != it["url"]:
                    entrada["url_final"] = ufin
                if it.get("revisar_contenido"):
                    entrada["firma_contenido"] = firma_c
                    entrada["motivo_cambio"] = ("contenido visible" if firma_c
                                                else "hash (la pagina no tiene <main>)")
                    entrada["reserializa"] = (previo is not None and sha != previo
                                              and not cambio)

                if cambio:
                    entrada["objeto"] = guardar(s3, bucket, sello, ahora, clave,
                                                datos, it["ext"], it["url"],
                                                capturado, sha, seco)
                    if not seco:
                        estado[clave] = {"sha256": sha, "objeto": entrada["objeto"],
                                         "visto_utc": capturado}
                        if firma_c is not None:
                            estado[clave]["firma_contenido"] = firma_c
                    nuevos += 1
                    marca = "NUEVO  →" if previo else "PRIMERA→"
                    print(f"   [mecD] {clave:<30} {marca} {len(datos):>9} bytes  {sha[:12]}…")
                else:
                    entrada["objeto"] = (estado.get(clave) or {}).get("objeto")
                    extra = ("  [reserializa: mismo contenido, otro hash]"
                             if it.get("revisar_contenido") and sha != previo else "")
                    print(f"   [mecD] {clave:<30} sin cambios {len(datos):>9} bytes  {sha[:12]}…{extra}")
                ok += 1
            except Exception as e:
                # MISMO DEFECTO QUE SE CORRIGIO HOY EN LA RAMA QUE ANDA, EN
                # LA RAMA QUE FALLA: el hueco tambien se armaba campo por
                # campo, sin los hechos de la fuente. Un link roto o publicado
                # en HTTP plano es JUSTO el que tiene mas chance de fallar, y
                # era justo el caso en el que el hecho se perdia.
                entrada = {"fuente": fuente, "tipo": "archivo", "archivo": clave,
                           "periodo": it["periodo"], "url": it["url"],
                           "capturado_utc": capturado,
                           "error": f"{type(e).__name__}: {e}"}
                for extra in ("href_roto_en_la_fuente", "etiqueta_corregida",
                              "anio_en_el_nombre", "fecha_en_el_nombre",
                              "esquema_corregido_por_guarismo",
                              "conflicto_celda_vs_etiqueta",
                              "fecha_en_la_agenda", "agenda_distinta_de_etiqueta",
                              "url_no_ascii", "conflicto_celda_vs_nombre",
                              "estado_en_el_nombre", "anio_de_la_pagina",
                              "hora_en_la_pagina", "id", "datetime_en_la_pagina"):
                    if extra in it:
                        entrada[extra] = it[extra]
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
