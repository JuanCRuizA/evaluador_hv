"""Utilidades de tests: cabeceras fetch + CSRF para las rutas JSON."""
import re

_META = re.compile(r'<meta name="csrf-token" content="([^"]*)"')


def token_csrf(client):
    """Token del <meta name="csrf-token"> con un GET a '/' (vacío si no hay sesión)."""
    html = client.get("/").get_data(as_text=True)
    m = _META.search(html)
    return m.group(1) if m else ""


def cabeceras(client):
    return {"X-Requested-With": "fetch", "X-CSRF-Token": token_csrf(client)}


# ---- PDF reales de insumos/ (solo lectura; no existen en un clon limpio) ----
import os
from functools import lru_cache
from pathlib import Path

import pytest

RAIZ_PDFS = Path(__file__).resolve().parent.parent / "insumos" / "hojas_de_vida"
MENSAJE_SIN_PDFS = "no hay PDF reales en insumos/hojas_de_vida (no van a git)"


def _carpeta_pdfs():
    """EVALUADOR_HV_PDFS_DIR permite apuntar a otra carpeta (p. ej. una vacía)."""
    return Path(os.environ.get("EVALUADOR_HV_PDFS_DIR") or RAIZ_PDFS)


def listar_pdfs():
    """Lista ordenada (puede estar vacía) de insumos/hojas_de_vida/**/*.pdf."""
    carpeta = _carpeta_pdfs()
    if not carpeta.is_dir():
        return []
    return sorted(carpeta.glob("**/*.pdf"), key=lambda p: p.relative_to(carpeta).as_posix())


def pdfs_reales():
    """Como listar_pdfs, pero salta el test si no hay ninguno."""
    pdfs = listar_pdfs()
    if not pdfs:
        pytest.skip(MENSAJE_SIN_PDFS)
    return pdfs


def indices_pdfs():
    """Parámetros pytest con ids 'pdf0', 'pdf1'... (sin nombres de archivo)."""
    n = len(listar_pdfs())
    if n == 0:
        return [pytest.param(0, id="sin-pdfs", marks=pytest.mark.skip(reason=MENSAJE_SIN_PDFS))]
    return [pytest.param(i, id=f"pdf{i}") for i in range(n)]


def pdf_por_indice(i):
    return pdfs_reales()[i]


@lru_cache(maxsize=None)
def _texto_limpio(ruta):
    from app.services import documentos as d
    return d.quitar_datos_personales(d.extraer_texto(Path(ruta).read_bytes()))


def texto_limpio(ruta):
    """Texto extraído y limpiado (R3) de un PDF, con caché."""
    return _texto_limpio(str(ruta))


def pdf_con_texto(max_bytes=None):
    """Primer PDF (por orden) con texto >= 200 caracteres; max_bytes filtra por tamaño (R1: 4 MB)."""
    from app.services import documentos as d
    for ruta in pdfs_reales():
        if max_bytes is not None and ruta.stat().st_size > max_bytes:
            continue
        if d.estado_por_texto(texto_limpio(ruta)) == "pendiente":
            return ruta
    pytest.skip("ningún PDF real tiene texto suficiente")


def pdf_sin_texto(max_bytes=None):
    """El primer PDF (por orden) cuyo texto tiene < 200 caracteres."""
    from app.services import documentos as d
    for ruta in pdfs_reales():
        if max_bytes is not None and ruta.stat().st_size > max_bytes:
            continue
        if d.estado_por_texto(texto_limpio(ruta)) == "sin_texto":
            return ruta
    pytest.skip("ningún PDF real carece de texto (escaneado)")

