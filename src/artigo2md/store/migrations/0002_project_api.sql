-- 0002_project_api.sql — extensões do esquema para a project-api (feature project-api).
--
-- Fonte de verdade: docs/specs/project-api/contracts/store-extensions.md. Identificadores em inglês;
-- comentários em português. Arquivo imutável depois de aplicado (o runner confere o checksum).
-- A 0001_initial.sql não é alterada: tudo o que muda está aqui.
--
-- Premissa para jobs `pending` já existentes (store-extensions.md, "Migração 0002"): nenhum backfill é feito.
-- Todo job `pending` preexistente fica com `published_at` nulo e, portanto, é tratado como NÃO publicado:
-- `list_unpublished_jobs` o devolve na primeira partida da API e a API o publica uma vez (at-least-once).
-- Se a mensagem original já tinha sido publicada por outro meio, o worker pode receber uma duplicata; o
-- resultado repetido é descartado pela transição do job (FR-016).

-- ---------------------------------------------------------------------------------------------------
-- jobs.published_at: instante em que a API confirmou a publicação do job na fila (FR-011).
-- ---------------------------------------------------------------------------------------------------
ALTER TABLE jobs ADD COLUMN published_at timestamptz;

-- Leitura de `list_unpublished_jobs` na partida: só jobs pendentes ainda não publicados.
CREATE INDEX jobs_unpublished_idx ON jobs (id) WHERE status = 'pending' AND published_at IS NULL;

-- ---------------------------------------------------------------------------------------------------
-- history_events: o tipo `project_renamed` passa a ser aceito (FR-002).
-- ---------------------------------------------------------------------------------------------------
ALTER TABLE history_events DROP CONSTRAINT history_events_event_type_check;
ALTER TABLE history_events ADD CONSTRAINT history_events_event_type_check CHECK (event_type IN (
    'project_created', 'project_archived', 'project_unarchived', 'source_added', 'document_added',
    'job_created', 'job_started', 'attempt_started', 'attempt_recorded', 'job_succeeded', 'job_failed',
    'job_cancelled', 'project_renamed'
));

-- ---------------------------------------------------------------------------------------------------
-- jobs_guard_transition(): `pending` passa a aceitar também `failed` (FR-011).
-- Um job cuja publicação não foi confirmada nunca chegou a um worker; registrá-lo como iniciado seria falso.
-- Demais regras (imutabilidade dos dados de origem, estado final) iguais às da 0001_initial.sql.
-- ---------------------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION jobs_guard_transition() RETURNS trigger LANGUAGE plpgsql AS $$
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
        (OLD.status = 'pending' AND NEW.status IN ('running', 'cancelled', 'failed'))
        OR (OLD.status = 'running' AND NEW.status IN ('succeeded', 'failed', 'cancelled'))
    ) THEN
        RAISE EXCEPTION 'Transição de estado de job não permitida.'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
