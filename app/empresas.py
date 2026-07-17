"""Catálogo de empresas del grupo Petroil.

Cada empresa determina, en SIPP: el texto a elegir en el login (combo
'id_Empresa'), su catálogo de cuentas bancarias y sus sucursales (estado de
cuenta). El usuario selecciona la empresa en la app antes de operar.
"""

from dataclasses import dataclass, field

from .cuentas_bancarias import CUENTAS_BANCARIAS, CuentaBancaria


def _parse_cuentas(texto: str) -> list[CuentaBancaria]:
    """Parsea líneas '{id}  -  {nombre}' (formato del combo chosen de SIPP)."""
    cuentas = []
    for linea in texto.strip().splitlines():
        linea = linea.strip()
        if not linea:
            continue
        id_sipp, _, nombre = linea.partition("  -  ")
        cuentas.append(CuentaBancaria(id_sipp.strip(), nombre.strip()))
    return cuentas


_CUENTAS_ACP = _parse_cuentas(
    """
18  -  ACP COMBUSTIBLES AMERICAN EXPRESS - AMERICAN EXPRESS
26  -  ACP COMBUSTIBLES - BANBAJIO - 0201 - BAJIO
13  -  BAJIO 24261570 ACP COMBUSTIBLES - BAJIO
16  -  BANAMEX 965783 ACP COMBUSTIBLES - BANAMEX
6  -  ANTICIPO DE CLIENTES - Banco General Para Pagos Corporativo
11  -  ANTICIPO DE CLIENTES DOLARES - Banco General Para Pagos Corporativo
1  -  BANORTE 0837975139 ACP COMBUSTIBLES - BANORTE
17  -  BANORTE 844623522 - BANORTE
2  -  BANREGIO 114981070017 ACP COMBUSTIBLES - BANREGIO
5  -  BANREGIO 114981070025 ACP COMBUSTIBLES dlls - BANREGIO
24  -  ACP COMBUSTIBLES MN - BBVA BANCOMER - 8025 - BBVA BANCOMER
25  -  ACP COMBUSTIBLES REF - BBVA BANCOMER - 2129 - BBVA BANCOMER
4  -  BBVA 0104728025 ACP COMBUSTIBLES - BBVA BANCOMER
12  -  BBVA DOLARES 0104728416 - BBVA BANCOMER
23  -  REF BBVA 1298 - BBVA BANCOMER
9  -  BANCO MONEX 27508759 ACP - BMONEX
10  -  BANCO MONEX 2848000 ACP - BMONEX
14  -  ACP COMBUSTIBLES HSBC - HSBC
15  -  HSBC - HSBC
19  -  ESTIMACIONES DE CUENTAS INCOBRABLES - Sin Definir
8  -  GASTOS NO DEDUCIBLES - Sin Definir
7  -  PETROPLAZAS SF - Sin Definir
"""
)

