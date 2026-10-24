"""Estado 'evaluando': reclamo atomico, liberacion en error y conteos."""
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import Candidato, Evaluacion, Vacante
from app.services import agente_evaluador, evaluacion, vacantes
from app.services.agente_evaluador import ErrorAgente, Metadatos
from tests.conftest import CLAVE
from tests.helpers import cabeceras
from tests.test_evaluacion import Doble, crear, respuesta


def _segundo_cliente(app):
    otro = app.test_client()
    otro.post("/login", data={"password": CLAVE})
    return otro


def test_segundo_post_durante_la_evaluacion_no_duplica(app, client_autenticado, monkeypatch):
    cid = crear(app)
    otro = _segundo_cliente(app)
    h_otro = cabeceras(otro)
    h_primero = cabeceras(client_autenticado)
    registro = {"llamadas": 0, "resp2": None, "estado_dentro": None}

    def doble(perfil, texto):
        registro["llamadas"] += 1
        if registro["llamadas"] == 1:
            registro["resp2"] = otro.post(f"/evaluar/{cid}", headers=h_otro)
        return respuesta(perfil), Metadatos("modelo-falso", 10, 5, 0.1)

    monkeypatch.setattr(agente_evaluador, "evaluar", doble)
    r1 = client_autenticado.post(f"/evaluar/{cid}", headers=h_primero)

    r2 = registro["resp2"]
    assert r2.status_code == 200
    assert r2.get_json()["sin_cambios"] is True
    assert r2.get_json()["estado"] == "evaluando"
    assert r1.status_code == 200 and r1.get_json()["sin_cambios"] is False
    assert registro["llamadas"] == 1
    with app.app_context():
        assert Evaluacion.query.count() == 1
        assert db.session.get(Candidato, cid).estado == "evaluado"


def test_error_de_ia_devuelve_a_pendiente(app, client_autenticado, monkeypatch):
    cid = crear(app)
    estados = []

    def falla(perfil, texto):
        with app.app_context():
            estados.append(db.session.get(Candidato, cid).estado)
        raise RuntimeError("fallo con datos del CV")

    monkeypatch.setattr(agente_evaluador, "evaluar", falla)
    r = client_autenticado.post(f"/evaluar/{cid}", headers=cabeceras(client_autenticado))
    assert estados == ["evaluando"]
    assert r.status_code == 502
    assert r.get_json()["error"] == "Error con la IA"
    with app.app_context():
        assert db.session.get(Candidato, cid).estado == "pendiente"
        assert Evaluacion.query.count() == 0


def test_error_agente_muestra_su_mensaje_y_libera(app, client_autenticado, monkeypatch):
    cid = crear(app)

    def falla(perfil, texto):
        raise ErrorAgente("Mensaje seguro del agente")

    monkeypatch.setattr(agente_evaluador, "evaluar", falla)
    r = client_autenticado.post(f"/evaluar/{cid}", headers=cabeceras(client_autenticado))
    assert r.status_code == 502 and r.get_json()["error"] == "Mensaje seguro del agente"
    with app.app_context():
        assert db.session.get(Candidato, cid).estado == "pendiente"


def test_error_de_guardado_devuelve_a_pendiente(app, client_autenticado, monkeypatch):
    cid = crear(app)
    monkeypatch.setattr(agente_evaluador, "evaluar",
                        lambda p, t: (respuesta(p), Metadatos("m", 1, 1, 0.1)))

    def dumps_que_falla(*a, **k):
        raise RuntimeError("fallo al serializar")

    # la serializacion ocurre en el bloque que guarda la evaluacion
    monkeypatch.setattr(evaluacion, "json",
                        SimpleNamespace(dumps=dumps_que_falla, loads=json.loads))
    r = client_autenticado.post(f"/evaluar/{cid}", headers=cabeceras(client_autenticado))
    assert r.status_code == 502
    assert r.get_json()["error"] == "No se pudo guardar la evaluación"
    with app.app_context():
        assert db.session.get(Candidato, cid).estado == "pendiente"
        assert Evaluacion.query.count() == 0


