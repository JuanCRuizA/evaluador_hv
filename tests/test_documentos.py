"""R2 y R3: extracción, estado y limpieza. Los PDF de insumos/ se leen solo en lectura."""
import re

import pytest

from app.services import documentos as d
from tests.helpers import indices_pdfs, pdf_con_texto, pdf_por_indice, pdf_sin_texto, pdfs_reales

CORREO = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
TEL_INTL = re.compile(r"\+\d[\d\s().-]{7,}")
TEL_MOVIL = re.compile(r"(?<![\w.,$])3\d{2}[\s.-]?\d{3}[\s.-]?\d{4}(?!\w)")


RANGO = re.compile(
    r"(?:[A-Za-z]{3,9}\.? )?(?:19|20)\d{2} ?[-–] ?(?:[A-Za-z]{3,9}\.? )?(?:(?:19|20)\d{2}|presente|actual)",
    re.I,
)


def _ws(t):
    return re.sub(r"\s+", " ", t)


@pytest.mark.parametrize("i", indices_pdfs())
def test_pdf_real_limpio_sin_pii_y_conserva_rangos(i):
    ruta = pdf_por_indice(i)
    original = d.extraer_texto(ruta.read_bytes())
    if not original.strip():
        pytest.skip("PDF escaneado: no hay texto que revisar")
    assert CORREO.search(original), "el PDF debía traer un correo para que la prueba tenga sentido"
    limpio = d.quitar_datos_personales(original)
    assert not CORREO.search(limpio)
    assert not TEL_INTL.search(limpio)
    assert not TEL_MOVIL.search(limpio)
    # los rangos de años que estaban en líneas conservadas siguen presentes
    limpio_ws = _ws(limpio)
    lineas_limpias = set(limpio.splitlines())
    for linea in original.splitlines():
        if linea in lineas_limpias:
            for rango in RANGO.findall(linea):
                assert _ws(rango) in limpio_ws


def test_recall_at_k_no_se_toca():
    con_marca = [r for r in pdfs_reales() if "recall@k" in d.extraer_texto(r.read_bytes())]
    if not con_marca:
        pytest.skip("ningún PDF real menciona recall@k")
    for ruta in con_marca:
        assert "recall@k" in d.quitar_datos_personales(d.extraer_texto(ruta.read_bytes()))


def test_pdf_escaneado_es_sin_texto():
    texto = d.quitar_datos_personales(d.extraer_texto(pdf_sin_texto().read_bytes()))
    assert d.estado_por_texto(texto) == "sin_texto"


def test_pdf_con_texto_es_pendiente():
    texto = d.quitar_datos_personales(d.extraer_texto(pdf_con_texto().read_bytes()))
    assert d.estado_por_texto(texto) == "pendiente"


def test_pdf_danado_lanza_error():
    with pytest.raises(d.PdfIlegibleError):
        d.extraer_texto(b"%PDF-1.4 basura que no es un pdf")


def test_estado_limite_200():
    assert d.MIN_CARACTERES == 200
    assert d.estado_por_texto("a" * 199) == "sin_texto"
    assert d.estado_por_texto("a" * 200) == "pendiente"
    assert d.estado_por_texto("") == "sin_texto"
    assert d.estado_por_texto(None) == "sin_texto"


def test_nombre_candidato():
    assert d.nombre_candidato("CV_Ana_Ruiz.pdf") == "CV Ana Ruiz"
    assert d.nombre_candidato("Ana  Ruiz.PDF") == "Ana Ruiz"
    assert d.nombre_candidato("") == "Sin nombre"


# ---- R3 sintéticos: casos que deben quitarse ----
@pytest.mark.parametrize(
    "dato",
    [
        "+57 300 123 4567",
        "+573001234567",
        "3001234567",
        "300 123 4567",
        "300-123-4567",
        "+1 (415) 555-0100",
        "+44 20 7946 0958",
    ],
)
def test_telefonos_se_quitan(dato):
    limpio = d.quitar_datos_personales(f"Contacto: {dato} disponible")
    assert not re.search(r"\d{3}", limpio), limpio
    assert "[teléfono]" in limpio


