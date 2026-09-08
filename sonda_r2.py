#!/usr/bin/env python3
"""
SONDA DESCARTABLE — diagnostico del AccessDenied en guarismo-reservado.

ESTE ARCHIVO SE BORRA cuando el diagnostico este cerrado. No es parte de la
boveda, no escribe nada en produccion y no se agenda en ningun reloj.

QUE PREGUNTA CONTESTA
    La corrida normal del 8-sep dio AccessDenido en el primer PutObject, con
    el token verificado en el panel: R2 > guarismo-reservado, Item Write.
    O sea: la credencial es valida y la firma cierra (no dio
    InvalidAccessKeyId ni SignatureDoesNotMatch), pero R2 dice que no.

    HIPOTESIS A: los secrets de GitHub tienen el valor del token VIEJO.
        Se ve asi: listar guarismo-crudo FUNCIONA con estas credenciales.

    HIPOTESIS B: R2_ENDPOINT lleva el bucket pegado en el path.
        Se ve asi: el endpoint tiene path y falla en los DOS buckets.

QUE NO HACE
    No imprime ninguna credencial, ni entera ni en pedazos. Solo longitudes
    y la forma del endpoint sin el id de cuenta.
    La unica escritura que intenta es un objeto de prueba en el bucket
    reservado, bajo _sonda/, y lo borra despues si puede.
"""

import os
import sys
from urllib.parse import urlparse


def linea(t=""):
    print(t, flush=True)


def main():
    endpoint = os.getenv("R2_ENDPOINT") or ""
    key = os.getenv("R2_RESERVADO_ACCESS_KEY_ID") or ""
    secret = os.getenv("R2_RESERVADO_SECRET_ACCESS_KEY") or ""

    linea("=== 1. LO QUE LLEGO AL RUNNER (sin revelar nada) ===")
    linea(f"  R2_ENDPOINT presente          : {bool(endpoint)}  (len {len(endpoint)})")
    linea(f"  ACCESS_KEY_ID presente        : {bool(key)}  (len {len(key)})")
    linea(f"  SECRET_ACCESS_KEY presente    : {bool(secret)}  (len {len(secret)})")
    for nom, val in (("ACCESS_KEY_ID", key), ("SECRET", secret)):
        if val != val.strip():
            linea(f"  *** {nom} tiene espacios o salto de linea en los bordes")

    if not (endpoint and key and secret):
        linea("  *** falta alguna variable — se corta aca")
        return 2

    u = urlparse(endpoint)
    linea()
    linea("=== 2. FORMA DEL ENDPOINT (hipotesis B) ===")
    linea(f"  esquema                       : {u.scheme}")
    linea(f"  host termina en               : "
          f"{'.r2.cloudflarestorage.com' if u.netloc.endswith('.r2.cloudflarestorage.com') else u.netloc[-30:]}")
    linea(f"  PATH                          : {u.path!r}   <-- tiene que ser '' o '/'")
    if u.path.strip("/"):
        linea(f"  *** HIPOTESIS B: el endpoint lleva '{u.path.strip('/')}' pegado en el path.")
        linea("      boto3 lo antepone a la ruta y toda escritura cae en otro lado.")

    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError

    s3 = boto3.client(
        "s3", endpoint_url=endpoint.rstrip("/"),
        aws_access_key_id=key, aws_secret_access_key=secret,
        region_name="auto",
        config=Config(signature_version="s3v4",
                      retries={"max_attempts": 2, "mode": "standard"}))

    def probar(que, fn):
        try:
            r = fn()
            linea(f"  {que:<44} OK   {r}")
            return True, None
        except ClientError as e:
            cod = e.response.get("Error", {}).get("Code")
            linea(f"  {que:<44} {cod}")
            return False, cod
        except Exception as e:
            linea(f"  {que:<44} {type(e).__name__}: {e}")
            return False, type(e).__name__

    linea()
    linea("=== 3. LECTURA EN LOS DOS BUCKETS (hipotesis A) ===")
    ok_res, _ = probar("list guarismo-reservado", lambda: str(
        s3.list_objects_v2(Bucket="guarismo-reservado", MaxKeys=1).get("KeyCount")) + " objeto(s)")
    ok_crudo, _ = probar("list guarismo-crudo", lambda: str(
        s3.list_objects_v2(Bucket="guarismo-crudo", MaxKeys=1).get("KeyCount")) + " objeto(s)")

    if ok_crudo:
        linea("  *** HIPOTESIS A: estas credenciales ALCANZAN guarismo-crudo.")
        linea("      El token de ese bucket no deberia poder. Los secrets de")
        linea("      GitHub tienen el valor del token viejo.")

    linea()
    linea("=== 4. ESCRITURA DE PRUEBA (bajo _sonda/, se borra) ===")
    clave = "_sonda/prueba.txt"
    ok_put, cod = probar(f"put {clave} en guarismo-reservado", lambda: (
        s3.put_object(Bucket="guarismo-reservado", Key=clave,
                      Body=b"sonda", ContentType="text/plain"), "escrito")[1])
    if ok_put:
        probar("delete del objeto de prueba", lambda: (
            s3.delete_object(Bucket="guarismo-reservado", Key=clave), "borrado")[1])

    linea()
    linea("=== VEREDICTO ===")
    if ok_put:
        linea("  La escritura ANDA. El AccessDenied de la corrida normal no era")
        linea("  el token: mirar la clave exacta que se intento escribir.")
    elif ok_crudo:
        linea("  HIPOTESIS A. Regenerar los dos secrets desde el token")
        linea("  Guarismo_Reservado. No hace falta tocar nada en Cloudflare.")
    elif u.path.strip("/"):
        linea("  HIPOTESIS B. El endpoint lleva el bucket en el path.")
    else:
        linea("  NINGUNA DE LAS DOS. Credencial valida, endpoint limpio, y")
        linea("  igual no lee ni escribe el bucket reservado. Sospechar")
        linea("  propagacion del token o que el Access Key mostrado al crearlo")
        linea("  no corresponde a este token. Regenerar y volver a probar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
