#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
medir_datosgob.py — Medicion HACIA ATRAS de los snapshots de datos.gob.ar.
SOLO LECTURA. No escribe un byte en R2, ni en Supabase, ni en ningun lado.

POR QUE EXISTE (consulta 21.8, 28-oct-2026)
    El 13 y el 14-oct-2026 datos.gob.ar devolvio la serie tcrm recortada
    (5000 y 2000 puntos de 10620) y boveda_datosgob.py la archivo como OK: la
    unica guardia era "cero puntos = hueco". La guardia nueva (commit 7fdee6d,
    28-oct) protege de aca en adelante. Este script mide el PASADO: recorre
    TODOS los snapshots bajo boveda/datosgob/ y dice, serie por serie y dia
    por dia, cuales llegaron recortados o con otro rango.

QUE MIRA, POR CADA SERIE Y CADA SNAPSHOT (en orden de sello)
    Contra lo MAS LARGO que esa serie tuvo en los snapshots ANTERIORES
    (mayor n, menor desde, mayor hasta):
      · n menor                  -> SOSPECHOSO: menos puntos
      · desde posterior          -> SOSPECHOSO: empieza despues
      · hasta anterior           -> SOSPECHOSO: termina antes
    Ademas: hueco (n=0 con error), degradada (ya marcada por la guardia nueva),
    y la integridad: el sha256 del objeto contra su sidecar .sha256.

    Es la misma regla que la guardia de produccion, pero con memoria TOTAL en
    vez de una ventana de 7. Es una medicion, no un veredicto: una serie que
    se achica de verdad (rebase, serie discontinuada) tambien aparece. Por eso
    se imprime la linea de tiempo entera de cada serie, para mirarla a ojo.

QUE IMPRIME
    1. Cuantos snapshots hay, desde y hasta, y cuantos sidecars coinciden.
    2. Por serie: la linea de tiempo comprimida (tramos de dias con el mismo
       n, desde y hasta) y cada snapshot SOSPECHOSO con su motivo.
    3. Un resumen final: series limpias, series con sospechosos y la lista
       de snapshots afectados (los que el motor de informes tiene que saber
       reconocer como captura fallida).

VARIABLES DE ENTORNO
    R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY   (obligatorias)
    R2_BUCKET                                             (opcional, default guarismo-crudo)

CODIGOS DE SALIDA
    0  medicion terminada (haya o no sospechosos: es informacion, no error)
    1  no se pudo leer R2 o no hay ningun snapshot
    2  faltan variables de entorno
