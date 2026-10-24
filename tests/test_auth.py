from tests.conftest import CLAVE


def _autenticado(client):
    with client.session_transaction() as s:
        return bool(s.get("autenticado"))


def test_sin_sesion_redirige_a_login(client):
    r = client.get("/")
    assert r.status_code == 302
    assert r.headers["Location"].startswith("/login")


def test_login_get_es_publico(client):
    assert client.get("/login").status_code == 200


def test_clave_errada_no_entra(client):
    r = client.post("/login", data={"password": "incorrecta"})
    assert r.status_code != 302
    assert not _autenticado(client)
    assert client.get("/").status_code == 302


def test_clave_correcta_entra(client):
    r = client.post("/login", data={"password": CLAVE})
    assert r.status_code == 302
    assert _autenticado(client)
    r = client.get("/")
    assert r.status_code == 200
    assert "Evaluador de hojas de vida" in r.get_data(as_text=True)


def test_logout_cierra_sesion(client_autenticado):
    assert client_autenticado.get("/").status_code == 200
    r = client_autenticado.get("/logout")
    assert r.status_code == 302
    assert not _autenticado(client_autenticado)
    assert client_autenticado.get("/").status_code == 302


def test_next_externo_se_ignora(client):
    for destino in ("http://evil.com", "//evil.com", "https://evil.com/x"):
        r = client.post(f"/login?next={destino}", data={"password": CLAVE})
        assert r.status_code == 302
        assert "evil.com" not in r.headers["Location"]
        client.get("/logout")


def test_next_local_se_respeta(client):
    r = client.post("/login?next=/?vacante=1", data={"password": CLAVE})
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/?vacante=1")
