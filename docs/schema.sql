BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 0001

INSERT INTO alembic_version (version_num) VALUES ('0001') RETURNING alembic_version.version_num;

-- Running upgrade 0001 -> 0002

CREATE TABLE organizations (
    id UUID DEFAULT uuidv7() NOT NULL, 
    name VARCHAR(200) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_organizations PRIMARY KEY (id)
);

CREATE UNIQUE INDEX uq_organizations_lower_name ON organizations (lower(name));

CREATE TABLE users (
    id UUID DEFAULT uuidv7() NOT NULL, 
    org_id UUID NOT NULL, 
    email VARCHAR(320) NOT NULL, 
    password_hash VARCHAR(255) NOT NULL, 
    full_name VARCHAR(200) NOT NULL, 
    role VARCHAR(32) NOT NULL, 
    is_active BOOLEAN DEFAULT true NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_users PRIMARY KEY (id), 
    CONSTRAINT fk_users_org_id_organizations FOREIGN KEY(org_id) REFERENCES organizations (id) ON DELETE RESTRICT, 
    CONSTRAINT ck_users_role CHECK (role IN ('REQUESTER', 'REVIEWER', 'APPROVER')), 
    CONSTRAINT uq_users_id_org_id UNIQUE (id, org_id)
);

CREATE UNIQUE INDEX uq_users_lower_email ON users (lower(email));

CREATE TABLE user_sessions (
    id UUID DEFAULT uuidv7() NOT NULL, 
    user_id UUID NOT NULL, 
    token_hash VARCHAR(64) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    revoked_at TIMESTAMP WITH TIME ZONE, 
    CONSTRAINT pk_user_sessions PRIMARY KEY (id), 
    CONSTRAINT fk_user_sessions_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
    CONSTRAINT uq_user_sessions_token_hash UNIQUE (token_hash)
);

CREATE INDEX ix_user_sessions_user_id ON user_sessions (user_id);

