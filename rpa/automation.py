import asyncio
import os
import re
import unicodedata
from datetime import date, datetime
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

# Folios ("Estado de Cuenta", col 1) de las conciliaciones listadas. Se omiten las
# CANCELADO (col 9 = Estatus): no cuentan como movimientos ya subidos.
_JS_FOLIOS_LISTADO = """() => {
    const out = [];
    document.querySelectorAll(".ngRow").forEach(r => {
        const cells = r.querySelectorAll('.ngCell');
        const folio = cells[1] ? cells[1].innerText.trim() : '';
        const estatus = cells[9] ? cells[9].innerText.trim().toUpperCase() : '';
        if (folio && !estatus.includes('CANCELADO')) out.push(folio);
    });
    return [...new Set(out)];
}"""

# Movimientos de una conciliación abierta: (abono, cliente, sucursal) por fila.
_JS_MOVS_CONCILIACION = """() => {
    const out = [];
    document.querySelectorAll("tr[ng-repeat='item in Listado']").forEach(tr => {
        const tds = tr.querySelectorAll(':scope > td');
        if (tds.length < 13) return;
        out.push({
            abono: tds[3] ? tds[3].innerText.trim() : '',
            cliente: tds[11] ? tds[11].innerText.trim() : '',
            sucursal: tds[12] ? tds[12].innerText.trim() : '',
        });
    });
    return out;
}"""

# Selecciona en un <select> (ng-model) la opción cuyo texto coincide/incluye.
_JS_SELECT_OPTION_POR_TEXTO = """([ngModel, texto]) => {
    const sel = document.querySelector(`select[ng-model='${ngModel}']`);
    if (!sel) return false;
    const norm = t => (t||'').trim().toLowerCase();
    const obj = norm(texto);
    let opt = [...sel.options].find(o => norm(o.text) === obj)
           || [...sel.options].find(o => norm(o.text).includes(obj));
    if (!opt) return false;
    sel.value = opt.value;
    sel.dispatchEvent(new Event('change', {bubbles: true}));
    try { const s = angular.element(sel).scope(); if (s && !s.$$phase) s.$apply(); } catch (e) {}
    return opt.text;
}"""

_JS_SELECT_ALL_H2H = """() => {
    if (!window.angular) return 'no-angular';
    const el = document.querySelector("[ng-grid='gridMovimientosH2H']");
    if (!el) return 'no-grid';
    const scope = angular.element(el).scope();
    const opts = scope.gridMovimientosH2H;
    // 1) API nativa de ngGrid: selectAll(true) marca TODA la data (aunque esté paginada).
    try {
        if (opts && typeof opts.selectAll === 'function') {
            opts.selectAll(true);
            if (!scope.$$phase) scope.$apply();
            return 'selectAll:' + ((scope.MovimientosH2HSeleccionados || []).length);
        }
    } catch (e) {}
    // 2) Respaldo: poblar el arreglo de seleccionados y marcar filas.
    try {
        const data = scope.MovimientosH2H || [];
        if (Array.isArray(scope.MovimientosH2HSeleccionados)) {
            scope.MovimientosH2HSeleccionados.length = 0;
            data.forEach(m => scope.MovimientosH2HSeleccionados.push(m));
        }
        const ng = opts && opts.ngGrid;
        const rows = ng && ng.rowFactory && ng.rowFactory.parsedData;
        if (rows) rows.forEach(r => { if (r && r.entity) r.selected = true; });
        if (!scope.$$phase) scope.$apply();
        return 'fallback:' + ((scope.MovimientosH2HSeleccionados || []).length);
    } catch (e) { return 'error:' + e.message; }
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
        const sucursalCelda = fila.querySelector('.col0');
        const folioCelda = fila.querySelector('.col1');
        const clienteCelda = fila.querySelector('.col2');
        return {
            sucursal: sucursalCelda ? sucursalCelda.textContent.trim() : null,
            folio: folioCelda ? folioCelda.textContent.trim() : null,
            cliente: clienteCelda ? clienteCelda.textContent.trim() : null,
            texto: fila.textContent.trim(),
        };
    });
}"""

_RE_MONTO = re.compile(r"\$?\s?(\d{1,3}(?:,\d{3})*\.\d{2})")


def _norm_folio(texto: Optional[str]) -> str:
    """Normaliza un folio para comparar: solo alfanuméricos, en mayúsculas
    (ej. 'FCL 190541' y 'FCL190541' quedan iguales)."""
    return re.sub(r"[^A-Z0-9]", "", (texto or "").upper())


# ──────────────────────────────────────────────────────────
# Selectores para el flujo de Factoraje (BAJA FERRIES), confirmados con el HTML
# real de SIPP (ingresosdiv.html / Ingdiv_verconciliacion.html / movimientosmodal.html).
# ──────────────────────────────────────────────────────────
# Listado "Ingresos Diversos": buscar por Estado de Cuenta (= id_Conciliacion).
_SEL_FACTORAJE_INPUT_FOLIO = "input[ng-model='filtros.id_Conciliacion']"
_SEL_FACTORAJE_BTN_BUSCAR = "[ng-click='listar()']"
_SEL_FACTORAJE_BTN_ABRIR = "[ng-click='Visualizar(row)']"
# Vista de la conciliación: filas de movimientos y guardar del editor.
_SEL_FACTORAJE_FILA = "tr[ng-repeat='item in Listado']"
_SEL_FACTORAJE_BTN_GUARDAR = "button[ng-click='guardarMovimiento(true, sn_Identificacion)']"


# Fuerza la sección de factoraje en el modal de edición cuando el checkbox viene
# deshabilitado (ng-disabled="sn_Identificacion || SN_MONEDEROELECTRONICO"): se
# actúa sobre la instancia VISIBLE, se limpian los flags que lo deshabilitan y se
# marca SN_FACTORAJE en el scope de Angular.
_JS_FORZAR_FACTORAJE = """() => {
    // Checkbox de la instancia VISIBLE (no dentro de un ancestro .ng-hide).
    const enModalVisible = e => { let n = e; while (n) {
        if (n.classList && n.classList.contains('ng-hide')) return false; n = n.parentElement; } return true; };
    const els = Array.from(document.querySelectorAll('input[ng-model="SN_FACTORAJE"]'));
    const el = els.find(enModalVisible) || els.find(e => e.offsetParent !== null) || els[0];
    if (!el || !window.angular) return 'no-el/angular';
    const $el = angular.element(el);
    const scope = $el.scope();
    if (!scope) return 'no-scope';
    const apply = () => { if (scope.$$phase) scope.$evalAsync(); else scope.$apply(); };
    // 1) Habilitar el checkbox (apagar ng-disabled) y aplicar el digest.
    scope.sn_Identificacion = false;
    scope.SN_MONEDEROELECTRONICO = false;
    apply();
    // 2) Marcar SN_FACTORAJE por el ngModelController (setea el scope correcto y
    //    dispara ng-change), ya con el input habilitado.
    const ctrl = $el.controller('ngModel');
    if (ctrl) { ctrl.$setViewValue(true); ctrl.$render(); } else { scope.SN_FACTORAJE = true; }
    apply();
    return 'ctrl=' + (!!ctrl) + ' SN_FACTORAJE=' + scope.SN_FACTORAJE + ' disabled=' + el.disabled;
}"""


# Diagnóstico: valores de los flags que deshabilitan el checkbox de factoraje.
_JS_FLAGS_FACTORAJE = """() => {
    const enVis = e => { let n=e; while(n){ if(n.classList&&n.classList.contains('ng-hide')) return false; n=n.parentElement;} return true; };
    const els = Array.from(document.querySelectorAll('input[ng-model="SN_FACTORAJE"]'));
    const el = els.find(enVis) || els[0];
    if (!el || !window.angular) return 'no-el/angular';
    const s = angular.element(el).scope();
    return 'sn_Identificacion=' + (s && s.sn_Identificacion) +
           ' SN_MONEDEROELECTRONICO=' + (s && s.SN_MONEDEROELECTRONICO) +
           ' disabled=' + el.disabled;
}"""

# Marca el checkbox de factoraje de forma atómica: apaga los flags de
# ng-disabled, habilita el input y hace un CLICK REAL del DOM (dispara el evento
# change → ng-model, igual que un clic humano, por lo que SÍ persiste al guardar
# —a diferencia de $setViewValue—). Todo en un bloque para ganarle al digest que
# lo re-deshabilita.
_JS_CLICK_FACTORAJE = """() => {
    const enVis = e => { let n=e; while(n){ if(n.classList&&n.classList.contains('ng-hide')) return false; n=n.parentElement;} return true; };
    const els = Array.from(document.querySelectorAll('input[ng-model="SN_FACTORAJE"]'));
    const el = els.find(enVis) || els.find(e => e.offsetParent !== null) || els[0];
    if (!el || !window.angular) return 'no-el/angular';
    const s = angular.element(el).scope();
    if (!s) return 'no-scope';
    s.sn_Identificacion = false;
    s.SN_MONEDEROELECTRONICO = false;
    el.disabled = false;
    if (!el.checked) el.click();
    if (s.$$phase) s.$evalAsync(); else s.$apply();
    return 'checked=' + el.checked + ' SN_FACTORAJE=' + s.SN_FACTORAJE + ' disabled=' + el.disabled;
}"""

