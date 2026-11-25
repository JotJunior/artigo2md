-- 0001_initial.sql — esquema inicial da persistência do artigo2md (feature job-store).
--
-- Fonte de verdade: docs/specs/job-store/data-model.md. Identificadores em inglês; comentários em português.
-- Arquivo imutável depois de aplicado (o runner confere o checksum): mudança de esquema = arquivo novo.
-- A tabela `schema_migrations` NÃO é criada aqui: o runner a cria antes de aplicar qualquer migração.
-- Todas as chaves estrangeiras usam ON DELETE CASCADE: excluir o projeto remove tudo o que depende dele (FR-011).

-- ---------------------------------------------------------------------------------------------------
-- projects
-- ---------------------------------------------------------------------------------------------------
CREATE TABLE projects (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Nome gravado sem espaços nas pontas (a camada aplica strip()).
    name        text NOT NULL,
    -- Chave de unicidade calculada pela camada (NFC + casefold); independe da localidade do banco (FR-001).
    name_key    text NOT NULL,
    status      text NOT NULL DEFAULT 'active',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT projects_name_check CHECK (btrim(name) <> ''),
    CONSTRAINT projects_name_key_check CHECK (name_key <> ''),
    CONSTRAINT projects_status_check CHECK (status IN ('active', 'archived'))
);

-- Nome único entre projetos ativos: arquivar libera o nome, desarquivar esbarra nele.
CREATE UNIQUE INDEX projects_active_name_key ON projects (name_key) WHERE status = 'active';

-- ---------------------------------------------------------------------------------------------------
-- sources
-- ---------------------------------------------------------------------------------------------------
CREATE TABLE sources (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id  bigint NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    kind        text NOT NULL,
    mode        text NOT NULL,
    -- Caminho como informado; a camada nunca o verifica no disco (FR-002).
    path        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT sources_kind_check CHECK (kind IN ('folder', 'file')),
    CONSTRAINT sources_mode_check CHECK (mode IN ('in_place', 'copied')),
    CONSTRAINT sources_path_check CHECK (path <> ''),
    -- Alvo da chave estrangeira composta de documents (a fonte é do mesmo projeto, FR-013).
    CONSTRAINT sources_id_project_key UNIQUE (id, project_id)
);

CREATE INDEX sources_project_idx ON sources (project_id);

-- ---------------------------------------------------------------------------------------------------
-- documents
-- ---------------------------------------------------------------------------------------------------
CREATE TABLE documents (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id  bigint NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    source_id   bigint NOT NULL,
    path        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT documents_path_check CHECK (path <> ''),
    -- A fonte pertence ao mesmo projeto do documento (FR-013).
    CONSTRAINT documents_source_fkey FOREIGN KEY (source_id, project_id)
        REFERENCES sources (id, project_id) ON DELETE CASCADE,
    -- O mesmo caminho não duplica no projeto; outro projeto pode repetir (FR-003).
    CONSTRAINT documents_project_path_key UNIQUE (project_id, path),
    -- Alvos das chaves estrangeiras compostas de jobs e history_events.
    CONSTRAINT documents_id_project_key UNIQUE (id, project_id)
);

-- Sustenta a verificação da chave composta quando uma fonte é removida em cascata.
CREATE INDEX documents_source_idx ON documents (source_id, project_id);

-- ---------------------------------------------------------------------------------------------------
-- jobs
-- ---------------------------------------------------------------------------------------------------
CREATE TABLE jobs (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Redundante com o documento: valida eventos e permite cancelar em lote por projeto.
    project_id        bigint NOT NULL,
    document_id       bigint NOT NULL,
    requested_engine  text NOT NULL,
    status            text NOT NULL DEFAULT 'pending',
    requested_at      timestamptz NOT NULL DEFAULT now(),
    started_at        timestamptz,
    finished_at       timestamptz,
    error_summary     text,
    CONSTRAINT jobs_document_fkey FOREIGN KEY (document_id, project_id)
        REFERENCES documents (id, project_id) ON DELETE CASCADE,
    CONSTRAINT jobs_requested_engine_check
        CHECK (requested_engine IN ('auto', 'docling', 'pymupdf', 'mineru', 'markitdown')),
    CONSTRAINT jobs_status_check
        CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')),
    -- Coerência entre estado e instantes (FR-010).
    CONSTRAINT jobs_timestamps_check CHECK (
        (status = 'pending' AND started_at IS NULL AND finished_at IS NULL)
        OR (status = 'running' AND started_at IS NOT NULL AND finished_at IS NULL)
        OR (status IN ('succeeded', 'failed', 'cancelled') AND finished_at IS NOT NULL)
    ),
    -- Job falho sempre carrega o resumo do erro.
    CONSTRAINT jobs_failed_error_check CHECK (status <> 'failed' OR error_summary IS NOT NULL),
    -- Alvos das chaves estrangeiras compostas de history_events.
    CONSTRAINT jobs_id_project_key UNIQUE (id, project_id),
    CONSTRAINT jobs_id_document_key UNIQUE (id, document_id)
);

