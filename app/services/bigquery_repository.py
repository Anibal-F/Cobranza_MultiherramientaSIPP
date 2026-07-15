"""Repository de BigQuery: acceso a datos crudos, reutilizable.

Encapsula el cliente y las consultas para que distintos módulos (Conciliación
Bancaria ahora; Dashboard de Ingresos en una entrega futura) obtengan datos SIN
duplicar la lógica de conexión ni el formato de UI. Devuelve `list[dict]` crudo.

A diferencia del Dashboard (que consulta agregados con SUM/GROUP BY), la
conciliación necesita movimientos a NIVEL DE FILA: descripción, referencia,
importe y fecha de cada movimiento del sistema, para cruzarlos contra el archivo
del banco.
"""

from datetime import date

from google.cloud import bigquery

from .bigquery_cliente import TABLA, cliente_bigquery

# --- Configuración de columnas del lado "Sistema" -------------------------------
# La tabla nueva es el formato de IgresosClientes MÁS de_CuentaBancaria, de_Referencia
# y de_Concepto. El conciliador compara la referencia y el concepto del sistema contra
# el concepto/referencia del banco (ver conciliador.py), así que aquí se mapean esos
# campos a las llaves canónicas de MovimientoConciliacion.
#
# Cada COL_* se interpola tal cual en el SELECT: puede ser el NOMBRE de una columna
# real o una EXPRESIÓN/LITERAL SQL. Ajustar aquí si cambian los nombres de columna.
COL_DESCRIPCION = "de_Concepto"     # concepto del movimiento (una de las agujas)
COL_REFERENCIA = "de_Referencia"    # referencia del movimiento (la otra aguja)
COL_IMPORTE = "im_Movimiento"
COL_FECHA = "fh_Envio"
COL_CUENTA = "de_CuentaBancaria"    # cuenta bancaria (se expone en `raw`, no se cruza)

# Filtro SQL opcional (sin el WHERE) que restringe al universo de IngresosDiversos.
# Vacío = sin restricción adicional. Ej.: "nb_TipoDeNegocio = 'IngresosDiversos'".
FILTRO_INGRESOS_DIVERSOS = ""


class BigQueryRepository:
    """Punto único de acceso a BigQuery para datos crudos.

    `tabla` es inyectable para pruebas o para apuntar a otra fuente sin tocar el
    resto del código.
    """

    def __init__(self, tabla: str = TABLA) -> None:
        self._cliente = cliente_bigquery()  # comparte el singleton del módulo cliente
        self._tabla = tabla

    def movimientos_crudos(
        self,
        fecha_inicio: date,
        fecha_fin: date,
        filtros: dict | None = None,
    ) -> list[dict]:
        """Movimientos del sistema a nivel de fila en [fecha_inicio, fecha_fin]
        (ambas incluidas). Devuelve `list[dict]` con llaves canónicas ya alineadas
        a MovimientoConciliacion: `descripcion`, `referencia`, `importe`, `fecha`.

        `filtros` (opcional) admite `empresa` y/o `cuenta` para acotar la consulta;
        se pasan siempre como query parameters (nunca interpolados) para evitar
        inyección SQL.
        """
        filtros = filtros or {}
        condiciones = ["DATE(%s) BETWEEN @fecha_inicio AND @fecha_fin" % COL_FECHA]
        parametros = [
            bigquery.ScalarQueryParameter("fecha_inicio", "DATE", fecha_inicio),
            bigquery.ScalarQueryParameter("fecha_fin", "DATE", fecha_fin),
        ]

        if FILTRO_INGRESOS_DIVERSOS:
            condiciones.append(FILTRO_INGRESOS_DIVERSOS)
        if filtros.get("empresa"):
            condiciones.append("nb_Empresa = @empresa")
            parametros.append(bigquery.ScalarQueryParameter("empresa", "STRING", filtros["empresa"]))
        if filtros.get("cuenta"):
            condiciones.append("nb_Cuenta = @cuenta")
            parametros.append(bigquery.ScalarQueryParameter("cuenta", "STRING", filtros["cuenta"]))

        query = f"""
            SELECT
                {COL_DESCRIPCION} AS descripcion,
                {COL_REFERENCIA} AS referencia,
                {COL_IMPORTE} AS importe,
                DATE({COL_FECHA}) AS fecha,
                {COL_CUENTA} AS cuenta
            FROM `{self._tabla}`
            WHERE {" AND ".join(condiciones)}
        """
        job_config = bigquery.QueryJobConfig(query_parameters=parametros)
        filas = self._cliente.query(query, job_config=job_config).result()
        return [dict(fila.items()) for fila in filas]
