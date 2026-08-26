"""Prueba de punta a punta contra SIPP **stage**.

Captura movimientos reales en 'Ingresos Diversos - Agregar' mezclando clientes
válidos con uno inexistente, y verifica que el RPA capture los buenos, NO capture
el malo (ni se lo asigne a otro cliente) y arme el resumen para el usuario.

NO presiona Guardar: los movimientos quedan en la tabla sin comprometerse y se
descartan al cerrar el navegador.

Está APAGADA por defecto porque toca un sistema externo y tarda ~1 minuto.
Para correrla:

    MH_E2E_STAGE=1 python -m pytest tests/test_e2e_stage.py -v -s

Requiere credenciales guardadas (sipp_credenciales.json) y acceso a stage.
"""

import asyncio
import os

import pytest

from app.credenciales import cargar_credenciales

pytestmark = pytest.mark.skipif(
    os.getenv("MH_E2E_STAGE", "").strip() not in ("1", "true", "si", "yes"),
    reason="Prueba contra SIPP stage apagada; actívala con MH_E2E_STAGE=1",
)

CUENTA_BANCARIA = os.getenv("MH_E2E_CUENTA", "7012")   # BBVA ...0100647012
FECHA_OPERACION = os.getenv("MH_E2E_FECHA", "24/08/2026")

# Clientes que existen en el catálogo de stage, escritos como los escribiría la
# app (con la razón social que SIPP no guarda), más uno inventado.
CLIENTE_VALIDO_1 = "COMERCIALIZADORA JANYKAR S.A. DE C.V."
CLIENTE_VALIDO_2 = "Comercializadora Agroindustrial del Norte"
CLIENTE_INEXISTENTE = "COMERCIALIZADORA XYZ QUE NO EXISTE SA DE CV"

# (concepto, referencia, monto, cliente, sucursal, tipos)
MOVIMIENTOS = [
    ("PRUEBA AUTOMATIZADA - valido 1", "E2E-001", 1234.56, CLIENTE_VALIDO_1, None, []),
    ("PRUEBA AUTOMATIZADA - inexistente", "E2E-002", 999.99, CLIENTE_INEXISTENTE, None, []),
    ("PRUEBA AUTOMATIZADA - valido 2", "E2E-003", 500.00, CLIENTE_VALIDO_2, None, []),
]


@pytest.mark.asyncio
async def test_captura_los_validos_y_reporta_el_cliente_desconocido():
    os.environ["SIPP_ENV"] = "test"  # obliga a apuntar a stage, nunca a producción

    from playwright.async_api import async_playwright

    from rpa.automation import RPAAutomation

    usuario, password = cargar_credenciales()
    if not usuario:
        pytest.skip("No hay credenciales de SIPP guardadas")

    rpa = RPAAutomation(usuario, password, headless=True, log_fn=lambda m, n="info": None)
    assert "stage" in rpa.base_url, f"la prueba NO debe correr contra {rpa.base_url}"

    async with async_playwright() as p:
        navegador = await p.chromium.launch(headless=True, slow_mo=40)
        contexto = await navegador.new_context(**rpa._opciones_contexto(), locale="es-MX")
        pagina = await contexto.new_page()
        pagina.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
        try:
            await rpa._login(pagina)
            await rpa._configure_session(pagina)
            rpa._base_navegacion = pagina.url.split("#")[0]
            await rpa._navigate_to_ingresos_diversos_agregar(pagina)
            await rpa._configurar_encabezado_ingresos_diversos(
                pagina, CUENTA_BANCARIA, FECHA_OPERACION
            )

            agregados = 0
            for i, mov in enumerate(MOVIMIENTOS):
                if await rpa._agregar_un_movimiento_manual(pagina, i, len(MOVIMIENTOS), *mov):
                    agregados += 1

            # 1) Se capturaron los dos válidos y solo esos.
            assert agregados == 2, f"se capturaron {agregados} movimiento(s), se esperaban 2"
            assert rpa.contadores.get("sin_cliente_confiable") == 1
            assert rpa.contadores.get("errores") is None, "el rechazo no debe contar como error"

            # 2) El cliente inexistente quedó reportado, no asignado a otro.
            assert len(rpa.incidencias_cliente) == 1
            incidencia = rpa.incidencias_cliente[0]
            assert incidencia.cliente_buscado == CLIENTE_INEXISTENTE
            assert incidencia.referencia == "E2E-002"

            # 3) El resumen le explica al usuario qué hacer.
            texto = rpa.resumen_incidencias(len(MOVIMIENTOS))
            assert "1 de 3" in texto and "E2E-002" in texto

            # 4) Lo que REALMENTE quedó en la tabla de SIPP: dos filas, con sus
            #    clientes correctos y sin rastro del inexistente.
            filas = await pagina.evaluate("""() => Array.from(
                document.querySelectorAll('table tbody tr')).map(tr =>
                    Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim())
                        .join(' | ')).filter(t => t.includes('PRUEBA AUTOMATIZADA'))""")
            assert len(filas) == 2, f"la tabla de SIPP tiene {len(filas)} fila(s), se esperaban 2"
            todo = " ".join(filas)
            assert "JANYKAR" in todo
            assert "AGROINDUSTRIAL DEL NORTE" in todo
            assert "999.99" not in todo, "el movimiento rechazado no debe estar en la tabla"
        finally:
            # Sin Guardar: al cerrar, SIPP descarta los movimientos de prueba.
            await navegador.close()


