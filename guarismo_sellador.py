#!/usr/bin/env python3
"""
Guarismo — Sellador diario (anclaje externo)
=============================================

Todos los días calcula la RAÍZ del archivo sellado —un único hash que resume el
estado de todas las cadenas— la publica en el repo público, y la ancla en
Bitcoin vía OpenTimestamps.

    python guarismo_sellador.py sellar       # diario
    python guarismo_sellador.py actualizar   # semanal


POR QUÉ HACE FALTA
------------------
La cadena de hashes prueba que el archivo no fue REESCRITO. No prueba CUÁNDO
existió: en teoría podría reconstruirse entera, hoy, con datos inventados, y
los hashes cerrarían igual.

El anclaje externo cierra ese agujero. Publicar la raíz en lugares que Guarismo
NO controla congela la historia hasta esa fecha:

  1. Commit en el repo público  → GitHub registra la fecha del commit
  2. OpenTimestamps             → la raíz entra en un bloque de Bitcoin

Para falsificar la historia habría que falsificar además el historial de GitHub
y la cadena de Bitcoin. Lo primero es detectable; lo segundo, inviable.

Cada día sin anclar es un día cuya antigüedad nadie puede probar, y eso NO se
puede agregar retroactivamente. Por eso corre desde el principio.


QUÉ ES LA RAÍZ
--------------
    raiz = sha256( canónico( { bucket: hash_cadena de su última captura } ) )

Anclando un solo hash queda anclada toda la historia previa: si cambiara
cualquier captura vieja, su hash cambiaría, y con él todos los eslabones
siguientes hasta la cabeza — que no coincidiría con la raíz ya publicada.


UN DÍA ANCLADO NO SE REESCRIBE — 16-sep-2026
---------------------------------------------
El workflow corre dos veces por día: cron-job.org a las 02:40 UTC y el
`schedule` de GitHub, que llega horas tarde. Hasta esta versión, la segunda
corrida encontraba otra raíz (el agregador capturó en el medio) y REESCRIBÍA
el .json del día. El .ots se quedaba con la prueba del .json VIEJO: `ots
stamp` no pisa un .ots existente (lo abre en modo exclusivo y sale con código
1). Y como el código solo miraba si el .ots EXISTÍA, imprimía "✓ sello
creado" igual.

Medido el 15 y 16-sep-2026: en 58 de 60 días el .json publicado no era el que
probaba su .ots. La versión probada quedó en el historial de git.

Ahora:
  - si el día ya tiene .ots, su .json NO se toca. Lo capturado después queda
    cubierto por el sello de mañana: la raíz de mañana encadena todo.
  - el "✓ sello creado" sale solo si `ots stamp` terminó bien Y el .ots es
    nuevo. Si había un .ots de antes para un .json recién escrito, la corrida
    termina en ROJO y no se publica: ese .ots no prueba lo escrito.
  - si el día quedó sin .ots (el anclaje falló) y la raíz no cambió, se ancla
    ahora. Para eso sirve la segunda corrida.


EL .bak CONGELABA LOS SELLOS — 25-sep-2026 (consulta 19.3 -> B)
---------------------------------------------------------------
`ots upgrade X.ots` renombra el original a X.ots.bak antes de escribir el
nuevo, y si X.ots.bak YA EXISTE sale con código 1 sin actualizar nada
(opentimestamps-client 0.7.2, cmds.py). Como el .bak se commiteaba, cada sello
se completaba UNA sola vez —el primer upgrade, unas horas después de nacer— y
después quedaba congelado: los calendarios que no habían anclado todavía no
entraban nunca. El error no se veía (continue-on-error, y nadie miraba el
código de salida).

Medido el 25-sep sobre los 69 sellos publicados: todos tienen al menos una
atestación de Bitcoin, pero 11 quedaron con una sola de cuatro posibles. Si el
primer upgrade llegaba antes que cualquier anclaje, el día quedaba sin
Bitcoin para siempre.

Ahora el upgrade se hace sobre una COPIA en una carpeta temporal. El .bak nace
y muere ahí. El .ots de sellos/ se reemplaza solo si:
  - cambió;
  - sigue probando el MISMO archivo (el "File sha256 hash" de `ots info` es
    el del .json de al lado), y
  - no pierde atestaciones de Bitcoin.
Si alguna guarda falla, el .ots queda como estaba y el log lo dice.

Los .ots.bak que ya están publicados NO se tocan: son registro público.
Consecuencia visible: desde este cambio, el commit de un día puede agrandar el
.ots de días ANTERIORES. Nunca un .json.
"""

