#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
medir_bcra.py — Medicion HACIA ATRAS de los snapshots del BCRA (mecanica C).
SOLO LECTURA. No escribe un byte en R2, ni en Supabase, ni en ningun lado.

POR QUE EXISTE (premisa de la 21.8-b, 29-oct-2026)
    boveda_bcra.py no tiene estado y corta por pagina corta. Si la API
    devuelve una serie con 0 puntos SIN error, se archiva como OK con n=0, sin
    hueco (l. 347-359 de d44f91fec8c5). Y no compara el rango de cada serie
    contra lo que el propio catalogo declara (primerFechaInformada /
    ultFechaInformada), que se guarda en el mismo snapshot.

    En datos.gob, medir el archivo encontro cuatro veces mas de lo que se veia
    a ojo, y la falla resulto ser otra (valores vacios) de la que se suponia.
    Antes de escribir la guardia del BCRA se mide su archivo con las mismas
    reglas que tendria la guardia.

QUE MIRA, POR CADA SERIE Y CADA SNAPSHOT (en orden de sello)
    Contra la VENTANA de los VENTANA snapshots anteriores con datos de esa
    serie (la misma regla que la guardia de datos.gob, no la historia entera):
      · CERO SIN HUECO       -> n=0 y sin "error" (el agujero conocido)
      · HUECO                -> n=0 con error (registrado: no es falla)
      · n menor              -> menos puntos que el maximo de la ventana
      · desde posterior      -> empieza despues que el minimo de la ventana
      · hasta anterior       -> termina antes que el maximo de la ventana
      · mas vacios           -> mas puntos sin valor que el minimo de la ventana
    Y contra el CATALOGO del mismo snapshot (la premisa de la 21.8-b):
      · hasta de la serie vs ultFechaInformada de su variable
      · desde de la serie vs primerFechaInformada de su variable
    Ademas: huellas (sha256 de la linea) y VAIVEN (vuelve una huella
    anterior), revisiones normales resumidas (cuantos dias, que tan atras),
    el catalogo por dia y la integridad contra el sidecar .sha256.

    Las series del BCRA cambian todos los dias (entra un punto nuevo). Por eso
    NO se imprime la linea de tiempo entera: solo las anomalias, con su dia.
    Es una medicion, no un veredicto.

VARIABLES DE ENTORNO
    R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY   (obligatorias)
    R2_BUCKET                                             (opcional, default guarismo-crudo)

CODIGOS DE SALIDA
    0  medicion terminada (haya o no anomalias: es informacion, no error)
    1  no se pudo leer R2 o no hay ningun snapshot
    2  faltan variables de entorno