# ──────────────────────────────────────────────────────────
# Dedup incremental contra el buzón H2H real
# ──────────────────────────────────────────────────────────

# Fecha con lotes en el buzón H2H de stage. Si stage se repuebla, ajústala (o
# pásala por MH_E2E_H2H_FECHA); la prueba se salta sola si no hay movimientos.
FECHA_H2H = os.getenv("MH_E2E_H2H_FECHA", "13/02/2026")


@pytest.mark.asyncio
async def test_los_movimientos_de_un_corte_anterior_no_se_re_suben():
    """El eslabón crítico del dedup, contra SIPP de verdad.

    Con BBVA no se sube archivo: los movimientos los trae el buzón H2H y la única
    forma de omitir los de un corte anterior es excluirlos en la previsualización.
    Aquí se traen movimientos reales, se pide excluir la mitad y se comprueba
    —leyendo `sn_Excluir` del propio Angular de SIPP, no lo que reporte el RPA—
    que queden marcados exactamente esos.

    No agrega al estado de cuenta ni guarda: al cerrar el navegador no queda nada.
    """
    os.environ["SIPP_ENV"] = "test"

    from playwright.async_api import async_playwright

    from rpa.automation import RPAAutomation

    usuario, password = cargar_credenciales()
    if not usuario:
        pytest.skip("No hay credenciales de SIPP guardadas")

    rpa = RPAAutomation(usuario, password, headless=True, log_fn=lambda m, n="info": None)
    assert "stage" in rpa.base_url, f"la prueba NO debe correr contra {rpa.base_url}"

    async with async_playwright() as p:
        navegador = await p.chromium.launch(headless=True, slow_mo=30)
        contexto = await navegador.new_context(**rpa._opciones_contexto(), locale="es-MX")
        pagina = await contexto.new_page()
        pagina.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
        try:
            await rpa._login(pagina)
            await rpa._configure_session(pagina)
            rpa._base_navegacion = pagina.url.split("#")[0]
            await rpa._navigate_to_ingresos_diversos_agregar(pagina)
            await rpa._configurar_encabezado_ingresos_diversos(
                pagina, CUENTA_BANCARIA, FECHA_OPERACION
            )

            if not await rpa._traer_movimientos_h2h(pagina, FECHA_H2H, FECHA_H2H):
                pytest.skip(f"El buzón H2H de stage no tiene lotes el {FECHA_H2H}")

            filas = await rpa._leer_filas_preview(pagina)
            assert len(filas) >= 4, f"hacen falta más movimientos para probar ({len(filas)})"

            # Se simula que las filas de índice PAR ya venían en un corte anterior.
            repetidas = [(ref, imp) for i, (ref, imp) in enumerate(filas) if i % 2 == 0 and imp]
            esperados = {i for i, (ref, imp) in enumerate(filas) if i % 2 == 0 and imp}

            excluidos = await rpa._eliminar_filas_preview(pagina, repetidas)

            de_mas = excluidos - esperados
            de_menos = esperados - excluidos
            assert not de_mas, (
                f"se excluyeron filas que SÍ debían subirse (ese cobro se perdería): {sorted(de_mas)}"
            )
            assert not de_menos, (
                f"quedaron sin excluir filas de un corte anterior "
                f"(se volverían a subir a SIPP): {sorted(de_menos)}"
            )

            # Comprobación independiente en el estado interno de SIPP.
            marcadas = await pagina.evaluate("""() => {
                const out = [];
                document.querySelectorAll("#modal-bodymodalDatosBanco [id^='EditarMovimiento_']")
                  .forEach(el => {
                    const tr = el.closest('tr');
                    const i = parseInt(el.id.replace('EditarMovimiento_',''));
                    let excl = null;
                    try {
                      const sc = window.angular && angular.element(tr).scope();
                      const it = sc && (sc.item || sc.mov || sc.row || (sc.$parent && sc.$parent.item));
                      if (it) excl = !!(it.sn_Excluir || it.snExcluir);
                    } catch (e) {}
                    out.push({i, excl});
                  });
                return out;
            }""")
            en_sipp = {m["i"] for m in marcadas if m["excl"]}
            if any(m["excl"] is not None for m in marcadas):
                assert en_sipp == esperados, (
                    "SIPP no quedó con las filas correctas marcadas como excluidas"
                )
            assert rpa.contadores.get("errores") is None
        finally:
            # Sin "Agregar al estado de cuenta" y sin Guardar.
            await navegador.close()