"""

import gzip
import hashlib
import json
import os
import sys

for _f in (sys.stdout, sys.stderr):
    try:
        _f.reconfigure(encoding="utf-8")
    except Exception:
        pass

BUCKET_DEFAULT = "guarismo-crudo"
PREFIJO = "boveda/datosgob/"


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
    """Todas las claves bajo boveda/datosgob/ (snapshots y sidecars)."""
    claves, token = [], None
    while True:
        args = {"Bucket": bucket, "Prefix": PREFIJO}
        if token:
            args["ContinuationToken"] = token
        r = s3.list_objects_v2(**args)
        claves.extend(o["Key"] for o in r.get("Contents") or [])
        if not r.get("IsTruncated"):
            break
        token = r.get("NextContinuationToken")
    return claves


def _sello(clave):
    """'snapshot_20261014T221540Z_38646309882.ndjson.gz' -> '20261014T221540Z'."""
    nombre = clave.rsplit("/", 1)[-1]
    return nombre[len("snapshot_"):len("snapshot_") + 16]


def _dia(sello):
    return f"{sello[6:8]}-{sello[4:6]}"


def leer_snapshot(s3, bucket, clave, sidecars):
    """Devuelve (filas_por_serie, estado_sidecar)."""
    cuerpo = s3.get_object(Bucket=bucket, Key=clave)["Body"].read()
    sha = hashlib.sha256(cuerpo).hexdigest()
    if clave + ".sha256" not in sidecars:
        estado = "sin sidecar"
    else:
        txt = s3.get_object(Bucket=bucket, Key=clave + ".sha256")["Body"].read()
        declarado = txt.decode("utf-8", "replace").split()[0] if txt.strip() else ""
        estado = "ok" if declarado == sha else f"NO COINCIDE (sidecar {declarado[:12]}… · objeto {sha[:12]}…)"
    filas = {}
    for linea in gzip.decompress(cuerpo).decode("utf-8").splitlines():
        if not linea.strip():
            continue
        f = json.loads(linea)
        if f.get("_manifiesto") or not f.get("serie"):
            continue
        filas[f["serie"]] = {
            "n": f.get("n") or 0,
            "desde": f.get("desde"),
            "hasta": f.get("hasta"),
            "error": f.get("error"),
            "degradada": bool(f.get("degradada")),
        }
    return filas, estado


def medir(snapshots):
    """snapshots: lista ordenada de (sello, clave, filas). Devuelve por serie
    (linea_de_tiempo, sospechosos)."""
    series = sorted({s for _, _, filas in snapshots for s in filas})
    informe = {}
    for serie in series:
        mejor = None
        tiempo, sospechosos = [], []
        for sello, clave, filas in snapshots:
            f = filas.get(serie)
            if f is None:
                tiempo.append((sello, "ausente"))
                continue
            if f["degradada"]:
                tiempo.append((sello, "DEGRADADA (guardia)"))
                sospechosos.append((sello, clave, f["error"] or "degradada"))
                continue
            if f["n"] == 0:
                tiempo.append((sello, "HUECO"))
                continue
            motivo = None
            if mejor:
                if f["n"] < mejor["n"]:
                    motivo = f"{f['n']} puntos y antes tuvo {mejor['n']}"
                elif f["desde"] > mejor["desde"]:
                    motivo = f"empieza en {f['desde']} y antes en {mejor['desde']}"
                elif f["hasta"] < mejor["hasta"]:
                    motivo = f"termina en {f['hasta']} y antes en {mejor['hasta']}"
            marca = f"{f['n']} pts {f['desde']}→{f['hasta']}"
            if motivo:
                marca += " ⚠"
                sospechosos.append((sello, clave, motivo))
            tiempo.append((sello, marca))
            if mejor is None:
                mejor = dict(f)
            else:
                mejor = {"n": max(mejor["n"], f["n"]),
                         "desde": min(mejor["desde"], f["desde"]),
                         "hasta": max(mejor["hasta"], f["hasta"])}
        informe[serie] = (tiempo, sospechosos)
    return informe


def _tramos(tiempo):
    """Comprime dias consecutivos con la misma marca."""
    tramos = []
    for sello, marca in tiempo:
        if tramos and tramos[-1][2] == marca:
            tramos[-1][1] = sello
        else:
            tramos.append([sello, sello, marca])
    return tramos


def main():
    print("=" * 66)
    print("  GUARISMO — Medicion hacia atras de datos.gob · SOLO LECTURA")
    print("=" * 66)
    s3 = cliente_r2()
    if s3 is None:
        print("✗ faltan R2_ENDPOINT / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY")
        return 2
    bucket = os.getenv("R2_BUCKET") or BUCKET_DEFAULT

    try:
        claves = listar(s3, bucket)
    except Exception as e:
        print(f"✗ no pude listar {bucket}/{PREFIJO}: {type(e).__name__}: {e}")
        return 1
    sidecars = {c for c in claves if c.endswith(".sha256")}
    snaps = sorted((c for c in claves if c.endswith(".ndjson.gz")
                    and c.rsplit("/", 1)[-1].startswith("snapshot_")), key=_sello)
    if not snaps:
        print(f"✗ no hay snapshots bajo {bucket}/{PREFIJO}")
        return 1
    print(f"   bucket {bucket} · {len(snaps)} snapshots · "
          f"{_sello(snaps[0])} … {_sello(snaps[-1])}")

    snapshots, integridad = [], {}
    for clave in snaps:
        try:
            filas, estado = leer_snapshot(s3, bucket, clave, sidecars)
        except Exception as e:
            print(f"   ✗ no pude leer {clave}: {type(e).__name__}: {e}")
            integridad[clave] = f"ILEGIBLE ({type(e).__name__})"
            continue
        integridad[clave] = estado
        snapshots.append((_sello(clave), clave, filas))

    malos = {c: e for c, e in integridad.items() if e != "ok"}
    print(f"   integridad: {len(integridad) - len(malos)}/{len(integridad)} con sidecar que coincide")
    for c, e in malos.items():
        print(f"      ⚠ {c.rsplit('/', 1)[-1]}: {e}")

    informe = medir(snapshots)
    afectados = {}
    print()
    for serie, (tiempo, sospechosos) in informe.items():
        print(f"▸ {serie}" + (f"   ⚠ {len(sospechosos)} sospechoso(s)" if sospechosos else "   limpia"))
        for desde, hasta, marca in _tramos(tiempo):
            rango = _dia(desde) if desde == hasta else f"{_dia(desde)}…{_dia(hasta)}"
            print(f"      {rango:<12} {marca}")
        for sello, clave, motivo in sospechosos:
            print(f"      ⚠ {_dia(sello)}  {motivo}")
            afectados.setdefault(clave, []).append(serie)
        print()

    limpias = [s for s, (_, sos) in informe.items() if not sos]
    sucias = [s for s, (_, sos) in informe.items() if sos]
    print("=" * 66)
    print(f"  RESUMEN · {len(snapshots)} snapshots · {len(informe)} series")
    print(f"  limpias ({len(limpias)}): {', '.join(limpias) or '—'}")
    print(f"  con sospechosos ({len(sucias)}): {', '.join(sucias) or '—'}")
    print(f"  snapshots afectados: {len(afectados)}")
    for clave, ss in sorted(afectados.items(), key=lambda x: _sello(x[0])):
        print(f"     {clave}  ·  {', '.join(ss)}")
    print("  SOLO LECTURA: no se escribio nada en ningun lado.")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
