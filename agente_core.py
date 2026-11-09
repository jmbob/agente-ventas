"""Núcleo del agente de ventas, compartido por la consola y la web."""

import os
from pathlib import Path
import re
import sqlite3

from copilot import CopilotClient
from copilot.session import PermissionHandler
from copilot.tools import define_tool
from dotenv import load_dotenv
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "sales_data.db"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
SYSTEM_PROMPT = """Eres un analista de datos de ventas con acceso a una base SQLite.
Tabla sales: Store_Number INTEGER, SKU_Coded INTEGER,
Product_Class_Code INTEGER, Sold_Date TEXT (AAAA-MM-DD),
Qty_Sold INTEGER, Total_Sale_Value REAL, On_Promo INTEGER (1/0).
Responde en español. Para cada pregunta sobre los datos, usa query_sales antes de
responder. Genera únicamente consultas SELECT sobre sales. Limita resultados
detallados a 100 filas. Da formato claro a las cifras monetarias e indica tus
suposiciones cuando la pregunta sea ambigua.
"""


class QuerySalesParams(BaseModel):
    sql: str = Field(description="Consulta SQL SELECT de solo lectura a la tabla sales")


@define_tool(description="Ejecuta una consulta SELECT de solo lectura sobre las ventas")
async def query_sales(params: QuerySalesParams) -> dict:
    sql = params.sql.strip()
    if not re.match(r"(?is)^SELECT\b", sql):
        return {"error": "Solo se permiten consultas SELECT"}
    try:
        connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        try:
            cursor = connection.execute(sql)
            rows = [dict(row) for row in cursor.fetchmany(101)]
        finally:
            connection.close()
        return {"rows": rows[:100], "count": min(len(rows), 100), "truncated": len(rows) > 100}
    except sqlite3.Error as exc:
        return {"error": str(exc)}


def get_api_key() -> str:
    load_dotenv(ROOT / ".env")
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError(f"Falta OPENROUTER_API_KEY en {ROOT / '.env'} o en el entorno.")
    return key


def check_database() -> None:
    if not DB_PATH.is_file():
        raise FileNotFoundError(f"No existe la base de datos: {DB_PATH}")


def create_client() -> CopilotClient:
    return CopilotClient(
        mode="empty",
        working_directory=str(ROOT),
        base_directory=str(ROOT / ".copilot-state"),
    )


async def create_session(client: CopilotClient, model: str):
    return await client.create_session(
        on_permission_request=PermissionHandler.approve_all,
        model=model,
        provider={
            "type": "openai",
            "base_url": "https://openrouter.ai/api/v1",
            "wire_api": "responses",
            "api_key": get_api_key(),
        },
        streaming=True,
        tools=[query_sales],
        available_tools=["custom:query_sales"],
        system_message={"mode": "replace", "content": SYSTEM_PROMPT},
    )
