import asyncio
import os
import re
from datetime import date
from typing import Callable, Dict, List, Optional, Tuple
from playwright.async_api import async_playwright, Page

# URL de login de SIPP. Producción por default; con SIPP_ENV=test (o stage/qa)
# el RPA apunta al entorno de pruebas. El resto de pantallas derivan su URL de
# page.url, así que basta con cambiar el login.
BASE_URL = "https://sipp.petroil.com.mx/login.html"
BASE_URL_TEST = "https://stage.sipp.petroil.dev/login.html"

_ENTORNOS_TEST = {"test", "stage", "staging", "qa", "pruebas", "dev"}


def es_modo_test() -> bool:
    """True si SIPP_ENV pide el entorno de pruebas."""
    return os.environ.get("SIPP_ENV", "").strip().lower() in _ENTORNOS_TEST


def resolver_base_url() -> str:
    return BASE_URL_TEST if es_modo_test() else BASE_URL

# ──────────────────────────────────────────────────────────
# JavaScript helpers that talk directly to AngularJS scopes
# ──────────────────────────────────────────────────────────
_JS_SET_EMPRESA = """() => {
    const sel = document.querySelector("select[ng-model='id_Empresa']");
    if (!sel) return false;
    const opt = Array.from(sel.options).find(o =>
        o.text.trim().toUpperCase().startsWith('PETROPLAZAS -')
    );
    if (!opt) return false;
    // Set native value and fire change so Angular + chosen both react
    sel.value = opt.value;
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    // Also trigger via Angular scope to be safe
    try {
        const scope = angular.element(sel).scope();
        scope.$apply(() => { scope.id_Empresa = opt.value; });
    } catch(e) {}
    // Tell chosen to refresh its UI
    if (typeof $ !== 'undefined') { $(sel).trigger('chosen:updated'); }
    return true;
}"""

_JS_SET_SUCURSAL = """() => {
    const sel = document.querySelector("select[ng-model='id_Sucursal']");
    if (!sel) return false;
    const opt = Array.from(sel.options).find(o =>
        o.text.toUpperCase().includes('CORPORATIVO')
    );
    if (!opt) return false;
    sel.value = opt.value;
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    try {
        const scope = angular.element(sel).scope();
        scope.$apply(() => { scope.id_Sucursal = opt.value; });
    } catch(e) {}
    if (typeof $ !== 'undefined') { $(sel).trigger('chosen:updated'); }
    return true;
}"""

_JS_SUCURSAL_LOADED = """() => {
    const sel = document.querySelector("select[ng-model='id_Sucursal']");
    return Boolean(sel && sel.options.length > 1);
}"""

_JS_SET_ESTATUS_VACIO = """() => {
    const sel = document.querySelector("select[ng-model='filtro.id_Estatus']");
    if (!sel) return;
    const scope = angular.element(sel).scope();
    scope.$apply(() => { scope.filtro.id_Estatus = ''; });
}"""

_JS_GRID_ROW_COUNT = """(gridAttr) => {
    const grid = document.querySelector(`[ng-grid="${gridAttr}"]`);
    return grid ? grid.querySelectorAll('.ngRow').length : 0;
}"""

# ──────────────────────────────────────────────────────────
# Helpers para la pantalla "Facturas - Listado" (búsqueda por folio)
# ──────────────────────────────────────────────────────────
_JS_GRID_FILAS_FACTURAS = """(gridAttr) => {
    const grid = document.querySelector(`[ng-grid="${gridAttr}"]`);
    if (!grid) return [];
    return Array.from(grid.querySelectorAll('.ngRow')).map(fila => {
        const clienteCelda = fila.querySelector('.col2');
        return {
            cliente: clienteCelda ? clienteCelda.textContent.trim() : null,
            texto: fila.textContent.trim(),
        };
    });
}"""

_RE_MONTO = re.compile(r"\$?\s?(\d{1,3}(?:,\d{3})*\.\d{2})")

# ──────────────────────────────────────────────────────────
# Helpers para "Ingresos Diversos - Agregar" (modal de previsualización)
# ──────────────────────────────────────────────────────────
_RE_NO_ALFANUM = re.compile(r"[^A-Za-z0-9]")


def _normalizar_referencia(texto: str) -> str:
    return _RE_NO_ALFANUM.sub("", texto or "").upper()


def _parsear_importe(texto: str) -> Optional[float]:
    limpio = (texto or "").replace("$", "").replace(",", "").strip()
    try:
        return float(limpio)
    except ValueError:
        return None


def _emparejar_movimiento(
    pendientes: List[Tuple[str, float, str]], referencia_modal: str, importe_modal: Optional[float]
) -> Optional[Tuple[str, float, str]]:
    """Empareja una fila del modal (referencia, importe) con un movimiento
    (referencia, abono, cliente) ya identificado en la app. Tolera que SIPP
    muestre la referencia sin el prefijo '_' u otros caracteres que sí guarda
    nuestro parser, comparando solo alfanuméricos."""
    ref_modal_norm = _normalizar_referencia(referencia_modal)

    candidatos = []
    for mov in pendientes:
        referencia, abono = mov[0], mov[1]
        if importe_modal is not None and abs(abono - importe_modal) > 0.01:
            continue
        ref_mov_norm = _normalizar_referencia(referencia)
        if ref_modal_norm and ref_mov_norm and (
            ref_modal_norm in ref_mov_norm or ref_mov_norm in ref_modal_norm
        ):
            candidatos.append(mov)

    if len(candidatos) == 1:
        return candidatos[0]

    if importe_modal is not None:
        solo_importe = [m for m in pendientes if abs(m[1] - importe_modal) <= 0.01]
        if len(solo_importe) == 1:
            return solo_importe[0]

    return None