def test_evaluando_no_cuenta_como_pendiente_ni_evaluada(app):
    crear(app, "Pend", estado="pendiente")
    crear(app, "Eval", estado="evaluado")
    crear(app, "Enproceso", estado="evaluando", evaluando_desde=evaluacion._ahora())
    with app.app_context():
        v = Vacante.query.filter_by(codigo="ING-IA-2026").first() or Vacante.query.first()
        c = vacantes.contadores(v.id)
        assert c["cargadas"] == 3
        assert c["pendientes"] == 1
        assert c["evaluadas"] == 1
        assert c["evaluando"] == 1
        rk = evaluacion.ranking(v.id)
        nombres_pend = [f["nombre"] for f in rk["filas"] if f["id"] in rk["pendientes_ids"]]
        assert nombres_pend == ["Pend"]
        fila = next(f for f in rk["filas"] if f["nombre"] == "Enproceso")
        assert fila["estado"] == "evaluando"
        assert fila["id"] not in rk["pendientes_ids"]
        assert fila["avanza"] is False and "posicion" not in fila
        assert rk["avanzan"] == 0


def test_evaluando_se_ve_en_el_inicio_y_deshabilita_evaluar(app, client_autenticado):
    cid = crear(app, "Enproceso", estado="evaluando", evaluando_desde=evaluacion._ahora())
    with app.app_context():
        vid = db.session.get(Candidato, cid).vacante_id
    html = client_autenticado.get(f"/?vacante={vid}").get_data(as_text=True)
    assert "Enproceso" in html
    assert "evaluando…" in html
    assert 'id="evaluar" type="button" class="btn" disabled' in html


def test_post_a_candidato_evaluando_sin_cambios(app, client_autenticado, monkeypatch):
    cid = crear(app, "Busy", estado="evaluando", evaluando_desde=evaluacion._ahora())
    llamadas = []
    monkeypatch.setattr(agente_evaluador, "evaluar", lambda p, t: llamadas.append(1))
    r = client_autenticado.post(f"/evaluar/{cid}", headers=cabeceras(client_autenticado))
    assert r.status_code == 200 and r.get_json()["sin_cambios"] is True
    assert llamadas == []
    with app.app_context():
        assert db.session.get(Candidato, cid).estado == "evaluando"


# ---- recuperación de reclamos caducados ----
def _caducado():
    return evaluacion._ahora() - timedelta(seconds=evaluacion.RECLAMO_CADUCA_S + 60)


@pytest.mark.parametrize("desde", ["caducado", "nulo"])
def test_evaluando_caducado_o_sin_fecha_se_reclama_y_evalua(app, client_autenticado,
                                                            monkeypatch, desde):
    cid = crear(app, "Viejo", estado="evaluando",
                evaluando_desde=_caducado() if desde == "caducado" else None)
    d = Doble()
    monkeypatch.setattr(agente_evaluador, "evaluar", d)
    r = client_autenticado.post(f"/evaluar/{cid}", headers=cabeceras(client_autenticado))
    assert r.status_code == 200 and r.get_json()["sin_cambios"] is False
    assert d.llamadas == 1
    with app.app_context():
        c = db.session.get(Candidato, cid)
        assert c.estado == "evaluado" and c.evaluando_desde is None
        assert Evaluacion.query.count() == 1
    # segundo llamado: ya evaluado, no vuelve a la IA
    r2 = client_autenticado.post(f"/evaluar/{cid}", headers=cabeceras(client_autenticado))
    assert r2.get_json()["sin_cambios"] is True
    assert d.llamadas == 1