-- Cancelamento em lote e exclusão em cascata por projeto.
CREATE INDEX jobs_project_idx ON jobs (project_id);

-- No máximo um job não final por documento (FR-004, FR-013); quem decide a corrida é o índice.
CREATE UNIQUE INDEX jobs_one_active_per_document ON jobs (document_id) WHERE status IN ('pending', 'running');

-- Status derivado do documento: job mais recente por (requested_at DESC, id DESC) (SC-003).
CREATE INDEX jobs_document_recent_idx ON jobs (document_id, requested_at DESC, id DESC);

-- ---------------------------------------------------------------------------------------------------
-- job_attempts
-- ---------------------------------------------------------------------------------------------------
CREATE TABLE job_attempts (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id           bigint NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    -- Ordem da tentativa no job, atribuída pela camada.
    position         integer NOT NULL,
    -- Texto livre: a camada não restringe os motores (FR-005).
    engine           text NOT NULL,
    seconds          double precision NOT NULL,
    -- Os seis campos de qualidade (espelho de router.Quality): todos nulos ou todos preenchidos.
    score            double precision,
    accepted         boolean,
    tables_found     integer,
    tables_expected  integer,
    chars_per_page   double precision,
    issues           text[],
    error            text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT job_attempts_position_check CHECK (position > 0),
    CONSTRAINT job_attempts_engine_check CHECK (engine <> ''),
    CONSTRAINT job_attempts_seconds_check CHECK (seconds >= 0),
    CONSTRAINT job_attempts_score_check CHECK (score IS NULL OR (score >= 0 AND score <= 100)),
    CONSTRAINT job_attempts_tables_found_check CHECK (tables_found IS NULL OR tables_found >= 0),
    CONSTRAINT job_attempts_tables_expected_check CHECK (tables_expected IS NULL OR tables_expected >= 0),
    CONSTRAINT job_attempts_chars_per_page_check CHECK (chars_per_page IS NULL OR chars_per_page >= 0),
    CONSTRAINT job_attempts_quality_all_or_none CHECK (
        (score IS NULL AND accepted IS NULL AND tables_found IS NULL AND tables_expected IS NULL
            AND chars_per_page IS NULL AND issues IS NULL)
        OR (score IS NOT NULL AND accepted IS NOT NULL AND tables_found IS NOT NULL
            AND tables_expected IS NOT NULL AND chars_per_page IS NOT NULL AND issues IS NOT NULL)
    ),
    -- Uma tentativa nunca fica sem qualidade e sem erro.
    CONSTRAINT job_attempts_quality_or_error CHECK (score IS NOT NULL OR error IS NOT NULL),
    CONSTRAINT job_attempts_job_position_key UNIQUE (job_id, position)
);

-- ---------------------------------------------------------------------------------------------------
-- job_results
-- ---------------------------------------------------------------------------------------------------
CREATE TABLE job_results (
    -- No máximo um resultado por job.
    job_id           bigint PRIMARY KEY REFERENCES jobs (id) ON DELETE CASCADE,
    markdown_path    text NOT NULL,
    engine           text NOT NULL,
    score            double precision NOT NULL,
    -- Pode ser false: resultado não aceito ainda é um job concluído (FR-006).
    accepted         boolean NOT NULL,
    tables_found     integer NOT NULL,
    tables_expected  integer NOT NULL,
    chars_per_page   double precision NOT NULL,
    -- Também são os avisos do resultado (FR-007).
    issues           text[] NOT NULL DEFAULT '{}',
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT job_results_markdown_path_check CHECK (markdown_path <> ''),
    CONSTRAINT job_results_engine_check CHECK (engine <> ''),
    CONSTRAINT job_results_score_check CHECK (score >= 0 AND score <= 100),
    CONSTRAINT job_results_tables_found_check CHECK (tables_found >= 0),
    CONSTRAINT job_results_tables_expected_check CHECK (tables_expected >= 0),
    CONSTRAINT job_results_chars_per_page_check CHECK (chars_per_page >= 0)
);