CREATE TABLE vendors (
    id UUID DEFAULT uuidv7() NOT NULL, 
    org_id UUID NOT NULL, 
    name VARCHAR(200) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_vendors PRIMARY KEY (id), 
    CONSTRAINT fk_vendors_org_id_organizations FOREIGN KEY(org_id) REFERENCES organizations (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_vendors_id_org_id UNIQUE (id, org_id)
);

CREATE UNIQUE INDEX uq_vendors_org_id_lower_name ON vendors (org_id, lower(name));

CREATE TABLE security_controls (
    id UUID DEFAULT uuidv7() NOT NULL, 
    code VARCHAR(64) NOT NULL, 
    title VARCHAR(200) NOT NULL, 
    description TEXT NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_security_controls PRIMARY KEY (id), 
    CONSTRAINT uq_security_controls_code UNIQUE (code)
);

CREATE TABLE assessments (
    id UUID DEFAULT uuidv7() NOT NULL, 
    org_id UUID NOT NULL, 
    vendor_id UUID NOT NULL, 
    created_by_user_id UUID NOT NULL, 
    title VARCHAR(200) NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_assessments PRIMARY KEY (id), 
    CONSTRAINT fk_assessments_org_id_organizations FOREIGN KEY(org_id) REFERENCES organizations (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_assessments_vendor_same_org FOREIGN KEY(vendor_id, org_id) REFERENCES vendors (id, org_id) ON DELETE RESTRICT, 
    CONSTRAINT fk_assessments_creator_same_org FOREIGN KEY(created_by_user_id, org_id) REFERENCES users (id, org_id) ON DELETE RESTRICT, 
    CONSTRAINT ck_assessments_status CHECK (status IN ('DRAFT', 'PROCESSING', 'PROCESSING_FAILED', 'IN_REVIEW', 'PENDING_APPROVAL', 'APPROVED', 'REJECTED'))
);

CREATE INDEX ix_assessments_org_id_status_created_at ON assessments (org_id, status, created_at);

CREATE INDEX ix_assessments_vendor_id ON assessments (vendor_id);

CREATE TABLE assessment_controls (
    id UUID DEFAULT uuidv7() NOT NULL, 
    assessment_id UUID NOT NULL, 
    control_id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_assessment_controls PRIMARY KEY (id), 
    CONSTRAINT fk_assessment_controls_assessment_id_assessments FOREIGN KEY(assessment_id) REFERENCES assessments (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_assessment_controls_control_id_security_controls FOREIGN KEY(control_id) REFERENCES security_controls (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_assessment_controls_assessment_id_control_id UNIQUE (assessment_id, control_id)
);

CREATE TABLE documents (
    id UUID DEFAULT uuidv7() NOT NULL, 
    assessment_id UUID NOT NULL, 
    uploaded_by_user_id UUID NOT NULL, 
    original_filename VARCHAR(255) NOT NULL, 
    size_bytes INTEGER NOT NULL, 
    page_count INTEGER NOT NULL, 
    sha256 VARCHAR(64) NOT NULL, 
    storage_path VARCHAR(500) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_documents PRIMARY KEY (id), 
    CONSTRAINT fk_documents_assessment_id_assessments FOREIGN KEY(assessment_id) REFERENCES assessments (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_documents_uploaded_by_user_id_users FOREIGN KEY(uploaded_by_user_id) REFERENCES users (id) ON DELETE RESTRICT, 
    CONSTRAINT ck_documents_size_bytes_positive CHECK (size_bytes > 0), 
    CONSTRAINT ck_documents_page_count_positive CHECK (page_count > 0), 
    CONSTRAINT uq_documents_id_assessment_id UNIQUE (id, assessment_id)
);

CREATE INDEX ix_documents_assessment_id_created_at ON documents (assessment_id, created_at);

CREATE TABLE processing_jobs (
    id UUID DEFAULT uuidv7() NOT NULL, 
    assessment_id UUID NOT NULL, 
    document_id UUID NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    attempts INTEGER DEFAULT 0 NOT NULL, 
    max_attempts INTEGER DEFAULT 3 NOT NULL, 
    run_after TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    locked_by VARCHAR(100), 
    locked_until TIMESTAMP WITH TIME ZONE, 
    last_error TEXT, 
    started_at TIMESTAMP WITH TIME ZONE, 
    finished_at TIMESTAMP WITH TIME ZONE, 
    model_name VARCHAR(100), 
    prompt_version VARCHAR(32), 
    normalizer_version VARCHAR(32), 
    input_tokens INTEGER, 
    output_tokens INTEGER, 
    duration_ms INTEGER, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_processing_jobs PRIMARY KEY (id), 
    CONSTRAINT fk_processing_jobs_assessment_id_assessments FOREIGN KEY(assessment_id) REFERENCES assessments (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_processing_jobs_document_same_assessment FOREIGN KEY(document_id, assessment_id) REFERENCES documents (id, assessment_id) ON DELETE RESTRICT, 
    CONSTRAINT ck_processing_jobs_status CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED')), 
    CONSTRAINT ck_processing_jobs_attempts_in_range CHECK (attempts >= 0 AND attempts <= max_attempts), 
    CONSTRAINT ck_processing_jobs_max_attempts_positive CHECK (max_attempts >= 1), 
    CONSTRAINT ck_processing_jobs_running_iff_locked CHECK ((status = 'RUNNING') = (locked_by IS NOT NULL AND locked_until IS NOT NULL)), 
    CONSTRAINT ck_processing_jobs_usage_not_negative CHECK (input_tokens >= 0 AND output_tokens >= 0 AND duration_ms >= 0)
);

CREATE UNIQUE INDEX uq_processing_jobs_one_active_per_assessment ON processing_jobs (assessment_id) WHERE status IN ('QUEUED', 'RUNNING');

CREATE INDEX ix_processing_jobs_active_run_after ON processing_jobs (run_after) WHERE status IN ('QUEUED', 'RUNNING');

CREATE INDEX ix_processing_jobs_assessment_id_created_at ON processing_jobs (assessment_id, created_at);

CREATE TABLE ai_findings (
    id UUID DEFAULT uuidv7() NOT NULL, 
    job_id UUID NOT NULL, 
    assessment_control_id UUID NOT NULL, 
    label VARCHAR(32) NOT NULL, 
    reason TEXT NOT NULL, 
    missing_info TEXT, 
    citations JSONB DEFAULT '[]'::jsonb NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_ai_findings PRIMARY KEY (id), 
    CONSTRAINT fk_ai_findings_job_id_processing_jobs FOREIGN KEY(job_id) REFERENCES processing_jobs (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_ai_findings_assessment_control_id_assessment_controls FOREIGN KEY(assessment_control_id) REFERENCES assessment_controls (id) ON DELETE RESTRICT, 
    CONSTRAINT ck_ai_findings_label CHECK (label IN ('SUPPORTED', 'INSUFFICIENT_EVIDENCE', 'CONTRADICTORY_EVIDENCE', 'MISSING_EVIDENCE')), 
    CONSTRAINT ck_ai_findings_citations_is_array CHECK (jsonb_typeof(citations) = 'array'), 
    CONSTRAINT uq_ai_findings_job_id_assessment_control_id UNIQUE (job_id, assessment_control_id), 
    CONSTRAINT uq_ai_findings_id_assessment_control_id UNIQUE (id, assessment_control_id)
);

CREATE TABLE review_decisions (
    id UUID DEFAULT uuidv7() NOT NULL, 
    assessment_control_id UUID NOT NULL, 
    ai_finding_id UUID NOT NULL, 
    reviewer_user_id UUID NOT NULL, 
    decision VARCHAR(32) NOT NULL, 
    final_label VARCHAR(32) NOT NULL, 
    comment TEXT, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_review_decisions PRIMARY KEY (id), 
    CONSTRAINT fk_review_decisions_assessment_control_id_assessment_controls FOREIGN KEY(assessment_control_id) REFERENCES assessment_controls (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_review_decisions_finding_same_control FOREIGN KEY(ai_finding_id, assessment_control_id) REFERENCES ai_findings (id, assessment_control_id) ON DELETE RESTRICT, 
    CONSTRAINT fk_review_decisions_reviewer_user_id_users FOREIGN KEY(reviewer_user_id) REFERENCES users (id) ON DELETE RESTRICT, 
    CONSTRAINT ck_review_decisions_decision CHECK (decision IN ('ACCEPTED', 'OVERRIDDEN')), 
    CONSTRAINT ck_review_decisions_final_label CHECK (final_label IN ('SUPPORTED', 'INSUFFICIENT_EVIDENCE', 'CONTRADICTORY_EVIDENCE', 'MISSING_EVIDENCE')), 
    CONSTRAINT ck_review_decisions_override_needs_comment CHECK (decision <> 'OVERRIDDEN' OR (comment IS NOT NULL AND btrim(comment) <> '')), 
    CONSTRAINT uq_review_decisions_assessment_control_id UNIQUE (assessment_control_id)
);

CREATE TABLE approvals (
    id UUID DEFAULT uuidv7() NOT NULL, 
    assessment_id UUID NOT NULL, 
    approver_user_id UUID NOT NULL, 
    decision VARCHAR(32) NOT NULL, 
    comment TEXT, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_approvals PRIMARY KEY (id), 
    CONSTRAINT fk_approvals_assessment_id_assessments FOREIGN KEY(assessment_id) REFERENCES assessments (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_approvals_approver_user_id_users FOREIGN KEY(approver_user_id) REFERENCES users (id) ON DELETE RESTRICT, 
    CONSTRAINT ck_approvals_decision CHECK (decision IN ('APPROVED', 'REJECTED')), 
    CONSTRAINT uq_approvals_assessment_id UNIQUE (assessment_id)
);

CREATE TABLE audit_events (
    id UUID DEFAULT uuidv7() NOT NULL, 
    org_id UUID NOT NULL, 
    actor_user_id UUID, 
    action VARCHAR(100) NOT NULL, 
    resource_type VARCHAR(50) NOT NULL, 
    resource_id UUID NOT NULL, 
    old_values JSONB, 
    new_values JSONB, 
    request_id VARCHAR(64), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_audit_events PRIMARY KEY (id), 
    CONSTRAINT fk_audit_events_org_id_organizations FOREIGN KEY(org_id) REFERENCES organizations (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_audit_events_actor_same_org FOREIGN KEY(actor_user_id, org_id) REFERENCES users (id, org_id) ON DELETE RESTRICT
);

CREATE INDEX ix_audit_events_org_id_resource_type_resource_id_created_at ON audit_events (org_id, resource_type, resource_id, created_at);

CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at := now();
            RETURN NEW;
        END;
        $$;

CREATE TRIGGER trg_users_set_updated_at BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_vendors_set_updated_at BEFORE UPDATE ON vendors FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_security_controls_set_updated_at BEFORE UPDATE ON security_controls FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_assessments_set_updated_at BEFORE UPDATE ON assessments FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_processing_jobs_set_updated_at BEFORE UPDATE ON processing_jobs FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_review_decisions_set_updated_at BEFORE UPDATE ON review_decisions FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE FUNCTION reject_audit_event_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only: % is not allowed', TG_OP;
        END;
        $$;

CREATE TRIGGER trg_audit_events_no_update_delete BEFORE UPDATE OR DELETE ON audit_events FOR EACH ROW EXECUTE FUNCTION reject_audit_event_change();

CREATE TRIGGER trg_audit_events_no_truncate BEFORE TRUNCATE ON audit_events FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_event_change();

UPDATE alembic_version SET version_num='0002' WHERE alembic_version.version_num = '0001';

-- Running upgrade 0002 -> 0003

CREATE INDEX ix_assessments_org_id_created_at_id ON assessments (org_id, created_at, id);

DROP INDEX ix_assessments_org_id_status_created_at;

CREATE INDEX ix_assessments_org_id_status_created_at_id ON assessments (org_id, status, created_at, id);

UPDATE alembic_version SET version_num='0003' WHERE alembic_version.version_num = '0002';

COMMIT;

