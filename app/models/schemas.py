"""Contratos Pydantic de la respuesta de la IA (R4). Sin dependencias de Flask ni de agents."""
from pydantic import BaseModel, Field


class CriterioEvaluado(BaseModel):
    codigo: str
    cumple: bool
    evidencia: str = ""


class EvaluacionHV(BaseModel):
    criterios: list[CriterioEvaluado]
    alertas_seguridad: list[str] = Field(default_factory=list)
    resumen: str = ""