class RPAAutomation:
    def __init__(
        self,
        username: str,
        password: str,
        headless: bool = False,
        log_fn: Callable = print,
        cancel_fn: Callable = lambda: False,
        base_url: Optional[str] = None,
        empresa_sipp: str = "ABASTECEDORA DE COMBUSTIBLES DEL PACIFICO",
        sucursal_sipp: str = "CORPORATIVO",
    ):
        self.username = username
        self.password = password
        self.headless = headless
        self._log_fn = log_fn
        self.should_cancel = cancel_fn
        # Empresa/sucursal a configurar en el login de SIPP (combos chosen).
        self.empresa_sipp = empresa_sipp
        self.sucursal_sipp = sucursal_sipp
        # Si no se pasa explícita, se resuelve de SIPP_ENV (prod por default).
        self.base_url = base_url or resolver_base_url()
        self._base_navegacion = ""  # origen SIPP para navegar pestañas nuevas
        self.skipped: List[str] = []
        self.not_found: List[str] = []

    def log(self, mensaje: str, nivel: str = "info") -> None:
        """Envía el mensaje al callback de la UI y además a la terminal: los
        diálogos de la app se cierran al terminar y sus logs se pierden, así que
        la consola queda como registro persistente para depurar."""
        try:
            self._log_fn(mensaje, nivel)
        except Exception:
            pass
        if self._log_fn is not print:
            print(f"[RPA {nivel}] {mensaje}", flush=True)

    # ──────────────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────────────
    async def run(
        self,
        folio_rows: List[Tuple[int, str]],
        on_progress: Callable = None,
    ) -> List[Tuple]:
        """
        Process every (row_num, folio) pair and return list of
        (row_num, cc, observaciones, subtotal, descuento, iva, gastos_envio, total_oc).
        """
        results: List[Tuple] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless,
                slow_mo=80,
                args=["--start-maximized"],
            )
            context = await browser.new_context(
                viewport={"width": 1440, "height": 900},
                locale="es-MX",
            )
            page = await context.new_page()

            # Dismiss any browser dialogs automatically
            page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))

            try:
                await self._login(page)
                await self._configure_session(page)
                await self._navigate_to_recepcion(page)

                processed = 0
                errors = 0
                seen: set = set()

                for row_num, folio in folio_rows:
                    if self.should_cancel():
                        self.log("Proceso cancelado por el usuario.", "warn")
                        break

                    folio = str(folio).strip()

                    # Duplicate guard
                    if folio in seen:
                        self.log(f"Folio duplicado omitido: {folio}", "warn")
                        self.skipped.append(folio)
                        continue
                    seen.add(folio)

                    if on_progress:
                        on_progress(processed, errors, folio)

                    try:
                        self.log(f"Procesando folio: {folio}", "info")
                        cc, obs, subtotal, descuento, iva, gastos_envio, total_oc, cuentas_contables = \
                            await self._process_folio(page, folio)
                        results.append((row_num, cc, obs, subtotal, descuento, iva, gastos_envio, total_oc, cuentas_contables))

                        if cc:
                            self.log(f"  CC: {cc}", "ok")
                        else:
                            self.log(f"  Sin datos de OC (folio: {folio})", "warn")

                        processed += 1

                    except Exception as exc:
                        self.log(f"  Error en folio {folio}: {exc}", "error")
                        results.append((row_num, "", "", "", "", "", "", "", []))
                        errors += 1
                        await self._recover_page(page)

                    if on_progress:
                        on_progress(processed, errors, folio)

            finally:
                await browser.close()

        return results

    # ──────────────────────────────────────────────────────
    # Step 1 — Login
    # ──────────────────────────────────────────────────────
    async def _login(self, page: Page):
        if self.base_url == BASE_URL_TEST:
            self.log(f"⚠ ENTORNO DE PRUEBAS (stage): {self.base_url}", "warn")
        self.log("Abriendo página de login...", "info")
        await page.goto(self.base_url, wait_until="networkidle", timeout=30_000)
        await page.wait_for_selector("#btnLogin", state="visible", timeout=15_000)
        await page.wait_for_timeout(400)

        self.log("Ingresando credenciales...", "info")
        await page.fill("#nb_Usuario", self.username)
        await page.fill("input[ng-model='de_password']", self.password)
        await page.wait_for_timeout(300)
        await page.click("#btnLogin")

        # Wait until we leave the login page
        await page.wait_for_function(
            "() => !window.location.href.includes('login.html')",
            timeout=30_000,
        )
        self.log("Login exitoso.", "ok")

    # ──────────────────────────────────────────────────────
    # Step 2 — Select company & branch via Chosen UI clicks
    # ──────────────────────────────────────────────────────
    async def _configure_session(self, page: Page):
        self.log("Configurando sesión...", "info")

        # Wait for the page and chosen to fully initialise
        await page.wait_for_selector(".chosen-container", state="visible", timeout=20_000)
        await page.wait_for_timeout(800)

        # Close password-update modal if it appears
        pwd_modal = page.locator("#divBloqueo_modalActualizarContrasena")
        if await pwd_modal.is_visible():
            self.log("Cerrando modal de contraseña predeterminada...", "warn")
            await page.locator(
                "#divBloqueo_modalActualizarContrasena .btn-cerrar25p"
            ).click()
            await page.wait_for_timeout(500)

        # ── Empresa: use Chosen UI so Angular sees a real user interaction ──
        # The Empresa chosen-container is the one whose underlying select has ng-model='id_Empresa'
        self.log(f"Seleccionando empresa: {self.empresa_sipp}...", "info")
        await self._chosen_select(page, "id_Empresa", self.empresa_sipp)
        self.log(f"Empresa seleccionada: {self.empresa_sipp}", "ok")
        await page.wait_for_timeout(1_500)

        # Wait for Sucursal options to load (server round-trip after empresa change)
        self.log("Esperando carga de sucursales...", "info")
        await page.wait_for_function(_JS_SUCURSAL_LOADED, timeout=15_000)
        await page.wait_for_timeout(500)

        # ── Sucursal ──
        await self._chosen_select(page, "id_Sucursal", self.sucursal_sipp)
        self.log(f"Sucursal seleccionada: {self.sucursal_sipp}", "ok")
        await page.wait_for_timeout(600)

        # Save session
        await page.click("button[ng-click='Guardar()']")
        await page.wait_for_timeout(2_500)
        self.log("Sesión guardada.", "ok")

    async def _chosen_select(self, page: Page, ng_model: str, text_filter: str):
        """
        Interact with a chosen-enhanced <select> by clicking through its UI.
        Finds the chosen container associated with the select that has the given
        ng-model, opens it, types to filter, and clicks the matching option.
        """
        # Find the chosen container via JS (it's inserted right after the hidden select)
        container_id = await page.evaluate(f"""() => {{
            const sel = document.querySelector("select[ng-model='{ng_model}']");
            if (!sel) return null;
            // chosen inserts a sibling div.chosen-container after the select
            let node = sel.nextElementSibling;
            while (node) {{
                if (node.classList && node.classList.contains('chosen-container')) {{
                    // Give it a temp id so Playwright can target it
                    if (!node.id) node.id = 'rpa_chosen_{ng_model}';
                    return node.id;
                }}
                node = node.nextElementSibling;
            }}
            return null;
        }}""")

        if not container_id:
            raise RuntimeError(f"No se encontró chosen-container para ng-model='{ng_model}'")

        container = page.locator(f"#{container_id}")

        # Click to open the dropdown
        await container.locator("a.chosen-single").click()
        await page.wait_for_timeout(300)

        # Type the filter text into the search box.
        # Chosen filtra su lista escuchando eventos de teclado (keyup); fill()
        # solo asigna value y NO dispara ese filtrado, así que en listas grandes
        # (ej. ~8000 clientes) la lista no se reduce y el <li> objetivo nunca
        # aparece. Tecleamos carácter por carácter para emitir keydown/keyup
        # reales y forzar el filtrado.
        search_input = container.locator(".chosen-search input")
        await search_input.click()
        await search_input.press_sequentially(text_filter, delay=15)
        await page.wait_for_timeout(500)

        # Click the first visible matching result
        result = container.locator(
            f".chosen-results li.active-result:has-text('{text_filter}')"
        ).first
        await result.wait_for(state="visible", timeout=5_000)
        await result.click()
        await page.wait_for_timeout(300)

    # ──────────────────────────────────────────────────────
    # Step 3 — Navigate to Recepción de Facturas
    # ──────────────────────────────────────────────────────
    async def _navigate_to_recepcion(self, page: Page):
        self.log("Navegando a Recepción de Facturas...", "info")
        base = page.url.split("#")[0]
        await page.goto(
            f"{base}#/RecepcionFacturas",
            wait_until="networkidle",
            timeout=30_000,
        )
        await page.wait_for_selector(
            "input[ng-model='filtro.nu_foliodocumento']",
            timeout=20_000,
        )
        # Pre-set Estatus to "Seleccionar" once; we keep it that way throughout
        await page.evaluate(_JS_SET_ESTATUS_VACIO)
        self.log("Página Recepción de Facturas lista.", "ok")

    # ──────────────────────────────────────────────────────
    # Búsqueda de cliente por folio en "Facturas - Listado"
    # ──────────────────────────────────────────────────────
    async def buscar_clientes_por_folio(
        self, folios: List[Tuple[str, Optional[float]]]
    ) -> Dict[Tuple[str, Optional[float]], Optional[str]]:
        """
        Abre su propia sesión de navegador (login + selección de empresa/sucursal),
        navega a Facturas - Listado y busca cada folio. Recibe pares (folio, monto)
        donde monto es el importe esperado (abono del movimiento bancario), usado
        para desambiguar cuando un folio devuelve varias facturas. Regresa un
        diccionario (folio, monto) -> nombre de cliente (o None si no se encontró
        o no se pudo desambiguar). Independiente del flujo de Recepción de
        Facturas (run/_process_folio), que sigue en construcción.
        """
        resultados: Dict[Tuple[str, Optional[float]], Optional[str]] = {}

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless,
                slow_mo=80,
                args=["--start-maximized"],
            )
            context = await browser.new_context(
                viewport={"width": 1440, "height": 900},
                locale="es-MX",
            )
            page = await context.new_page()
            page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))

            try:
                await self._login(page)
                await self._configure_session(page)
                await self._navigate_to_facturas_listado(page)

                for folio, monto in folios:
                    if self.should_cancel():
                        self.log("Búsqueda de folios cancelada por el usuario.", "warn")
                        break
                    try:
                        self.log(f"Buscando folio {folio} en SIPP...", "info")
                        cliente = await self._buscar_folio_en_listado(page, folio, monto)
                        resultados[(folio, monto)] = cliente
                        if cliente:
                            self.log(f"  Folio {folio} -> {cliente}", "ok")
                        else:
                            self.log(f"  Folio {folio} sin resultados.", "warn")
                    except Exception as exc:
                        self.log(f"  Error buscando folio {folio}: {exc}", "error")
                        resultados[(folio, monto)] = None
            finally:
                await browser.close()

        return resultados

    async def _navigate_to_facturas_listado(self, page: Page):
        self.log("Navegando a Facturas - Listado...", "info")
        base = page.url.split("#")[0]
        await page.goto(
            f"{base}#/FacturasListado",
            wait_until="networkidle",
            timeout=30_000,
        )
        await page.wait_for_selector(
            "input[ng-model='filtros.fl_FolioDocumento']",
            timeout=20_000,
        )
        # Rango de fechas amplio una sola vez: el folio ya es único, pero la
        # búsqueda también filtra por fecha de documento.
        await self._set_rango_fechas_amplio(page)
        self.log("Página Facturas - Listado lista.", "ok")

    async def _set_rango_fechas_amplio(self, page: Page):
        hoy = date.today().strftime("%d%m%Y")
        await self._llenar_fecha_mascara(page, "input[ng-model='dt_fh_inicio']:visible", "01012026")
        await self._llenar_fecha_mascara(page, "input[ng-model='dt_fh_fin']:visible", hoy)

    async def _llenar_fecha_mascara(self, page: Page, selector: str, texto: str):
        """Los campos de fecha usan ui-mask; se escriben carácter por carácter
        para que la máscara los acepte, en vez de un fill() directo."""
        campo = page.locator(selector)
        await campo.click()
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        await campo.type(texto, delay=40)
        await page.keyboard.press("Tab")
        await page.wait_for_timeout(300)

    async def _buscar_folio_en_listado(
        self, page: Page, folio: str, monto_esperado: Optional[float] = None
    ) -> Optional[str]:
        await page.fill("input[ng-model='filtros.fl_FolioDocumento']", folio)
        await page.click("button[ng-click='buscar()']")
        await page.wait_for_timeout(1_500)
        filas = await page.evaluate(_JS_GRID_FILAS_FACTURAS, "gridFacturas")

        if not filas:
            return None
        if len(filas) == 1:
            return filas[0]["cliente"]

        if monto_esperado is not None:
            for fila in filas:
                montos = [float(m.replace(",", "")) for m in _RE_MONTO.findall(fila["texto"])]
                if any(abs(monto - monto_esperado) < 0.01 for monto in montos):
                    return fila["cliente"]
            self.log(
                f"  Folio {folio}: {len(filas)} resultados, ninguno coincide con "
                f"el monto ${monto_esperado:,.2f}; se omite.",
                "warn",
            )
            return None

        self.log(
            f"  Folio {folio}: {len(filas)} resultados ambiguos y sin monto de "
            "referencia para desambiguar; se omite.",
            "warn",
        )
        return None

    # ──────────────────────────────────────────────────────
    # Carga de movimientos en "Ingresos Diversos - Agregar"
    # ──────────────────────────────────────────────────────
    async def cargar_ingresos_diversos(
        self,
        movimientos: List[Tuple[str, float, str]],
        cuenta_bancaria_nombre: str,
        fecha_operacion_ddmmyyyy: str,
        ruta_csv: str,
    ) -> None:
        """
        Abre su propia sesión (login + selección de empresa/sucursal), navega a
        "Ingresos Diversos - Agregar", llena Día de Operación y Cuenta Bancaria,
        sube ruta_csv (el mismo archivo ya procesado en la app) y, en el modal
        de previsualización que abre SIPP, asigna el cliente identificado a
        cada movimiento (referencia, abono, cliente) recibido en `movimientos`.

        No hace click en "Guardar": el browser se deja abierto para que el
        usuario revise y guarde manualmente desde SIPP.
        """
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=self.headless,
            slow_mo=25,  # más rápido: este flujo hace muchas acciones por fila
            args=["--start-maximized"],
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="es-MX",
        )
        page = await context.new_page()
        page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))

        self.log(
            f"Iniciando carga de Ingresos Diversos: cuenta '{cuenta_bancaria_nombre}', "
            f"fecha {fecha_operacion_ddmmyyyy}, {len(movimientos)} movimiento(s) identificado(s).",
            "info",
        )
        await self._login(page)
        await self._configure_session(page)
        self._base_navegacion = page.url.split("#")[0]
        await self._navigate_to_ingresos_diversos_agregar(page)
        await self._configurar_encabezado_ingresos_diversos(
            page, cuenta_bancaria_nombre, fecha_operacion_ddmmyyyy
        )

        self.log("  [paso] esperando campo 'Subir Excel'...", "info")
        archivo_input = page.locator("input[type='file'][ng-model='arfile']")
        await archivo_input.wait_for(state="attached", timeout=5_000)
        try:
            await page.wait_for_function(
                "() => { const el = document.querySelector(\"input[type='file'][ng-model='arfile']\");"
                " return el && !el.disabled; }",
                timeout=10_000,
            )
        except Exception:
            raise RuntimeError(
                "El campo 'Subir Excel' sigue deshabilitado. Verifica que la Cuenta "
                "Bancaria seleccionada corresponda al banco del archivo (Santander/BanRegio)."
            )

        self.log(f"  [paso] subiendo archivo bancario: {os.path.basename(ruta_csv)}...", "info")
        await archivo_input.set_input_files(ruta_csv)

        self.log("  [paso] esperando modal de previsualización (Datos Banco)...", "info")
        await page.wait_for_selector("#divBloqueo_modalDatosBanco", state="visible", timeout=20_000)
        await page.wait_for_timeout(500)

        filas = page.locator("#modal-bodymodalDatosBanco table tbody tr")
        total_filas = await filas.count()
        self.log(f"{total_filas} movimiento(s) en la previsualización.", "info")

        # Snapshot de (referencia, importe) de TODAS las filas antes de editar
        # ninguna: editar una fila re-renderiza la tabla y rompe las lecturas
        # posteriores (inner_text timeout).
        filas_datos: List[Tuple[str, Optional[float]]] = []
        for i in range(total_filas):
            fila = filas.nth(i)
            ref = (await fila.locator("td").nth(2).inner_text()).strip()
            imp = _parsear_importe(await fila.locator("td").nth(3).inner_text())
            filas_datos.append((ref, imp))

        pendientes = list(movimientos)
        asignados = 0
        omitidas = 0

        for i, (referencia_modal, importe_modal) in enumerate(filas_datos):
            if self.should_cancel():
                self.log("Asignación de clientes cancelada por el usuario.", "warn")
                break

            mov = _emparejar_movimiento(pendientes, referencia_modal, importe_modal)
            if mov is None:
                # Sin movimiento identificado: se deja SIN cliente (vacía). No la
                # editamos; se guardará tal cual para captura/identificación manual.
                omitidas += 1
                self.log(
                    f"  Fila {i + 1}/{total_filas} ({referencia_modal}): sin cliente identificado, se deja vacía.",
                    "warn",
                )
                continue

            cliente = mov[2]
            sucursal_sugerida = mov[3] if len(mov) > 3 else None
            forzar_sucursal = mov[4] if len(mov) > 4 else False
            pendientes.remove(mov)

            self.log(
                f"  Fila {i + 1}/{total_filas} ({referencia_modal}): asignando cliente '{cliente}'...",
                "info",
            )
            try:
                # NO usamos filas.nth(i): al entrar en edición, Angular inserta
                # sub-filas (ng-repeat MovSucursales), así que el índice posicional
                # deja de apuntar al movimiento correcto. El id #EditarMovimiento_i
                # es único y global → inmune a esas sub-filas. Scopeamos el combo
                # y la sucursal al <tr> ancestro de ese lápiz.
                pencil = page.locator(f"#EditarMovimiento_{i}")
                await pencil.scroll_into_view_if_needed()
                # dispatch_event dispara el ng-click directo (entra en edición y
                # commitea la fila anterior, como al presionar el lápiz a mano).
                await pencil.dispatch_event("click")
                await page.wait_for_timeout(400)

                fila = pencil.locator("xpath=ancestor::tr[1]")
                combo = fila.locator(".combo_Clientes")
                await combo.scroll_into_view_if_needed()
                await combo.locator("a.chosen-single").click()
                await page.wait_for_timeout(150)
                busqueda = combo.locator(".chosen-search input")
                await busqueda.click()
                resultados = combo.locator(".chosen-results li.active-result")

                # Escribimos el nombre completo de un golpe (rápido: una sola
                # acción, no carácter por carácter). Chosen filtra con eventos de
                # teclado, por eso usamos press_sequentially y no .fill(). Si el
                # nombre completo sobre-filtra a 0 (el nombre en SIPP viene
                # recortado, ej. "...3T" vs nuestro "...3T SA DE CV"), borramos
                # caracteres hasta que reaparezca una opción, buscando quedar en 1.
                await busqueda.press_sequentially(cliente, delay=0)
                await page.wait_for_timeout(200)
                count = await resultados.count()
                guard = 0
                while count == 0 and guard < len(cliente):
                    await busqueda.press("Backspace")
                    await page.wait_for_timeout(50)
                    count = await resultados.count()
                    guard += 1

                elegido = None
                if count == 1:
                    elegido = resultados.first
                elif count > 1:
                    # Varias opciones: preferimos match exacto por texto; si no hay,
                    # no adivinamos (se deja la fila vacía).
                    exacto = combo.locator(
                        f".chosen-results li.active-result:has-text('{cliente}')"
                    ).first
                    if await exacto.count():
                        elegido = exacto

                if elegido is None:
                    raise RuntimeError(
                        f"el dropdown no se redujo a una sola opción para '{cliente}'"
                    )

                cliente_asignado = (await elegido.inner_text()).strip()
                await elegido.click()
                await page.wait_for_timeout(200)

                # Al seleccionar el cliente, SIPP auto-sugiere la sucursal. Si la
                # dejó vacía ("Seleccionar") y el estado de cuenta nos dio una
                # sugerencia, la aplicamos (solo rellenamos vacías; respetamos lo
                # que SIPP ya puso). El usuario puede corregirla en SIPP.
                sucursal_select = fila.locator("select:visible").first

                async def _texto_sucursal():
                    try:
                        return await sucursal_select.evaluate(
                            "el => el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : ''"
                        )
                    except Exception:
                        return "(?)"

                etiqueta_suc = await _texto_sucursal()
                origen_suc = "auto-sugerida (SIPP)"
                vacia = etiqueta_suc.strip().lower() in ("", "seleccionar", "(?)")
                # La declarada se fuerza siempre; la sugerida solo rellena vacías.
                if sucursal_sugerida and (vacia or forzar_sucursal):
                    valor = await self._valor_opcion_en_select(sucursal_select, sucursal_sugerida)
                    if valor:
                        await sucursal_select.select_option(value=valor)
                        await page.wait_for_timeout(150)
                        etiqueta_suc = await _texto_sucursal()
                        origen_suc = "declarada (usuario)" if forzar_sucursal else "sugerida (estado de cuenta)"
                    else:
                        self.log(
                            f"    sucursal '{sucursal_sugerida}' no existe en el combo de SIPP.",
                            "warn",
                        )

                asignados += 1
                self.log(
                    f"  Fila {i + 1} ({referencia_modal}): cliente '{cliente_asignado}', "
                    f"sucursal '{etiqueta_suc}' [{origen_suc}].",
                    "ok",
                )
            except Exception as exc:
                self.log(f"  Error llenando fila {i + 1} ({referencia_modal}): {exc}", "error")
                await self._volcar_html(page, f"ingdiv_fila_{i + 1}")

        self.log(
            f"{asignados}/{total_filas} cliente(s) asignado(s), {omitidas} fila(s) sin "
            "cliente identificado (dejadas vacías).",
            "info",
        )

        # Guardado: secuencia de SIPP (cada "Guardar" dispara un confirm que
        # aceptamos). Al final, el modal de Subir Estado de Cuenta se CANCELA
        # para que el usuario adjunte el soporte y envíe a mano.
        try:
            self.log("  [paso] Guardar movimientos del archivo bancario...", "info")
            await page.click("button[ng-click='AgregarMovimientosArchivoBancario()']")
            await self._aceptar_confirms(page, "'¿Agregar los movimientos al estado de cuenta?'")

            self.log("  [paso] Guardar conciliación...", "info")
            await page.wait_for_selector(
                "button[ng-click='guardar()']", state="visible", timeout=15_000
            )
            await page.click("button[ng-click='guardar()']")
            await self._aceptar_confirms(page, "'¿Seguro que desea Guardar la conciliación?'")

            self.log("  [paso] esperando modal 'Subir Estado de Cuenta' para Cancelar...", "info")
            await page.wait_for_selector(
                "#divBloqueo_modalSubirEdoCuenta", state="visible", timeout=15_000
            )
            await page.wait_for_timeout(400)
            await page.locator("#divBloqueo_modalSubirEdoCuenta").locator(
                "button", has_text="Cancelar"
            ).first.click()
            self.log(
                "Conciliación guardada. Se canceló el envío: adjunta el archivo soporte y presiona "
                "Guardar y Enviar manualmente en SIPP cuando estés conforme.",
                "ok",
            )
        except Exception as exc:
            self.log(f"  No se pudo completar el guardado de la conciliación: {exc}", "error")
            await self._volcar_html(page, "ingdiv_guardar")
        # Browser deliberadamente abierto para que el usuario adjunte soporte y envíe.

    async def _navigate_to_ingresos_diversos_agregar(self, page: Page):
        self.log("Navegando a Ingresos Diversos - Agregar...", "info")
        # En pestañas nuevas page.url es "about:blank": usamos la base SIPP ya
        # guardada tras el login. En los flujos de una sola pestaña, _base_navegacion
        # está vacío y caemos a page.url (la pestaña ya está en SIPP).
        base = self._base_navegacion or page.url.split("#")[0]
        destino = f"{base}#/conciliacionagregar"
        self.log(f"  goto {destino}", "info")
        await page.goto(destino, wait_until="networkidle", timeout=30_000)
        await page.wait_for_selector("input[ng-model='dt_fh_Envio']", timeout=20_000)
        self.log("Página Ingresos Diversos - Agregar lista.", "ok")

    async def _configurar_encabezado_ingresos_diversos(
        self, page: Page, cuenta_bancaria_nombre: str, fecha_operacion_ddmmyyyy: str
    ) -> None:
        self.log(f"Estableciendo día de operación: {fecha_operacion_ddmmyyyy}...", "info")
        await self._llenar_fecha_mascara(
            page, "input[ng-model='dt_fh_Envio']", fecha_operacion_ddmmyyyy.replace("/", "")
        )

        self.log(f"Seleccionando cuenta bancaria: {cuenta_bancaria_nombre}...", "info")
        await self._chosen_select(page, "id_CuentaBancaria", cuenta_bancaria_nombre)
        await page.wait_for_timeout(500)

    # ──────────────────────────────────────────────────────
    # "Pagos de Contado" capturados del Buzón O365 → modal "Agregar
    # Movimientos" en "Ingresos Diversos - Agregar"
    # ──────────────────────────────────────────────────────
    async def cargar_pagos_contado(
        self,
        grupos: List[Tuple[str, List[Tuple[str, str, str, str, str, float, Optional[str]]]]],
        fecha_operacion_ddmmyyyy: str,
        enviar_automaticamente: bool = False,
    ) -> None:
        """
        Abre su propia sesión y arma una conciliación de "Ingresos Diversos -
        Agregar" POR CADA cuenta bancaria destino. `grupos` es una lista de
        (cuenta_bancaria_nombre, pagos), donde cada pago es la tupla
        (concepto, referencia, tipo_movimiento, cliente, plaza, monto, ruta_comprobante).

        tipo_movimiento debe ser "Anticipo" o "Contado".

        Como la pantalla de SIPP es por cuenta, cada cuenta se procesa en su
        propia pestaña del navegador (la primera reusa la pestaña inicial).

        Si enviar_automaticamente es False (default), el RPA agrega los
        movimientos de cada cuenta en su pestaña y se detiene: el usuario revisa
        y presiona Guardar/Guardar y Enviar manualmente en cada pestaña.

        Si es True, por cada cuenta el RPA presiona "Guardar", acepta el aviso
        de adjuntar soporte, sube los comprobantes de esa cuenta y presiona
        "Guardar y Enviar" — envía cada conciliación de forma definitiva, sin
        pausa para revisión humana.
        """
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=self.headless,
            slow_mo=80,
            args=["--start-maximized"],
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="es-MX",
        )
        page = await context.new_page()
        page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))

        await self._login(page)
        await self._configure_session(page)
        # Origen SIPP ya autenticado (ej. https://stage.sipp.petroil.dev/index.cfm).
        # Las pestañas nuevas arrancan en about:blank, así que no podemos derivar
        # la URL de page.url en ellas: guardamos la base aquí y la reutilizamos.
        self._base_navegacion = page.url.split("#")[0]
        self.log(f"Base de navegación SIPP: {self._base_navegacion}", "info")

        cuentas_con_error = 0

        for idx, (cuenta_bancaria_nombre, pagos) in enumerate(grupos):
            if self.should_cancel():
                self.log("Carga de pagos de contado cancelada por el usuario.", "warn")
                break

            # La primera cuenta reusa la pestaña inicial; las demás abren una
            # pestaña nueva para poder dejarlas todas abiertas en revisión.
            if idx == 0:
                page_cuenta = page
            else:
                page_cuenta = await context.new_page()
                page_cuenta.on("dialog", lambda d: asyncio.ensure_future(d.accept()))

            self.log(
                f"Cuenta {idx + 1}/{len(grupos)}: '{cuenta_bancaria_nombre}' "
                f"({len(pagos)} movimiento(s))...",
                "info",
            )
            await self._navigate_to_ingresos_diversos_agregar(page_cuenta)
            await self._configurar_encabezado_ingresos_diversos(
                page_cuenta, cuenta_bancaria_nombre, fecha_operacion_ddmmyyyy
            )

            agregados, _ = await self._agregar_movimientos_contado(page_cuenta, pagos)
            self.log(
                f"  {agregados}/{len(pagos)} movimiento(s) agregado(s) en '{cuenta_bancaria_nombre}'.",
                "info",
            )

            # Los comprobantes (el 7º campo de cada tupla) se arman DIRECTAMENTE
            # desde los pagos del grupo, no desde el loop de movimientos: así el
            # archivo de soporte se sube aunque la detección de cierre del modal
            # haya sido inestable. Filtramos los que existen en disco.
            comprobantes = [p[6] for p in pagos if p[6] and os.path.exists(p[6])]
            self.log(f"  {len(comprobantes)} comprobante(s) para subir en esta cuenta.", "info")

            if enviar_automaticamente:
                try:
                    await self._guardar_y_enviar_contado(page_cuenta, comprobantes)
                    # Enviada con éxito: cerramos su pestaña (en automático no se
                    # requiere revisión manual).
                    await page_cuenta.close()
                    self.log(f"  Pestaña de '{cuenta_bancaria_nombre}' cerrada tras enviar.", "ok")
                except Exception as exc:
                    cuentas_con_error += 1
                    self.log(
                        f"  No se pudo guardar/enviar la cuenta '{cuenta_bancaria_nombre}': {exc}. "
                        "Continúo con las demás; revisa esta pestaña manualmente.",
                        "error",
                    )

        if not enviar_automaticamente:
            self.log(
                "Movimientos agregados en una pestaña por cuenta. Revisa la tabla en "
                "SIPP y presiona Guardar, luego sube los comprobantes y Guardar y "
                "Enviar manualmente en cada pestaña (el RPA no envía automáticamente).",
                "ok",
            )
            # Browser deliberadamente abierto para revisión del usuario.
        elif cuentas_con_error == 0:
            # Todo enviado y sus pestañas cerradas: cerramos el navegador.
            self.log("Todas las conciliaciones se enviaron. Cerrando el navegador.", "ok")
            try:
                await context.close()
                await browser.close()
                await playwright.stop()
            except Exception:
                pass
        else:
            self.log(
                f"{cuentas_con_error} cuenta(s) quedaron abiertas para revisión manual; "
                "el navegador permanece abierto.",
                "warn",
            )

    async def _agregar_movimientos_contado(
        self,
        page: Page,
        pagos: List[Tuple[str, str, str, str, str, float, Optional[str]]],
    ) -> Tuple[int, List[str]]:
        """Agrega, vía el modal "Agregar Movimientos", cada pago de `pagos` en
        la pantalla ya posicionada en `page`. Regresa (agregados, comprobantes)."""
        comprobantes: List[str] = []
        agregados = 0

        for i, (concepto, referencia, tipo_movimiento, cliente, plaza, monto, ruta_comprobante) in enumerate(pagos):
            if self.should_cancel():
                self.log("Carga de pagos de contado cancelada por el usuario.", "warn")
                break

            self.log(f"Agregando movimiento {i + 1}/{len(pagos)}: {concepto[:60]}...", "info")
            paso = "abrir modal"
            try:
                self.log("  [paso] abriendo modal Agregar Movimientos...", "info")
                await page.click("button[ng-click='agregarMovimientos()']")
                await page.wait_for_selector(
                    "#divBloqueo_modalAgregarMovimientos", state="visible", timeout=10_000
                )
                await page.wait_for_timeout(300)
                modal = page.locator("#divBloqueo_modalAgregarMovimientos")

                paso = "llenar concepto/referencia/importe"
                self.log("  [paso] llenando concepto, referencia e importe...", "info")
                await modal.locator("#DE_CONCEPTO_Agregar").fill(concepto)
                await modal.locator("#DE_REFERENCIA_Agregar").fill(referencia)
                await modal.locator("#IM_MOVIMIENTO_Agregar").fill(f"{monto:.2f}")

                # Para este flujo, Contado siempre aplica; Anticipo se marca
                # además, cuando corresponde (no es excluyente con Contado).
                # force=True: los checkboxes de SIPP suelen ser <input> ocultos
                # con un estilo encima, y .check() normal puede no accionarlos.
                paso = "marcar check Contado"
                self.log("  [paso] marcando '¿Es Contado?'...", "info")
                await modal.locator("#chk_sn_Contado").check(force=True)
                if tipo_movimiento == "Anticipo":
                    paso = "marcar check Anticipo"
                    self.log("  [paso] marcando '¿Es Anticipo?'...", "info")
                    await modal.locator("#chk_sn_Anticipo").check(force=True)

                paso = "seleccionar cliente"
                self.log(f"  [paso] seleccionando cliente '{cliente}'...", "info")
                await self._chosen_select(page, "ID_CLIENTE", cliente)
                await page.wait_for_timeout(300)

                paso = "seleccionar plaza"
                self.log(f"  [paso] seleccionando plaza '{plaza}'...", "info")
                opcion = await self._opcion_plaza_por_nombre(page, "#ID_SUCURSAL_Agregar_0", plaza)
                if not opcion:
                    raise RuntimeError(f"No se encontró la plaza '{plaza}' en el combo de sucursales.")
                await modal.locator("#ID_SUCURSAL_Agregar_0").select_option(value=opcion["value"])
                self.log(f"    plaza seleccionada: '{opcion['text']}'", "ok")
                await modal.locator("#IM_MOVIMIENTO_Agregar_0").fill(f"{monto:.2f}")
                await page.wait_for_timeout(300)

                paso = "guardar movimiento"
                self.log("  [paso] clic en 'Guardar Movimiento'...", "info")
                await modal.locator("button.btn-info", has_text="Guardar Movimiento").click()
                # El modal se cierra agregando la clase ng-hide (Angular). Esperar
                # state="hidden" no sirve porque la animación de salida (fadeOutUp)
                # lo mantiene "visible" para Playwright; ng-hide es la señal fiable.
                # En stage el cierre puede tardar; si no aparece ng-hide a tiempo,
                # NO fallamos: el movimiento ya se agregó al guardar (se ve en la
                # tabla) y los comprobantes se recolectan aparte.
                try:
                    await page.wait_for_selector(
                        "#divBloqueo_modalAgregarMovimientos.ng-hide", timeout=30_000
                    )
                except Exception:
                    self.log("    (el modal tardó en cerrar; asumo movimiento agregado)", "warn")
                await page.wait_for_timeout(400)

                if ruta_comprobante:
                    comprobantes.append(ruta_comprobante)
                agregados += 1
                self.log(f"  Movimiento {i + 1} agregado: cliente '{cliente}', plaza '{plaza}'.", "ok")
            except Exception as exc:
                self.log(
                    f"  Error agregando movimiento {i + 1} en el paso '{paso}': {exc}",
                    "error",
                )
                await self._volcar_html(page, f"modal_mov_{i + 1}")

        return agregados, comprobantes

    async def _valor_opcion_en_select(self, select_locator, nombre: str):
        """Dado un <select> (locator) con opciones tipo 'MZO - Manzanillo',
        regresa el value de la opción que corresponde a `nombre` (match exacto
        contra la parte tras ' - ', luego texto exacto, luego substring), o None."""
        try:
            return await select_locator.evaluate(
                """(s, nombre) => {
                    const norm = (t) => (t || '').trim().toLowerCase();
                    const obj = norm(nombre);
                    const opts = Array.from(s.options);
                    for (const o of opts) {
                        const p = o.text.split(' - ');
                        if (norm(p[p.length - 1]) === obj) return o.value;
                    }
                    for (const o of opts) { if (norm(o.text) === obj) return o.value; }
                    for (const o of opts) { if (norm(o.text).includes(obj)) return o.value; }
                    return null;
                }""",
                nombre,
            )
        except Exception:
            return None

    async def _opcion_plaza_por_nombre(self, page: Page, select_selector: str, nombre: str):
        """Encuentra, en el <select> de plaza (opciones tipo 'TIJ - Tijuana'),
        la opción que corresponde a `nombre`. Regresa {value, text} o None.

        Playwright no soporta regex en select_option(label=...), y un match por
        substring sería ambiguo ('Mexicali' ⊂ 'HMexicali', y hay dos 'Tijuana').
        Por eso comparamos primero EXACTO contra la parte tras ' - ' (el nombre
        real de la sucursal), y solo si no hay, caemos a substring."""
        return await page.evaluate(
            """([sel, nombre]) => {
                const s = document.querySelector(sel);
                if (!s) return null;
                const norm = (t) => (t || '').trim().toLowerCase();
                const objetivo = norm(nombre);
                const opts = Array.from(s.options);
                // 1) Exacto contra la cola tras ' - ' (ej. 'Tijuana' en 'TIJ - Tijuana')
                for (const o of opts) {
                    const partes = o.text.split(' - ');
                    if (norm(partes[partes.length - 1]) === objetivo) {
                        return { value: o.value, text: o.text };
                    }
                }
                // 2) Exacto contra el texto completo
                for (const o of opts) {
                    if (norm(o.text) === objetivo) return { value: o.value, text: o.text };
                }
                // 3) Substring (último recurso)
                for (const o of opts) {
                    if (norm(o.text).includes(objetivo)) return { value: o.value, text: o.text };
                }
                return null;
            }""",
            [select_selector, nombre],
        )

    async def _aceptar_confirms(self, page: Page, etiqueta: str, intentos: int = 3) -> None:
        """Acepta los confirms encadenados de SIPP (overlay #divBloqueoAlert,
        botón Aceptar #__btn_aceptarConfirm__) hasta que no quede ninguno.

        El clic es tolerante: tras el último confirm, SIPP recarga (envía la
        conciliación), y un clic más se colgaría esperando un botón que ya no es
        accionable. En ese caso rompemos en silencio: el envío ya se completó."""
        for i in range(intentos):
            try:
                await page.wait_for_selector("#divBloqueoAlert", state="visible", timeout=8_000)
            except Exception:
                break
            await page.wait_for_timeout(400)
            self.log(f"  [paso] aceptando confirmación {etiqueta} ({i + 1})...", "info")
            try:
                await page.click("#__btn_aceptarConfirm__", timeout=8_000)
            except Exception:
                self.log("    (sin más confirmaciones accionables; el paso ya se completó)", "info")
                break
            try:
                await page.wait_for_selector("#divBloqueoAlert", state="hidden", timeout=8_000)
            except Exception:
                pass
            await page.wait_for_timeout(500)

    async def _volcar_html(self, page: Page, etiqueta: str) -> None:
        """Guarda el HTML actual de la página en /tmp para inspeccionar los
        selectores reales del modal cuando un paso falla."""
        try:
            contenido = await page.content()
            ruta = os.path.join("/tmp", f"mh_rpa_{etiqueta}.html")
            with open(ruta, "w", encoding="utf-8") as f:
                f.write(contenido)
            self.log(f"  HTML de depuración guardado en: {ruta}", "warn")
        except Exception as exc:
            self.log(f"  No se pudo volcar HTML de depuración: {exc}", "warn")

    async def _guardar_y_enviar_contado(self, page: Page, comprobantes: List[str]) -> None:
        """Guarda la conciliación de la cuenta en `page`, sube sus comprobantes
        y presiona "Guardar y Enviar" — envío definitivo, sin revisión."""
        paso = "guardar conciliación"
        try:
            self.log("  [paso] clic en 'Guardar' de la conciliación...", "info")
            await page.click("button[ng-click='guardar()']")

            # Tras 'Guardar' pueden aparecer uno o más confirms (overlay
            # #divBloqueoAlert, botón Aceptar #__btn_aceptarConfirm__), siendo el
            # último "¿Desea adjuntar el archivo de soporte y enviar la
            # conciliación?". Aceptamos cada uno hasta que aparezca el modal de
            # Subir Estado de Cuenta (o se agoten los confirms).
            paso = "confirmar guardado/adjuntar"
            for intento in range(4):
                modal_subir = page.locator("#divBloqueo_modalSubirEdoCuenta:not(.ng-hide)")
                if await modal_subir.count():
                    break
                try:
                    await page.wait_for_selector("#divBloqueoAlert", state="visible", timeout=10_000)
                except Exception:
                    break  # no hay (más) confirm pendiente
                await page.wait_for_timeout(400)  # dejar terminar la animación de entrada
                self.log(f"  [paso] aceptando confirmación ({intento + 1})...", "info")
                await page.click("#__btn_aceptarConfirm__")
                try:
                    await page.wait_for_selector("#divBloqueoAlert", state="hidden", timeout=10_000)
                except Exception:
                    pass
                await page.wait_for_timeout(500)

            paso = "modal subir estado de cuenta"
            self.log("  [paso] esperando modal 'Subir Estado de Cuenta'...", "info")
            await page.wait_for_selector(
                "#divBloqueo_modalSubirEdoCuenta", state="visible", timeout=30_000
            )

            # El input de archivo está duplicado en el DOM (mismo id en
            # #div_Contenido, deshabilitado, y en el modal). Scopeamos al modal
            # para resolver al input correcto (visible y habilitado).
            modal_subir = page.locator("#divBloqueo_modalSubirEdoCuenta")
            if comprobantes:
                paso = "subir comprobantes"
                self.log(f"  [paso] subiendo {len(comprobantes)} comprobante(s)...", "info")
                await modal_subir.locator("#ar_Comprobante").set_input_files(comprobantes)
                await page.wait_for_timeout(800)
            else:
                self.log("  Sin comprobantes para esta cuenta.", "warn")

            paso = "guardar y enviar conciliación"
            self.log("  [paso] clic en 'Guardar y Enviar'...", "info")
            await modal_subir.locator(
                "button[ng-click='guardarEdoCuenta_EnviarConciliacion()']"
            ).click()

            # Confirm final: "¿Desea guardar el documento y enviar a conciliación?"
            # (mismo overlay #divBloqueoAlert / botón #__btn_aceptarConfirm__).
            # Tras el último, SIPP recarga (envío completado); _aceptar_confirms
            # es tolerante a que el botón ya no sea accionable.
            paso = "confirmar envío final"
            await self._aceptar_confirms(page, "'¿Desea guardar el documento y enviar a conciliación?'")

            self.log(
                "Conciliación enviada con los comprobantes adjuntos. Revisa el resultado en SIPP.",
                "ok",
            )
        except Exception as exc:
            self.log(f"  Error en guardado/envío, paso '{paso}': {exc}", "error")
            await self._volcar_html(page, f"guardar_envio_{paso.replace(' ', '_')}")
            raise