"""

import gzip
import hashlib
import json
import os
import sys
from collections import Counter

for _f in (sys.stdout, sys.stderr):
    try:
        _f.reconfigure(encoding="utf-8")
    except Exception:
        pass

BUCKET_DEFAULT = "guarismo-crudo"
PREFIJO = "boveda/bcra/"
VENTANA = 7          # la misma ventana que la guardia de datos.gob
TOPE_LISTA = 12      # cuantas anomalias de un mismo tipo se listan por serie


def cliente_r2():
    import boto3
    from botocore.config import Config

    endpoint = os.getenv("R2_ENDPOINT")
    key = os.getenv("R2_ACCESS_KEY_ID")
    secret = os.getenv("R2_SECRET_ACCESS_KEY")
    if not (endpoint and key and secret):
        return None
    return boto3.client(
        "s3",
        endpoint_url=endpoint.rstrip("/"),
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        region_name="auto",
        config=Config(signature_version="s3v4",
                      retries={"max_attempts": 5, "mode": "standard"}),
    )


def listar(s3, bucket):
    """Todas las claves bajo boveda/bcra/ con su tamano."""
    objetos, token = {}, None
    while True:
        args = {"Bucket": bucket, "Prefix": PREFIJO}
        if token:
            args["ContinuationToken"] = token
        r = s3.list_objects_v2(**args)
        for o in r.get("Contents") or []:
            objetos[o["Key"]] = o.get("Size") or 0
        if not r.get("IsTruncated"):
            break
        token = r.get("NextContinuationToken")
    return objetos


def _sello(clave):
    nombre = clave.rsplit("/", 1)[-1]
    return nombre[len("snapshot_"):len("snapshot_") + 16]


def _dia(sello):
    return f"{sello[6:8]}-{sello[4:6]}"


def _vacio(p):
    """Un punto sin valor: todo lo que no es la fecha viene vacio (o no viene)."""
    if not isinstance(p, dict):
        return True
    valores = [v for k, v in p.items() if k != "fecha"]
    return not valores or all(v is None for v in valores)


def _f(x):
    """Fechas a texto comparable (YYYY-MM-DD...). None queda None."""
    return None if x in (None, "") else str(x)[:10]


def leer_snapshot(s3, bucket, clave, sidecars):
    """Devuelve (catalogo, series, estado_sidecar, claves_punto).

    catalogo: {idVariable: (primerFechaInformada, ultFechaInformada)} o None
              si el catalogo vino en hueco.
    series:   {serie: dict resumido}
    """
    cuerpo = s3.get_object(Bucket=bucket, Key=clave)["Body"].read()
    sha = hashlib.sha256(cuerpo).hexdigest()
    if clave + ".sha256" not in sidecars:
        estado = "sin sidecar"
    else:
        txt = s3.get_object(Bucket=bucket, Key=clave + ".sha256")["Body"].read()
        declarado = txt.decode("utf-8", "replace").split()[0] if txt.strip() else ""
        estado = "ok" if declarado == sha else f"NO COINCIDE (sidecar {declarado[:12]}… · objeto {sha[:12]}…)"
    catalogo, cat_error, series, claves_punto = {}, None, {}, Counter()
    for linea in gzip.decompress(cuerpo).decode("utf-8").splitlines():
        if not linea.strip():
            continue
        f = json.loads(linea)
        if f.get("_manifiesto"):
            continue
        if f.get("tipo") == "catalogo":
            if f.get("error"):
                cat_error = f["error"]
                continue
            catalogo[f.get("idVariable")] = (_f(f.get("primerFechaInformada")),
                                            _f(f.get("ultFechaInformada")))
        elif f.get("tipo") == "serie" and f.get("serie"):
            puntos = f.get("puntos") or []
            for p in puntos[:1]:
                if isinstance(p, dict):
                    claves_punto[tuple(sorted(p))] += 1
            series[f["serie"]] = {
                "id": f.get("id_variable"),
                "n": f.get("n") or 0,
                "desde": _f(f.get("desde")),
                "hasta": _f(f.get("hasta")),
                "error": f.get("error"),
                "sha": f.get("sha256"),
                "vacios": sum(1 for p in puntos if _vacio(p)),
                "mapa": {str(p.get("fecha")): json.dumps(
                    {k: v for k, v in p.items() if k != "fecha"},
                    sort_keys=True, default=str)
                    for p in puntos if isinstance(p, dict)},
            }
    if cat_error is not None and not catalogo:
        catalogo = None
    return catalogo, cat_error, series, estado, claves_punto


def _contra_catalogo(valor, declarado):
    if valor is None or declarado is None:
        return "sin dato"
    if valor == declarado:
        return "igual"
    return "atras" if valor < declarado else "adelante"


def main():
    print("=" * 66)
    print("  GUARISMO — Medicion hacia atras del BCRA · SOLO LECTURA")
    print("=" * 66)
    s3 = cliente_r2()
    if s3 is None:
        print("✗ faltan R2_ENDPOINT / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY")
        return 2
    bucket = os.getenv("R2_BUCKET") or BUCKET_DEFAULT

    try:
        objetos = listar(s3, bucket)
    except Exception as e:
        print(f"✗ no pude listar {bucket}/{PREFIJO}: {type(e).__name__}: {e}")
        return 1
    sidecars = {c for c in objetos if c.endswith(".sha256")}
    snaps = sorted((c for c in objetos if c.endswith(".ndjson.gz")
                    and c.rsplit("/", 1)[-1].startswith("snapshot_")), key=_sello)
    if not snaps:
        print(f"✗ no hay snapshots bajo {bucket}/{PREFIJO}")
        return 1
    mb = sum(objetos[c] for c in snaps) / 1024 / 1024
    print(f"   bucket {bucket} · {len(snaps)} snapshots · "
          f"{_sello(snaps[0])} … {_sello(snaps[-1])} · {mb:.1f} MB")

    integridad, cat_por_dia, claves_punto = {}, [], Counter()
    estado = {}   # por serie: memoria de la medicion
    for clave in snaps:
        sello = _sello(clave)
        try:
            catalogo, cat_error, series, est, cp = leer_snapshot(s3, bucket, clave, sidecars)
        except Exception as e:
            print(f"   ✗ no pude leer {clave}: {type(e).__name__}: {e}")
            integridad[clave] = f"ILEGIBLE ({type(e).__name__})"
            continue
        integridad[clave] = est
        claves_punto.update(cp)
        cat_por_dia.append((sello, "HUECO" if catalogo is None else len(catalogo), cat_error))
        for serie, s in series.items():
            e = estado.setdefault(serie, {
                "id": s["id"], "ventana": [], "anomalias": [], "huellas": [],
                "vistas": set(), "ultima": None, "vaiven": [], "previo": None,
                "revisiones": 0, "rev_mas_vieja": None, "rev_max": 0,
                "vs_hasta": Counter(), "vs_desde": Counter(),
                "ej_hasta": [], "ej_desde": [], "primero": None, "ultimo": None,
                "huecos": 0,
            })
            if s["n"] == 0:
                if s["error"]:
                    e["huecos"] += 1
                else:
                    e["anomalias"].append((sello, clave, "CERO SIN HUECO: n=0 y sin error"))
                continue
            # contra la ventana
            v = e["ventana"][-VENTANA:]
            motivo = None
            if v:
                n_max = max(x[0] for x in v)
                d_min = min(x[1] for x in v if x[1]) if any(x[1] for x in v) else None
                h_max = max(x[2] for x in v if x[2]) if any(x[2] for x in v) else None
                vac_min = min(x[3] for x in v)
                if s["n"] < n_max:
                    motivo = f"{s['n']} puntos y la ventana tiene {n_max}"
                elif d_min and s["desde"] and s["desde"] > d_min:
                    motivo = f"empieza en {s['desde']} y la ventana en {d_min}"
                elif h_max and s["hasta"] and s["hasta"] < h_max:
                    motivo = f"termina en {s['hasta']} y la ventana en {h_max}"
                elif s["vacios"] > vac_min:
                    motivo = f"{s['vacios']} vacios y el minimo de la ventana es {vac_min}"
            if motivo:
                e["anomalias"].append((sello, clave, motivo))
            e["ventana"].append((s["n"], s["desde"], s["hasta"], s["vacios"]))
            # contra el catalogo del mismo snapshot
            if catalogo is not None:
                prim, ult = catalogo.get(s["id"], (None, None))
                rh, rd = _contra_catalogo(s["hasta"], ult), _contra_catalogo(s["desde"], prim)
                e["vs_hasta"][rh] += 1
                e["vs_desde"][rd] += 1
                if rh not in ("igual",) and len(e["ej_hasta"]) < TOPE_LISTA:
                    e["ej_hasta"].append(f"{_dia(sello)} {s['hasta']} vs {ult}")
                if rd not in ("igual",) and len(e["ej_desde"]) < TOPE_LISTA:
                    e["ej_desde"].append(f"{_dia(sello)} {s['desde']} vs {prim}")
            # huellas y VAIVEN
            if s["sha"]:
                if e["ultima"] is not None and s["sha"] != e["ultima"] and s["sha"] in e["vistas"]:
                    e["vaiven"].append(sello)
                e["vistas"].add(s["sha"])
                e["ultima"] = s["sha"]
            # revisiones normales: valores que cambian en fechas que ya estaban
            if e["previo"] is not None:
                rev = sorted(k for k in set(e["previo"]) & set(s["mapa"])
                             if e["previo"][k] != s["mapa"][k])
                if rev:
                    e["revisiones"] += 1
                    e["rev_max"] = max(e["rev_max"], len(rev))
                    if e["rev_mas_vieja"] is None or rev[0] < e["rev_mas_vieja"]:
                        e["rev_mas_vieja"] = rev[0]
            e["previo"] = s["mapa"]
            if e["primero"] is None:
                e["primero"] = (sello, s["n"], s["desde"], s["hasta"])
            e["ultimo"] = (sello, s["n"], s["desde"], s["hasta"], s["vacios"])

    malos = {c: x for c, x in integridad.items() if x != "ok"}
    print(f"   integridad: {len(integridad) - len(malos)}/{len(integridad)} con sidecar que coincide")
    for c, x in malos.items():
        print(f"      ⚠ {c.rsplit('/', 1)[-1]}: {x}")
    print("   forma de los puntos (claves del primer punto, por serie y snapshot): "
          + (" · ".join(f"{list(k)} ×{n}" for k, n in claves_punto.most_common()) or "—"))

    # ---- catalogo por dia --------------------------------------------------
    print()
    print("▸ CATALOGO · variables por snapshot")
    tramos = []
    for sello, n, err in cat_por_dia:
        if tramos and tramos[-1][2] == n:
            tramos[-1][1] = sello
        else:
            tramos.append([sello, sello, n, err])
    for desde, hasta, n, err in tramos:
        rango = _dia(desde) if desde == hasta else f"{_dia(desde)}…{_dia(hasta)}"
        print(f"      {rango:<12} {n}" + (f"  ({err})" if n == "HUECO" and err else ""))

    # ---- series --------------------------------------------------------------
    print()
    afectados = {}
    con = []
    for serie in sorted(estado):
        e = estado[serie]
        marca = f"⚠ {len(e['anomalias'])} anomalia(s)" if e["anomalias"] else "sin anomalias"
        print(f"▸ {serie} (id {e['id']})   {marca}")
        if e["primero"]:
            s0, n0, d0, h0 = e["primero"]
            s1, n1, d1, h1, v1 = e["ultimo"]
            print(f"      {_dia(s0)}  {n0} pts {d0}→{h0}   …   {_dia(s1)}  {n1} pts {d1}→{h1} · {v1} vacios")
        if e["huecos"]:
            print(f"      huecos registrados: {e['huecos']}")
        for sello, clave, motivo in e["anomalias"][:TOPE_LISTA]:
            print(f"      ⚠ {_dia(sello)}  {motivo}")
            afectados.setdefault(clave, []).append(serie)
        for sello, clave, motivo in e["anomalias"][TOPE_LISTA:]:
            afectados.setdefault(clave, []).append(serie)
        if len(e["anomalias"]) > TOPE_LISTA:
            print(f"      … y {len(e['anomalias']) - TOPE_LISTA} mas")
        vh, vd = e["vs_hasta"], e["vs_desde"]
        print(f"      hasta vs ultFechaInformada: " + " · ".join(f"{k} {vh[k]}" for k in ("igual", "atras", "adelante", "sin dato") if vh[k]))
        if e["ej_hasta"]:
            print(f"         ej.: " + " | ".join(e["ej_hasta"][:4]))
        print(f"      desde vs primerFechaInformada: " + " · ".join(f"{k} {vd[k]}" for k in ("igual", "atras", "adelante", "sin dato") if vd[k]))
        if e["ej_desde"]:
            print(f"         ej.: " + " | ".join(e["ej_desde"][:4]))
        print(f"      huellas distintas: {len(e['vistas'])}"
              + (f" · VAIVEN ×{len(e['vaiven'])}: " + ", ".join(_dia(x) for x in e["vaiven"][:TOPE_LISTA]) if e["vaiven"] else " · sin vaiven"))
        print(f"      revisiones de valores ya publicados: {e['revisiones']} dia(s)"
              + (f" · la mas vieja {e['rev_mas_vieja']} · hasta {e['rev_max']} valores en un dia" if e["revisiones"] else ""))
        print()
        if e["anomalias"]:
            con.append(serie)

    tot_h, tot_d = Counter(), Counter()
    for e in estado.values():
        tot_h.update(e["vs_hasta"])
        tot_d.update(e["vs_desde"])
    print("=" * 66)
    print(f"  RESUMEN · {len(snaps)} snapshots · {len(estado)} series")
    print(f"  con anomalias ({len(con)}): {', '.join(con) or '—'}")
    print(f"  sin anomalias ({len(estado) - len(con)}): "
          f"{', '.join(s for s in sorted(estado) if s not in con) or '—'}")
    ceros = sum(1 for e in estado.values() for a in e["anomalias"] if a[2].startswith("CERO SIN HUECO"))
    print(f"  CERO SIN HUECO (el agujero conocido): {ceros} vez/veces")
    print(f"  hasta vs ultFechaInformada (todas): " + " · ".join(f"{k} {tot_h[k]}" for k in ("igual", "atras", "adelante", "sin dato") if tot_h[k]))
    print(f"  desde vs primerFechaInformada (todas): " + " · ".join(f"{k} {tot_d[k]}" for k in ("igual", "atras", "adelante", "sin dato") if tot_d[k]))
    print(f"  snapshots con anomalias: {len(afectados)}")
    for clave, ss in sorted(afectados.items(), key=lambda x: _sello(x[0])):
        print(f"     {clave}  ·  {', '.join(ss)}")
    print("  SOLO LECTURA: no se escribio nada en ningun lado.")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
