# Pruebas automatizadas

    pip install -r requirements-dev.txt
    python -m pytest tests/ -v

## Qué cubre cada archivo

| Archivo | Qué prueba | Necesita |
|---|---|---|
| `test_coincidencia_clientes.py` | La regla "ante la duda no se asigna" al elegir cliente | nada |
| `test_rpa_combo_cliente.py` | El RPA real (Playwright) contra un combo `chosen` falso | `playwright install chromium` |
| `test_duplicidad_cortes.py` | Dedup incremental con cortes reales de BBVA | los cortes (ver abajo) |
| `test_identificacion_cortes.py` | Identificación de clientes + salud del catálogo | los cortes y `Catalogos/` |
| `test_e2e_stage.py` | Punta a punta contra SIPP **stage** (apagada por defecto) | credenciales y acceso a stage |
| `test_ui_flet.py` | La interfaz real: arranque, pestañas y carga de un corte (apagada por defecto) | `playwright install chromium` |

## Los cortes de ejemplo

`test_duplicidad_cortes.py` y `test_identificacion_cortes.py` usan dos cortes reales
de BBVA del mismo día (`Corte_1.xls` con 80 movimientos y `Corte_2.xls` con 172 =
los mismos 80 + 92 nuevos). **No están en el repositorio** porque traen datos de
clientes. Se buscan en `~/Downloads/MH_Ejemplo/BBVA`, o donde apunte la variable:

    MH_CORTES_DIR=/ruta/a/los/cortes python -m pytest tests/ -v

Si no están, esas pruebas se saltan (no fallan).

## Probar el RPA sin SIPP

`tests/combo_chosen_falso.html` reproduce el combo de clientes de SIPP (mismo DOM,
mismo filtrado por teclado que la librería `chosen`). Gracias a eso, la salvaguarda
de clientes se prueba en cualquier computadora **sin VPN ni credenciales**.

Validado además contra SIPP stage: con el catálogo real, buscar un cliente que no
existe borraba 24 caracteres y habría capturado el pago a
`COMERCIALIZADORA AGROINDUSTRIAL DEL NORTE`. Ahora no asigna y lo reporta.

## La prueba contra SIPP stage

`test_e2e_stage.py` captura movimientos de verdad en 'Ingresos Diversos - Agregar'
(cuenta BBVA ...7012) mezclando dos clientes válidos con uno inexistente, y verifica
que se capturen solo los dos buenos, que el malo quede reportado y que la tabla de
SIPP no tenga rastro de él. **No presiona Guardar**: al cerrar el navegador, stage
descarta los movimientos de prueba.

Está apagada por defecto porque toca un sistema externo y tarda ~45 s:

    MH_E2E_STAGE=1 python -m pytest tests/test_e2e_stage.py -v

La prueba se niega a correr si la URL no es la de stage.

## Las pruebas de interfaz

`test_ui_flet.py` levanta la app como servidor web (Flet sirve la misma interfaz
que en escritorio), la maneja con Playwright y verifica que arranque, que se pueda
navegar entre pestañas y que cargar `Corte_1.xls` muestre sus 80 movimientos.

    MH_UI_TESTS=1 python -m pytest tests/test_ui_flet.py -v

Flet dibuja con Flutter sobre un `<canvas>`, así que no hay HTML que clicar: se usa
el **árbol de accesibilidad** de Flutter (`<flt-semantics>`), que publica el texto de
cada control. `tests/ui_flet.py` encapsula esa mecánica; lo que hay que recordar es
que Flutter repone el activador del árbol cada vez que reconstruye la interfaz, y
que hacer `.click()` sobre un nodo semántico NO acciona el control — hay que hacer
un clic de ratón real sobre sus coordenadas.

**Diferencia con escritorio**: en la app instalada, el selector de archivos es el
del sistema operativo; aquí es el del navegador. El resto del flujo es el mismo
código, pero un fallo exclusivo del diálogo nativo de Windows no se detecta aquí.
