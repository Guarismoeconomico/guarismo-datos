#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
motor/indice.py — PIEZA 1 DEL MOTOR: el indice del archivo. SOLO LECTURA.

QUE ES (Plan del motor, 14-oct-2026 · consultas 23.1-A y 23.3-A, 29-oct-2026)
    Para cada serie de datos.gob.ar, la lista de TODAS sus capturas en la
    boveda, con todo lo que una hoja va a tener que decir de cada una:

      · cuando se capturo (UTC y hora argentina, con zona)
      · que objeto de R2 la guarda, y su sha256 recalculado al bajarlo,
        contra su sidecar .sha256 y contra el anclaje que lo cubre
      · la huella de la serie (sha256_puntos), n, desde, hasta y vacios
      · su ESTADO: OK · HUECO · DEGRADADA · FALLIDA (por regla)
      · su clase (A: datos.gob no tiene backfill, todo snapshot es del dia)
      · el anclaje que lo cubre y el DIA DE SELLO

    No escribe nada en R2, ni en Supabase, ni en el repo. Se regenera entero
    cada vez: no guarda estado. Las piezas 2 y 3 (lector y as-of) lo usan
    importando construir_indice().

ESTADO DE CADA CAPTURA (las mismas reglas que la diaria, medidas el 29-oct)
    HUECO       la linea vino con n=0 y error (registrado por la diaria)
    DEGRADADA   la linea trae "degradada" (la guardia 21.8 / 22.20 la aparto)
    FALLIDA     la diaria la archivo como OK, pero cumple una de las dos
                reglas contra la VENTANA de los 7 snapshots anteriores con
                datos de esa serie:
                  (1) menos puntos que el maximo, o empieza despues del
                      minimo, o termina antes del maximo
                  (2) mas valores vacios que el minimo
                Medido con medir_datosgob.py (commit 82e39db): las dos juntas
                marcan exactamente los 8 snapshots del 11 al 18-oct, sin
                falsas alarmas en 76. Las capturas fallidas NO se borran
                (append-only): se reconocen por regla, aca.
    OK          todo lo demas

    LIMITE CONOCIDO: una serie que la fuente recorta DE VERDAD (como
    emae_var_mensual el 21-sep) sale FALLIDA hasta que la ventana se llena
    con la forma nueva (7 snapshots). Es conservador a proposito: el indice
    prefiere no usar una captura dudosa antes que usar una mala.

DIA DE SELLO
    anclar_boveda.py escribe boveda/anclajes/AAAA/MM/anclaje_<sello>.json con
    la lista de objetos (clave + sha256) y su `generado_utc`; la fila que
    inserta en guarismo_historico lleva ese mismo instante como
    `capturado_en`, y el sellador lo copia en la cabeza `boveda` de cada
    sellos/AAAA-MM-DD.json. Un anclaje queda cubierto por el PRIMER sello cuya
    cabeza boveda tiene capturado_en >= generado_utc del anclaje (la cadena
    es acumulativa). Lo capturado del 1-sep al 14-oct esta en la genesis y
    su dia de sello es el 15-oct. Un objeto sin anclaje (el del dia, antes
    de la corrida del anclaje) sale "sin anclar todavia".

    Si ese sello ya tiene su prueba en Bitcoin lo dice el .ots; eso lo mira el
    verificador (pieza 5), no el indice.

USO
    python motor/indice.py                  # resumen de las 16 series + detalle de emae_desest
    python motor/indice.py --serie=tcrm     # detalle de otra serie
    python motor/indice.py --salida=x.json  # ademas, el indice completo en JSON

VARIABLES DE ENTORNO
    R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY   (obligatorias)
    R2_BUCKET                                             (opcional, default guarismo-crudo)

CODIGOS DE SALIDA
    0  indice armado (haya o no capturas fallidas: es informacion)
    1  no se pudo leer R2, o no hay snapshots, o la integridad no cierra
    2  faltan variables de entorno o el --serie no existe