import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import requests

SUPABASE_URL = "https://rudepkizcatkhqprqjfw.supabase.co"
ANON_KEY = "sb_publishable_nwqxJVCewzhySYY1JZ6Lxw_DhbY0w8J"

BUCKETS = ("oficial", "agregador")
DIR_SELLOS = pathlib.Path("sellos")
TIMEOUT = 30


def _canon(o) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def _sha(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
def cabezas() -> dict:
    """Última captura de cada bucket: el extremo vivo de cada cadena."""
    h = {"apikey": ANON_KEY, "Authorization": f"Bearer {ANON_KEY}"}
    out = {}
    for b in BUCKETS:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/guarismo_historico",
            params={"bucket": f"eq.{b}",
                    "select": "id,capturado_en,hash_cadena",
                    "order": "id.desc", "limit": "1"},
            headers=h, timeout=TIMEOUT)
        r.raise_for_status()
        filas = r.json()
        if not filas:
            print(f"   [aviso] bucket '{b}' vacío, se omite.")
            continue
        f = filas[0]
        out[b] = {"id": f["id"],
                  "capturado_en": f["capturado_en"],
                  "hash_cadena": f["hash_cadena"]}
    return out


def sellar() -> int:
    if ANON_KEY.startswith("PEGAR"):
        print("✗ Falta configurar ANON_KEY.")
        return 2

    print("=" * 62)
    print("  GUARISMO — Sellado diario")
    print("=" * 62)

    cab = cabezas()
    if not cab:
        print("\n✗ El archivo está vacío: no hay nada que sellar.")
        return 1

    raiz = _sha(_canon(cab))
    hoy = dt.datetime.now(dt.timezone.utc)

    doc = {
        "guarismo": "sello diario del archivo",
        "fecha": hoy.strftime("%Y-%m-%d"),
        "generado_utc": hoy.isoformat(timespec="seconds"),
        "cabezas": cab,
        "raiz": raiz,
        "algoritmo": "sha256(json canónico de 'cabezas')",
        "canonico": 'sort_keys=True, separators=(",",":"), ensure_ascii=False',
        "verificar": "https://github.com/Guarismoeconomico/guarismo-datos",
    }

    print("\n▸ Cabezas de cadena")
    for b, c in cab.items():
        print(f"   {b:<10} id={c['id']:<6} {c['hash_cadena'][:16]}…")
    print(f"\n▸ RAÍZ del día: {raiz}")

    DIR_SELLOS.mkdir(exist_ok=True)
    archivo = DIR_SELLOS / f"{doc['fecha']}.json"
    ots = archivo.parent / f"{archivo.name}.ots"

    # UN DÍA ANCLADO NO SE REESCRIBE. Ver el encabezado.
    anclar_existente = False
    if archivo.exists():
        try:
            previo = json.loads(archivo.read_text(encoding="utf-8"))
        except Exception:
            previo = {}
        if ots.exists():
            if previo.get("raiz") == raiz:
                print(f"\n   {archivo} ya existe con la misma raíz. Nada que hacer.")
            else:
                print(f"\n   {archivo} ya está ANCLADO: tiene .ots, y la raíz de")
                print("   ahora es otra (hubo capturas después). No se reescribe:")
                print("   el .ots prueba ESE archivo, y reescribirlo lo dejaría sin")
                print("   prueba. Lo nuevo queda cubierto por el sello de mañana.")
            return 0
        if previo.get("raiz") == raiz:
            print(f"\n   {archivo} existe con la misma raíz pero SIN .ots:"
                  " se ancla ahora.")
            anclar_existente = True
        else:
            print(f"\n   {archivo} existe con otra raíz y SIN .ots: se actualiza"
                  f" (hubo capturas nuevas hoy).")

    if not anclar_existente:
        archivo.write_text(_canon(doc) + "\n", encoding="utf-8")
        print(f"\n▸ Escrito {archivo}")

    ots_previo = ots.exists()

    # --- anclaje en Bitcoin -------------------------------------------------
    # Si falla, NO se aborta: la raíz ya quedó publicada en el repo, que es la
    # garantía mínima. El sello se puede reintentar después.
    print("\n▸ Anclando en Bitcoin (OpenTimestamps)…")
    try:
        r = subprocess.run(["ots", "stamp", str(archivo)],
                           capture_output=True, text=True, timeout=120)
        salida = (r.stdout + r.stderr).strip()
        for linea in salida.splitlines():
            print(f"   {linea}")
        if ots_previo:
            # Un .ots de ANTES al lado de un .json recién escrito no lo prueba.
            # No se publica: la corrida termina en rojo para que se vea.
            print(f"   ✗ ya había un {ots.name} de antes: NO prueba el .json")
            print("     recién escrito. No se publica. Revisar a mano.")
            return 1
        if r.returncode == 0 and ots.exists():
            print("   ✓ sello creado. Confirma en Bitcoin en unas horas;")
            print("     después correr:  python guarismo_sellador.py actualizar")
        else:
            print(f"   ⚠ no se generó el .ots (código {r.returncode}). "
                  "La raíz igual quedó publicada.")
    except FileNotFoundError:
        print("   ⚠ 'ots' no está instalado (pip install opentimestamps-client).")
        print("     La raíz igual quedó publicada en el repo.")
    except Exception as e:
        print(f"   ⚠ falló el sellado: {e}")
        print("     La raíz igual quedó publicada en el repo.")

    print("\n" + "=" * 62)
    print("  ✓ listo")
    print("=" * 62)
    return 0


