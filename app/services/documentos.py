"""R2 y R3: extracción de texto, estado y limpieza de datos personales. Python puro."""
import io
import re

import pdfplumber

MIN_CARACTERES = 200


class PdfIlegibleError(ValueError):
    """El PDF está dañado o no se puede leer."""


def extraer_texto(contenido):
    """Texto de todas las páginas. Tolera páginas sin texto; PDF dañado lanza PdfIlegibleError."""
    try:
        partes = []
        with pdfplumber.open(io.BytesIO(contenido)) as pdf:
            for pagina in pdf.pages:
                try:
                    partes.append(pagina.extract_text() or "")
                except Exception:  # una página rota no tumba el documento
                    partes.append("")
        return limpiar_control("\n".join(partes)).strip()
    except Exception as exc:
        raise PdfIlegibleError("PDF dañado o ilegible") from exc


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\ud800-\udfff]")


def limpiar_control(texto):
    """Quita NUL y demás caracteres de control no imprimibles (salvo salto de línea y tab);
    PostgreSQL rechaza \x00 en columnas de texto."""
    return _CONTROL.sub("", texto or "")


def nombre_candidato(nombre_archivo):
    """Nombre del archivo sin .pdf, con guiones bajos convertidos en espacios."""
    base = re.sub(r"\.pdf$", "", (nombre_archivo or "").strip(), flags=re.IGNORECASE)
    base = re.sub(r"[_\s]+", " ", base).strip()
    return base or "Sin nombre"


def estado_por_texto(texto):
    """R2: 'sin_texto' si el texto tiene menos de MIN_CARACTERES; si no, 'pendiente'."""
    return "sin_texto" if len((texto or "").strip()) < MIN_CARACTERES else "pendiente"


_I = re.IGNORECASE
_CORREO = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

# Compromiso R3: se trabaja línea por línea. Si en una línea hay datos con etiqueta se
# reemplaza SOLO ese fragmento (la etiqueta con su valor) por nada, para no perder el resto
# de las columnas que pdfplumber junta en la misma línea. Si tras quitarlo la línea queda sin
# letras ni números, se borra entera. Los fragmentos sin valor reconocible (p. ej. "lugar de
# nacimiento: ...") se quitan hasta el siguiente separador (| • · ;) o fin de línea.
_SEP = r"[^|•·;]*"
_FECHA = (
    r"(?:\d{1,2}\s+de\s+[a-záéíóú]+\s+(?:de\s+)?\d{4}"
    r"|[a-z]+\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}"
    r"|\d{1,2}(?:st|nd|rd|th)?\s+(?:de\s+)?[a-záéíóú]+\.?,?\s+\d{4}"
    r"|\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4})"
)
_FECHA_SUELTA = re.compile(r"(?<![\d/.-])\d{1,2}[/.-]\d{1,2}[/.-](?:19|20)?\d{2}(?![\d/.-])")
_CONTEXTO_NAC = re.compile(
    r"\b(?:nacimiento|naci[oó]|nacid[oa]|born|birth\w*|dob|edad|age)\b|\bf\.\s*nac", _I)

_VALORES_CIVIL = (
    r"(?:solter[oa](?:\s*/\s*a|\(a\))?|casad[oa](?:\s*/\s*a|\(a\))?|divorciad[oa](?:\s*/\s*a|\(a\))?"
    r"|viud[oa](?:\s*/\s*a|\(a\))?|separad[oa]|uni[oó]n\s+libre|single|married|divorced|widowed)"
)
_LINEA_CIVIL = re.compile(
    rf"^\s*(?:estado\s*[:\-]\s*)?{_VALORES_CIVIL}(?:\s*[/,]\s*{_VALORES_CIVIL})*\s*[.,]?\s*$", _I)

_DOC_ETIQUETA = (
    r"(?<![\w.])(?:c[eé]dula(?:\s+de\s+(?:ciudadan[ií]a|extranjer[ií]a))?"
    r"|documento(?:\s+de\s+identidad)?|identificaci[oó]n|id\s+number|identity\s+(?:card|number)"
    r"|dni|nit|pasaporte|passport(?:\s+(?:no|number))?|tarjeta\s+de\s+identidad"
    r"|c\.\s?c\.?|cc|t\.\s?i\.?)"
)
# Valor: 6 a 14 dígitos con puntos, guiones o espacios (opcional 1-2 letras al inicio).
_DOC_VALOR = (
    r"(?!(?:19|20)\d{2}\s*[-–]\s*(?:19|20)\d{2})(?:[a-z]{1,2})?(?:\d[.\- ]?){6,14}(?<![.\- ])"
)

