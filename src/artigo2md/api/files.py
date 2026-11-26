"""Limpeza segura de arquivos na exclusão de projeto (FR-004; plan S-01; `contracts/http-api.md`, "DELETE").

A API NUNCA remove um caminho informado pelo cliente. Os únicos alvos possíveis são derivados aqui:

- `<mount>/<job_id>/` para cada job do projeto (`job_id` é inteiro vindo do banco), onde o worker grava o Markdown;
- `<mount>/sources/<project_id>/`, a pasta onde o app copia as fontes `copied` do projeto.

Arquivos de fontes `in_place` ficam fora do diretório de dados e nunca são alvo. Cada alvo só é removido se
`os.lstat` confirmar um diretório real (link simbólico, arquivo comum ou outro tipo é ignorado e nunca seguido) cujo
`realpath` continue sob o `realpath` do ponto de montagem; a remoção usa `shutil.rmtree`, que recusa link simbólico na
raiz e não segue links internos.

Fluxo (a rota orquestra): `plan_removal` → `check_removable` (antes do COMMIT; só leitura de permissões via
`os.access`, jamais `chmod`) → COMMIT → `remove_planned` (falha só vai ao log e não desfaz a exclusão). Funções
síncronas: a rota as chama em `asyncio.to_thread`.
"""
from __future__ import annotations

import logging
import os
import shutil
import stat
from collections.abc import Iterable
from dataclasses import dataclass

from .errors import ApiError
from .paths import SOURCES_DIRNAME, is_under

logger = logging.getLogger("artigo2md.api")

_ACCESS_DIR = os.R_OK | os.W_OK | os.X_OK


@dataclass(frozen=True)
class RemovalPlan:
    """Diretórios reais existentes a remover e os pais cujas permissões a remoção exige."""

    root: str  # realpath do ponto de montagem
    targets: tuple[str, ...]


def _is_real_dir(path: str) -> bool:
    """`True` se `path` existe e é um diretório de verdade (não link simbólico)."""
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def derived_targets(root: str, project_id: int, job_ids: Iterable[int]) -> list[str]:
    """Caminhos candidatos derivados de ids inteiros; nada do cliente entra aqui."""
    targets: list[str] = []
    for job_id in job_ids:
        if isinstance(job_id, int) and not isinstance(job_id, bool) and job_id > 0:
            targets.append(os.path.join(root, str(job_id)))
    if isinstance(project_id, int) and not isinstance(project_id, bool) and project_id > 0:
        targets.append(os.path.join(root, SOURCES_DIRNAME, str(project_id)))
    return targets


def plan_removal(data_dir_mount: str | None, project_id: int, job_ids: Iterable[int]) -> RemovalPlan:
    """Resolve o ponto de montagem e filtra os candidatos que existem como diretório real sob ele.

    `ApiError(data_dir_unavailable)` se o ponto de montagem não está configurado ou não é um diretório."""
    if not data_dir_mount:
        raise ApiError("data_dir_unavailable")
    try:
        root = os.path.realpath(data_dir_mount, strict=True)
    except OSError:
        raise ApiError("data_dir_unavailable") from None
    if not os.path.isdir(root):
        raise ApiError("data_dir_unavailable")

    targets: list[str] = []
    for candidate in derived_targets(root, project_id, job_ids):
        if not _is_real_dir(candidate):
            continue  # inexistente, link simbólico ou arquivo: nada a remover, e nunca seguimos o link
        if os.path.realpath(candidate) != candidate or not is_under(candidate, root):
            logger.warning("Um diretório derivado do projeto %d resolve fora do diretório de dados e foi ignorado.", project_id)
            continue
        targets.append(candidate)
    return RemovalPlan(root=root, targets=tuple(targets))


def _unremovable_dirs(target: str) -> bool:
    """`True` se algum diretório da árvore de `target` (incluindo ele) não pode ser listado e esvaziado.
    Não segue link simbólico: link dentro da árvore é só uma entrada a desvincular."""
    stack = [target]
    while stack:
        current = stack.pop()
        if not os.access(current, _ACCESS_DIR):
            return True
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
        except PermissionError:
            return True
        except OSError:
            continue  # sumiu entre a listagem e a leitura: nada a bloquear
    return False


def check_removable(plan: RemovalPlan) -> None:
    """Antes do COMMIT: recusa com `data_dir_permission_denied` se o usuário atual não puder remover os alvos.

    Só leitura de estado (`os.access`); nenhuma permissão é alterada. Remover um diretório exige escrita e busca no
    pai e acesso total aos diretórios da árvore."""
    for target in plan.targets:
        parent = os.path.dirname(target)
        if not os.access(parent, os.W_OK | os.X_OK) or _unremovable_dirs(target):
            raise ApiError("data_dir_permission_denied")


def remove_planned(plan: RemovalPlan, project_id: int) -> int:
    """Depois do COMMIT: remove os alvos planejados. Devolve quantos foram removidos; falha vai ao log (sem o caminho
    nem texto do SO) e não desfaz a exclusão — o banco já não aponta para esses arquivos."""
    removed = 0
    for target in plan.targets:
        # Reconfere no momento da remoção: o alvo ainda é diretório real sob o ponto de montagem?
        if not _is_real_dir(target) or not is_under(os.path.realpath(target), plan.root):
            logger.warning("Um diretório do projeto %d mudou de natureza antes da remoção e foi ignorado.", project_id)
            continue
        try:
            shutil.rmtree(target)
            removed += 1
        except OSError as exc:
            logger.warning(
                "Não foi possível remover um diretório de arquivos do projeto %d (%s); a exclusão do projeto foi mantida.",
                project_id,
                type(exc).__name__,
            )
    return removed
