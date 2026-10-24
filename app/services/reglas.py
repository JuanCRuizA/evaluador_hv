"""R5 a R7: verificación de citas, puntaje y ranking. Python puro."""
import re
import unicodedata

MIN_CITA = 4
# Marcadores que deja la limpieza (R3) y el saneo (R9); ya normalizados.
_RE_MARCADORES = re.compile(r"\[(?:correo|telefono|enlace|etiqueta eliminada)\]")


def normalizar(texto):
    """Sin mayúsculas, tildes ni espacios extra (incluye saltos de línea)."""
    s = unicodedata.normalize("NFD", texto or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s.casefold()).strip()


def cita_existe(texto, cita):
    """R5: la cita está contenida en el texto y, sin marcadores de limpieza
    ([correo], [teléfono], [enlace], [etiqueta eliminada]), tiene al menos 4 caracteres."""
    c = normalizar(cita)
    sin_marcadores = _RE_MARCADORES.sub(" ", c).strip()
    return len(sin_marcadores) >= MIN_CITA and c in normalizar(texto)


def _get(obj, clave, defecto=None):
    if isinstance(obj, dict):
        return obj.get(clave, defecto)
    return getattr(obj, clave, defecto)


def resultado(perfil, texto, criterios):
    """R5 y R6. `criterios`: lista de objetos o dicts con codigo, cumple, evidencia.

    Devuelve dict con: criterios (lista de dicts), avanza, puntaje, no_verificados.
    """
    por_codigo = {}
    for c in criterios or []:
        por_codigo.setdefault(str(_get(c, "codigo", "")).strip(), c)

    items = [(c, "O") for c in perfil.get("obligatorios", [])]
    items += [(c, "D") for c in perfil.get("deseables", [])]

    detalle, no_verificados = [], []
    for crit, tipo in items:
        cod = crit["codigo"]
        r = por_codigo.get(cod)
        cumple_ia = bool(_get(r, "cumple", False)) if r is not None else False
        evidencia = (_get(r, "evidencia", "") or "") if r is not None else ""
        verificada = cumple_ia and cita_existe(texto, evidencia)
        if cumple_ia and not verificada:
            no_verificados.append(cod)
        detalle.append({
            "codigo": cod, "tipo": tipo, "cumple_ia": cumple_ia,
            "evidencia": evidencia, "verificada": verificada,
            "cuenta": cumple_ia and verificada,
            "puntos": crit.get("puntos", 0) if tipo == "D" else 0,
        })

    avanza = all(d["cuenta"] for d in detalle if d["tipo"] == "O")
    puntaje = 0
    if avanza:
        puntaje = sum(d["puntos"] for d in detalle if d["tipo"] == "D" and d["cuenta"])
    return {"criterios": detalle, "avanza": avanza, "puntaje": puntaje,
            "no_verificados": no_verificados}


def alerta_no_verificados(codigos):
    """Texto de alerta R5, o '' si no hay códigos."""
    return "Evidencia no verificada en: " + ", ".join(codigos) if codigos else ""


def ordenar_ranking(items):
    """R7: avanzan primero, puntaje descendente, luego nombre (sin mayúsculas ni tildes)."""
    return sorted(items, key=lambda x: (
        not _get(x, "avanza", False),
        -(_get(x, "puntaje", 0) or 0),
        normalizar(_get(x, "nombre", "")),
    ))