def test_correo_se_quita():
    limpio = d.quitar_datos_personales("Escribir a ana.ruiz+cv@mail.example.co hoy")
    assert "@" not in limpio
    assert "[correo]" in limpio


@pytest.mark.parametrize(
    "linea",
    [
        "Fecha de nacimiento: 12 de mayo de 1990",
        "Nacido el 3 de marzo de 1990",
        "Cédula de ciudadanía 1.234.567.890",
        "C.C. 1234567890",
        "Documento de identidad: 98765432",
        "Estado civil: Soltero",
    ],
)
def test_lineas_personales_se_eliminan(linea):
    limpio = d.quitar_datos_personales(f"Experiencia en Python\n{linea}\nProyecto de datos")
    assert "Experiencia en Python" in limpio and "Proyecto de datos" in limpio
    assert linea not in limpio
    assert not re.search(r"nacimiento|estado civil|1234567|98765432|Soltero", limpio, re.I)


# ---- R3 sintéticos: casos que deben quedar intactos ----
@pytest.mark.parametrize(
    "texto",
    [
        "2019 - 2023",
        "Mayo 2024 - Abril 2026",
        "Salario 580.000 mensuales",
        "Ahorro de $50M al año",
        "Mejoró recall@k en 15%",
        "Reducción de 1.250.000 registros",
    ],
)
def test_no_se_tocan_rangos_ni_cifras(texto):
    assert d.quitar_datos_personales(texto) == texto


# ---- R3 ampliado ----
def _con_contexto(linea):
    return d.quitar_datos_personales(f"Experiencia en Python\n{linea}\nProyecto de datos")


@pytest.mark.parametrize("linea", [
    "Date of birth: 12/05/1990",
    "DOB 1990-05-12",
    "Born on May 12, 1990",
    "Nació el 3 de marzo de 1990",
    "Fecha de nacimiento: 12/05/1990",
])
def test_fecha_de_nacimiento_es_en(linea):
    limpio = _con_contexto(linea)
    assert "Experiencia en Python" in limpio and "Proyecto de datos" in limpio
    assert not re.search(r"1990|birth|born|nac", limpio, re.I), limpio


@pytest.mark.parametrize("linea,digitos", [
    ("CC 1234567890", "1234567890"),
    ("C.C. 1.234.567.890", "234.567"),
    ("T.I. 1234567890", "1234567890"),
    ("DNI 12345678", "12345678"),
    ("Pasaporte AB123456", "123456"),
    ("Identificación: 1234567890", "1234567890"),
])
def test_documentos_de_identidad(linea, digitos):
    limpio = _con_contexto(linea)
    assert "Experiencia en Python" in limpio and "Proyecto de datos" in limpio
    assert digitos not in limpio, limpio


@pytest.mark.parametrize("linea", ["Estado civil: soltero", "Marital status: Married", "Soltero"])
def test_estado_civil_con_y_sin_etiqueta(linea):
    limpio = _con_contexto(linea)
    assert "Experiencia en Python" in limpio and "Proyecto de datos" in limpio
    assert not re.search(r"soltero|married|civil|marital", limpio, re.I), limpio


@pytest.mark.parametrize("linea,prohibido", [
    ("Edad: 34 años", r"34|edad"),
    ("Género: Masculino", r"masculino|g[eé]nero"),
])
def test_edad_y_genero(linea, prohibido):
    limpio = _con_contexto(linea)
    assert "Experiencia en Python" in limpio and "Proyecto de datos" in limpio
    assert not re.search(prohibido, limpio, re.I), limpio