# Habilita el checkbox de factoraje apagando los flags de ng-disabled (sin tocar
# SN_FACTORAJE: éste se marca luego con un clic REAL en el label, que sí persiste).
_JS_HABILITAR_FACTORAJE = """() => {
    const enVis = e => { let n=e; while(n){ if(n.classList&&n.classList.contains('ng-hide')) return false; n=n.parentElement;} return true; };
    const els = Array.from(document.querySelectorAll('input[ng-model="SN_FACTORAJE"]'));
    const el = els.find(enVis) || els[0];
    if (!el || !window.angular) return 'no-el/angular';
    const s = angular.element(el).scope();
    if (!s) return 'no-scope';
    s.sn_Identificacion = false;
    s.SN_MONEDEROELECTRONICO = false;
    if (s.$$phase) s.$evalAsync(); else s.$apply();
    return 'disabled=' + el.disabled;
}"""


# Setea el interés de factoraje por el modelo de Angular (cuando el campo no se
# hace visible). Replica el ng-change del input: recalcula IM_COMISION y el
# importe. Recibe el interés como string.
_JS_SET_INTERES = """(interes) => {
    const enVisible = e => { let n = e; while (n) {
        if (n.classList && n.classList.contains('ng-hide')) return false; n = n.parentElement; } return true; };
    const els = Array.from(document.querySelectorAll('input[ng-model="IM_FACTORAJEINTERES"]'));
    const el = els.find(enVisible) || els.find(e => e.offsetParent !== null) || els[0];
    if (!el || !window.angular) return 'no-el/angular';
    const $el = angular.element(el);
    const scope = $el.scope();
    if (!scope) return 'no-scope';
    const val = parseFloat(interes) || 0;
    const ctrl = $el.controller('ngModel');
    if (ctrl) { ctrl.$setViewValue(val); ctrl.$render(); } else { scope.IM_FACTORAJEINTERES = val; }
    scope.IM_COMISION = val + (scope.IM_FACTORAJECOMISION || 0);
    if (typeof scope.ActualizarImporteMovimiento2 === 'function') {
        try { scope.ActualizarImporteMovimiento2(); } catch (e) {}
    }
    if (scope.$$phase) scope.$evalAsync(); else scope.$apply();
    return 'IM_FACTORAJEINTERES=' + scope.IM_FACTORAJEINTERES + ' IM_COMISION=' + scope.IM_COMISION;
}"""


def _emparejar_item_factoraje(referencia_fila: str, texto_fila: str, items: List[dict]) -> Optional[dict]:
    """Empareja una fila de la conciliación con un item del PDF: por REFERENCIA
    (celda, la más confiable), luego por folio (FLM/FMZ en el texto), luego por
    monto neto."""
    ref_fila = _norm_folio(referencia_fila)
    for it in items:
        ref = _norm_folio(it.get("referencia"))
        if ref and ref == ref_fila:
            return it
    texto_norm = _norm_folio(texto_fila)
    for it in items:
        folio = _norm_folio(it.get("folio"))
        if folio and folio in texto_norm:
            return it
    montos = [float(m.replace(",", "")) for m in _RE_MONTO.findall(texto_fila)]
    for it in items:
        abono = it.get("abono")
        if abono is not None and any(abs(v - abono) < 0.01 for v in montos):
            return it
    return None

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


