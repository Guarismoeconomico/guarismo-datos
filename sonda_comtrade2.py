#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sonda_comtrade2.py — SEGUNDA SONDA. Solo lectura. No escribe un byte.

TRES TRABAJOS, en orden de importancia:

  A. AUDITAR MI PROPIO FILTRO.
     boveda_comtrade.py pide con publishedDateFrom=2000-01-01 para "traer
     todo". Si un dataset no tiene lastReleased, o lo tiene fuera de rango,
     ESE FILTRO LO ESTA BORRANDO — y no solo para India.
     Se compara CON filtro contra SIN filtro, sobre un pais que si devuelve
     datos. Si los numeros no coinciden, tengo un bug en el corazon del modulo.

  B. DIAGNOSTICAR EL CERO DE INDIA.
     Tres hipotesis, y cada llamada separa una:
       A) reporta bajo otra clasificacion   -> probar SITC
       B) el filtro de fecha lo borra       -> probar sin filtro
       C) dejo de reportar de verdad        -> si todo da cero

  C. COSECHAR LO QUE YA ESTA A LA VISTA.
     Sobre los datos que ya se piden igual: tasa de revision por pais,
     brecha entre primera y ultima publicacion, y una revision de los
     timestamps que la fuente publica de su propio pipeline.

Nada de esto escribe. Ni R2, ni estado, ni Supabase.
"""

import os
import sys
import time
from datetime import datetime

import requests

BASE = "https://comtradeapi.un.org"
TIMEOUT = 90
PAUSA = 1.5
MAX_LLAMADAS = 48
DESDE = "2000-01-01"

REPORTERS = {
    "32": "Argentina",
    "76": "Brasil",
    "156": "China",
    "842": "EEUU",
    "356": "India",
    "704": "Vietnam",
}

_llamadas = 0


def _p(t=""):
    print(t, flush=True)


def _titulo(t):
    _p()
    _p("=" * 74)
    _p(f"[sonda2] {t}")
    _p("=" * 74)


def pedir(ruta, params=None, con_clave=True):
    global _llamadas
    if _llamadas >= MAX_LLAMADAS:
        raise RuntimeError("presupuesto agotado")
    _llamadas += 1
    headers = {"Accept": "application/json"}
    if con_clave:
        k = os.getenv("COMTRADE_KEY", "").strip()
        if not k:
            raise RuntimeError("falta COMTRADE_KEY")
        headers["Ocp-Apim-Subscription-Key"] = k
    r = requests.get(BASE + ruta, headers=headers, params=params, timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    d = r.json()
    time.sleep(PAUSA)
    return d


def getda(cod, freq, cl="HS", con_fecha=True):
    """Devuelve (filas, error_o_None)."""
    params = {"reporterCode": cod}
    if con_fecha:
        params["publishedDateFrom"] = DESDE
        params["publishedDateTo"] = datetime.utcnow().strftime("%Y-%m-%d")
    try:
        d = pedir(f"/data/v1/getDa/C/{freq}/{cl}", params)
    except Exception as e:
        return None, str(e)
    return (d.get("data") or []), None


def _fecha(s):
    """La fuente manda hasta 7 decimales de segundo. Python acepta 6."""
    if not s:
        return None
    s = str(s).strip()
    if "." in s:
        cabeza, cola = s.split(".", 1)
        s = cabeza + "." + cola[:6]
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# A. AUDITORIA DEL FILTRO
# ---------------------------------------------------------------------------

def auditar_filtro():
    _titulo("A. ¿MI FILTRO DE FECHAS ESTA BORRANDO DATOS?")
    _p("Si CON filtro y SIN filtro no dan lo mismo, el modulo tiene un bug.")
    _p()

    for cod, nombre in [("76", "Brasil"), ("32", "Argentina")]:
        con, e1 = getda(cod, "M", con_fecha=True)
        sin, e2 = getda(cod, "M", con_fecha=False)
        if e1 or e2:
            _p(f"[sonda2] {nombre}: no se pudo comparar · con={e1} · sin={e2}")
            continue

        n_con, n_sin = len(con), len(sin)
        veredicto = "IGUALES ✔" if n_con == n_sin else f"DIFIEREN ✗ faltan {n_sin - n_con}"
        _p(f"[sonda2] {nombre}/M · con filtro: {n_con:5d} · sin filtro: {n_sin:5d} · {veredicto}")

        if n_con != n_sin:
            claves_con = {str(d.get("datasetCode")) for d in con}
            perdidos = [d for d in sin if str(d.get("datasetCode")) not in claves_con]
            _p(f"[sonda2]    LOS QUE EL FILTRO TIRA ({len(perdidos)}), primeros 5:")
            for d in perdidos[:5]:
                _p(f"[sonda2]      periodo={d.get('period')} "
                   f"first={d.get('firstReleased')} last={d.get('lastReleased')}")
            sin_last = sum(1 for d in perdidos if not d.get("lastReleased"))
            _p(f"[sonda2]    de esos, SIN lastReleased: {sin_last}")


# ---------------------------------------------------------------------------
# B. INDIA
# ---------------------------------------------------------------------------

CLASIFICACIONES = ["HS", "H0", "H1", "H2", "H3", "H4", "H5", "H6",
                   "S1", "S2", "S3", "S4"]

CONTROL = [("356", "India"), ("76", "Brasil")]


def diagnosticar_india():
    """Matriz clasificacion x pais. Brasil es el CONTROL.

    POR QUE UNA MATRIZ Y NO UNA LISTA
      Barrer India sola no alcanza: una fila de ceros no distingue "India no
      reporta" de "la API no sirve ese codigo a NADIE". Sin control, el cero
      es ambiguo. Es el mismo problema que reporters_en_cero.

    POR QUE FALLO SITC EN LA CORRIDA ANTERIOR
      Porque "SITC" es el nombre de la FAMILIA, no un codigo. La ruta
      /data/v1/getDa/C/{freq}/{cl} espera el codigo de la revision concreta:
      H0..H6 para HS, S1..S4 para SITC.

    SIN filtro de fecha a proposito: es el escenario mas permisivo. Si algo
    existe, con esto aparece.
    """
    _titulo("B. EL CERO DE INDIA — matriz de clasificaciones, con control")
    _p(f"{len(CLASIFICACIONES)} codigos x {len(CONTROL)} paises = "
       f"{len(CLASIFICACIONES) * len(CONTROL)} llamadas, mensual, sin filtro.")
    _p()
    _p(f"{'codigo':8s} {'India':>10s} {'Brasil':>10s}   que declaran los datasets")
    _p("-" * 74)

    matriz = {}
    for cl in CLASIFICACIONES:
        fila = {}
        for cod, nombre in CONTROL:
            filas, err = getda(cod, "M", cl, con_fecha=False)
            fila[nombre] = ("error", err) if err else ("ok", filas)
        matriz[cl] = fila

        def celda(nombre):
            estado, v = fila[nombre]
            if estado == "error":
                return "AGOTADO" if "presupuesto" in str(v) else "ERROR"
            return str(len(v))

        # Que classificationCode se declaran a si mismos los que SI vienen.
        # Es gratis: ya esta en los datos que pedimos igual.
        nota = ""
        for nombre in ("Brasil", "India"):
            estado, v = fila[nombre]
            if estado == "ok" and v:
                declara = sorted({str(d.get("classificationCode")) for d in v})
                nota = f"{nombre}: {','.join(declara[:4])}"
                break
        if not nota:
            estado, v = fila["Brasil"]
            if estado == "error":
                nota = str(v)[:44]

        _p(f"{cl:8s} {celda('India'):>10s} {celda('Brasil'):>10s}   {nota}")

    # -----------------------------------------------------------------------
    # Veredicto. NO se canta si alguna prueba fallo.
    # -----------------------------------------------------------------------
    _p()
    fallidas = [(cl, n, v) for cl, f in matriz.items()
                for n, (e, v) in f.items() if e == "error"]
    agotado = [x for x in fallidas if "presupuesto" in str(x[2])]

    if agotado:
        _p(f"[sonda2] VEREDICTO: INCONCLUSO — se agoto el presupuesto en "
           f"{len(agotado)} celda(s).")
        _p("[sonda2]            Subir MAX_LLAMADAS y repetir. El presupuesto")
        _p("[sonda2]            agotado NO es un cero de la fuente.")
        return
    if fallidas:
        _p(f"[sonda2] VEREDICTO: INCONCLUSO — {len(fallidas)} celda(s) con error "
           f"de la API:")
        for cl, n, v in fallidas[:6]:
            _p(f"[sonda2]            {cl}/{n}: {str(v)[:70]}")
        return

    def total(nombre):
        return sum(len(v) for f in matriz.values()
                   for n, (e, v) in f.items() if n == nombre and e == "ok")

    india, brasil = total("India"), total("Brasil")
    india_cl = [cl for cl, f in matriz.items() if f["India"][1]]
    brasil_cl = [cl for cl, f in matriz.items() if f["Brasil"][1]]

    _p(f"[sonda2] India responde en: {india_cl or 'NINGUNA'}  ({india} datasets)")
    _p(f"[sonda2] Brasil responde en: {brasil_cl or 'NINGUNA'}  ({brasil} datasets)")
    _p()

    if not brasil_cl:
        _p("[sonda2] VEREDICTO: INCONCLUSO — el CONTROL tampoco responde.")
        _p("[sonda2]            Brasil dio datos esta manana bajo HS, asi que")
        _p("[sonda2]            esto apunta a la ruta o a la clave, no a India.")
    elif not india_cl:
        _p("[sonda2] VEREDICTO: hipotesis C — India NO reporta en ninguna de las")
        _p("[sonda2]            12 clasificaciones, mientras el control si.")
        _p("[sonda2]            No es un bug nuestro: es un hecho de la fuente,")
        _p("[sonda2]            y se archiva como tal en reporters_en_cero.")
    elif india_cl == ["HS"]:
        _p("[sonda2] VEREDICTO: India responde SOLO bajo HS. El cero diario")
        _p("[sonda2]            no se explica por la clasificacion.")
    else:
        _p("[sonda2] VEREDICTO: hipotesis A CONFIRMADA — India responde bajo")
        _p(f"[sonda2]            {india_cl}. El modulo tiene que cablearlo.")

    solo_alias = brasil_cl == ["HS"]
    if solo_alias:
        _p()
        _p("[sonda2] NOTA: el control solo responde bajo el alias 'HS'. Los")
        _p("[sonda2]       codigos de revision no se sirven por esta ruta, asi")
        _p("[sonda2]       que la matriz no puede descartar la hipotesis A.")


# ---------------------------------------------------------------------------
# C. COSECHA
# ---------------------------------------------------------------------------

def cosechar():
    _titulo("C. LO QUE YA ESTA A LA VISTA — tasa de revision por pais")
    _p("Un dataset con lastReleased != firstReleased fue REESCRITO por la fuente.")
    _p("La version original no existe en ningun lado.")
    _p()
    _p(f"{'pais':12s} {'datasets':>9s} {'revisados':>10s} {'%':>6s} {'brecha max':>12s}")
    _p("-" * 74)

    guardados = {}
    for cod, nombre in REPORTERS.items():
        filas, err = getda(cod, "M", con_fecha=True)
        if err or not filas:
            _p(f"{nombre:12s} {'—':>9s} {'—':>10s} {'—':>6s} {'—':>12s}")
            continue
        guardados[nombre] = filas

        revisados, brecha_max, peor = 0, 0, None
        for d in filas:
            f1, f2 = _fecha(d.get("firstReleased")), _fecha(d.get("lastReleased"))
            if f1 and f2 and f2 > f1:
                revisados += 1
                dias = (f2 - f1).days
                if dias > brecha_max:
                    brecha_max, peor = dias, d
        pct = 100.0 * revisados / len(filas)
        _p(f"{nombre:12s} {len(filas):9d} {revisados:10d} {pct:5.1f}% {brecha_max:9d} d")
        if peor is not None:
            _p(f"{'':12s}   record: periodo {peor.get('period')} · "
               f"publicado {str(peor.get('firstReleased'))[:10]} · "
               f"reescrito {str(peor.get('lastReleased'))[:10]}")

    # Argentina en detalle: es la que mas nos importa.
    ar = guardados.get("Argentina")
    if ar:
        _p()
        _p("[sonda2] ARGENTINA — las 5 reescrituras mas recientes:")
        conf = [(d, _fecha(d.get("lastReleased"))) for d in ar]
        conf = [(d, f) for d, f in conf if f]
        conf.sort(key=lambda x: x[1], reverse=True)
        for d, f in conf[:5]:
            f1 = _fecha(d.get("firstReleased"))
            marca = "REESCRITO" if (f1 and f > f1) else "original "
            _p(f"[sonda2]    {marca} periodo {d.get('period')} · "
               f"last={str(d.get('lastReleased'))[:19]} · "
               f"registros={d.get('totalRecords')}")
        periodos = sorted(str(d.get("period")) for d in ar)
        _p(f"[sonda2]    rango de periodos: {periodos[0]} → {periodos[-1]}")


# ---------------------------------------------------------------------------
# D. EL PIPELINE DE LA FUENTE
# ---------------------------------------------------------------------------

def revisar_pipeline():
    _titulo("D. LOS TIMESTAMPS QUE LA FUENTE PUBLICA DE SU PROPIO PIPELINE")
    _p("En la sonda 1 vi un registro que TERMINO antes de EMPEZAR.")
    _p("Aca se cuenta sobre los 50: ¿fue uno solo o es sistematico?")
    _p()
    try:
        d = pedir("/data/v1/getLiveUpdate")
    except Exception as e:
        _p(f"[sonda2] no disponible: {e}")
        return
    filas = d.get("data") or []

    invertidos, ok, sinfecha = 0, 0, 0
    ejemplos = []
    for f in filas:
        a, b = _fecha(f.get("startedAt")), _fecha(f.get("completedAt"))
        if not a or not b:
            sinfecha += 1
            continue
        if b < a:
            invertidos += 1
            if len(ejemplos) < 3:
                ejemplos.append(f)
        else:
            ok += 1

    _p(f"[sonda2] de {len(filas)} publicaciones recientes:")
    _p(f"[sonda2]    completedAt DESPUES de startedAt (normal): {ok}")
    _p(f"[sonda2]    completedAt ANTES de startedAt (invertido): {invertidos}")
    _p(f"[sonda2]    sin alguna de las dos fechas: {sinfecha}")
    for f in ejemplos:
        _p(f"[sonda2]      reporter={f.get('reporterCode')} periodo={f.get('period')} "
           f"started={f.get('startedAt')} completed={f.get('completedAt')}")

    estados = {}
    for f in filas:
        e = str(f.get("releaseStatus"))
        estados[e] = estados.get(e, 0) + 1
    _p(f"[sonda2]    releaseStatus presentes: {estados}")

    paises = {}
    for f in filas:
        c = str(f.get("reporterCode"))
        paises[c] = paises.get(c, 0) + 1
    _p(f"[sonda2]    paises distintos en las ultimas 50 publicaciones: {len(paises)}")
    nuestros = [c for c in paises if c in REPORTERS]
    if nuestros:
        _p(f"[sonda2]    de los nuestros aparecen: "
           f"{[REPORTERS[c] + ' x' + str(paises[c]) for c in nuestros]}")
    else:
        _p("[sonda2]    ninguno de los nuestros aparece en las ultimas 50")


def main():
    _p("[sonda2] SEGUNDA SONDA · SOLO LECTURA · no escribe en ningun lado")
    if not os.getenv("COMTRADE_KEY", "").strip():
        _p("[sonda2] falta COMTRADE_KEY. Aborto.")
        return 1

    for etapa in (auditar_filtro, diagnosticar_india, cosechar, revisar_pipeline):
        try:
            etapa()
        except Exception as e:
            _p(f"[sonda2] etapa {etapa.__name__} corto: {type(e).__name__}: {e}")

    _p()
    _p("=" * 74)
    _p(f"[sonda2] FIN · {_llamadas} llamadas de {MAX_LLAMADAS} · nada escrito")
    _p("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