def _info(ots: pathlib.Path):
    """(hash que prueba, atestaciones de Bitcoin) según `ots info`, sin red."""
    r = subprocess.run(["ots", "info", str(ots)],
                       capture_output=True, text=True, timeout=60)
    m = re.search(r"File sha256 hash: ([0-9a-f]{64})", r.stdout)
    return (m.group(1) if m else None), r.stdout.count("BitcoinBlockHeaderAttestation")


def actualizar() -> int:
    """Completa los sellos pendientes a medida que Bitcoin los confirma.

    Sobre una copia: ver "EL .bak CONGELABA LOS SELLOS" en el encabezado.
    """
    print("=" * 62)
    print("  GUARISMO — Actualización de sellos")
    print("=" * 62)

    if not DIR_SELLOS.exists():
        print("\n  (todavía no hay sellos)")
        return 0

    sellos = sorted(DIR_SELLOS.glob("*.json.ots"))
    if not sellos:
        print("\n  (no hay archivos .ots)")
        return 0

    crecieron = iguales = rechazados = 0
    for p in sellos:
        js = p.with_name(p.name[:-len(".ots")])
        try:
            antes_b = p.read_bytes()
            sha_json = hashlib.sha256(js.read_bytes()).hexdigest() if js.exists() else None
            prueba_antes, btc_antes = _info(p)
            with tempfile.TemporaryDirectory() as d:
                copia = pathlib.Path(d) / p.name
                copia.write_bytes(antes_b)
                r = subprocess.run(["ots", "upgrade", str(copia)],
                                   capture_output=True, text=True, timeout=120)
                despues_b = copia.read_bytes()
                if despues_b == antes_b:
                    iguales += 1
                    continue
                prueba, btc = _info(copia)
                motivo = None
                if prueba is None or prueba != prueba_antes:
                    motivo = "la copia prueba OTRO contenido"
                elif sha_json is not None and prueba != sha_json:
                    motivo = "la copia no prueba el .json de al lado"
                elif btc < btc_antes:
                    motivo = f"perdería atestaciones ({btc_antes} -> {btc})"
                if motivo:
                    rechazados += 1
                    print(f"\n▸ {p.name}  ⚠ NO SE REEMPLAZA: {motivo}")
                    for linea in (r.stdout + r.stderr).strip().splitlines():
                        print(f"   {linea}")
                    continue
                # Reemplazo atómico, desde la misma carpeta: nada queda a medias
                # ni suelto en sellos/.
                tmp = p.with_name(p.name + ".tmp")
                tmp.write_bytes(despues_b)
                os.replace(tmp, p)
                crecieron += 1
                print(f"\n▸ {p.name}  ✓ {len(antes_b)} -> {len(despues_b)} bytes · "
                      f"Bitcoin {btc_antes} -> {btc}")
        except FileNotFoundError:
            print("   ⚠ 'ots' no está instalado.")
            return 0
        except Exception as e:
            print(f"\n▸ {p.name}  ⚠ {e}")

    print("\n" + "=" * 62)
    print(f"  ✓ listo · {crecieron} crecieron · {iguales} sin cambios · "
          f"{rechazados} rechazados")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "sellar"
    if modo == "sellar":
        sys.exit(sellar())
    if modo == "actualizar":
        sys.exit(actualizar())
    print(f"Uso: python {sys.argv[0]} [sellar|actualizar]")
    sys.exit(2)