-- ---------------------------------------------------------------------------------------------------
-- history_events
-- ---------------------------------------------------------------------------------------------------
CREATE TABLE history_events (
    -- Desempate estável da ordem da linha do tempo.
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id   bigint NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    document_id  bigint,
    job_id       bigint,
    event_type   text NOT NULL,
    -- Instante em que a linha foi gravada (clock_timestamp), não o início da transação (now()).
    occurred_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
    message      text NOT NULL,
    CONSTRAINT history_events_event_type_check CHECK (event_type IN (
        'project_created', 'project_archived', 'project_unarchived', 'source_added', 'document_added',
        'job_created', 'job_started', 'attempt_started', 'attempt_recorded', 'job_succeeded', 'job_failed',
        'job_cancelled'
    )),
    -- Documento e job citados pertencem ao projeto do evento (FR-008); chaves compostas com colunas
    -- nulas não são verificadas (MATCH SIMPLE), então evento só do projeto continua válido.
    CONSTRAINT history_events_document_fkey FOREIGN KEY (document_id, project_id)
        REFERENCES documents (id, project_id) ON DELETE CASCADE,
    CONSTRAINT history_events_job_project_fkey FOREIGN KEY (job_id, project_id)
        REFERENCES jobs (id, project_id) ON DELETE CASCADE,
    -- Quando documento e job vêm juntos, o job é daquele documento.
    CONSTRAINT history_events_job_document_fkey FOREIGN KEY (job_id, document_id)
        REFERENCES jobs (id, document_id) ON DELETE CASCADE
);

CREATE INDEX history_events_project_idx ON history_events (project_id, occurred_at, id);
CREATE INDEX history_events_document_idx ON history_events (document_id, occurred_at, id)
    WHERE document_id IS NOT NULL;
-- Sustenta as chaves compostas por job (exclusão em cascata).
CREATE INDEX history_events_job_idx ON history_events (job_id) WHERE job_id IS NOT NULL;

-- ---------------------------------------------------------------------------------------------------
-- Gatilhos (defesa em profundidade: a camada Python já valida, o banco repete a regra)
-- ---------------------------------------------------------------------------------------------------

-- Transições de job (FR-010): pending -> running | cancelled; running -> succeeded | failed | cancelled.
-- Estados finais não mudam mais; id, projeto, documento, motor pedido e instante do pedido são imutáveis.
CREATE FUNCTION jobs_guard_transition() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id <> OLD.id
        OR NEW.project_id <> OLD.project_id
        OR NEW.document_id <> OLD.document_id
        OR NEW.requested_engine <> OLD.requested_engine
        OR NEW.requested_at <> OLD.requested_at THEN
        RAISE EXCEPTION 'Os dados de origem de um job não podem ser alterados.'
            USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status IN ('succeeded', 'failed', 'cancelled') THEN
        RAISE EXCEPTION 'Um job em estado final não pode ser alterado.'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status <> OLD.status AND NOT (
        (OLD.status = 'pending' AND NEW.status IN ('running', 'cancelled'))
        OR (OLD.status = 'running' AND NEW.status IN ('succeeded', 'failed', 'cancelled'))
    ) THEN
        RAISE EXCEPTION 'Transição de estado de job não permitida.'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER jobs_guard_transition
    BEFORE UPDATE ON jobs
    FOR EACH ROW EXECUTE FUNCTION jobs_guard_transition();

-- Só job concluído (succeeded) recebe resultado (FR-006).
CREATE FUNCTION job_results_require_succeeded_job() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    current_status text;
BEGIN
    SELECT status INTO current_status FROM jobs WHERE id = NEW.job_id;
    IF current_status IS DISTINCT FROM 'succeeded' THEN
        RAISE EXCEPTION 'Só um job concluído pode receber resultado.'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER job_results_require_succeeded_job
    BEFORE INSERT ON job_results
    FOR EACH ROW EXECUTE FUNCTION job_results_require_succeeded_job();

-- Job concluído sem resultado nunca é confirmado (FR-006): a verificação é adiada para o commit, para que
-- estado, resultado e evento possam ser gravados na mesma transação, em qualquer ordem entre eles.
CREATE FUNCTION jobs_succeeded_requires_result() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- O job pode ter sido removido (exclusão do projeto) na mesma transação: nada a verificar.
    PERFORM 1 FROM jobs WHERE id = NEW.id AND status = 'succeeded';
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    PERFORM 1 FROM job_results WHERE job_id = NEW.id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Um job concluído precisa ter resultado.'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER jobs_succeeded_requires_result
    AFTER INSERT OR UPDATE ON jobs
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    WHEN (NEW.status = 'succeeded')
    EXECUTE FUNCTION jobs_succeeded_requires_result();

-- O histórico só recebe acréscimos: alteração é recusada (a remoção só ocorre em cascata, pela exclusão do projeto).
CREATE FUNCTION history_events_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Eventos do histórico não podem ser alterados.'
        USING ERRCODE = 'check_violation';
END;
$$;

CREATE TRIGGER history_events_append_only
    BEFORE UPDATE ON history_events
    FOR EACH ROW EXECUTE FUNCTION history_events_append_only();
