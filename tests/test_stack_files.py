"""Teste estático dos arquivos da stack (`stack/compose.yaml`, `.env.example`, `.dockerignore`).

Roda sem Docker. Garante as promessas de segurança do projeto (FR-001, FR-005, FR-006, FR-007):
portas só em 127.0.0.1, nada privilegiado, sem credencial real versionada.

O YAML é lido com PyYAML, que já vem instalado como dependência transitiva do Docling.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "stack" / "compose.yaml"
MANAGEMENT_COMPOSE = ROOT / "stack" / "compose.management.yaml"
ENV_EXAMPLE = ROOT / "stack" / ".env.example"
DOCKERFILE = ROOT / "stack" / "api" / "Dockerfile"
EXAMPLE_PASSWORD = "troque-esta-senha"
NO_NEW_PRIVILEGES = "no-new-privileges:true"


# ── verificadores (também testados contra exemplos ruins, para o guarda não ser decorativo) ──
def port_violations(compose: dict) -> list[str]:
    problems = []
    for name, svc in (compose.get("services") or {}).items():
        for entry in svc.get("ports") or []:
            if isinstance(entry, str):
                if not entry.startswith("127.0.0.1:"):
                    problems.append(f"{name}: porta fora do loopback: {entry.split(':')[0]}…")
            elif isinstance(entry, dict):
                if entry.get("host_ip") != "127.0.0.1":
                    problems.append(f"{name}: forma longa sem host_ip 127.0.0.1")
            else:
                problems.append(f"{name}: forma de porta desconhecida")
    return problems


def _mounts_docker_sock(volumes) -> bool:
    for v in volumes or []:
        text = v if isinstance(v, str) else " ".join(str(x) for x in v.values())
        if "docker.sock" in text:
            return True
    return False


def privilege_violations(compose: dict) -> list[str]:
    problems = []
    for name, svc in (compose.get("services") or {}).items():
        if svc.get("privileged"):
            problems.append(f"{name}: privileged")
        if "network_mode" in svc:
            problems.append(f"{name}: network_mode")
        if _mounts_docker_sock(svc.get("volumes")):
            problems.append(f"{name}: monta docker.sock")
    return problems


def no_new_privileges_missing(compose: dict, services: tuple[str, ...]) -> list[str]:
    svcs = compose.get("services") or {}
    return [
        name
        for name in services
        if NO_NEW_PRIVILEGES not in (svcs.get(name, {}).get("security_opt") or [])
    ]


PORT_ENTRY = re.compile(r"127\.0\.0\.1:\$\{ARTIGO2MD_[A-Z_]+_PORT:\?[^}]+\}:\d+")


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def management() -> dict:
    return yaml.safe_load(MANAGEMENT_COMPOSE.read_text(encoding="utf-8"))


def with_management(base: dict, extra: dict) -> dict:
    """Configuração efetiva de `up.sh --management-panel`: as listas `ports` são concatenadas (Compose)."""
    merged = {"services": {n: dict(s) for n, s in base["services"].items()}}
    for name, svc in (extra.get("services") or {}).items():
        merged["services"][name]["ports"] = list(merged["services"][name].get("ports", [])) + list(svc.get("ports", []))
    return merged


# ── compose.yaml real ────────────────────────────────────────────────────────────
def test_compose_has_the_three_services_and_a_fixed_project_name(compose):
    assert compose["name"] == "artigo2md"
    assert set(compose["services"]) == {"postgres", "rabbitmq", "api"}
    assert set(compose["volumes"]) == {"pgdata", "rabbitmqdata"}


def test_every_published_port_is_bound_to_loopback_only(compose, management):
    published = [p for svc in compose["services"].values() for p in svc.get("ports", [])]
    assert len(published) == 3  # postgres, amqp, api: o painel de gestão não é publicado por padrão
    assert port_violations(compose) == []
    # a regra de loopback vale também para a configuração com o opt-in do painel
    opt_in = with_management(compose, management)
    assert sum(len(s.get("ports", [])) for s in opt_in["services"].values()) == 4
    assert port_violations(opt_in) == []


def test_port_numbers_come_from_required_variables(compose):
    for svc in compose["services"].values():
        for entry in svc.get("ports", []):
            # variável obrigatória (`:?`): o Compose recusa valor ausente
            assert PORT_ENTRY.fullmatch(entry), entry


def test_erlang_distribution_and_epmd_ports_are_not_published(compose, management):
    targets = {entry.rsplit(":", 1)[1] for entry in compose["services"]["rabbitmq"]["ports"]}
    assert targets == {"5672"}  # sem o painel de gestão (15672) por padrão
    opt_in = with_management(compose, management)
    opt_in_targets = {entry.rsplit(":", 1)[1] for entry in opt_in["services"]["rabbitmq"]["ports"]}
    assert opt_in_targets == {"5672", "15672"}


def test_management_compose_publishes_only_the_panel_on_loopback_with_a_required_variable(management):
    assert set(management) == {"services"}  # só o opt-in: nada de nome, volumes ou outros serviços
    assert set(management["services"]) == {"rabbitmq"}
    assert set(management["services"]["rabbitmq"]) == {"ports"}
    entries = management["services"]["rabbitmq"]["ports"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry.startswith("127.0.0.1:")  # IP literal fixo
    assert entry.endswith(":15672")
    assert "${ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT:?" in entry  # variável obrigatória
    assert PORT_ENTRY.fullmatch(entry), entry
    assert port_violations(management) == []
    assert "host_ip" not in MANAGEMENT_COMPOSE.read_text(encoding="utf-8")


def test_no_service_is_privileged_or_exposes_the_docker_socket(compose):
    assert privilege_violations(compose) == []


def test_no_new_privileges_is_set_on_all_three_services(compose):
    assert no_new_privileges_missing(compose, ("postgres", "rabbitmq", "api")) == []


def test_only_the_api_service_gets_the_extra_hardening(compose):
    api = compose["services"]["api"]
    assert api["user"] == "10001:10001"
    assert api["read_only"] is True
    assert api["tmpfs"] == ["/tmp:size=768m"]
    assert api["cap_drop"] == ["ALL"]
    # decisão do operador (block-004): Postgres e RabbitMQ só recebem no-new-privileges
    for name in ("postgres", "rabbitmq"):
        svc = compose["services"][name]
        for key in ("user", "read_only", "cap_drop", "cap_add", "mem_limit", "deploy"):
            assert key not in svc, f"{name}: {key} não faz parte da decisão do operador"


def test_services_have_healthchecks_and_api_waits_for_the_others(compose):
    for name, svc in compose["services"].items():
        assert svc["healthcheck"]["test"], name
    assert compose["services"]["api"]["depends_on"] == {
        "postgres": {"condition": "service_healthy"},
        "rabbitmq": {"condition": "service_healthy"},
    }


def test_no_restart_policy_and_required_credentials(compose):
    for name, svc in compose["services"].items():
        assert "restart" not in svc, name
    pg_env = compose["services"]["postgres"]["environment"]
    mq_env = compose["services"]["rabbitmq"]["environment"]
    for value in [*pg_env.values(), *mq_env.values()]:
        assert value.startswith("${") and ":?" in value, "credencial obrigatória sem ':?'"
    assert set(pg_env) == {"POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"}


def test_data_volumes_are_named_and_mounted_where_the_images_expect(compose):
    assert compose["services"]["postgres"]["volumes"] == ["pgdata:/var/lib/postgresql"]
    assert compose["services"]["rabbitmq"]["volumes"] == ["rabbitmqdata:/var/lib/rabbitmq"]
    assert compose["services"]["rabbitmq"]["hostname"] == "rabbitmq"
    # único volume da API: a pasta de dados do app (bind mount obrigatório), montada em /data
    assert compose["services"]["api"]["volumes"] == [
        "${ARTIGO2MD_DATA_DIR:?defina ARTIGO2MD_DATA_DIR em stack/.env}:/data"
    ]


def test_api_image_is_built_locally_from_the_repo_root(compose):
    api = compose["services"]["api"]
    assert api["image"] == "artigo2md-api:local"
    assert api["build"] == {"context": "..", "dockerfile": "stack/api/Dockerfile"}


# ── os verificadores recusam exemplos ruins ──────────────────────────────────────
@pytest.mark.parametrize(
    "entry",
    ["5432:5432", "0.0.0.0:5432:5432", "${HOST}:5432:5432", "5432", {"target": 5432, "published": 5432},
     {"target": 5432, "host_ip": "0.0.0.0"}],
)
def test_port_checker_rejects_non_loopback_entries(entry):
    assert port_violations({"services": {"x": {"ports": [entry]}}}) != []


@pytest.mark.parametrize(
    "entry",
    ["127.0.0.1:5432:5432", {"target": 5432, "published": 5432, "host_ip": "127.0.0.1"}],
)
def test_port_checker_accepts_loopback_entries(entry):
    assert port_violations({"services": {"x": {"ports": [entry]}}}) == []


@pytest.mark.parametrize(
    "svc",
    [
        {"privileged": True},
        {"network_mode": "host"},
        {"volumes": ["/var/run/docker.sock:/var/run/docker.sock"]},
        {"volumes": [{"type": "bind", "source": "/var/run/docker.sock", "target": "/x"}]},
    ],
)
def test_privilege_checker_rejects_dangerous_settings(svc):
    assert privilege_violations({"services": {"x": svc}}) != []


def test_no_new_privileges_checker_flags_missing_option():
    compose = {"services": {"a": {"security_opt": [NO_NEW_PRIVILEGES]}, "b": {}, "c": {"security_opt": ["seccomp:x"]}}}
    assert no_new_privileges_missing(compose, ("a", "b", "c")) == ["b", "c"]


# ── .env.example, .gitignore, .dockerignore, Dockerfile ──────────────────────────
def _env_pairs() -> dict[str, str]:
    pairs = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            pairs[key] = value
    return pairs


def test_env_example_has_the_contract_variables_and_no_real_credential():
    pairs = _env_pairs()
    assert set(pairs) == {
        "ARTIGO2MD_POSTGRES_PORT", "ARTIGO2MD_RABBITMQ_PORT", "ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT",
        "ARTIGO2MD_API_PORT", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB",
        "RABBITMQ_DEFAULT_USER", "RABBITMQ_DEFAULT_PASS", "ARTIGO2MD_DATA_DIR",
    }
    assert pairs["ARTIGO2MD_DATA_DIR"] == ""  # obrigatória: o up.sh recusa enquanto não for preenchida
    assert pairs["POSTGRES_PASSWORD"] == EXAMPLE_PASSWORD
    assert pairs["RABBITMQ_DEFAULT_PASS"] == EXAMPLE_PASSWORD
    assert "POSTGRES_HOST_AUTH_METHOD" not in pairs
    assert all("$" not in value for value in pairs.values())


def test_env_example_defaults_match_the_contract():
    pairs = _env_pairs()
    assert [pairs[k] for k in ("ARTIGO2MD_POSTGRES_PORT", "ARTIGO2MD_RABBITMQ_PORT",
                               "ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT", "ARTIGO2MD_API_PORT")] == [
        "5432", "5672", "15672", "8778"]


def test_no_variable_controls_the_publication_address():
    suspicious = ("HOST", "BIND", "ADDR", "LISTEN", "INTERFACE")
    for key in _env_pairs():
        assert not any(word in key for word in suspicious if key != "POSTGRES_HOST_AUTH_METHOD"), key
    text = COMPOSE.read_text(encoding="utf-8")
    assert "host_ip" not in text and "${HOST" not in text and "${BIND" not in text
    management_text = MANAGEMENT_COMPOSE.read_text(encoding="utf-8")
    assert "${HOST" not in management_text and "${BIND" not in management_text


def test_env_example_comments_are_in_portuguese_and_warn_about_the_rules():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for fragment in ("12 ou mais caracteres", "`$`", "docker inspect", "PRIMEIRA inicialização",
                     "--apagar-dados", "127.0.0.1"):
        assert fragment in text, fragment


def test_env_example_documents_the_optional_dev_origin_variable_commented_out():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "ARTIGO2MD_API_ALLOWED_ORIGINS" not in text  # variável removida (13.9): a lista de origens é fixa
    assert "# ARTIGO2MD_API_ALLOW_DEV_ORIGIN=" in text
    assert "artigo2md worker --data-dir" in text
    assert "ARTIGO2MD_API_ALLOW_DEV_ORIGIN" not in _env_pairs()  # desligada por padrão


def test_api_environment_matches_the_configuration_contract(compose):
    env = compose["services"]["api"]["environment"]
    assert env["ARTIGO2MD_DATA_DIR_MOUNT"] == "/data"
    assert env["ARTIGO2MD_DATA_DIR_HOST"] == "${ARTIGO2MD_DATA_DIR}"
    assert "ARTIGO2MD_API_ALLOWED_ORIGINS" not in env  # variável removida (13.9): a lista de origens é fixa
    assert env["ARTIGO2MD_API_ALLOW_DEV_ORIGIN"] == "${ARTIGO2MD_API_ALLOW_DEV_ORIGIN:-}"
    # portas e hosts de dentro da rede do Compose (não as publicadas no computador)
    assert (env["ARTIGO2MD_POSTGRES_HOST"], env["ARTIGO2MD_POSTGRES_PORT"]) == ("postgres", "5432")
    assert (env["ARTIGO2MD_RABBITMQ_HOST"], env["ARTIGO2MD_RABBITMQ_PORT"]) == ("rabbitmq", "5672")
    for name in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "RABBITMQ_DEFAULT_USER",
                 "RABBITMQ_DEFAULT_PASS"):
        assert env[name].startswith("${") and ":?" in env[name], name


def test_stack_env_is_git_ignored():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "stack/.env" in [line.strip() for line in lines]


def test_dockerignore_is_an_allowlist():
    lines = [l.strip() for l in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.strip().startswith("#")]
    assert lines[0] == "*"
    allowed = {l for l in lines if l.startswith("!")}
    assert allowed == {"!pyproject.toml", "!uv.lock", "!src/artigo2md/"}
    assert not any("stack" in l or ".env" in l for l in allowed)


def test_dockerfile_installs_only_the_api_group_and_runs_unprivileged():
    full = DOCKERFILE.read_text(encoding="utf-8")
    text = "\n".join(l for l in full.splitlines() if not l.lstrip().startswith("#"))
    assert "FROM python:3.13-slim-trixie" in text
    assert "ghcr.io/astral-sh/uv:0.13.9" in text
    assert "uv sync --frozen --only-group api" in text
    assert text.index("uv sync") < text.index("COPY src/artigo2md/")  # camada de dependências antes do código
    assert "USER 10001:10001" in text
    assert "docling" not in text.lower() and "pymupdf" not in text.lower()


# ── registro de licenças (FR-011, SC-006) ────────────────────────────────────────
LICENSES = ROOT / "stack" / "THIRD_PARTY_LICENSES.md"
PRINCIPLE_II = {
    "permissiva",
    "copyleft fraco sem modificação",
    "documentado (SO base, dec-013)",
    "rejeitado",
}


def license_rows(text: str) -> list[list[str]]:
    """Linhas de dados das tabelas do registro (6 colunas), sem cabeçalho nem separador."""
    rows = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells[0] in ("Componente",) or set(cells[0]) <= {"-", " "}:
            continue
        rows.append(cells)
    return rows


def license_violations(rows: list[list[str]]) -> list[str]:
    problems = []
    for cells in rows:
        name = cells[0] if cells else "?"
        if len(cells) != 6 or not all(cells):
            problems.append(f"{name}: linha incompleta")
            continue
        _, _, lic, principle, source, checked = cells
        if re.search(r"presum|desconhecid|tbd|todo|\?\?", lic, re.IGNORECASE):
            problems.append(f"{name}: licença presumida ou desconhecida")
        if principle not in PRINCIPLE_II:
            problems.append(f"{name}: enquadramento fora do enum")
        if principle == "rejeitado":
            problems.append(f"{name}: item rejeitado exige decisão do operador")
        if not (source.startswith("https://") or source.startswith("`")):
            problems.append(f"{name}: fonte sem endereço oficial")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", checked):
            problems.append(f"{name}: data de consulta inválida")
    return problems


def test_license_registry_has_no_blank_or_presumed_license():
    rows = license_rows(LICENSES.read_text(encoding="utf-8"))
    assert len(rows) >= 20
    assert license_violations(rows) == []


def test_license_checker_rejects_bad_rows():
    good = ["x", "img", "MIT", "permissiva", "https://example.org/x", "2026-11-24"]
    assert license_violations([good]) == []
    assert license_violations([good[:2] + [""] + good[3:]])  # licença vazia
    assert license_violations([good[:2] + ["presumida MIT"] + good[3:]])
    assert license_violations([good[:3] + ["GPL"] + good[4:]])  # enquadramento fora do enum
    assert license_violations([good[:3] + ["rejeitado"] + good[4:]])
    assert license_violations([good[:4] + ["sem fonte"] + good[5:]])
    assert license_violations([good[:5] + ["ontem"]])


def test_license_registry_covers_every_compose_image_and_api_dependency():
    text = LICENSES.read_text(encoding="utf-8")
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    for svc in compose["services"].values():
        assert svc["image"] in text, svc["image"]
    assert "ghcr.io/astral-sh/uv:0.13.9" in text

    tomllib = pytest.importorskip("tomllib")  # Python >= 3.11
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    by_name: dict[str, list[dict]] = {}
    for pkg in lock["package"]:
        by_name.setdefault(pkg["name"], []).append(pkg)
    api_group = by_name["artigo2md"][0]["dev-dependencies"]["api"]
    todo = [d["name"] for d in api_group]
    closure: set[str] = set()
    while todo:
        name = todo.pop()
        if name in closure:
            continue
        closure.add(name)
        for pkg in by_name[name]:
            for dep in pkg.get("dependencies", []):
                if "< '3.11'" in dep.get("marker", ""):  # a imagem usa Python 3.13
                    continue
                todo.append(dep["name"])
    registered = {}
    for cells in license_rows(text):
        parts = cells[0].split()
        if len(parts) == 2 and cells[1] == "`artigo2md-api:local`":
            registered[parts[0]] = parts[1]
    missing = sorted(closure - set(registered))
    assert not missing, f"dependências do grupo api sem licença registrada: {missing}"
    for name, version in registered.items():
        if name in by_name:
            assert version in {p["version"] for p in by_name[name]}, f"{name} {version} fora do uv.lock"
