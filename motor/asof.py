#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
motor/asof.py — PIEZAS 2 Y 3 DEL MOTOR: el lector y la operacion as-of.
SOLO LECTURA.

QUE CONTESTA (Plan del motor, 14-oct · consultas 21.6-A, 23.1-A y 23.8-B)
    "Que decia la serie S para el periodo P el dia D", con todo su origen:
    que objeto de R2, su sha256, la huella de la serie, la hora de captura
    con zona, la clase y el dia de sello. Y la comparacion entre dos fechas,
    que son dos as-of lado a lado: IGUAL o DISTINTO. Dos hechos con fecha,
    nunca una causa, y ningun calculo sobre los numeros.

    Esto NO es todavia la hoja canonica (pieza 4): es la operacion que la
    alimenta. Imprime el resultado y lo deja en JSON.

PIEZA 2 — EL LECTOR (leer_serie)
    Baja de R2 el objeto que eligio el indice, le recalcula el sha256 y lo
    compara con el del indice; despues saca la linea de la serie, valida la
    forma de cada punto ([fecha AAAA-MM-DD, numero o null]) y recalcula la
    huella (sha256_puntos) con la misma regla que la diaria. Si algo no
    coincide, o la forma no es la conocida, FALLA RUIDOSO (ErrorLector):
    nunca adivina, nunca entrega un numero que no pudo comprobar.
    No toca motor/indice.py: lo importa.

PIEZA 3 — EL AS-OF (as_of), REGLA 23.8 -> B (estricta)
    Se usa SOLO una captura del dia D (dia en hora argentina). Si ese dia
    hay varias, la ultima OK. Si ese dia no hay ninguna OK, la respuesta es
    la TARJETA DE HUECO, con el estado y el motivo de cada captura del dia
    (HUECO · DEGRADADA · FALLIDA · integridad). Como dato aparte, NUNCA como
    respuesta, se nombran la captura OK mas cercana antes y despues.
    Una notaria no rellena: si ese dia no hay constancia, lo dice.

    RESULTADOS POSIBLES
      VALOR          la captura del dia trae el periodo con un numero
      VACIO          la captura del dia trae el periodo, pero sin valor (null)
      PERIODO_FUERA  la serie que publicaba la fuente ese dia no incluia P
      HUECO          ese dia hay capturas, pero ninguna OK (tarjeta de hueco)
      SIN_CAPTURA    ese dia no hay ninguna captura de esa serie

USO
    python motor/asof.py --serie=emae_desest --periodo=2026-07 --fecha=2026-10-20
    python motor/asof.py --serie=emae_desest --periodo=2026-07 --fecha=2026-10-20 --fecha2=2026-11-20
    ... --salida=asof.json     ademas, el resultado en JSON (determinístico)

    --periodo acepta AAAA-MM (se lee como AAAA-MM-01, el formato mensual de
    datos.gob) o AAAA-MM-DD (series diarias, como tcrm).
    --raiz=carpeta   solo para pruebas: donde buscar sellos/ (default, el repo)

VARIABLES DE ENTORNO
    R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY   (obligatorias)
    R2_BUCKET                                             (opcional, default guarismo-crudo)

CODIGOS DE SALIDA
    0  contesto (cualquier resultado, tambien HUECO: es informacion)
    1  no se pudo leer R2 o armar el indice, o el lector no pudo comprobar
       la captura elegida (sha, huella o forma): falla ruidoso a proposito
    2  parametros invalidos o serie inexistente