_CUENTAS_PETROSMART = _parse_cuentas(
    """
9  -  AMERICAN EXPRESS - AMERICAN EXPRESS
3  -  BAJÍO 013729652 PETRO SMART - BAJIO
45  -  PETRO SMART - BANBAJIO - 0201 - BAJIO
21  -  PETRO SMART COMBUSTIBLES S.A. DE C.V. - BANAMEX
48  -  PETRO SMART COMBUSTIBLES S.A. DE C.V. - BANAMEX - 0297 - BANAMEX
17  -  ANTICIPO DE CLIENTES - Banco General Para Pagos Corporativo
2  -  BANORTE 0264211330 PETRO SMART - BANORTE
28  -  BANORTE 1234072250 PESOS - BANORTE
25  -  PETRO SMART BANORTE 0273762384 - BANORTE
32  -  PETRO SMART COMB BANORTE 7340 - BANORTE
4  -  BANREGIO 114989340013 PETRO SMART - BANREGIO
5  -  BANREGIO 114989340064 PETRO SMART DLLS - BANREGIO
26  -  BANREGIO NAVOJOA - BANREGIO
29  -  BANREGIO NAVOJOA DLLS - BANREGIO
1  -  BBVA BANCOMER 0199876693 PETRO SMART - BBVA BANCOMER
35  -  BBVA BANCOMER 0199878130 PETRO SMART TDE - BBVA BANCOMER
44  -  PETRO SMART COMBUSTIBLES - BBVA BANCOMER - 2013 - BBVA BANCOMER
24  -  PETRO SMART COMBUSTIBLES BBVA DLLS - BBVA BANCOMER
20  -  PETRO SMART COMBUSTIBLES DEL PACIFICO DLLS - BBVA BANCOMER
16  -  PETRO SMART COMBUSTIBLES SA DE CV DLLS - BBVA BANCOMER
30  -  PETRO SMART HERMOSILLO BBVA - BBVA BANCOMER
33  -  PETROSMART BANCOMER DOLARES - BBVA BANCOMER
34  -  REF BBVA 0134 - BBVA BANCOMER
18  -  BANCO MONEX 27508759 PETRO SMART - BMONEX
19  -  BANCO MONEX 2848000 PETRO SMART - BMONEX
40  -  PETRO SMART COMBUSTIBLES - MONEX - 0014 - BMONEX
27  -  HSBC 4068670355 PESOS - HSBC
22  -  PETRO SMART COMBUSTIBLES HSBC - HSBC
36  -  PETRO SMART COMBUSTIBLES SA DE CV - HSBC - 2328 - HSBC
31  -  PETRO SMART COM SABADELL - SABADELL
6  -  SANTANDER 65504900213 PETRO SMART - SANTANDER
8  -  ACREEDORES DIVERSOS - Sin Definir
10  -  COMISIONES - Sin Definir
11  -  DES Y BONIF S/VENTAS - Sin Definir
23  -  ESTIMACIONES DE CUENTAS INCOBRABLES - Sin Definir
13  -  GASTOS NO DEDUCIBLES - Sin Definir
14  -  PETROPLAZAS SF - Sin Definir
12  -  SOLUNION - Sin Definir
38  -  PETRO SMART COMBUSTIBLES - VEPORMAS - 2436 - VE POR MAS
39  -  PETRO SMART COMBUSTIBLES - VEPORMAS - 2457 - VE POR MAS
"""
)


@dataclass(frozen=True)
class Empresa:
    clave: str            # id interno
    nombre: str           # display en la app
    sipp_empresa: str     # texto a elegir en el combo de empresa del login SIPP
    sipp_sucursal: str    # sucursal/plaza del login (normalmente CORPORATIVO)
    nombre_reporte: str   # cómo aparece la empresa en col A del estado de cuenta
    # Razón social EXACTA que espera el param `empresa` de la API /api/facturas
    # (difiere del texto del combo de login; validada contra la API de test).
    api_empresa: str = ""
    cuentas: list[CuentaBancaria] = field(default_factory=list)


EMPRESAS: list[Empresa] = [
    Empresa(
        clave="ABASTECEDORA",
        nombre="Abastecedora",
        sipp_empresa="ABASTECEDORA DE COMBUSTIBLES DEL PACIFICO",
        sipp_sucursal="CORPORATIVO",
        nombre_reporte="Abastecedora",
        api_empresa="ABASTECEDORA DE COMBUSTIBLES DEL PACIFICO",
        cuentas=CUENTAS_BANCARIAS,
    ),
    Empresa(
        clave="ACP",
        nombre="ACP Combustibles",
        sipp_empresa="ACP COMBUSTIBLES - (ACP COMBUSTIBLES )",
        sipp_sucursal="CORPORATIVO",
        nombre_reporte="ACP Combustibles",
        api_empresa="ACP COMBUSTIBLES",
        cuentas=_CUENTAS_ACP,
    ),
    Empresa(
        clave="PETROSMART",
        nombre="Petro Smart",
        sipp_empresa="PETRO SMART COMBUSTIBLES - (PETRO SMART )",
        sipp_sucursal="CORPORATIVO",
        nombre_reporte="Petro Smart",
        api_empresa="PETRO SMART COMBUSTIBLES",
        cuentas=_CUENTAS_PETROSMART,
    ),
]

EMPRESA_POR_CLAVE = {e.clave: e for e in EMPRESAS}
EMPRESA_DEFAULT = EMPRESAS[0]