@pytest.mark.parametrize("url,rastro", [
    ("https://www.linkedin.com/in/juan-perez", "juan-perez"),
    ("github.com/usuario-ejemplo", "usuario-ejemplo"),
    ("https://portafolio.vercel.app", "portafolio"),
])
def test_urls_se_reemplazan_por_enlace(url, rastro):
    limpio = d.quitar_datos_personales(f"Perfil: {url} y más proyectos")
    assert "[enlace]" in limpio
    assert rastro not in limpio and "http" not in limpio
    assert "más proyectos" in limpio


@pytest.mark.parametrize("tel", ["(415) 555-0100", "00 57 300 123 4567", "Tel: 604 123 4567"])
def test_telefonos_adicionales(tel):
    limpio = d.quitar_datos_personales(f"Contacto: {tel} disponible")
    assert "[teléfono]" in limpio
    assert not re.search(r"\d{3}", limpio), limpio


@pytest.mark.parametrize("texto", [
    "2019 - 2023",
    "Mayo 2024 - Abril 2026",
    "580.000",
    "$50M",
    "recall@k",
    "Python, SQL, Azure, Docker",
    "Node.js",
    "Socket.io",
])
def test_negativos_intactos(texto):
    assert d.quitar_datos_personales(texto) == texto
    assert d.quitar_datos_personales(f"Stack: {texto} en producción") == f"Stack: {texto} en producción"


def test_linea_mixta_conserva_python():
    limpio = d.quitar_datos_personales("Bogotá | Estado civil: soltero | Python")
    assert "Python" in limpio
    assert not re.search(r"soltero|civil", limpio, re.I)


@pytest.mark.parametrize("i", indices_pdfs())
def test_pdf_reales_sin_correo_telefono_ni_enlace(i):
    limpio = d.quitar_datos_personales(d.extraer_texto(pdf_por_indice(i).read_bytes()))
    if not limpio.strip():
        pytest.skip("PDF escaneado: no hay texto que revisar")
    assert not CORREO.search(limpio)
    assert not TEL_INTL.search(limpio)
    assert not TEL_MOVIL.search(limpio)
    assert not re.search(r"https?://|www\.", limpio, re.I)
    assert not re.search(r"(?:linkedin|github|gitlab|behance)\.(?:com|in|io)", limpio, re.I)
    assert not re.search(r"\(\d{2,4}\)\s?\d{3}[\s.-]?\d{4}", limpio)


# ---- R3 ronda 2: ubicación, dominios, números sueltos, control, idempotencia ----
@pytest.mark.parametrize("etiqueta,valor", [
    ("Dirección", "Calle 5 # 3-4"),
    ("Address", "742 Evergreen Terrace"),
    ("Residencia", "Medellín, Antioquia"),
    ("Barrio", "Laureles"),
    ("Nacionalidad", "Colombiana"),
    ("Nationality", "Colombian"),
    ("Location", "Bogota, Colombia"),
    ("Ubicación", "Cali"),
])
def test_ubicacion_con_etiqueta_se_elimina_y_conserva_otras_columnas(etiqueta, valor):
    limpio = d.quitar_datos_personales(f"{etiqueta}: {valor} | Python, SQL")
    assert "Python, SQL" in limpio
    assert valor not in limpio and etiqueta.lower() not in limpio.lower()


def test_direccion_ejemplo_del_enunciado():
    limpio = d.quitar_datos_personales("Dirección: Calle 5 # 3-4 | Python, SQL")
    assert "Python, SQL" in limpio
    assert "Calle" not in limpio and "Dirección" not in limpio


def test_linea_de_solo_ubicacion_se_borra():
    limpio = d.quitar_datos_personales("Experiencia en Python\nDirección: Calle 5 # 3-4\nSQL")
    assert limpio == "Experiencia en Python\nSQL"


def test_ubicacion_sin_etiqueta_no_se_toca():
    texto = "Trabajé en Bogotá con equipos de Medellín"
    assert d.quitar_datos_personales(texto) == texto