def _norm_txt(texto) -> str:
    """Mayúsculas, sin acentos y solo alfanumérico+espacio, para comparar nombres
    de cliente/sucursal entre la app y SIPP."""
    t = unicodedata.normalize("NFKD", str(texto or "").upper())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^A-Z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _cliente_sin_codigo(texto) -> str:
    """Quita el prefijo 'NNNNN - ' del cliente que muestra SIPP
    (ej. '05881 - LOGISTICA TEROMO' → 'LOGISTICA TEROMO')."""
    return re.sub(r"^\s*\d+\s*-\s*", "", str(texto or "")).strip()


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
        contador_fn: Optional[Callable] = None,
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
        # Conteos de lo que el RPA REALMENTE hizo (no de lo que se le pidió). La UI
        # los pinta como resumen para que el usuario no tenga que leer el log crudo:
        # los mensajes técnicos (reintentos, volcados de HTML) iban en naranja/rojo y
        # se leían como "falló" aunque la carga hubiera salido bien.
        self.contadores: dict = {}
        self._contador_fn = contador_fn
        # Navegador que algunos flujos dejan ABIERTO al terminar (para que el usuario
        # adjunte el soporte y envíe en SIPP). Se guardan para poder cerrarlo desde la
        # UI: antes no se cerraba nunca ni se hacía playwright.stop(), así que la
        # ventana se quedaba girando y el proceso quedaba vivo.
        self._playwright = None
        self._browser = None

    @property
    def navegador_abierto(self) -> bool:
        return self._browser is not None

    async def cerrar_navegador(self) -> None:
        """Cierra el navegador dejado abierto y libera el proceso de Playwright.
        Idempotente: se puede llamar aunque ya esté cerrado."""
        browser, playwright = self._browser, self._playwright
        self._browser = self._playwright = None
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception:
                pass

    async def traer_al_frente(self) -> None:
        """Trae al frente la pestaña del navegador dejado abierto."""
        if self._browser is None:
            return
        try:
            for contexto in self._browser.contexts:
                for pagina in contexto.pages:
                    await pagina.bring_to_front()
                    return
        except Exception:
            pass

    def contar(self, clave: str, n: int = 1) -> None:
        """Suma n al contador `clave` y notifica a la UI para refrescar el resumen."""
        self.contadores[clave] = self.contadores.get(clave, 0) + n
        if self._contador_fn is not None:
            try:
                self._contador_fn(dict(self.contadores))
            except Exception:
                pass

    def _opciones_contexto(self) -> dict:
        """Opciones de tamaño del contexto del navegador.

        En modo VISIBLE se usa `no_viewport`: al lanzar con --start-maximized y a la
        vez fijar viewport=1440x900, la VENTANA queda maximizada pero la PÁGINA se
        renderiza a 1440x900 dentro de ella. Al redimensionar o hacer zoom, el
        contenido se corta y los botones de SIPP (Aceptar/Guardar) quedan fuera de
        vista. Sin viewport fijo, la página toma el tamaño real de la ventana y es
        responsiva.

        En HEADLESS no hay ventana real de la cual tomar el tamaño, así que ahí sí se
        fija un viewport amplio (si no, Chromium usa uno pequeño por defecto y los
        modales de SIPP no caben)."""
        if self.headless:
            return {"viewport": {"width": 1600, "height": 1000}}
        return {"no_viewport": True}

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
                **self._opciones_contexto(),
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
        await page.wait_for_timeout(400)

        # El texto del combo en SIPP puede diferir del pegado (sufijos "S.A. DE
        # C.V." vs "SA DE CV", espacios, acentos): con el nombre completo el filtro
        # de chosen a veces no deja ningún resultado. Se borra carácter por carácter
        # (desde el final) hasta que aparezca al menos un resultado visible.
        borrados = 0
        while await self._num_resultados_chosen(container) == 0:
            valor = await search_input.input_value()
            if not valor:
                break  # ya se vació la búsqueda y aún no hay resultados
            await search_input.press("Backspace")
            await page.wait_for_timeout(150)
            borrados += 1
        if borrados:
            self.log(
                f"    combo '{ng_model}': sin match exacto; se recortó el texto "
                f"{borrados} carácter(es) hasta encontrar resultado.",
                "warn",
            )

        # Elegir el mejor resultado visible: el que contenga el texto de búsqueda
        # actual; si no, el primero. Se marca con un atributo para hacerle un clic
        # real (chosen selecciona al hacer clic en el <li>).
        hay = await container.evaluate(
            """(c) => {
                const norm = (t) => (t || '').trim().toLowerCase();
                const buscado = norm(c.querySelector('.chosen-search input')?.value);
                const visibles = Array.from(c.querySelectorAll('.chosen-results li.active-result'))
                    .filter(li => { const s = getComputedStyle(li);
                        return s.display !== 'none' && s.visibility !== 'hidden'; });
                if (!visibles.length) return false;
                let obj = visibles[0];
                if (buscado) {
                    const m = visibles.find(li => norm(li.textContent).includes(buscado));
                    if (m) obj = m;
                }
                c.querySelectorAll('li[data-rpa-pick]').forEach(li => li.removeAttribute('data-rpa-pick'));
                obj.setAttribute('data-rpa-pick', '1');
                return true;
            }"""
        )
        if not hay:
            raise RuntimeError(
                f"No se encontró ningún resultado en el combo para '{text_filter}' "
                f"(ng-model='{ng_model}')."
            )
        await container.locator("li[data-rpa-pick='1']").first.click()
        await page.wait_for_timeout(300)

    async def _num_resultados_chosen(self, container) -> int:
        """Cuenta los resultados VISIBLES (li.active-result) de un combo chosen."""
        return await container.evaluate(
            """(c) => Array.from(c.querySelectorAll('.chosen-results li.active-result'))
                .filter(li => { const s = getComputedStyle(li);
                    return s.display !== 'none' && s.visibility !== 'hidden'; }).length"""
        )

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
    ) -> Dict[Tuple[str, Optional[float]], Optional[Tuple[str, Optional[str]]]]:
        """
        Abre su propia sesión de navegador (login + selección de empresa/sucursal),
        navega a Facturas - Listado y busca cada folio. Recibe pares (folio, monto)
        donde monto es el importe esperado (abono del movimiento bancario), usado
        para desambiguar cuando un folio devuelve varias facturas. Regresa un
        diccionario (folio, monto) -> (nombre de cliente, sucursal) tomados de la
        factura, o None si no se encontró o no se pudo desambiguar. Independiente
        del flujo de Recepción de Facturas (run/_process_folio), que sigue en
        construcción.
        """
        resultados: Dict[Tuple[str, Optional[float]], Optional[Tuple[str, Optional[str]]]] = {}

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless,
                slow_mo=80,
                args=["--start-maximized"],
            )
            context = await browser.new_context(
                **self._opciones_contexto(),
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
                        encontrado = await self._buscar_folio_en_listado(page, folio, monto)
                        resultados[(folio, monto)] = encontrado
                        if encontrado:
                            cliente, sucursal = encontrado
                            suc_txt = f" [sucursal: {sucursal}]" if sucursal else ""
                            self.log(f"  Folio {folio} -> {cliente}{suc_txt}", "ok")
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
    ) -> Optional[Tuple[str, Optional[str]]]:
        # El folio puede traer serie de sucursal (ej. 'FCL190541'). Se busca por
        # el número en SIPP (su campo hace coincidencia parcial), y si la serie
        # existe se usa para desambiguar exactamente cuál factura es —sin depender
        # del monto, porque los abonos parciales no igualan el total de la factura.
        folio_norm = _norm_folio(folio)
        numero = re.sub(r"\D", "", folio) or folio
        tiene_serie = bool(re.search(r"[A-Z]", folio_norm))

        await page.fill("input[ng-model='filtros.fl_FolioDocumento']", numero)
        await page.click("button[ng-click='buscar()']")
        await page.wait_for_timeout(1_500)
        filas = await page.evaluate(_JS_GRID_FILAS_FACTURAS, "gridFacturas")

        if not filas:
            return None

        # 1) Desambiguación por serie/folio exacto (la más confiable).
        if tiene_serie:
            exactas = [f for f in filas if _norm_folio(f.get("folio")) == folio_norm]
            if len(exactas) == 1:
                return (exactas[0]["cliente"], exactas[0]["sucursal"])
            if len(exactas) > 1:
                filas = exactas  # varias con la misma serie: seguir con monto

        if len(filas) == 1:
            return (filas[0]["cliente"], filas[0]["sucursal"])

        # 2) Desambiguación por monto (fallback cuando no hay serie o hay empate).
        if monto_esperado is not None:
            for fila in filas:
                montos = [float(m.replace(",", "")) for m in _RE_MONTO.findall(fila["texto"])]
                if any(abs(monto - monto_esperado) < 0.01 for monto in montos):
                    return (fila["cliente"], fila["sucursal"])
            self.log(
                f"  Folio {folio}: {len(filas)} resultados, ninguno coincide con "
                f"el monto ${monto_esperado:,.2f} (¿abono parcial?); se omite.",
                "warn",
            )
            return None

        self.log(
            f"  Folio {folio}: {len(filas)} resultados ambiguos y sin serie ni "
            "monto para desambiguar; se omite.",
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
        a_eliminar: Optional[List[Tuple]] = None,
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
            **self._opciones_contexto(),
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

        await self._asignar_clientes_preview(page, movimientos, a_eliminar)

        try:
            await self._agregar_movimientos_archivo_banco(page)
            await self._guardar_conciliacion_archivo(page)
        except Exception as exc:
            self.log(f"  No se pudo completar el guardado de la conciliación: {exc}", "error")
            await self._volcar_html(page, "ingdiv_guardar")
        # Browser deliberadamente abierto para que el usuario adjunte soporte y envíe.
        # Se retiene para que la UI pueda cerrarlo desde el modal de confirmación.
        self._playwright, self._browser = playwright, browser

    # ──────────────────────────────────────────────────────
    # BBVA: Ingresos Diversos vía buzón Host-to-Host (H2H) + respaldo manual.
    # BBVA no tiene "Subir Excel"; sus abonos llegan por el buzón H2H, que caen en
    # la MISMA previsualización que el flujo CSV. El .xls es la fuente de
    # identificación; lo que el H2H aún no tenga se captura con el '+'.
    # ──────────────────────────────────────────────────────
    async def cargar_ingresos_diversos_bbva_h2h(
        self,
        movimientos: List[Tuple],
        cuenta_bancaria_nombre: str,
        fecha_operacion_ddmmyyyy: str,
        fecha_inicio_ddmmyyyy: str,
        fecha_fin_ddmmyyyy: str,
        a_eliminar: Optional[List[Tuple]] = None,
    ) -> int:
        """`movimientos`: tuplas (referencia, abono, cliente, sucursal, forzar,
        tipos, concepto). `a_eliminar`: tuplas (referencia, abono) de los movimientos
        que NO deben subirse (ya extraídos en un corte anterior / excluidos); el
        buzón H2H los trae de todos modos, así que se eliminan en la
        previsualización. No guarda y envía: deja el navegador abierto para revisar
        y adjuntar el soporte. Devuelve cuántos movimientos se procesaron."""
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=self.headless, slow_mo=30, args=["--start-maximized"]
        )
        context = await browser.new_context(
            **self._opciones_contexto(), locale="es-MX"
        )
        page = await context.new_page()
        page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))

        self.log(
            f"BBVA H2H: cuenta '{cuenta_bancaria_nombre}', fecha {fecha_operacion_ddmmyyyy}, "
            f"rango {fecha_inicio_ddmmyyyy}–{fecha_fin_ddmmyyyy}, {len(movimientos)} "
            "movimiento(s) identificado(s) del .xls.",
            "info",
        )
        await self._login(page)
        await self._configure_session(page)
        self._base_navegacion = page.url.split("#")[0]
        await self._navigate_to_ingresos_diversos_agregar(page)
        await self._configurar_encabezado_ingresos_diversos(
            page, cuenta_bancaria_nombre, fecha_operacion_ddmmyyyy
        )

        # 1) Buzón H2H → previsualización → identificar con los datos del .xls.
        no_encontrados = list(movimientos)  # si el H2H no trae nada, todo es manual
        if await self._traer_movimientos_h2h(page, fecha_inicio_ddmmyyyy, fecha_fin_ddmmyyyy):
            no_encontrados = await self._asignar_clientes_preview(page, movimientos, a_eliminar)
            try:
                await self._agregar_movimientos_archivo_banco(page)
            except Exception as exc:
                self.log(f"  No se pudieron agregar los movimientos del H2H: {exc}", "error")
                await self._volcar_html(page, "bbva_h2h_agregar")
        else:
            self.log(
                "El buzón H2H no devolvió movimientos; se capturarán todos manualmente.",
                "warn",
            )

        # 2) Respaldo: lo que el .xls tiene pero el H2H no trajo → captura manual '+'.
        faltan = [t for t in no_encontrados if t]
        if faltan:
            self.log(
                f"{len(faltan)} movimiento(s) del .xls no estaban en el H2H; se agregan con '+'.",
                "info",
            )
            agregados = 0
            for i, t in enumerate(faltan):
                if self.should_cancel():
                    break
                referencia, abono, cliente = t[0], t[1], t[2]
                sucursal = t[3] if len(t) > 3 else None
                tipos = t[5] if len(t) > 5 else []
                concepto = t[6] if len(t) > 6 else ""
                if not cliente:
                    continue  # sin cliente no se puede capturar por '+'
                if await self._agregar_un_movimiento_manual(
                    page, i, len(faltan), concepto, referencia, abono, cliente, sucursal, tipos
                ):
                    agregados += 1
            self.log(f"{agregados}/{len(faltan)} movimiento(s) faltante(s) agregado(s) manualmente.", "ok")

        # 3) Guardar la conciliación (cancela el modal de subir soporte).
        try:
            await self._guardar_conciliacion_archivo(page)
        except Exception as exc:
            self.log(f"  No se pudo guardar la conciliación: {exc}", "error")
            await self._volcar_html(page, "bbva_h2h_guardar")
        # Browser abierto para adjuntar el soporte; se retiene para que la UI lo cierre.
        self._playwright, self._browser = playwright, browser
        return len(movimientos)

    async def _traer_movimientos_h2h(
        self, page: Page, fecha_inicio_ddmmyyyy: str, fecha_fin_ddmmyyyy: str
    ) -> bool:
        """Abre el buzón H2H, busca en el rango (fechas del .xls), selecciona TODOS
        los lotes y da Aceptar. Devuelve True si apareció la previsualización."""
        paso = "abrir buzón H2H"
        try:
            self.log("  [paso] abriendo buzón H2H (Subir Movimientos H2H)...", "info")
            await page.click("button[ng-click='agregarMovimientosH2H()']")
            await page.wait_for_selector(
                "#divBloqueo_modalBusquedaMovimientosH2H", state="visible", timeout=15_000
            )
            await page.wait_for_timeout(400)

            paso = "fijar rango de fechas"
            self.log(
                f"  [paso] rango H2H: {fecha_inicio_ddmmyyyy} a {fecha_fin_ddmmyyyy}...", "info"
            )
            await self._llenar_fecha_mascara(
                page, "input[ng-model='dt_fh_Inicio']", fecha_inicio_ddmmyyyy.replace("/", "")
            )
            await self._llenar_fecha_mascara(
                page, "input[ng-model='dt_fh_Fin']", fecha_fin_ddmmyyyy.replace("/", "")
            )
            # "Movimientos Diarios" (por defecto).
            try:
                await page.check("#rdbTipoMovimiento_1", force=True)
            except Exception:
                pass

            paso = "buscar (lupa)"
            self.log("  [paso] buscando movimientos H2H en el rango...", "info")
            await page.click("button[ng-click='listarMovimientosH2H()']")
            try:
                await page.wait_for_function(
                    "() => document.querySelectorAll(\"[ng-grid='gridMovimientosH2H'] .ngRow\").length > 0",
                    timeout=12_000,
                )
            except Exception:
                self.log("    el buzón H2H no devolvió lotes en el rango.", "warn")
                try:
                    await page.click("#divBloqueo_modalBusquedaMovimientosH2H button.btn-warning")
                except Exception:
                    pass
                return False
            await page.wait_for_timeout(500)

            paso = "seleccionar todos los lotes"
            resultado = await page.evaluate(_JS_SELECT_ALL_H2H)
            self.log(f"  [paso] selección de lotes H2H: {resultado}.", "info")
            await page.wait_for_timeout(300)

            paso = "Aceptar"
            self.log("  [paso] Aceptar: trayendo movimientos a la previsualización...", "info")
            await page.click("button[ng-click='getDetallesH2H()']")
            await page.wait_for_selector(
                "#divBloqueo_modalDatosBanco", state="visible", timeout=25_000
            )
            return True
        except Exception as exc:
            self.log(f"  Error en el buzón H2H (paso '{paso}'): {exc}", "error")
            await self._volcar_html(page, "bbva_h2h_buzon")
            return False

    async def _leer_filas_preview(self, page: Page) -> List[Tuple[str, Optional[float]]]:
        """Snapshot de (referencia, importe) de TODAS las filas del modal.

        Se recorren por sus lápices #EditarMovimiento_i (uno por movimiento). NO se
        usa "tbody tr": la columna "Importe Sucursales" trae tablas anidadas cuyas
        <tr> también matchean y rompen la lectura por índice (td.nth(3) inexistente
        → timeout). Por eso se leen SOLO los <td> directos (xpath=./td)."""
        lapices = page.locator("#modal-bodymodalDatosBanco [id^='EditarMovimiento_']")
        total_filas = await lapices.count()
        filas: List[Tuple[str, Optional[float]]] = []
        for i in range(total_filas):
            fila = page.locator(f"#EditarMovimiento_{i}").locator("xpath=ancestor::tr[1]")
            celdas = fila.locator("xpath=./td")
            ref = (await celdas.nth(2).inner_text()).strip()
            imp = _parsear_importe(await celdas.nth(3).inner_text())
            filas.append((ref, imp))
        return filas

    async def _eliminar_filas_preview(self, page: Page, a_eliminar: List[Tuple]) -> set:
        """Excluye del modal las filas que NO deben subirse a SIPP: las ya extraídas
        en un corte anterior (ya_subido) y las excluidas (traspasos a filiales,
        portal BBVA). En el flujo CSV esas filas ya vienen quitadas del archivo, así
        que esto es una red de seguridad; en el flujo H2H de BBVA (que no sube
        archivo, sino que jala del buzón por rango de fechas) es el ÚNICO punto donde
        se pueden omitir.

        El control es el botón rojo 'Excluir registro' de cada fila: un <span>
        (glyphicon-remove-circle, ng-model='btnExcluir') que TOGGLEA
        item.sn_Excluir. NO borra la fila ni re-renderiza la tabla, así que los
        índices #EditarMovimiento_i se mantienen estables; se hace un solo click por
        fila (de false→true) y en orden natural.

        Devuelve el conjunto de índices de fila excluidos, para que la asignación de
        clientes no los vuelva a procesar ni los cuente como 'sin cliente'."""
        if not a_eliminar:
            return set()

        filas_datos = await self._leer_filas_preview(page)
        pendientes = list(a_eliminar)
        objetivos: List[Tuple[int, str]] = []
        for i, (ref, imp) in enumerate(filas_datos):
            mov = _emparejar_movimiento(pendientes, ref, imp)
            if mov is not None:
                pendientes.remove(mov)
                objetivos.append((i, ref))

        if not objetivos:
            self.log(
                f"  Ninguna de las {len(a_eliminar)} fila(s) a omitir apareció en la "
                "previsualización (ya venían fuera del archivo).",
                "info",
            )
            return set()

        excluidos: set = set()
        for i, ref in objetivos:
            try:
                fila = page.locator(f"#EditarMovimiento_{i}").locator("xpath=ancestor::tr[1]")
                # Botón 'Excluir registro' (toggle sn_Excluir). Timeout corto: si no
                # está, se avisa y se sigue, en vez de colgar 30s por fila.
                toggle = fila.locator("[ng-model='btnExcluir']").first
                await toggle.scroll_into_view_if_needed(timeout=5_000)
                await toggle.dispatch_event("click")
                await page.wait_for_timeout(200)
                excluidos.add(i)
                self.contar("omitidos")
                self.log(f"  Fila {i + 1} ({ref}): excluida (ya extraída / no se sube).", "info")
            except Exception as exc:
                self.contar("errores")
                self.log(f"  No se pudo excluir la fila {i + 1} ({ref}): {exc}", "warn")

        self.log(
            f"{len(excluidos)}/{len(objetivos)} fila(s) excluida(s) de la previsualización.",
            "ok" if len(excluidos) == len(objetivos) else "warn",
        )
        return excluidos

    async def _asignar_clientes_preview(self, page: Page, movimientos, a_eliminar=None) -> list:
        """En el modal 'Previsualización de datos en Archivo Bancario' (mismo para
        el flujo CSV 'Subir Excel' y el flujo H2H de BBVA), asigna a cada fila su
        cliente/sucursal y marca Ant./Cnt. según los candidatos `movimientos`
        (tuplas ref, abono, cliente, sucursal, forzar, tipos). Antes de asignar,
        elimina las filas de `a_eliminar` (ya extraídas / excluidas) para que no se
        suban. Devuelve la lista de candidatos que NO se encontraron en la
        previsualización (para respaldo)."""
        self.log("  [paso] esperando modal de previsualización (Datos Banco)...", "info")
        await page.wait_for_selector("#divBloqueo_modalDatosBanco", state="visible", timeout=20_000)
        await page.wait_for_timeout(500)

        # Primero se excluyen las filas que no deben subirse (ya extraídas); así no
        # se les asigna cliente ni se cuentan como 'sin cliente' abajo. El toggle no
        # re-renderiza, por lo que los índices se mantienen estables.
        excluidos_idx: set = set()
        if a_eliminar:
            excluidos_idx = await self._eliminar_filas_preview(page, a_eliminar)
            await page.wait_for_timeout(300)

        # Snapshot de (referencia, importe) de TODAS las filas antes de editar
        # ninguna: editar una fila re-renderiza la tabla y rompe las lecturas
        # posteriores.
        filas_datos = await self._leer_filas_preview(page)
        total_filas = len(filas_datos)
        self.log(f"{total_filas} movimiento(s) en la previsualización.", "info")

        pendientes = list(movimientos)
        asignados = 0
        omitidas = 0

        for i, (referencia_modal, importe_modal) in enumerate(filas_datos):
            if self.should_cancel():
                self.log("Asignación de clientes cancelada por el usuario.", "warn")
                break

            if i in excluidos_idx:
                continue  # ya excluida (ya extraída): no se le asigna cliente

            mov = _emparejar_movimiento(pendientes, referencia_modal, importe_modal)
            if mov is None:
                # Sin movimiento identificado: se deja SIN cliente (vacía). No la
                # editamos; se guardará tal cual para captura/identificación manual.
                omitidas += 1
                self.contar("sin_cliente")
                self.log(
                    f"  Fila {i + 1}/{total_filas} ({referencia_modal}): sin cliente identificado, se deja vacía.",
                    "warn",
                )
                continue

            cliente = mov[2]
            sucursal_sugerida = mov[3] if len(mov) > 3 else None
            forzar_sucursal = mov[4] if len(mov) > 4 else False
            tipos_mov = mov[5] if len(mov) > 5 else []
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

                # Fallback: si tras todo el movimiento sigue SIN sucursal
                # ("Seleccionar"), se pone "Corporativo" por defecto. SIPP no
                # permite guardar con la sucursal vacía y el flujo se trabaría.
                etiqueta_actual = await _texto_sucursal()
                if etiqueta_actual.strip().lower() in ("", "seleccionar", "(?)"):
                    valor_corp = await self._valor_opcion_en_select(sucursal_select, "Corporativo")
                    if valor_corp:
                        await sucursal_select.select_option(value=valor_corp)
                        await page.wait_for_timeout(150)
                        etiqueta_suc = await _texto_sucursal()
                        origen_suc = "Corporativo (default)"
                    else:
                        self.log("    no se encontró la opción 'Corporativo' en el combo.", "warn")

                # SIPP auto-agrega una fila por CADA sucursal del cliente y solo
                # pone el importe en una; las filas de importe vacío impiden
                # guardar. Se eliminan las vacías (conservando al menos una).
                borradas = await self._eliminar_sucursales_vacias(page, fila)
                if borradas:
                    self.log(f"    {borradas} sucursal(es) vacía(s) eliminada(s).", "info")

                # Marca las columnas Ant./Cnt. según los tipos del movimiento. En
                # la previsualización de archivo (CSV) SOLO existen esas dos; el
                # resto de tipos solo se pueden marcar en la captura manual ('+').
                if tipos_mov:
                    await self._marcar_ant_cnt_preview(fila, tipos_mov)

                asignados += 1
                self.contar("identificados")
                self.log(
                    f"  Fila {i + 1} ({referencia_modal}): cliente '{cliente_asignado}', "
                    f"sucursal '{etiqueta_suc}' [{origen_suc}].",
                    "ok",
                )
            except Exception as exc:
                self.contar("errores")
                self.log(f"  Error llenando fila {i + 1} ({referencia_modal}): {exc}", "error")
                await self._volcar_html(page, f"ingdiv_fila_{i + 1}")

        self.log(
            f"{asignados}/{total_filas} cliente(s) asignado(s), {omitidas} fila(s) sin "
            "cliente identificado (dejadas vacías).",
            "info",
        )
        return pendientes

    async def _agregar_movimientos_archivo_banco(self, page: Page) -> None:
        """Pasa los movimientos de la previsualización a la tabla de la
        conciliación (botón 'Agregar los movimientos al estado de cuenta')."""
        self.log("  [paso] Guardar movimientos del archivo bancario...", "info")
        await page.click("button[ng-click='AgregarMovimientosArchivoBancario()']")
        await self._aceptar_confirms(page, "'¿Agregar los movimientos al estado de cuenta?'")

    async def _guardar_conciliacion_archivo(self, page: Page) -> None:
        """Guarda la conciliación y cancela el modal de Subir Estado de Cuenta
        (el usuario adjunta el soporte y envía a mano)."""
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

    async def _marcar_ant_cnt_preview(self, fila, tipos: List[str]) -> None:
        """En el modal de previsualización de archivo bancario (CSV), marca las
        columnas Ant. (Anticipo) y Cnt. (Contado) de la fila según `tipos`. Son las
        ÚNICAS disponibles ahí; si se pidieron otros tipos se avisa (solo se pueden
        capturar por el modal '+' manual)."""
        # td directos: 0 Fecha, 1 Concepto, 2 Referencia, 3 Importe, 4 Ant., 5 Cnt.
        celdas = fila.locator("xpath=./td")
        objetivos = {"Anticipo": 4, "Contado": 5}
        for etiqueta, idx in objetivos.items():
            if etiqueta not in tipos:
                continue
            try:
                chk = celdas.nth(idx).locator("input[type='checkbox']").first
                if await chk.count() and await self._check_forzado(chk):
                    self.log(f"    marcado en previsualización: {etiqueta}.", "ok")
                else:
                    self.log(f"    no se pudo marcar '{etiqueta}' en la previsualización.", "warn")
            except Exception as exc:
                self.log(f"    error al marcar '{etiqueta}': {exc}", "warn")
        otros = [t for t in tipos if t not in objetivos]
        if otros:
            self.log(
                "    tipos no disponibles en la previsualización de archivo (solo por "
                f"'+' manual): {', '.join(otros)}.",
                "warn",
            )

    async def _eliminar_sucursales_vacias(self, page: Page, fila) -> int:
        """Elimina las sub-filas de sucursal cuyo importe está vacío/0,
        conservando al menos una. SIPP agrega una fila por CADA sucursal del
        cliente y solo llena una; las vacías bloquean guardar.

        Se anclan por el input de importe (ng-model='MovSucursal.IM_MOVIMIENTO'):
        el atributo ng-repeat NO existe en el DOM renderizado. El botón de borrar
        (btn-eliminar15p → eliminarSucursalModal) vive en el <tr> de la sub-fila."""
        borradas = 0
        vacios = ("", "0", "0.00", "0,00", "$0.00")
        for _ in range(30):  # tope de seguridad
            inputs = fila.locator("input[ng-model='MovSucursal.IM_MOVIMIENTO']")
            n = await inputs.count()
            if n <= 1:
                break
            idx_vacia = None
            for j in range(n):
                try:
                    monto = (await inputs.nth(j).input_value()).strip()
                except Exception:
                    monto = ""
                if monto in vacios:
                    idx_vacia = j
                    break
            if idx_vacia is None:
                break
            # El botón de borrar está en el <tr> de esa sub-fila de sucursal.
            fila_suc = inputs.nth(idx_vacia).locator("xpath=ancestor::tr[1]")
            btn = fila_suc.locator("button.btn-eliminar15p")
            if not await btn.count():
                break
            await btn.first.click()
            await page.wait_for_timeout(200)
            borradas += 1
        return borradas

    async def _consolidar_sucursal_modal(
        self, page: Page, modal, monto: float,
        sucursal_objetivo: Optional[str], plaza_requerida: bool = False,
    ) -> str:
        """En el modal 'Agregar Movimientos' deja UNA sola fila de sucursal
        ('Aplicar en:') y le fija sucursal + importe. Al elegir cliente, SIPP crea
        una fila por CADA sucursal del cliente, y con más de una NO deja guardar.

        Prioridad de la sucursal:
          1) `sucursal_objetivo` (la identificada en la app), si viene;
          2) si no, se RESPETA la que SIPP ya dejó por default en la fila;
          3) solo si quedó vacía ('Seleccionar') se pone 'Corporativo'.

        `plaza_requerida=True` (pagos de contado): si se pidió una sucursal concreta
        y no está en el combo, lanza en vez de caer a 'Corporativo'.

        Devuelve el texto de la sucursal final aplicada."""
        # 1) Reducir a una sola fila. Los botones 'Eliminar Sucursal'
        # (btn-eliminar15p → eliminarSucursalModal) solo existen mientras hay más de
        # una fila; se borra la última hasta que no quede ninguno.
        borradas = 0
        for _ in range(30):  # tope de seguridad
            del_btns = modal.locator("button.btn-eliminar15p")
            if await del_btns.count() == 0:
                break
            try:
                await del_btns.last.click()
            except Exception:
                await del_btns.last.dispatch_event("click")
            await page.wait_for_timeout(200)
            borradas += 1
        if borradas:
            self.log(f"    {borradas} sucursal(es) extra eliminada(s) (SIPP exige una sola).", "info")

        select = modal.locator("#ID_SUCURSAL_Agregar_0")

        async def _texto_actual() -> str:
            try:
                return (await select.evaluate(
                    "s => s.options[s.selectedIndex] ? s.options[s.selectedIndex].text : ''"
                )).strip()
            except Exception:
                return ""

        # 2) Resolver la sucursal de la única fila.
        if sucursal_objetivo:
            opcion = await self._opcion_plaza_por_nombre(page, "#ID_SUCURSAL_Agregar_0", sucursal_objetivo)
            if not opcion and plaza_requerida:
                raise RuntimeError(
                    f"No se encontró la plaza '{sucursal_objetivo}' en el combo de sucursales."
                )
            if not opcion:
                self.log(f"    sucursal '{sucursal_objetivo}' no está en el combo; uso 'Corporativo'.", "warn")
                opcion = await self._opcion_plaza_por_nombre(page, "#ID_SUCURSAL_Agregar_0", "Corporativo")
            if opcion:
                await select.select_option(value=opcion["value"])
        else:
            # Sin sucursal en la app: si SIPP ya puso una por default, se respeta;
            # solo si quedó en 'Seleccionar' se fuerza 'Corporativo'.
            actual = await _texto_actual()
            if actual.lower() in ("", "seleccionar"):
                opcion = await self._opcion_plaza_por_nombre(page, "#ID_SUCURSAL_Agregar_0", "Corporativo")
                if opcion:
                    await select.select_option(value=opcion["value"])
                    self.log("    sin sucursal en la app y SIPP no puso default: 'Corporativo'.", "info")
                else:
                    self.log("    no se encontró 'Corporativo' en el combo.", "warn")
            else:
                self.log(f"    sin sucursal en la app; se respeta la default de SIPP: '{actual}'.", "info")

        await page.wait_for_timeout(150)
        # 3) Importe en la fila de la sucursal.
        await modal.locator("#IM_MOVIMIENTO_Agregar_0").fill(f"{monto:.2f}")
        return await _texto_actual()

    # ──────────────────────────────────────────────────────
    # Ingresos Diversos por captura MANUAL (modal "Agregar Movimientos"),
    # para bancos que SIPP no importa por "Subir Excel" (ej. BanBajío).
    # ──────────────────────────────────────────────────────
    # ids conocidos de los checkboxes "¿Es ...?" (patrón chk_sn_...). Los que no
    # aparezcan aquí se buscan por el texto del <label>.
    _IDS_CHECK_TIPO = {
        "Contado": "chk_sn_Contado",
        "Anticipo": "chk_sn_Anticipo",
        "Factoraje Financiero": "chk_sn_Factoraje",
    }

    async def cargar_ingresos_diversos_manual(
        self,
        movimientos: List[Tuple[str, str, float, str, Optional[str], List[str]]],
        cuenta_bancaria_nombre: str,
        fecha_operacion_ddmmyyyy: str,
    ) -> int:
        """Agrega en 'Ingresos Diversos - Agregar' cada movimiento identificado
        usando el modal 'Agregar Movimientos' (el '+'), en vez de 'Subir Excel'.
        Pensado para bancos cuyo archivo SIPP no importa (BanBajío).

        Cada movimiento es (concepto, referencia, monto, cliente, sucursal, tipos),
        donde `tipos` es una lista de etiquetas ('Contado', 'Anticipo', ...) cuyos
        checkboxes '¿Es ...?' se marcan. No guarda: deja el navegador abierto para
        revisión. Devuelve cuántos movimientos se agregaron."""
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=self.headless, slow_mo=40, args=["--start-maximized"]
        )
        context = await browser.new_context(
            **self._opciones_contexto(), locale="es-MX"
        )
        page = await context.new_page()
        page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))

        self.log(
            f"Captura manual de Ingresos Diversos: cuenta '{cuenta_bancaria_nombre}', "
            f"fecha {fecha_operacion_ddmmyyyy}, {len(movimientos)} movimiento(s).",
            "info",
        )
        await self._login(page)
        await self._configure_session(page)
        self._base_navegacion = page.url.split("#")[0]
        await self._navigate_to_ingresos_diversos_agregar(page)
        await self._configurar_encabezado_ingresos_diversos(
            page, cuenta_bancaria_nombre, fecha_operacion_ddmmyyyy
        )

        agregados = 0
        for i, mov in enumerate(movimientos):
            if self.should_cancel():
                self.log("Captura cancelada por el usuario.", "warn")
                break
            if await self._agregar_un_movimiento_manual(page, i, len(movimientos), *mov):
                agregados += 1

        self.log(
            f"{agregados}/{len(movimientos)} movimiento(s) agregado(s). Revisa la tabla "
            "en SIPP (sucursales e importes) y presiona Guardar cuando estés conforme.",
            "ok",
        )
        return agregados

    async def _agregar_un_movimiento_manual(
        self, page: Page, i: int, total: int,
        concepto: str, referencia: str, monto: float,
        cliente: str, sucursal: Optional[str], tipos: List[str],
    ) -> bool:
        paso = "abrir modal"
        try:
            etiqueta_cli = cliente if cliente else "(sin cliente)"
            self.log(f"Agregando {i + 1}/{total}: '{etiqueta_cli}' ${monto:,.2f}...", "info")
            await page.click("button[ng-click='agregarMovimientos()']")
            await page.wait_for_selector(
                "#divBloqueo_modalAgregarMovimientos", state="visible", timeout=10_000
            )
            await page.wait_for_timeout(300)
            modal = page.locator("#divBloqueo_modalAgregarMovimientos")

            paso = "llenar concepto/referencia/importe"
            await modal.locator("#DE_CONCEPTO_Agregar").fill((concepto or "")[:250])
            await modal.locator("#DE_REFERENCIA_Agregar").fill(referencia or "")
            await modal.locator("#IM_MOVIMIENTO_Agregar").fill(f"{monto:.2f}")

            # Marcar los tipos indicados (checkboxes "¿Es ...?").
            for etiqueta in (tipos or []):
                paso = f"marcar '¿Es {etiqueta}?'"
                if await self._marcar_check_tipo(modal, etiqueta):
                    self.log(f"    marcado: ¿Es {etiqueta}?", "ok")
                else:
                    self.log(f"    no se encontró el check '¿Es {etiqueta}?'", "warn")
                await page.wait_for_timeout(150)

            # Sin cliente identificado: se captura el movimiento SOLO con importe
            # (SIPP lo permite). No se toca cliente ni sucursal; el usuario lo
            # identifica después en SIPP.
            if not cliente:
                self.log("    sin cliente: se captura solo el importe (a identificar en SIPP).", "warn")
            else:
                paso = "seleccionar cliente"
                await self._chosen_select(page, "ID_CLIENTE", cliente)
                await page.wait_for_timeout(300)

                # Deja UNA sola sucursal (SIPP agrega una por cada sucursal del
                # cliente y no deja guardar con varias) y le fija sucursal+importe:
                # la de la app si se identificó; si no, la default de SIPP; y solo si
                # SIPP la dejó vacía, 'Corporativo'.
                paso = "resolver sucursal"
                final_suc = await self._consolidar_sucursal_modal(page, modal, monto, sucursal)
                self.log(f"    sucursal aplicada: {final_suc or '(sin definir)'}", "ok")
                await page.wait_for_timeout(200)

            paso = "guardar movimiento"
            await modal.locator("button.btn-info", has_text="Guardar Movimiento").click()
            # El modal se cierra tras guardar, pero NO siempre vía la clase
            # ng-hide (a veces por display/opacity/offsetParent). Se detecta por
            # varias señales para no esperar el timeout completo cada vez.
            if not await self._esperar_cierre_modal_agregar(page, timeout=15_000):
                self.log("    (el modal no cerró a tiempo; lo fuerzo y continúo)", "warn")
                await page.evaluate(
                    "() => { const m = document.querySelector('#divBloqueo_modalAgregarMovimientos');"
                    " if (m) m.classList.add('ng-hide'); }"
                )
            await page.wait_for_timeout(150)
            self.contar("capturados_manual")
            self.log(f"  Movimiento {i + 1} agregado.", "ok")
            return True
        except Exception as exc:
            self.contar("errores")
            self.log(f"  Error agregando movimiento {i + 1} en '{paso}': {exc}", "error")
            await self._volcar_html(page, f"ingdiv_manual_{i + 1}")
            # Cerrar el modal para no bloquear el siguiente movimiento.
            try:
                await page.evaluate(
                    "() => { const m = document.querySelector('#divBloqueo_modalAgregarMovimientos');"
                    " if (m) m.classList.add('ng-hide'); }"
                )
            except Exception:
                pass
            return False

    async def _esperar_cierre_modal_agregar(self, page: Page, timeout: int = 15_000) -> bool:
        """Espera a que el modal 'Agregar Movimientos' se cierre, detectando
        cualquier señal de ocultamiento (ng-hide, display/visibility/opacity,
        offsetParent). Devuelve True si cerró; False si venció el timeout."""
        try:
            await page.wait_for_function(
                """() => {
                    const m = document.querySelector('#divBloqueo_modalAgregarMovimientos');
                    if (!m) return true;
                    if (m.classList.contains('ng-hide')) return true;
                    const s = getComputedStyle(m);
                    if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) === 0) return true;
                    if (m.offsetParent === null) return true;
                    return false;
                }""",
                timeout=timeout,
            )
            return True
        except Exception:
            return False

    async def _marcar_check_tipo(self, modal, etiqueta: str) -> bool:
        """Marca el checkbox '¿Es {etiqueta}?' del modal. Intenta primero por id
        conocido (chk_sn_...) y, si no, por el texto del <label>."""
        cid = self._IDS_CHECK_TIPO.get(etiqueta)
        if cid:
            loc = modal.locator(f"#{cid}")
            if await loc.count():
                return await self._check_forzado(loc.first)
        texto = f"¿Es {etiqueta}?"
        label = modal.locator("label", has_text=texto).first
        if await label.count():
            chk = label.locator("input[type='checkbox']")
            if not await chk.count():
                chk = label.locator(
                    "xpath=preceding-sibling::input[@type='checkbox'][1]"
                    " | following-sibling::input[@type='checkbox'][1]"
                )
            if not await chk.count():
                for_id = await label.get_attribute("for")
                if for_id:
                    chk = modal.locator(f"#{for_id}")
            if await chk.count():
                return await self._check_forzado(chk.first)
        return False

    async def _check_forzado(self, locator) -> bool:
        try:
            if not await locator.is_checked():
                await locator.check(force=True)
            return True
        except Exception:
            try:
                await locator.click(force=True)
                return True
            except Exception:
                return False

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
        grupos: list,
        fecha_operacion_ddmmyyyy: str,
        enviar_automaticamente: bool = False,
    ) -> List[dict]:
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
            **self._opciones_contexto(),
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
        duplicados_global: List[dict] = []  # para el resumen visual en la app

        for idx, grupo in enumerate(grupos):
            # Compatibilidad: cada grupo es (cuenta, pagos) o (cuenta, pagos,
            # fecha_min_correo). La fecha del correo (día que cayó al buzón) es el
            # inicio del rango de búsqueda de duplicados.
            cuenta_bancaria_nombre, pagos = grupo[0], grupo[1]
            fecha_dup_min = grupo[2] if len(grupo) > 2 else None
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

            # Verificación de duplicados: revisa las conciliaciones ya subidas en el
            # rango [fecha del correo … fecha de operación] de esta cuenta y omite
            # los pagos que ya estén (mismo monto, cliente y sucursal).
            fecha_ini_dup, fecha_fin_dup = self._rango_dup(fecha_dup_min, fecha_operacion_ddmmyyyy)
            existentes = await self._leer_movimientos_conciliaciones(
                page_cuenta, cuenta_bancaria_nombre, fecha_ini_dup, fecha_fin_dup
            )
            pagos, dups = self._filtrar_duplicados_contado(pagos, existentes)
            for p, folio in dups:
                duplicados_global.append({
                    "cliente": p[3],
                    "monto": p[5],
                    "sucursal": p[4],
                    "folio": folio,
                    "cuenta": cuenta_bancaria_nombre,
                })
                self.log(
                    f"  ⚠ Posible duplicado ya subido (OMITIDO): '{p[3]}' ${p[5]:,.2f} · "
                    f"sucursal '{p[4]}' → conciliación {folio}.",
                    "warn",
                )
            if dups:
                self.log(
                    f"  {len(dups)} pago(s) omitido(s) por duplicado; se agregan {len(pagos)}.",
                    "info",
                )
            if not pagos:
                self.log(
                    f"  Todos los pagos de '{cuenta_bancaria_nombre}' ya estaban subidos; "
                    "nada que agregar.",
                    "warn",
                )
                continue

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

        return duplicados_global

    # ──────────────────────────────────────────────────────
    # Verificación de duplicados de contado: antes de agregar, se revisan las
    # conciliaciones YA existentes de esa fecha+cuenta en SIPP.
    # ──────────────────────────────────────────────────────
    def _rango_dup(self, fecha_min_ddmmyyyy: Optional[str], fecha_operacion_ddmmyyyy: str):
        """Devuelve (inicio, fin) en dd/mm/yyyy para buscar duplicados: desde la
        fecha del correo (si es anterior) hasta la fecha de operación. Si no hay
        fecha del correo, usa la de operación para ambos extremos."""
        if not fecha_min_ddmmyyyy:
            return fecha_operacion_ddmmyyyy, fecha_operacion_ddmmyyyy
        try:
            a = datetime.strptime(fecha_min_ddmmyyyy, "%d/%m/%Y").date()
            b = datetime.strptime(fecha_operacion_ddmmyyyy, "%d/%m/%Y").date()
            ini, fin = (a, b) if a <= b else (b, a)
            return ini.strftime("%d/%m/%Y"), fin.strftime("%d/%m/%Y")
        except ValueError:
            return fecha_min_ddmmyyyy, fecha_operacion_ddmmyyyy

    async def _leer_movimientos_conciliaciones(
        self, page: Page, cuenta_bancaria_nombre: str,
        fecha_inicio_ddmmyyyy: str, fecha_fin_ddmmyyyy: str,
    ) -> List[dict]:
        """Va a ConciliacionListado, filtra por rango de fechas + cuenta, abre cada
        conciliación y lee (monto, cliente, sucursal) de sus movimientos ya subidos.
        Devuelve la lista de esos movimientos existentes (vacía si no hay o falla)."""
        existentes: List[dict] = []
        try:
            base = self._base_navegacion or page.url.split("#")[0]
            self.log(
                f"  [dup] revisando conciliaciones previas ({fecha_inicio_ddmmyyyy} a "
                f"{fecha_fin_ddmmyyyy}) de esta cuenta...",
                "info",
            )
            await page.goto(f"{base}#/ConciliacionListado", wait_until="networkidle", timeout=30_000)
            await page.wait_for_selector("input[ng-model='dt_fh_inicio']", timeout=20_000)
            await self._llenar_fecha_mascara(page, "input[ng-model='dt_fh_inicio']", fecha_inicio_ddmmyyyy.replace("/", ""))
            await self._llenar_fecha_mascara(page, "input[ng-model='dt_fh_fin']", fecha_fin_ddmmyyyy.replace("/", ""))
            cuenta_ok = await page.evaluate(
                _JS_SELECT_OPTION_POR_TEXTO, ["filtros.id_CuentaBancaria", cuenta_bancaria_nombre]
            )
            if not cuenta_ok:
                self.log("    [dup] no se pudo filtrar por cuenta; se revisa por fecha solamente.", "warn")
            await page.wait_for_timeout(300)
            await page.click("[ng-click='listar()']")
            await page.wait_for_timeout(2_500)

            folios = await page.evaluate(_JS_FOLIOS_LISTADO)
            self.log(
                f"    [dup] {len(folios)} conciliación(es) previa(s) en "
                f"{fecha_inicio_ddmmyyyy}–{fecha_fin_ddmmyyyy}.",
                "info",
            )
            for folio in folios:
                if not folio:
                    continue
                try:
                    # _abrir_conciliacion_por_folio re-navega al listado y busca por
                    # folio, así que no hace falta volver manualmente entre folios.
                    await self._abrir_conciliacion_por_folio(page, folio)
                    movs = await page.evaluate(_JS_MOVS_CONCILIACION)
                    for mv in movs:
                        existentes.append({
                            "monto": _parsear_importe(mv.get("abono")),
                            "cliente": mv.get("cliente", ""),
                            "sucursal": mv.get("sucursal", ""),
                            "folio": folio,
                        })
                except Exception as exc:
                    self.log(f"    [dup] no se pudo leer la conciliación {folio}: {exc}", "warn")
            self.log(f"    [dup] {len(existentes)} movimiento(s) ya subido(s) leído(s).", "info")
        except Exception as exc:
            self.log(f"  [dup] no se pudieron revisar conciliaciones previas: {exc}", "warn")
            await self._volcar_html(page, "contado_dup_listado")
        return existentes

    def _es_mismo_movimiento(self, monto, cliente: str, sucursal: str, e: dict) -> bool:
        """True si el pago (monto, cliente, sucursal) coincide con un movimiento ya
        subido `e`. Fecha implícita: el listado ya se filtró por la fecha.

        La sucursal es CONDICIONAL: si el pago no trae sucursal (el correo no la
        estipuló), no se exige y basta con fecha+cliente+monto; si sí la trae, debe
        coincidir."""
        try:
            if e.get("monto") is None or abs(float(monto) - float(e["monto"])) > 0.01:
                return False
        except (TypeError, ValueError):
            return False
        # Sucursal: solo se compara si el pago la trae. Igual o una contiene a la
        # otra (tolera formatos 'GDL - Guadalajara').
        sa = _norm_txt(sucursal)
        if sa:
            sb = _norm_txt(e.get("sucursal"))
            if not (sb and (sa == sb or sa in sb or sb in sa)):
                return False
        # Cliente: se quita el código 'NNNNN - ' de SIPP y se compara por inclusión.
        ca, cb = _norm_txt(cliente), _norm_txt(_cliente_sin_codigo(e.get("cliente")))
        return bool(ca and cb and (ca == cb or ca in cb or cb in ca))

    def _filtrar_duplicados_contado(self, pagos: list, existentes: List[dict]):
        """Separa `pagos` en (nuevos, duplicados) comparando cada uno contra los
        movimientos ya subidos. Tupla de pago: (concepto, ref, tipo, cliente,
        plaza, monto, comprobante). `duplicados` es una lista de (pago, folio) con
        el folio de la conciliación donde se encontró la coincidencia."""
        nuevos, dups = [], []
        for p in pagos:
            cliente, plaza, monto = p[3], p[4], p[5]
            match = next(
                (e for e in existentes if self._es_mismo_movimiento(monto, cliente, plaza, e)),
                None,
            )
            if match is not None:
                dups.append((p, match.get("folio", "")))
            else:
                nuevos.append(p)
        return nuevos, dups

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

            # Contado requiere sucursal para crear el movimiento. Si el correo no la
            # trajo (y no resultó ser duplicado), se salta con aviso: el usuario la
            # indica en la app y lo vuelve a cargar.
            if not (plaza or "").strip():
                self.log(
                    f"  Movimiento {i + 1}/{len(pagos)} '{cliente}' ${monto:,.2f} SIN plaza: "
                    "no se agrega (indícala en la app y recarga).",
                    "warn",
                )
                continue

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

                # Deja UNA sola sucursal (si el cliente tiene varias, SIPP crea una
                # fila por cada una y no deja guardar) con la plaza del pago.
                paso = "seleccionar plaza"
                self.log(f"  [paso] fijando plaza '{plaza}' (una sola sucursal)...", "info")
                final_suc = await self._consolidar_sucursal_modal(
                    page, modal, monto, plaza, plaza_requerida=bool(plaza)
                )
                self.log(f"    plaza seleccionada: '{final_suc}'", "ok")
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
                self.contar("capturados_manual")
                self.log(f"  Movimiento {i + 1} agregado: cliente '{cliente}', plaza '{plaza}'.", "ok")
            except Exception as exc:
                self.contar("errores")
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

    # ──────────────────────────────────────────────────────
    # Factoraje (BAJA FERRIES): editar movimientos ya conciliados para capturar
    # el interés de factoraje del PDF NAFIN/BBVA.
    # ──────────────────────────────────────────────────────
    async def aplicar_factoraje(
        self,
        folio_conciliacion: str,
        institucion_value: str,
        items: List[dict],
    ) -> int:
        """Abre la conciliación `folio_conciliacion`, localiza los movimientos de
        BAJA FERRIES y, por cada uno que empate (por folio de documento o por
        monto neto) con un renglón de `items`, marca "Es Factoraje Financiero",
        elige la institución (`institucion_value`, value del combo) y captura el
        interés, guardando el movimiento.

        `items`: lista de dicts {folio, interes, abono, referencia}.
        Regresa cuántos movimientos se editaron.
        """
        aplicados = 0
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=self.headless, slow_mo=8, args=["--start-maximized"]
        )
        context = await browser.new_context(**self._opciones_contexto(), locale="es-MX")
        page = await context.new_page()
        page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))

        try:
            self.log(
                f"Factoraje: abriendo conciliación '{folio_conciliacion}' para "
                f"{len(items)} movimiento(s) de BAJA FERRIES.",
                "info",
            )
            await self._login(page)
            await self._configure_session(page)
            self._base_navegacion = page.url.split("#")[0]

            await self._abrir_conciliacion_por_folio(page, folio_conciliacion)

            # FASE 1: leer todas las filas UNA vez y quedarnos con las referencias
            # de BAJA FERRIES que empatan con el PDF (con su item).
            filas = page.locator(_SEL_FACTORAJE_FILA)
            total = await filas.count()
            self.log(f"  {total} movimiento(s) en la conciliación.", "info")
            pendientes: List[Tuple[str, dict]] = []
            for i in range(total):
                fila = filas.nth(i)
                texto = (await fila.inner_text()).replace("\n", " ")
                try:
                    referencia = (await fila.locator("xpath=./td").nth(2).inner_text()).strip()
                except Exception:
                    referencia = ""
                item = _emparejar_item_factoraje(referencia, texto, items)
                if item and referencia:
                    pendientes.append((referencia, item))
            self.log(f"  {len(pendientes)} movimiento(s) de BAJA FERRIES a editar.", "info")

            # FASE 2: procesar cada uno RE-BUSCANDO su fila por referencia. Editar
            # un movimiento re-renderiza la lista; buscar por referencia (no por
            # índice) evita el desfase (off-by-one) que abría el movimiento previo.
            for referencia, item in pendientes:
                if self.should_cancel():
                    self.log("Factoraje cancelado por el usuario.", "warn")
                    break
                fila = await self._fila_por_referencia(page, referencia)
                if fila is None:
                    self.log(f"  No se encontró la fila de ref {referencia}; se omite.", "warn")
                    continue
                try:
                    await self._editar_factoraje_en_fila(page, fila, item, institucion_value)
                    aplicados += 1
                    self.log(
                        f"  ✓ Factoraje aplicado: ref {referencia} "
                        f"(folio {item.get('folio')}) interés ${item.get('interes', 0):,.2f}",
                        "ok",
                    )
                except Exception as exc:
                    self.log(f"  Error aplicando factoraje en ref {referencia}: {exc}", "error")
                    await self._volcar_html(page, f"factoraje_ref_{referencia}")
                    await self._cerrar_modal_editar(page)

            self.log(f"Factoraje terminado: {aplicados} movimiento(s) editado(s).", "ok")

            # Guardar la conciliación para concluir el flujo.
            if aplicados:
                await self._guardar_conciliacion_factoraje(page)
        finally:
            if not self.headless:
                self.log("Revisa el resultado en SIPP (el navegador queda abierto).", "info")
            else:
                await browser.close()
        return aplicados

    async def _guardar_conciliacion_factoraje(self, page: Page) -> None:
        """Cierra el modal de edición y guarda la conciliación. Tras Guardar,
        SIPP muestra confirm(s) que se ACEPTAN y, al final, un modal para subir
        archivo soporte que se CANCELA (se ignora). Se distingue por el mensaje
        del diálogo (los de soporte mencionan 'soporte/archivo/estado de cuenta')."""
        await self._cerrar_modal_editar(page)
        self.log("  [paso] guardando la conciliación...", "info")
        try:
            btn = page.locator("button[ng-click='guardar()']").first
            await btn.wait_for(state="visible", timeout=10_000)
            await btn.dispatch_event("click")  # dispatch: por si queda fuera del viewport

            claves_soporte = ("soporte", "archivo", "subir", "estado de cuenta", "edo de cuenta", "edo. de cuenta")
            for _ in range(6):
                try:
                    await page.wait_for_selector("#divBloqueoAlert, #subDivAlert", state="visible", timeout=8_000)
                except Exception:
                    break
                await page.wait_for_timeout(400)
                mensaje = ""
                try:
                    mensaje = (await page.locator("#divMensaje").first.inner_text()).strip().lower()
                except Exception:
                    pass
                if any(k in mensaje for k in claves_soporte):
                    self.log("  Modal de archivo soporte: Cancelar (ignorado).", "info")
                    await page.locator("button[onclick='fnc_closeConfirm(false)']").first.dispatch_event("click")
                    await page.wait_for_timeout(500)
                    break
                self.log(f"  [paso] aceptando confirmación: {mensaje[:60] or '(sin texto)'}...", "info")
                try:
                    await page.locator("#__btn_aceptarConfirm__").first.dispatch_event("click")
                except Exception:
                    break
                await page.wait_for_timeout(600)

            # Cancelar también el posible modal de subir estado de cuenta (no-confirm).
            try:
                if await page.locator("#divBloqueo_modalSubirEdoCuenta:visible").count():
                    await page.locator("#divBloqueo_modalSubirEdoCuenta").locator(
                        "button", has_text="Cancelar"
                    ).first.click()
            except Exception:
                pass

            self.log("Conciliación guardada. Factoraje concluido.", "ok")
        except Exception as exc:
            self.log(f"  No se pudo guardar la conciliación automáticamente: {exc}", "warn")
            await self._volcar_html(page, "factoraje_guardar")

    async def _abrir_conciliacion_por_folio(self, page: Page, folio: str) -> None:
        """Navega al listado de Ingresos Diversos, busca por Estado de Cuenta
        (id_Conciliacion = folio) y abre la conciliación (botón Visualizar)."""
        self.log(f"  [paso] navegando al listado de Ingresos Diversos...", "info")
        base = self._base_navegacion or page.url.split("#")[0]
        await page.goto(f"{base}#/ConciliacionListado", wait_until="networkidle", timeout=30_000)
        await page.wait_for_selector(_SEL_FACTORAJE_INPUT_FOLIO, timeout=20_000)
        self.log(f"  [paso] buscando Estado de Cuenta '{folio}'...", "info")
        await page.fill(_SEL_FACTORAJE_INPUT_FOLIO, folio)
        await page.click(_SEL_FACTORAJE_BTN_BUSCAR)
        await page.wait_for_timeout(2_000)
        await page.locator(_SEL_FACTORAJE_BTN_ABRIR).first.click()
        await page.wait_for_selector(_SEL_FACTORAJE_FILA, timeout=20_000)
        await page.wait_for_timeout(1_000)
        self.log("  Conciliación abierta.", "ok")

    async def _fila_por_referencia(self, page: Page, referencia: str):
        """Devuelve la fila (tr) de la conciliación cuya 3ª celda (referencia)
        coincide con `referencia`, re-escaneando el DOM actual (robusto al
        re-render tras guardar). None si no la encuentra."""
        filas = page.locator(_SEL_FACTORAJE_FILA)
        n = await filas.count()
        for i in range(n):
            fila = filas.nth(i)
            try:
                ref = (await fila.locator("xpath=./td").nth(2).inner_text()).strip()
            except Exception:
                continue
            if ref == referencia:
                return fila
        return None

    async def _cerrar_modal_editar(self, page: Page) -> None:
        """Cierra el modal 'Editar Movimiento' si quedó abierto (su overlay
        intercepta clics y bloquea los siguientes movimientos). El botón X suele
        quedar fuera del viewport, por eso se dispara el click por evento (sin
        requerir scroll) y, si falla, se llama modalClose() en el scope Angular."""
        modal = "#divBloqueo_modalAgregarMovimientos"
        try:
            if not await page.locator(f"{modal}:visible").count():
                return
            # Se dispara modalClose() por evento (el botón X suele quedar fuera del
            # viewport). Luego se ESPERA a que el modal desaparezca de verdad
            # (offsetParent null = display:none tras el fadeOut), para que el
            # siguiente movimiento abra fresco (checkbox habilitado).
            await page.evaluate(
                "() => { const els = document.querySelectorAll(\"[ng-click='modalClose()']\");"
                " els.forEach(el => { if (el.offsetParent !== null) el.click(); }); }"
            )
            try:
                await page.wait_for_function(
                    "() => { const el = document.querySelector('input[ng-model=\"SN_FACTORAJE\"]');"
                    " return !el || el.offsetParent === null; }",
                    timeout=2_500,
                )
            except Exception:
                pass
            await page.wait_for_timeout(200)
        except Exception as exc:
            self.log(f"    (no se pudo cerrar el modal de edición: {exc})", "warn")

    async def _editar_factoraje_en_fila(self, page: Page, fila, item: dict, institucion_value: str) -> None:
        check_sel = "input[type='checkbox'][ng-model='SN_FACTORAJE']"
        combo_sel = "select[ng-model='ID_INSTITUCIONFINANCIERA']"

        check = page.locator(f"{check_sel}:visible").first
        combo = page.locator(f"{combo_sel}:visible").first
        label = page.locator("label[for='chk_sn_Factoraje']:visible").first
        ref_area = page.locator("textarea[ng-model='DE_REFERENCIA']:visible").first
        ref_esperada = str(item.get("referencia") or "").strip()

        # 1) Abrir el editor con dispatch_event('click') → dispara editar(item)
        # directo. editar() repuebla el modal de forma ASÍNCRONA, así que el
        # textarea de referencia muestra la del movimiento ANTERIOR un instante;
        # por eso se ESPERA (polling) a que muestre la referencia esperada, en vez
        # de leerla una vez (lo que provocaba el off-by-one y el reabrir constante).
        cargado = False
        for intento in range(3):
            await fila.locator("button.btn-editar15p").first.dispatch_event("click")
            await ref_area.wait_for(state="visible", timeout=10_000)
            try:
                await page.wait_for_function(
                    "(ref) => { const t = document.querySelector(\"textarea[ng-model='DE_REFERENCIA']\");"
                    " return t && t.value.trim() === ref; }",
                    arg=ref_esperada,
                    timeout=6_000,
                )
                cargado = True
                break
            except Exception:
                ref_modal = (await ref_area.input_value()).strip()
                self.log(f"    modal cargó ref '{ref_modal}', esperaba '{ref_esperada}'; reabriendo...", "warn")
                await self._cerrar_modal_editar(page)
        if not cargado and ref_esperada:
            raise RuntimeError(f"el modal no cargó la referencia {ref_esperada}")
        await page.wait_for_timeout(150)

        # 2) Marcar factoraje SOLO si no está ya marcado para ESTE movimiento
        # (decidir por is_checked, NO por si la sección es visible: el estado
        # heredado engañaba). El checkbox suele venir deshabilitado por un digest
        # que lo re-deshabilita en una carrera; por eso se habilita y se hace el
        # CLICK REAL en un solo bloque JS (persiste como un clic humano).
        if not await check.is_checked():
            # Intento 1: clic real en el label (cuando el checkbox está habilitado).
            if not await check.is_disabled():
                try:
                    await label.click(force=True)
                    await page.wait_for_timeout(300)
                except Exception:
                    pass
            # Intento 2 (o si estaba deshabilitado): habilitar + click real vía JS.
            if not await check.is_checked():
                res = await page.evaluate(_JS_CLICK_FACTORAJE)
                self.log(f"    marcar factoraje (JS): {res}", "info")
                await page.wait_for_timeout(300)
        if not await check.is_checked():
            raise RuntimeError("el checkbox de factoraje no quedó marcado")

        # 3) Elegir la institución. Dispara mostrarCamposFactoraje() → muestra interés.
        await combo.wait_for(state="visible", timeout=6_000)
        await combo.select_option(value=str(institucion_value))
        await page.wait_for_timeout(400)

        # 4) Interés de factoraje. Se intenta por UI; si el campo no aparece
        # (sn_FactorajeInteres no se activó), se setea por el MODELO de Angular
        # —Guardar lee el modelo aunque el campo esté oculto—. NO se hace
        # dispatch sobre el combo (cuelga si ya no es visible).
        interes = f"{float(item.get('interes') or 0):.2f}"
        campo = page.locator("input[ng-model='IM_FACTORAJEINTERES']:visible").first
        lleno_por_ui = False
        try:
            await campo.wait_for(state="visible", timeout=4_000)
            await campo.fill(interes)
            await campo.dispatch_event("input")
            await campo.dispatch_event("change")
            lleno_por_ui = True
        except Exception:
            pass
        if not lleno_por_ui:
            res = await page.evaluate(_JS_SET_INTERES, interes)
            self.log(
                f"    ⚠ interés por modelo (revisar en SIPP si persistió): {res}", "warn"
            )
        await page.wait_for_timeout(300)

        # 5) Guardar. Luego se cierra el modal EXPLÍCITAMENTE: "Guardar y Cerrar"
        # no siempre lo cierra, y su overlay bloquearía el siguiente movimiento.
        await page.locator(_SEL_FACTORAJE_BTN_GUARDAR).first.click()
        await self._aceptar_confirms(page, "guardar movimiento factoraje")
        await page.wait_for_timeout(500)
        await self._cerrar_modal_editar(page)