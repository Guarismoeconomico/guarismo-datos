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
"""

import datetime as dt
import hashlib
import json
import pathlib
import subprocess
import sys

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


def actualizar() -> int:
    """Completa los sellos pendientes una vez que Bitcoin los confirmó."""
    print("=" * 62)
    print("  GUARISMO — Actualización de sellos")
    print("=" * 62)

    if not DIR_SELLOS.exists():
        print("\n  (todavía no hay sellos)")
        return 0

    pendientes = sorted(DIR_SELLOS.glob("*.json.ots"))
    if not pendientes:
        print("\n  (no hay archivos .ots)")
        return 0

    for p in pendientes:
        print(f"\n▸ {p.name}")
        try:
            r = subprocess.run(["ots", "upgrade", str(p)],
                               capture_output=True, text=True, timeout=120)
            for linea in (r.stdout + r.stderr).strip().splitlines():
                print(f"   {linea}")
        except FileNotFoundError:
            print("   ⚠ 'ots' no está instalado.")
            return 0
        except Exception as e:
            print(f"   ⚠ {e}")

    print("\n" + "=" * 62)
    print("  ✓ listo")
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
