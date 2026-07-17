Les comparto la documentación correspondiente a la API de la Multiherramienta de Cobranza, disponible actualmente en ambiente de pruebas.

URL Base
https://us-central1-soluciones-petroil.cloudfunctions.net/billing-toolkit-testing

Consideraciones Generales
La API está disponible únicamente en ambiente TEST.
Todos los endpoints requieren el siguiente header:
HTTP
x-auth-token: P6sgpjDWQayUBuURQTDQ4JVio2upEfrP4Cqg
Nota: Este token podrá cambiar al migrar a ambiente productivo. En ese caso, deberá solicitarse al equipo responsable.


Endpoint: /api/facturas
Método: GET
Descripción: Devuelve el listado de facturas correspondientes exclusivamente a clientes. No incluye notas de crédito, facturas de traslado u otros tipos.
Query Params
Obligatorios:
empresa: Razón social de la empresa (texto)
fechaVencimientoInicio: Fecha en formato YYYY-MM-DD
fechaVencimientoFin: Fecha en formato YYYY-MM-DD
Opcionales:
folio: UUID de la factura
page: Número de página
pageSize: Tamaño de página
Nota: El campo sucursal será agregado en el transcurso del día, sujeto a validación.

Ejemplo de respuesta
JSON
{
"msg": "Success",
"meta": {
"totalRecords": 26,
"totalPages": 2,
"page": 1,
"pageSize": 20
},
    "data": [
{
"fl_FolioDocumento": "FMZ233715",
"fh_Documento": "2026-05-22T00:00:00.000Z",
"fh_Vencimiento": "2026-06-21T00:00:00.000Z",
"im_Total": 3367.74,
"im_SaldoFactura": 3367.74,
"de_UUID": "26D35A9C-A88C-4E71-A1E4-EC19D6254FF5",
"de_RazonSocialCliente": "PETRO SMART COMBUSTIBLES DEL PACIFICO"
}
    ]
}
Endpoint: /api/clientes
Método: GET
Descripción: Obtiene el listado de clientes registrados en el sistema.
Query Params
Opcionales:
id_Cliente: Identificador único del cliente
sn_Activo: 1 o 0 (activo/inactivo)
page: Número de página
pageSize: Tamaño de página
Ejemplo de respuesta
JSON
{
"msg": "Success",
"meta": {
"totalRecords": 1,
"totalPages": 1,
"page": 1,
"pageSize": 20
     },
"data": [
{
"id_Cliente": 47,
"de_RFC": "ACP000726NG7",
"de_RazonSocial": "ABASTECEDORA DE COMBUSTIBLES DEL PACIFICO",
"sn_Activo": true
}
    ]
}
Endpoint: /api/clientes/sucursales
Método: GET
Descripción:
Devuelve las plazas (sucursales) de los clientes donde se han generado facturas dentro del sistema SIPP.
Query Params
Opcionales:
id_Cliente: Identificador del cliente
sn_Activo: 1 o 0 (activo/inactivo)
Ejemplo de respuesta
JSON
{
"msg": "Success",
"data": [
{
"de_Cliente": "00001 - AVANCE ACUICOLA SA DE CV",
"id_Cliente": 1,
"de_RFC": "AAC060314AE3",
"sn_Activo": 1,
"plazas": [
{
"id_Plaza": 8,
"nb_Plaza": "Los Mochis"
}
]
}
]
}