"""

import gzip
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone

for _f in (sys.stdout, sys.stderr):
    try:
        _f.reconfigure(encoding="utf-8")
    except Exception:
        pass

BUCKET_DEFAULT = "guarismo-crudo"
PREFIJO_SNAP = "boveda/datosgob/"
PREFIJO_ANCLAJES = "boveda/anclajes/"
VENTANA = 7                     # la misma que VENTANA_REFERENCIA de la diaria
SERIE_DEFAULT = "emae_desest"
ARG = timezone(timedelta(hours=-3), "ART")   # Argentina, sin horario de verano
RAIZ_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def listar(s3, bucket, prefijo):
    claves, token = [], None
    while True:
        args = {"Bucket": bucket, "Prefix": prefijo}
        if token:
            args["ContinuationToken"] = token
        r = s3.list_objects_v2(**args)
        claves.extend(o["Key"] for o in r.get("Contents") or [])
        if not r.get("IsTruncated"):
            break
        token = r.get("NextContinuationToken")
    return claves


def _leer(s3, bucket, clave):
    return s3.get_object(Bucket=bucket, Key=clave)["Body"].read()


def _sello_snap(clave):
    """'.../snapshot_20261014T221540Z_38646309882.ndjson.gz' -> datetime UTC."""
    nombre = clave.rsplit("/", 1)[-1]
    return datetime.strptime(nombre[len("snapshot_"):len("snapshot_") + 16],
                             "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def _utc(texto):
    """ISO con zona -> datetime UTC. None si no hay."""
    if not texto:
        return None
    t = datetime.fromisoformat(str(texto).replace("Z", "+00:00"))
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _iso(t):
    return t.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _arg(t):
    return t.astimezone(ARG).strftime("%Y-%m-%d %H:%M ART")


def _vacios(puntos):
    return sum(1 for p in puntos if len(p) < 2 or p[1] is None)


# ---------------------------------------------------------------------------
# Anclajes y sellos
# ---------------------------------------------------------------------------

def leer_anclajes(s3, bucket):
    """{clave_objeto: {"anclaje", "generado_utc", "sha256"}} de todos los anclajes."""
    por_objeto, anclajes = {}, []
    for clave in sorted(c for c in listar(s3, bucket, PREFIJO_ANCLAJES) if c.endswith(".json")):
        a = json.loads(_leer(s3, bucket, clave).decode("utf-8"))
        gen = _utc(a.get("generado_utc"))
        anclajes.append((clave, gen, a.get("tipo"), len(a.get("objetos") or [])))
        for o in a.get("objetos") or []:
            # un objeto queda cubierto por el PRIMER anclaje que lo lista
            if o.get("clave") and o["clave"] not in por_objeto:
                por_objeto[o["clave"]] = {"anclaje": clave, "generado_utc": gen,
                                          "sha256": o.get("sha256")}
    return por_objeto, anclajes


def leer_sellos(raiz=RAIZ_REPO):
    """[(fecha_sello, capturado_en_cabeza_boveda)] de sellos/*.json con cabeza boveda."""
    carpeta = os.path.join(raiz, "sellos")
    out = []
    for nombre in sorted(os.listdir(carpeta)):
        if len(nombre) != 15 or not nombre.endswith(".json"):
            continue        # deja afuera .ots, .ots.bak, .sin_anclar.json, el .md
        with open(os.path.join(carpeta, nombre), encoding="utf-8") as f:
            s = json.load(f)
        cab = (s.get("cabezas") or {}).get("boveda")
        if cab and cab.get("capturado_en"):
            out.append((nombre[:10], _utc(cab["capturado_en"])))
    return out


def dia_de_sello(generado, sellos):
    """El primer sello cuya cabeza boveda es de ese momento o posterior."""
    if generado is None:
        return None
    for fecha, cap in sellos:
        if cap >= generado:
            return fecha
    return None


# ---------------------------------------------------------------------------
# El indice
# ---------------------------------------------------------------------------

def _estado(fila, ventana):
    """(estado, motivo) de una linea de serie, contra la ventana previa."""
    if fila.get("degradada"):
        return "DEGRADADA", fila.get("error") or "degradada"
    if not fila.get("n"):
        return "HUECO", fila.get("error") or "sin datos"
    if ventana:
        n_max = max(v["n"] for v in ventana)
        d_min = min(v["desde"] for v in ventana)
        h_max = max(v["hasta"] for v in ventana)
        vac_min = min(v["vacios"] for v in ventana)
        if fila["n"] < n_max:
            return "FALLIDA", f"regla 1: {fila['n']} puntos y la ventana tiene {n_max}"
        if fila["desde"] > d_min:
            return "FALLIDA", f"regla 1: empieza en {fila['desde']} y la ventana en {d_min}"
        if fila["hasta"] < h_max:
            return "FALLIDA", f"regla 1: termina en {fila['hasta']} y la ventana en {h_max}"
        v = _vacios(fila.get("puntos") or [])
        if v > vac_min:
            return "FALLIDA", f"regla 2: {v} vacios y el minimo de la ventana es {vac_min}"
    return "OK", None


def construir_indice(s3, bucket, raiz=RAIZ_REPO, con_puntos=False):
    """El indice completo. Devuelve (indice, integridad, notas).

    indice: {serie: [captura, ...]} en orden de captura. Cada captura es un
    dict con las claves que imprime este script. Con con_puntos=True, las
    capturas OK llevan ademas "puntos" (para el lector de la pieza 2).
    """
    claves = listar(s3, bucket, PREFIJO_SNAP)
    sidecars = {c for c in claves if c.endswith(".sha256")}
    snaps = sorted((c for c in claves if c.endswith(".ndjson.gz")
                    and c.rsplit("/", 1)[-1].startswith("snapshot_")), key=_sello_snap)
    anclado, anclajes = leer_anclajes(s3, bucket)
    sellos = leer_sellos(raiz)
    integridad, indice, ventanas = {}, {}, {}
    for clave in snaps:
        cuerpo = _leer(s3, bucket, clave)
        sha = hashlib.sha256(cuerpo).hexdigest()
        sidecar = None
        if clave + ".sha256" in sidecars:
            txt = _leer(s3, bucket, clave + ".sha256").decode("utf-8", "replace").split()
            sidecar = txt[0] if txt else None
        anc = anclado.get(clave)
        problemas = []
        if sidecar != sha:
            problemas.append("sidecar distinto" if sidecar else "sin sidecar")
        if anc and anc.get("sha256") != sha:
            problemas.append("anclaje distinto")
        integridad[clave] = problemas
        capturado = _sello_snap(clave)
        sello = dia_de_sello(anc["generado_utc"], sellos) if anc else None
        for linea in gzip.decompress(cuerpo).decode("utf-8").splitlines():
            if not linea.strip():
                continue
            fila = json.loads(linea)
            if fila.get("_manifiesto") or not fila.get("serie"):
                continue
            serie = fila["serie"]
            vent = ventanas.setdefault(serie, [])
            estado, motivo = _estado(fila, vent[-VENTANA:])
            cap = {
                "capturado_utc": _iso(capturado),
                "capturado_arg": _arg(capturado),
                "objeto": clave,
                "sha256_objeto": sha,
                "integridad": "ok" if not problemas else " · ".join(problemas),
                "clase": "A",
                "estado": estado,
                "motivo": motivo,
                "n": fila.get("n") or 0,
                "desde": fila.get("desde"),
                "hasta": fila.get("hasta"),
                "vacios": _vacios(fila.get("puntos") or []) if fila.get("n") else None,
                "sha256_puntos": fila.get("sha256_puntos"),
                "anclaje": anc["anclaje"] if anc else None,
                "dia_de_sello": sello,
                "sello": f"sellos/{sello}.json" if sello else None,
            }
            if con_puntos and estado == "OK":
                cap["puntos"] = fila.get("puntos")
            indice.setdefault(serie, []).append(cap)
            if fila.get("n") and not fila.get("degradada"):
                vent.append({"n": fila["n"], "desde": fila["desde"], "hasta": fila["hasta"],
                             "vacios": _vacios(fila.get("puntos") or [])})
    notas = {"snapshots": len(snaps), "anclajes": len(anclajes), "sellos_con_boveda": len(sellos),
             "primero": _iso(_sello_snap(snaps[0])) if snaps else None,
             "ultimo": _iso(_sello_snap(snaps[-1])) if snaps else None}
    return indice, integridad, notas


def _tramos(caps):
    """Comprime capturas consecutivas con el mismo estado, huella y sello."""
    tramos = []
    for c in caps:
        clave = (c["estado"], c["sha256_puntos"], c["dia_de_sello"], c["motivo"])
        if tramos and tramos[-1][0] == clave:
            tramos[-1][2] = c
        else:
            tramos.append([clave, c, c])
    return tramos


def _canon(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def main(argv):
    serie_pedida, salida = SERIE_DEFAULT, None
    for a in argv:
        if a.startswith("--serie="):
            serie_pedida = a.split("=", 1)[1].strip()
        elif a.startswith("--salida="):
            salida = a.split("=", 1)[1].strip()
    print("=" * 66)
    print("  GUARISMO — Motor · pieza 1 · el indice del archivo · SOLO LECTURA")
    print("=" * 66)
    s3 = cliente_r2()
    if s3 is None:
        print("✗ faltan R2_ENDPOINT / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY")
        return 2
    bucket = os.getenv("R2_BUCKET") or BUCKET_DEFAULT
    try:
        indice, integridad, notas = construir_indice(s3, bucket)
    except Exception as e:
        print(f"✗ no pude armar el indice: {type(e).__name__}: {e}")
        return 1
    if not notas["snapshots"]:
        print(f"✗ no hay snapshots bajo {bucket}/{PREFIJO_SNAP}")
        return 1
    malos = {c: p for c, p in integridad.items() if p}
    sin_anclar = [c for c in integridad if not any(
        cap["objeto"] == c and cap["anclaje"] for caps in indice.values() for cap in caps)]
    print(f"   {notas['snapshots']} snapshots · {notas['primero']} … {notas['ultimo']}")
    print(f"   {notas['anclajes']} anclajes · {notas['sellos_con_boveda']} sellos con cabeza boveda")
    print(f"   integridad (objeto = sidecar = anclaje): {len(integridad) - len(malos)}/{len(integridad)}")
    for c, p in malos.items():
        print(f"      ⚠ {c.rsplit('/', 1)[-1]}: {' · '.join(p)}")
    print(f"   sin anclar todavia: {len(sin_anclar)}"
          + (f" ({', '.join(c.rsplit('/', 1)[-1][9:25] for c in sin_anclar)})" if sin_anclar else ""))

    if serie_pedida not in indice:
        print(f"✗ la serie '{serie_pedida}' no esta en el archivo. Hay: {', '.join(sorted(indice))}")
        return 2

    print()
    print("▸ RESUMEN POR SERIE (capturas por estado)")
    for serie in sorted(indice):
        caps = indice[serie]
        cuenta = {e: sum(1 for c in caps if c["estado"] == e) for e in ("OK", "FALLIDA", "DEGRADADA", "HUECO")}
        print(f"   {serie:<20} {len(caps):>3} capturas · "
              + " · ".join(f"{e} {n}" for e, n in cuenta.items()))

    print()
    print(f"▸ DETALLE · {serie_pedida} (tramos de capturas iguales, hora argentina)")
    for (estado, huella, sello, motivo), c0, c1 in _tramos(indice[serie_pedida]):
        rango = c0["capturado_arg"][:10] if c0 is c1 else f"{c0['capturado_arg'][:10]} … {c1['capturado_arg'][:10]}"
        datos = (f"{c1['n']} pts {c1['desde']}→{c1['hasta']} · {c1['vacios']} vacios · huella {huella[:12]}…"
                 if huella else "—")
        print(f"   {rango:<25} {estado:<9} {datos}")
        print(f"   {'':<25} {'':<9} sello: {sello or 'sin anclar todavia'}"
              + (f" · {motivo}" if motivo else ""))
    ultima_ok = next((c for c in reversed(indice[serie_pedida]) if c["estado"] == "OK"), None)
    if ultima_ok:
        print(f"   ultima captura OK: {ultima_ok['capturado_arg']} · {ultima_ok['objeto']}")

    texto = _canon({"indice": indice, "notas": notas})
    print()
    print(f"   sha256 del indice: {hashlib.sha256(texto.encode('utf-8')).hexdigest()}")
    if salida:
        with open(salida, "w", encoding="utf-8") as f:
            f.write(texto + "\n")
        print(f"   indice completo en {salida} ({len(texto) / 1024:.0f} KB)")
    print("   SOLO LECTURA: no se escribio nada en R2, ni en Supabase, ni en el repo.")
    print("=" * 66)
    return 1 if malos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
