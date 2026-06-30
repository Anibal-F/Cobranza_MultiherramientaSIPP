from dataclasses import dataclass


@dataclass(frozen=True)
class CuentaBancaria:
    id_sipp: str
    nombre: str


# Catálogo tal como aparece en el <select id="cmbCuentasBancarias"> de SIPP
# (pantalla "Ingresos Diversos - Agregar"). El id_sipp es el value del <option>,
# usado únicamente como referencia; la selección en pantalla se hace por texto
# (igual que el resto de los selects "chosen" de SIPP).
CUENTAS_BANCARIAS: list[CuentaBancaria] = [
    CuentaBancaria("19", "AMERICAN EXPRESS - AMERICAN EXPRESS"),
    CuentaBancaria("11", "BAJÍO 001608447 ABASTECEDORA - BAJIO"),
    CuentaBancaria("15", "BAJIO 16084470201 ABASTECEDORA - BAJIO"),
    CuentaBancaria("41", "ABASTECEDORA DE COMBUSTIBLES BANAMEX - BANAMEX"),
    CuentaBancaria("18", "BANAMEX 394-7680454 ABASTECEDORA - BANAMEX"),
    CuentaBancaria("16", "BANAMEX DOLARES 237-9137728 ABASTECEDORA - BANAMEX"),
    CuentaBancaria("36", "ABASTECEDORA SF /AENE - Banco General Para Pagos Corporativo"),
    CuentaBancaria("29", "ANTICIPO DE CLIENTES - Banco General Para Pagos Corporativo"),
    CuentaBancaria("28", "Anticipos Corporativo VP - Banco General Para Pagos Corporativo"),
    CuentaBancaria("40", "DEUDORES DIVERSOS - Banco General Para Pagos Corporativo"),
    CuentaBancaria("38", "INVERSION - Banco General Para Pagos Corporativo"),
    CuentaBancaria("44", "ABASTECEDORA DE COM COPPEL - BANCOPPEL"),
    CuentaBancaria("1", "BANORTE 0502939411 ABASTECEDORA - BANORTE"),
    CuentaBancaria("34", "Banorte 0510125198 - BANORTE"),
    CuentaBancaria("35", "EDENRED MEXICO SA DE CV - BANORTE"),
    CuentaBancaria("53", "EUGENIA ELIENAI MEDEL GARCIA - BANORTE"),
    CuentaBancaria("42", "PETROPLAZAS MONEDEROS - BANORTE"),
    CuentaBancaria("27", "ABASTECEDORA DE COMBUSTI BANREGIO DLLS - BANREGIO"),
    CuentaBancaria("2", "BANREGIO 114999250012 ABASTECEDORA - BANREGIO"),
    CuentaBancaria("32", "BANREGIO DOLARES 1149992500407 ABASTECEDORA - BANREGIO"),
    CuentaBancaria("60", "ABASTECEDORA DE COMBUSTIBLES DEL PACIFICO - BBVA BANCOMER - 1947 - BBVA BANCOMER"),
    CuentaBancaria("39", "ABASTECEDORA DE COMBUSTIBLES TDE - BBVA BANCOMER"),
    CuentaBancaria("4", "BBVA BANCOMER  DLLS 0104729250 ABASTECEDORA - BBVA BANCOMER"),
    CuentaBancaria("3", "BBVA BANCOMER 0100647012 ABASTECEDORA - BBVA BANCOMER"),
    CuentaBancaria("52", "LUXDEI ENERGY SAPI DE CV - BBVA BANCOMER"),
    CuentaBancaria("50", "REF BBVA 9475 - BBVA BANCOMER"),
    CuentaBancaria("65", "ABASTECEDORA - MONEX - 0875 - BMONEX"),
    CuentaBancaria("30", "BANCO MONEX 27508759 ABASTECEDORA - BMONEX"),
    CuentaBancaria("31", "BANCO MONEX 2848000 ABASTECEDORA - BMONEX"),
    CuentaBancaria("17", "MONEX DLLS 2793487 - BMONEX"),
    CuentaBancaria("51", "ABASTECEDORA DE COMBUSTIBLES DEL PACIFICO - BMULTIVA"),
    CuentaBancaria("55", "ABASTECEDORA DE COMBUSTIBLES DEL PA - HSBC - 2302 - HSBC"),
    CuentaBancaria("6", "HSBC 4029940657 ABASTECEDORA - HSBC"),
    CuentaBancaria("7", "HSBC 4057717886 ABASTECEDORA - HSBC"),
    CuentaBancaria("45", "ABASTECEDORA DE COM INBURSA - INBURSA"),
    CuentaBancaria("49", "INTERCAM 0119 - INTERCAM"),
    CuentaBancaria("64", "ABASTECEDORA - kapital - 0011 - Kapital Bank"),
    CuentaBancaria("46", "MULTIVA ABASTECEDORA - MULTIVA"),
    CuentaBancaria("43", "ABASTECEDORA DE COM SABADELL - SABADELL"),
    CuentaBancaria("56", "ABASTECEDORA DE COMBUSTIBLES DEL PACIFIC - SANTANDER - 0249 - SANTANDER"),
    CuentaBancaria("33", "AMADO SABAS GUZMAN REYNAUD - SANTANDER"),
    CuentaBancaria("5", "SANTANDER 65501847917 ABASTECEDORA - SANTANDER"),
    CuentaBancaria("8", "SANTANDER 65502640042 ABASTECEDORA - SANTANDER"),
    CuentaBancaria("12", "SANTANDER 65502869481 ABASTECEDORA - SANTANDER"),
    CuentaBancaria("9", "SCOTIABANK 1700512613 ABASTECEDORA - SCOTIABANK"),
    CuentaBancaria("24", "ACREEDORES DIVERSOS - Sin Definir"),
    CuentaBancaria("20", "COMISIONES - Sin Definir"),
    CuentaBancaria("21", "DES Y BONIF S/VENTAS - Sin Definir"),
    CuentaBancaria("37", "ESTIMACIONES DE CUENTAS INCOBRABLES - Sin Definir"),
    CuentaBancaria("23", "GASTOS NO DEDUCIBLES - Sin Definir"),
    CuentaBancaria("25", "PETROPLAZAS SF - Sin Definir"),
    CuentaBancaria("22", "SOLUNION - Sin Definir"),
    CuentaBancaria("62", "ABASTECEDORA - VEPORMAS - 9181 - VE POR MAS"),
    CuentaBancaria("10", "VE POR MAS 29180 ABASTECEDORA - VE POR MAS"),
]