_FRAGMENTOS = [
    # Nacimiento: etiqueta + fecha, o hasta el separador si no hay fecha reconocible.
    re.compile(
        r"(?<![\w])(?:(?:fecha|lugar)\s+de\s+nacimiento|f\.\s*(?:de\s+)?nac\w*|nacimiento(?=\s*[:\-])"
        r"|date\s+of\s+birth|birth\s*date|birthday|birth\s*place|place\s+of\s+birth|dob)"
        rf"(?:\s*[:\-]?\s*{_FECHA}|{_SEP})", _I),
    re.compile(
        rf"(?<![\w])(?:naci[oó]|nacid[oa]|born)\b{_SEP}", _I),
    # Documento de identidad (solo con etiqueta + número).
    re.compile(
        rf"{_DOC_ETIQUETA}\s*(?:(?:no|n[°º]|num\w*|number)\.?\s*)?[:#\-–]?\s*{_DOC_VALOR}", _I),
    # Estado civil con etiqueta (valor opcional, una palabra si no está en la lista).
    re.compile(
        rf"(?<![\w])(?:estado\s+civil|marital\s+status)\s*[:\-]?\s*(?:{_VALORES_CIVIL}|\w+)?", _I),
    re.compile(rf"(?<![\w])estado\s*:\s*{_VALORES_CIVIL}", _I),
    # Edad, género, sexo.
    re.compile(r"(?<![\w])(?:edad|age)\s*[:\-]\s*\d{1,2}(?:\s*(?:a[ñn]os|years?)(?:\s+old)?)?", _I),
    re.compile(r"(?<![\w])\d{2}\s+a[ñn]os\s+de\s+edad", _I),
    re.compile(r"(?<![\w])(?:g[eé]nero|sexo|gender)\s*[:\-]\s*[\wáéíóúñ/]+", _I),
    # Enlaces: cualquier URL (contienen el nombre de la persona) y redes con usuario.
    re.compile(r"(?:https?://|www\.)\S+", _I),
    re.compile(
        r"(?<![\w@])(?:[\w-]+\.)?(?:linkedin|github|gitlab|behance|twitter|instagram|facebook"
        r"|kaggle|medium|lnkd)\.(?:com|in|co|io)\S*", _I),
    re.compile(r"(?<![\w@])[\w-]+\.(?:com|co|net|org|io|dev)/\S+", _I),
    re.compile(
        r"(?<![\w])(?:linkedin|github|gitlab|behance|instagram|twitter)\s*[:|]\s*\S+", _I),
]
_FRAGMENTOS += [
    # Ubicación, origen y residencia con etiqueta explícita (R8): hasta el separador o fin de línea.
    re.compile(
        r"(?<![\w])(?:direcci[oó]n(?:\s+de\s+(?:residencia|domicilio))?|address|residencia|residence"
        r"|domicilio|barrio|nacionalidad|nationality|lugar\s+de\s+origen|place\s+of\s+origin"
        r"|ciudad\s+de\s+residencia|city\s+of\s+residence|pa[ií]s\s+de\s+residencia"
        r"|location|ubicaci[oó]n)\s*[:\-–]" + _SEP, _I),
    # Dominios sin esquema (juanperez.dev); ver _HOSTS_TECNOLOGICOS y la limitación.
    re.compile(
        r"(?<![\w@./-])(?P<host>[a-z0-9][\w-]{3,})\.(?:dev|me|co|io|com|net|org|app|site|xyz|tech)"
        r"(?:\.[a-z]{2})?(?![\w-])(?:/\S*)?", _I),
]
_FRAGMENTOS_ENLACE = {8, 9, 10, 11, len(_FRAGMENTOS) - 1}
_DOMINIO = _FRAGMENTOS[-1]
# Tecnologías/archivos que parecen dominio. Limitación: un dominio personal con host de menos
# de 4 caracteres ("ana.me") o con TLD fuera de la lista (.ai, .page) no se detecta, y una
# tecnología con TLD de la lista que no esté aquí se tomaría por dominio (queda como [enlace]).
_HOSTS_TECNOLOGICOS = {
    "socket", "asp", "vb", "ado", "dot", "dockerfile", "docker-compose", "docker", "config",
    "settings", "webpack", "vite", "appsettings", "launch", "package", "tsconfig",
}

