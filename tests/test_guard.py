"""El guard autouse de conftest impide llamar a la API y falla de forma ruidosa."""
import pytest

from app.services import agente_evaluador
from tests.conftest import MENSAJE_GUARD, LlamadaApiProhibida
from tests.helpers import cabeceras
from tests.test_evaluacion import crear


def test_clave_de_prueba_invalida():
    import os
    assert os.environ["OPENROUTER_API_KEY"] == "clave-de-prueba-invalida"
    assert os.environ["OPENROUTER_MODEL"] == "modelo-guard"
    for nombre in ("OPENROUTER_BASE_URL", "OPENROUTER_REASONING_EFFORT",
                   "OPENROUTER_MAX_TOKENS", "GEMINI_API_KEY", "GEMINI_MODEL",
                   "OPENAI_API_KEY", "OPENAI_MODEL"):
        assert nombre not in os.environ, nombre


def test_la_excepcion_del_guard_no_es_exception():
    assert not issubclass(LlamadaApiProhibida, Exception)
    assert issubclass(LlamadaApiProhibida, BaseException)


def test_guard_dispara_en__ejecutar():
    with pytest.raises(LlamadaApiProhibida, match=MENSAJE_GUARD):
        agente_evaluador._ejecutar(None, "m", None, 1, 1)


@pytest.mark.parametrize("nombre", ["run", "run_sync", "run_streamed"])
def test_guard_dispara_en_runner(nombre):
    with pytest.raises(LlamadaApiProhibida, match=MENSAJE_GUARD):
        getattr(agente_evaluador.Runner, nombre)(None, "m")


def test_guard_dispara_si_evaluar_no_fue_sustituido():
    with pytest.raises(LlamadaApiProhibida, match=MENSAJE_GUARD):
        agente_evaluador.evaluar({"obligatorios": [], "deseables": []}, "texto")


def test_except_exception_no_traga_el_guard():
    with pytest.raises(LlamadaApiProhibida):
        try:
            agente_evaluador._ejecutar(None, "m", None, 1, 1)
        except Exception:  # noqa: BLE001 - justo lo que el guard debe esquivar
            pytest.fail("un except Exception se trago el guard")


def test_evaluar_sin_doble_falla_el_test_en_vez_de_502(app, client_autenticado):
    """POST /evaluar sin sustituir la IA: la excepcion del guard sale por el cliente de
    pruebas (no se convierte en un 502 'Error con la IA')."""
    cid = crear(app)
    with pytest.raises(LlamadaApiProhibida, match=MENSAJE_GUARD):
        client_autenticado.post(f"/evaluar/{cid}", headers=cabeceras(client_autenticado))