def test_evaluando_vigente_no_se_reclama_ni_llama_a_la_ia(app, client_autenticado, monkeypatch):
    desde = evaluacion._ahora() - timedelta(seconds=evaluacion.RECLAMO_CADUCA_S - 60)
    cid = crear(app, "Vigente", estado="evaluando", evaluando_desde=desde)
    d = Doble()
    monkeypatch.setattr(agente_evaluador, "evaluar", d)
    r = client_autenticado.post(f"/evaluar/{cid}", headers=cabeceras(client_autenticado))
    assert r.status_code == 200 and r.get_json()["sin_cambios"] is True
    assert r.get_json()["estado"] == "evaluando"
    assert d.llamadas == 0
    with app.app_context():
        c = db.session.get(Candidato, cid)
        assert c.estado == "evaluando" and c.evaluando_desde == desde


def test_reclamar_directo_gana_con_caducado_y_pierde_con_vigente(app):
    viejo = crear(app, "Viejo", estado="evaluando", evaluando_desde=_caducado())
    nuevo = crear(app, "Nuevo", estado="evaluando", evaluando_desde=evaluacion._ahora())
    evaluado = crear(app, "Hecho", estado="evaluado")
    with app.app_context():
        assert evaluacion._reclamar(viejo) is True
        assert db.session.get(Candidato, viejo).evaluando_desde >= evaluacion._limite()
        assert evaluacion._reclamar(nuevo) is False
        assert evaluacion._reclamar(evaluado) is False


def test_contadores_ranking_e_ids_tratan_caducado_como_pendiente(app):
    crear(app, "Pend", estado="pendiente")
    crear(app, "Caducado", estado="evaluando", evaluando_desde=_caducado())
    crear(app, "SinFecha", estado="evaluando", evaluando_desde=None)
    crear(app, "Vigente", estado="evaluando", evaluando_desde=evaluacion._ahora())
    crear(app, "Eval", estado="evaluado")
    with app.app_context():
        vid = Vacante.query.filter_by(codigo="ING-IA-2026").first().id
        c = vacantes.contadores(vid)
        assert c == {"cargadas": 5, "pendientes": 3, "evaluando": 1, "evaluadas": 1}
        rk = evaluacion.ranking(vid)
        estado = {f["nombre"]: f["estado"] for f in rk["filas"]}
        assert estado["Caducado"] == "pendiente" and estado["SinFecha"] == "pendiente"
        assert estado["Vigente"] == "evaluando"
        pend = {f["nombre"] for f in rk["filas"] if f["id"] in rk["pendientes_ids"]}
        assert pend == {"Pend", "Caducado", "SinFecha"}


def test_estado_efectivo_y_reclamo_vigente():
    ahora = evaluacion._ahora()
    assert evaluacion.estado_efectivo("evaluando", ahora) == "evaluando"
    assert evaluacion.estado_efectivo("evaluando", _caducado()) == "pendiente"
    assert evaluacion.estado_efectivo("evaluando", None) == "pendiente"
    assert evaluacion.estado_efectivo("evaluado", None) == "evaluado"
    assert evaluacion.estado_efectivo("sin_texto", None) == "sin_texto"
    assert evaluacion.reclamo_vigente("pendiente", ahora) is False


def test_liberar_limpia_la_marca(app):
    cid = crear(app, "Marca", estado="evaluando", evaluando_desde=evaluacion._ahora())
    with app.app_context():
        evaluacion._liberar(cid)
        c = db.session.get(Candidato, cid)
        assert c.estado == "pendiente" and c.evaluando_desde is None


def test_error_de_ia_limpia_evaluando_desde(app, client_autenticado, monkeypatch):
    cid = crear(app)

    def falla(perfil, texto):
        with app.app_context():
            assert db.session.get(Candidato, cid).evaluando_desde is not None
        raise RuntimeError("x")

    monkeypatch.setattr(agente_evaluador, "evaluar", falla)
    r = client_autenticado.post(f"/evaluar/{cid}", headers=cabeceras(client_autenticado))
    assert r.status_code == 502
    with app.app_context():
        c = db.session.get(Candidato, cid)
        assert c.estado == "pendiente" and c.evaluando_desde is None