_TELEFONOS = [
    # Internacional con +: +57 300 123 4567, +573001234567, +1 (415) 555-0100
    re.compile(r"(?<![\w])\+\d{1,3}(?:[\s.-]?\(?\d{1,4}\)?){2,5}(?!\d)"),
    # Prefijo 00: 00 57 300 123 4567
    re.compile(r"(?<![\w+])00[\s.-]?\d{1,3}(?:[\s.-]?\(?\d{1,4}\)?){2,5}(?!\d)"),
    # Móvil colombiano con cualquier agrupación: 3001234567, 300 123 4567, 300 12 34 567
    re.compile(r"(?<![\w.,$])3\d{2}(?:[\s.-]?\d){7}(?![\w]|[.,]\d)"),
    # Fijo con indicativo: (601) 123 4567, 601-123-4567
    re.compile(r"(?<![\w.,$])\(?60\d\)?[\s.-]?\d{3}[\s.-]?\d{4}(?![\w]|[.,]\d)"),
    # Otros países: (415) 555-0100 y 415-555-0100 / 415 555 0100
    re.compile(r"(?<![\w.,$])\(\d{2,4}\)[\s.-]?\d{3}[\s.-]?\d{4}(?![\w]|[.,]\d)"),
    re.compile(r"(?<![\w.,$])\d{3}[\s.-]\d{3}[\s.-]\d{4}(?![\w]|[.,]\d)"),
]
_TEL_ETIQUETA = re.compile(
    r"(?<![\w])(?P<et>tel(?:[eé]fono)?s?|phone|cel(?:ular)?|m[oó]vil|mobile|whatsapp|fono|fax)\.?"
    r"(?P<sep>\s*[:\-]?\s*)(?P<num>\+?\(?\d[\d\s().-]{5,}\d)", _I)


def _tel_con_etiqueta(m):
    digitos = re.sub(r"\D", "", m.group("num"))
    if not 7 <= len(digitos) <= 13:
        return m.group(0)
    return f"{m.group('et')}{m.group('sep')}[teléfono]"


def _sub_dominio(m):
    host = m.group("host").lower()
    if host in _HOSTS_TECNOLOGICOS or re.fullmatch(r"[\d-]+", host):
        return m.group(0)
    return "[enlace]"


# Línea que es solo un número de 8 a 10 dígitos (opcional "No."/"N°"/"#" y puntos/comas de
# miles): se toma por documento de identidad. No toca 580.000 (6 dígitos) ni años.
_NUM_ID = re.compile(
    r"^\s*(?:(?:no|n[°º]|nro|num)\.?\s*|#\s*)?(\d{8,10}|\d{1,3}(?:[.,]\d{3}){2,3})\s*$", _I)


def _es_numero_id(linea):
    m = _NUM_ID.match(linea)
    return bool(m) and 8 <= len(re.sub(r"\D", "", m.group(1))) <= 10


def _limpiar_linea(linea):
    if _LINEA_CIVIL.match(linea):
        return ""
    con_nacimiento = bool(_CONTEXTO_NAC.search(linea))
    original = linea
    linea = _CORREO.sub("[correo]", linea)
    for i, patron in enumerate(_FRAGMENTOS):
        if patron is _DOMINIO:
            linea = patron.sub(_sub_dominio, linea)
        else:
            linea = patron.sub("[enlace]" if i in _FRAGMENTOS_ENLACE else "", linea)
    if con_nacimiento:  # fechas dd/mm/aaaa sueltas solo si la línea habla de nacimiento
        linea = _FECHA_SUELTA.sub("", linea)
    for patron in _TELEFONOS:
        linea = patron.sub("[teléfono]", linea)
    linea = _TEL_ETIQUETA.sub(_tel_con_etiqueta, linea)
    if _es_numero_id(linea):
        return ""
    if linea != original:
        linea = re.sub(r"(?:\s*[|•·;]\s*){2,}", " | ", linea)
        linea = re.sub(r"^[\s|•·;,:-]+|[\s|•·;,:-]+$", "", linea)
        linea = re.sub(r"[ \t]{2,}", " ", linea)
        if not re.search(r"\w", linea):
            return ""
    return linea


def _pasada(texto):
    salida = []
    for linea in texto.split("\n"):
        nueva = _limpiar_linea(linea)
        if nueva == "" and linea.strip() != "":
            continue  # la línea era solo un dato personal
        salida.append(nueva)
    return "\n".join(salida)


def quitar_datos_personales(texto):
    """R3: correos -> [correo], teléfonos -> [teléfono], enlaces -> [enlace]; quita fecha de
    nacimiento, documento, estado civil, edad, género, dirección/residencia/nacionalidad/
    ubicación etiquetadas y números de identificación sueltos; elimina caracteres de control.
    Idempotente: se repite hasta que el texto no cambia."""
    actual = limpiar_control(texto)
    for _ in range(5):
        nuevo = _pasada(actual)
        if nuevo == actual:
            break
        actual = nuevo
    return actual