"""

import gzip
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from indice import (BUCKET_DEFAULT, RAIZ_REPO, _leer, cliente_r2,  # noqa: E402
                    construir_indice)

for _f in (sys.stdout, sys.stderr):
    try:
        _f.reconfigure(encoding="utf-8")
    except Exception:
        pass

FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PERIODO = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")
SERIE = re.compile(r"^[a-z0-9_]{1,40}$")
META = ("id", "descripcion", "unidades", "frecuencia", "fuente", "dataset")


class ErrorLector(Exception):
    """La captura elegida no se pudo comprobar. Nunca se entrega un numero asi."""


# ---------------------------------------------------------------------------
# Pieza 2 — el lector
# ---------------------------------------------------------------------------

def _huella(puntos):
    """La misma regla que _hash_puntos() de boveda_datosgob.py."""
    crudo = json.dumps(puntos, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), default=str)
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()


def _validar_punto(p, i):
    if not (isinstance(p, list) and len(p) == 2):
        raise ErrorLector(f"punto {i}: forma desconocida {p!r:.60} (se espera [fecha, valor])")
    fecha, valor = p
    if not (isinstance(fecha, str) and FECHA.match(fecha)):
        raise ErrorLector(f"punto {i}: fecha desconocida {fecha!r:.40}")
    if valor is not None and (isinstance(valor, bool) or not isinstance(valor, (int, float))):
        raise ErrorLector(f"punto {i}: valor que no es numero ni vacio {valor!r:.40}")


def leer_serie(s3, bucket, captura, serie):
    """La serie tal como quedo en ESA captura, comprobada. Lanza ErrorLector."""
    cuerpo = _leer(s3, bucket, captura["objeto"])
    sha = hashlib.sha256(cuerpo).hexdigest()
    if sha != captura["sha256_objeto"]:
        raise ErrorLector(f"el objeto {captura['objeto']} tiene sha {sha[:12]}… y el indice "
                          f"{captura['sha256_objeto'][:12]}…")
    filas = [json.loads(l) for l in gzip.decompress(cuerpo).decode("utf-8").splitlines()
             if l.strip()]
    filas = [f for f in filas if f.get("serie") == serie and not f.get("_manifiesto")]
    if len(filas) != 1:
        raise ErrorLector(f"el objeto trae {len(filas)} lineas de {serie} (se espera 1)")
    fila = filas[0]
    puntos = fila.get("puntos")
    if not isinstance(puntos, list) or not puntos:
        raise ErrorLector(f"la linea de {serie} no trae puntos")
    for i, p in enumerate(puntos):
        _validar_punto(p, i)
    huella = _huella(puntos)
    for nombre, esperada in (("la linea", fila.get("sha256_puntos")),
                             ("el indice", captura.get("sha256_puntos"))):
        if huella != esperada:
            raise ErrorLector(f"huella recalculada {huella[:12]}… distinta de la de {nombre} "
                              f"{str(esperada)[:12]}…")
    if len(puntos) != fila.get("n"):
        raise ErrorLector(f"{len(puntos)} puntos y la linea declara n={fila.get('n')}")
    fechas = [p[0] for p in puntos]
    if len(set(fechas)) != len(fechas):
        raise ErrorLector("hay periodos repetidos en la serie")
    return {"serie": serie, "huella": huella, "n": len(puntos),
            "desde": fechas[0], "hasta": fechas[-1],
            "meta": {k: fila.get(k) for k in META},
            "valores": {p[0]: p[1] for p in puntos}}


# ---------------------------------------------------------------------------
# Pieza 3 — el as-of
# ---------------------------------------------------------------------------

def _dia(c):
    return c["capturado_arg"][:10]


def _usable(c):
    return c["estado"] == "OK" and c["integridad"] == "ok"


def _origen(c):
    return {k: c[k] for k in ("capturado_utc", "capturado_arg", "objeto", "sha256_objeto",
                              "sha256_puntos", "clase", "anclaje", "dia_de_sello", "sello")}


def _cercanas(caps, fecha):
    antes = [c for c in caps if _usable(c) and _dia(c) < fecha]
    despues = [c for c in caps if _usable(c) and _dia(c) > fecha]
    return {"antes": _dia(antes[-1]) if antes else None,
            "despues": _dia(despues[0]) if despues else None}


def as_of(s3, bucket, indice, serie, periodo, fecha):
    """Que decia `serie` para `periodo` el dia `fecha` (AAAA-MM-DD, hora argentina)."""
    caps = indice[serie]
    del_dia = [c for c in caps if _dia(c) == fecha]
    base = {"serie": serie, "periodo": periodo, "fecha": fecha,
            "capturas_del_dia": len(del_dia)}
    if not del_dia:
        return {**base, "resultado": "SIN_CAPTURA", "cercanas": _cercanas(caps, fecha)}
    buenas = [c for c in del_dia if _usable(c)]
    if not buenas:
        estados = [{"capturado_arg": c["capturado_arg"], "objeto": c["objeto"],
                    "estado": c["estado"] if c["integridad"] == "ok" else "INTEGRIDAD",
                    "motivo": c["motivo"] if c["integridad"] == "ok" else c["integridad"]}
                   for c in del_dia]
        return {**base, "resultado": "HUECO", "estados_del_dia": estados,
                "cercanas": _cercanas(caps, fecha)}
    c = buenas[-1]
    s = leer_serie(s3, bucket, c, serie)
    out = {**base, "origen": _origen(c), "meta": s["meta"],
           "serie_ese_dia": {"n": s["n"], "desde": s["desde"], "hasta": s["hasta"]}}
    if periodo not in s["valores"]:
        return {**out, "resultado": "PERIODO_FUERA"}
    valor = s["valores"][periodo]
    if valor is None:
        return {**out, "resultado": "VACIO", "valor": None}
    return {**out, "resultado": "VALOR", "valor": valor}


def comparar(a, b):
    """Dos as-of lado a lado. Solo IGUAL o DISTINTO; nada de diferencias calculadas."""
    if a["resultado"] == "VALOR" and b["resultado"] == "VALOR":
        veredicto = "IGUAL" if a["valor"] == b["valor"] else "DISTINTO"
        misma_huella = a["origen"]["sha256_puntos"] == b["origen"]["sha256_puntos"]
    else:
        veredicto, misma_huella = "NO_COMPARABLE", None
    return {"veredicto": veredicto, "misma_huella": misma_huella}


# ---------------------------------------------------------------------------
# Salida
# ---------------------------------------------------------------------------

def _canon(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _imprimir(r, rotulo):
    print(f"▸ {rotulo} · {r['serie']} · periodo {r['periodo']} · el dia {r['fecha']}")
    res = r["resultado"]
    if res in ("VALOR", "VACIO", "PERIODO_FUERA"):
        o, m, se = r["origen"], r["meta"], r["serie_ese_dia"]
        if res == "VALOR":
            print(f"   {res}: {r['valor']!r}" + (f" ({m['unidades']})" if m.get("unidades") else ""))
        elif res == "VACIO":
            print(f"   {res}: la fuente publicaba el periodo sin valor")
        else:
            print(f"   {res}: la serie de ese dia iba de {se['desde']} a {se['hasta']}")
        print(f"   capturado: {o['capturado_arg']} ({o['capturado_utc']}) · clase {o['clase']}")
        print(f"   objeto: {o['objeto']}")
        print(f"   sha256 del objeto: {o['sha256_objeto']}")
        print(f"   huella de la serie: {o['sha256_puntos']} · {se['n']} puntos")
        print(f"   sello: {o['dia_de_sello'] or 'sin anclar todavia'}"
              + (f" · {o['sello']}" if o.get("sello") else ""))
        if m.get("descripcion") or m.get("fuente"):
            print(f"   fuente declarada: {m.get('fuente') or '—'} · {m.get('descripcion') or '—'}")
    else:
        if res == "HUECO":
            print("   HUECO: ese dia hay captura, pero ninguna sirve. No se entrega un numero.")
            for e in r["estados_del_dia"]:
                print(f"      {e['capturado_arg']} · {e['estado']} · {e['motivo']}")
        else:
            print("   SIN_CAPTURA: ese dia no hay ninguna captura de esta serie.")
        ce = r["cercanas"]
        print(f"   (dato aparte, no es la respuesta) captura OK mas cercana: "
              f"antes {ce['antes'] or '—'} · despues {ce['despues'] or '—'}")
    if r["capturas_del_dia"] > 1:
        print(f"   nota: ese dia hay {r['capturas_del_dia']} capturas; se usa la ultima OK")


def main(argv):
    args = {}
    for a in argv:
        if a.startswith("--") and "=" in a:
            k, v = a[2:].split("=", 1)
            args[k] = v.strip()
    serie, periodo = args.get("serie", ""), args.get("periodo", "")
    fecha, fecha2, salida = args.get("fecha", ""), args.get("fecha2", ""), args.get("salida")
    print("=" * 66)
    print("  GUARISMO — Motor · piezas 2 y 3 · lector y as-of · SOLO LECTURA")
    print("=" * 66)
    if not SERIE.match(serie) or not PERIODO.match(periodo) or not FECHA.match(fecha) \
            or (fecha2 and not FECHA.match(fecha2)):
        print("✗ parametros: --serie=nombre --periodo=AAAA-MM[-DD] --fecha=AAAA-MM-DD "
              "[--fecha2=AAAA-MM-DD]")
        return 2
    if len(periodo) == 7:
        periodo += "-01"
    s3 = cliente_r2()
    if s3 is None:
        print("✗ faltan R2_ENDPOINT / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY")
        return 2
    bucket = os.getenv("R2_BUCKET") or BUCKET_DEFAULT
    try:
        indice, integridad, notas = construir_indice(s3, bucket, raiz=args.get("raiz") or RAIZ_REPO)
    except Exception as e:
        print(f"✗ no pude armar el indice: {type(e).__name__}: {e}")
        return 1
    if serie not in indice:
        print(f"✗ la serie '{serie}' no esta en el archivo. Hay: {', '.join(sorted(indice))}")
        return 2
    print(f"   indice: {notas['snapshots']} snapshots · {notas['primero']} … {notas['ultimo']}")
    print()
    try:
        r1 = as_of(s3, bucket, indice, serie, periodo, fecha)
        r2 = as_of(s3, bucket, indice, serie, periodo, fecha2) if fecha2 else None
    except ErrorLector as e:
        print(f"✗ el lector no pudo comprobar la captura: {e}")
        print("   No se entrega ningun numero. Revisar el objeto antes de seguir.")
        return 1
    _imprimir(r1, "AS-OF" if not r2 else "FECHA 1")
    resultado = {"as_of": r1}
    if r2:
        print()
        _imprimir(r2, "FECHA 2")
        cmp_ = comparar(r1, r2)
        resultado = {"fecha_1": r1, "fecha_2": r2, "comparacion": cmp_}
        print()
        print(f"▸ COMPARACION: {cmp_['veredicto']}"
              + ("" if cmp_["misma_huella"] is None else
                 f" · la serie entera {'es la misma' if cmp_['misma_huella'] else 'cambio'}"
                 " entre las dos capturas"))
    texto = _canon(resultado)
    print()
    print(f"   sha256 del resultado: {hashlib.sha256(texto.encode('utf-8')).hexdigest()}")
    if salida:
        with open(salida, "w", encoding="utf-8") as f:
            f.write(texto + "\n")
        print(f"   resultado en {salida}")
    print("   SOLO LECTURA: no se escribio nada en R2, ni en Supabase, ni en el repo.")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
