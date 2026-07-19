#!/usr/bin/env python3
"""
Guarismo — Verificador público del archivo sellado
===================================================

Este script comprueba, de forma INDEPENDIENTE, que el archivo histórico de
Guarismo no fue alterado después de haber sido escrito.

No hace falta confiar en Guarismo: este código es abierto, los datos se leen
con la clave pública de solo-lectura, y cualquiera puede correrlo.

    pip install requests
    python verificar_archivo.py


QUÉ VERIFICA
------------
1. Que el hash de cada captura corresponda a su contenido.
   (si alguien editara un valor, el hash dejaría de coincidir)

2. Que cada captura esté encadenada a la anterior.
   (si alguien borrara o insertara una captura en el medio, la cadena se corta)

3. Que la primera captura de cada bucket sea un génesis legítimo.


QUÉ **NO** VERIFICA — importante, y lo decimos de frente
--------------------------------------------------------
Este verificador prueba INTEGRIDAD INTERNA: que el archivo no se tocó después
de escrito. NO prueba que el dato coincida con lo que publicó la fuente: para
eso hace falta el anclaje externo de tiempo (OpenTimestamps / sello TSA), que
es la próxima capa, y la observación independiente de un tercero.

Dicho sin vueltas: hoy esto demuestra que la historia no fue reescrita.
Todavía no demuestra que la historia sea cierta.


DEFINICIONES (no cambiar: la cadena entera depende de esto)
------------------------------------------------------------
  hash_contenido = sha256( contenido )                    en UTF-8
  hash_cadena    = sha256( hash_previo + hash_contenido ) en UTF-8

El campo `contenido` se guarda como TEXTO canónico ya serializado
(sort_keys=True, separators=(",",":"), ensure_ascii=False), así que para
verificar NO hay que volver a serializar nada: se hashea el texto tal cual.
"""

import hashlib
import sys

import requests

# ---------------------------------------------------------------------------
# Configuración pública. La clave `anon` de Supabase es de solo lectura y es
# pública por diseño (viaja en el JavaScript de la app). No es un secreto.
# ---------------------------------------------------------------------------
SUPABASE_URL = "https://rudepkizcatkhqprqjfw.supabase.co"
ANON_KEY = "sb_publishable_nwqxJVCewzhySYY1JZ6Lxw_DhbY0w8J"

TABLA = "guarismo_historico"
PAGINA = 1000
TIMEOUT = 30


def _sha(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def bajar_filas(bucket: str) -> list:
    """Trae todas las capturas de un bucket, ordenadas, paginando de a 1000."""
    headers = {"apikey": ANON_KEY, "Authorization": f"Bearer {ANON_KEY}"}
    filas, desde = [], 0
    while True:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{TABLA}",
            params={
                "bucket": f"eq.{bucket}",
                "select": "id,capturado_en,contenido,hash_contenido,hash_previo,hash_cadena",
                "order": "id.asc",
            },
            headers={**headers, "Range": f"{desde}-{desde + PAGINA - 1}"},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        lote = r.json()
        filas.extend(lote)
        if len(lote) < PAGINA:
            return filas
        desde += PAGINA


def verificar(filas: list) -> list:
    """Recorre la cadena y devuelve la lista de errores encontrados."""
    errores = []
    previo_esperado = None

    for pos, f in enumerate(filas):
        ident = f"id={f['id']} ({f['capturado_en']})"

        # 1. el hash del contenido corresponde al contenido guardado
        real = _sha(f["contenido"])
        if real != f["hash_contenido"]:
            errores.append(
                f"{ident}: CONTENIDO ALTERADO\n"
                f"      hash guardado : {f['hash_contenido']}\n"
                f"      hash real     : {real}"
            )

        # 2. el eslabón apunta a la captura anterior
        if f["hash_previo"] != previo_esperado:
            errores.append(
                f"{ident}: CADENA CORTADA\n"
                f"      esperaba hash_previo : {previo_esperado or '(génesis)'}\n"
                f"      encontró             : {f['hash_previo'] or '(génesis)'}"
            )

        # 3. el hash de cadena se computa como corresponde
        cad = _sha((f["hash_previo"] or "") + f["hash_contenido"])
        if cad != f["hash_cadena"]:
            errores.append(
                f"{ident}: ESLABÓN INVÁLIDO\n"
                f"      hash_cadena guardado : {f['hash_cadena']}\n"
                f"      hash_cadena real     : {cad}"
            )

        # 4. solo la primera captura puede ser génesis
        if pos > 0 and f["hash_previo"] is None:
            errores.append(f"{ident}: génesis duplicado (no es la primera captura)")

        previo_esperado = f["hash_cadena"]

    return errores


def main() -> int:
    if ANON_KEY.startswith("PEGAR"):
        print("✗ Falta configurar ANON_KEY en el encabezado de este archivo.")
        return 2

    print("=" * 66)
    print("  GUARISMO — Verificación del archivo sellado")
    print("=" * 66)

    total_errores = 0
    for bucket in ("oficial", "agregador"):
        print(f"\n▸ Bucket '{bucket}'")
        try:
            filas = bajar_filas(bucket)
        except Exception as e:
            print(f"   ✗ no se pudo leer: {e}")
            total_errores += 1
            continue

        if not filas:
            print("   (sin capturas todavía)")
            continue

        errores = verificar(filas)
        print(f"   capturas   : {len(filas)}")
        print(f"   desde      : {filas[0]['capturado_en']}")
        print(f"   hasta      : {filas[-1]['capturado_en']}")
        print(f"   última     : {filas[-1]['hash_cadena']}")

        if errores:
            print(f"   ✗ {len(errores)} PROBLEMA(S):")
            for e in errores:
                print(f"      · {e}")
            total_errores += len(errores)
        else:
            print("   ✓ cadena íntegra: ningún eslabón fue alterado")

    print("\n" + "=" * 66)
    if total_errores:
        print(f"  RESULTADO: ✗ {total_errores} problema(s). El archivo fue alterado.")
    else:
        print("  RESULTADO: ✓ archivo íntegro.")
        print("  (integridad interna; el anclaje externo de tiempo es otra capa)")
    print("=" * 66)
    return 1 if total_errores else 0


if __name__ == "__main__":
    sys.exit(main())