@pytest.mark.parametrize("dominio", ["juanperez.dev", "miportafolio.me", "nombre.co"])
def test_dominios_sin_esquema_son_enlace(dominio):
    limpio = d.quitar_datos_personales(f"Portafolio {dominio} con proyectos")
    assert "[enlace]" in limpio
    assert dominio not in limpio and "proyectos" in limpio


@pytest.mark.parametrize("tec", [
    "Node.js", "Socket.io", "ASP.NET", "Vue.js", "scikit-learn", "next.js", "Chart.js",
    "Dockerfile.dev",
])
def test_tecnologias_no_se_tocan(tec):
    assert d.quitar_datos_personales(tec) == tec
    frase = f"Stack: {tec}, Python"
    assert d.quitar_datos_personales(frase) == frase


@pytest.mark.parametrize("linea", ["1020304050", "No. 52123456", "# 1.020.304.050"])
def test_numero_suelto_de_identificacion_se_quita(linea):
    limpio = d.quitar_datos_personales(f"Experiencia en Python\n{linea}\nProyecto de datos")
    assert limpio == "Experiencia en Python\nProyecto de datos"


@pytest.mark.parametrize("texto", ["580.000", "2019", "2019-2021"])
def test_numeros_cortos_y_anios_se_conservan(texto):
    entorno = f"Experiencia en Python\n{texto}\nProyecto de datos"
    assert d.quitar_datos_personales(entorno) == entorno
    assert d.quitar_datos_personales(texto) == texto


def test_caracteres_de_control_se_eliminan():
    assert d.quitar_datos_personales("a\x00b\x01c\td\ne") == "abc\td\ne"
    assert d.limpiar_control("a\x00b\x01c\td\ne") == "abc\td\ne"
    assert d.limpiar_control(None) == ""


MUESTRAS_IDEMPOTENCIA = [
    "Escribir a ana@mail.co | Tel: 300 123 4567 | juanperez.dev",
    "Dirección: Calle 5 # 3-4 | Python, SQL\nEstado civil: Soltero\n1020304050",
    "https://www.linkedin.com/in/juan-perez y github.com/usuario-ejemplo",
    "Fecha de nacimiento: 12 de mayo de 1990\nCC 1234567890\nNode.js, Socket.io",
    "[correo] [teléfono] [enlace] [etiqueta eliminada]",
]


@pytest.mark.parametrize("texto", MUESTRAS_IDEMPOTENCIA)
def test_idempotencia_sintetica(texto):
    una = d.quitar_datos_personales(texto)
    assert d.quitar_datos_personales(una) == una


@pytest.mark.parametrize("i", indices_pdfs())
def test_idempotencia_pdf_reales(i):
    una = d.quitar_datos_personales(d.extraer_texto(pdf_por_indice(i).read_bytes()))
    if not una.strip():
        pytest.skip("PDF escaneado: no hay texto que revisar")
    assert d.quitar_datos_personales(una) == una


def test_sin_texto_se_decide_sobre_el_texto_limpio():
    # 260 caracteres en bruto, pero casi todo es dato personal que se elimina
    sucio = ("Dirección: " + "Calle 5 # 3-4 Barrio Centro " * 6 + "\n"
             "Fecha de nacimiento: 12 de mayo de 1990\nCC 1234567890\nPython")
    assert len(sucio.strip()) >= d.MIN_CARACTERES
    assert d.estado_por_texto(sucio) == "pendiente"  # sin limpiar parecería evaluable
    limpio = d.quitar_datos_personales(sucio)
    assert d.estado_por_texto(limpio) == "sin_texto"


def test_texto_largo_con_contenido_real_sigue_pendiente_tras_limpiar():
    sucio = "Dirección: Calle 5 # 3-4 | " + "Desarrollo de servicios en Python. " * 10
    limpio = d.quitar_datos_personales(sucio)
    assert "Calle" not in limpio
    assert d.estado_por_texto(limpio) == "pendiente"
