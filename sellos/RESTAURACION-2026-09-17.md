# Restauración de sellos — 17-sep-2026

Entre el 20-jul y el 16-sep-2026, el workflow de sellado corría dos veces por día. La segunda
corrida reescribía `<fecha>.json` con una raíz posterior, mientras que `<fecha>.json.ots` seguía
probando la primera versión. En 58 de esos días, el `.json` a la vista no era el que probaba
su `.ots`.

## Qué se hizo

- `<fecha>.json` vuelve a ser la **versión anclada**: la primera del día, tomada del historial
  de git. Su sha256 es el que declara `<fecha>.json.ots`.
- La versión posterior, que no tiene prueba propia, queda como `<fecha>.sin_anclar.json`.
  No se borró nada: todas las versiones siguen en el historial del repositorio.
- Lo registrado en cada versión posterior quedó cubierto por el sello anclado del día
  siguiente (verificado en los 58 días).
- Desde el 17-sep-2026 el sellador no reescribe un día que ya tiene `.ots`.

## Cómo verificar

    ots info sellos/<fecha>.json.ots      # "File sha256 hash"
    sha256sum sellos/<fecha>.json         # tiene que ser el mismo

Los `.sin_anclar.json` no tienen prueba y no se verifican.

## Días restaurados (58)

Del 2026-07-20 al 2026-09-16, salvo 2026-08-02 (que ya coincidía).
