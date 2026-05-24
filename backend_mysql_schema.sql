-- Prototype MySQL schema for moving backend JSON state out of local files.
-- Original uploads and generated exports stay on filesystem/object storage.

CREATE TABLE IF NOT EXISTS documents (
  document_id VARCHAR(32) PRIMARY KEY,
  owner_user_id VARCHAR(255) NOT NULL,
  owner_email VARCHAR(512) NOT NULL,
  owner_auth_provider VARCHAR(64) NOT NULL,
  original_filename VARCHAR(512) NOT NULL,
  source_format VARCHAR(32) NOT NULL,
  target_words_per_section INT NOT NULL,
  metadata_json JSON NOT NULL,
  parsed_document_json JSON NOT NULL,
  sections_json JSON NOT NULL,
  translations_json JSON NOT NULL,
  section_translations_json JSON NOT NULL,
  glossary_json JSON NOT NULL,
  latest_export_json JSON NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_documents_owner_created (owner_user_id, created_at)
);

CREATE TABLE IF NOT EXISTS jobs (
  job_id VARCHAR(32) PRIMARY KEY,
  document_id VARCHAR(32) NOT NULL,
  owner_user_id VARCHAR(255) NOT NULL,
  owner_email VARCHAR(512) NOT NULL,
  owner_auth_provider VARCHAR(64) NOT NULL,
  job_type VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  progress INT NOT NULL DEFAULT 0,
  message VARCHAR(512) NOT NULL DEFAULT '',
  payload_json JSON NOT NULL,
  result_json JSON NOT NULL,
  error_text TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_jobs_document_status (document_id, status),
  INDEX idx_jobs_owner_status (owner_user_id, status),
  CONSTRAINT fk_jobs_document_id
    FOREIGN KEY (document_id)
    REFERENCES documents(document_id)
    ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS usage_records (
  usage_id VARCHAR(32) PRIMARY KEY,
  job_id VARCHAR(32) NOT NULL,
  document_id VARCHAR(32) NOT NULL,
  owner_user_id VARCHAR(255) NOT NULL,
  owner_email VARCHAR(512) NOT NULL,
  owner_auth_provider VARCHAR(64) NOT NULL,
  job_type VARCHAR(64) NOT NULL,
  mode VARCHAR(64) NULL,
  section_id VARCHAR(64) NULL,
  source_language VARCHAR(128) NULL,
  target_language VARCHAR(128) NULL,
  document_type VARCHAR(128) NULL,
  content_form VARCHAR(128) NULL,
  word_count INT NOT NULL DEFAULT 0,
  chunk_count INT NOT NULL DEFAULT 0,
  chunk_size_words INT NOT NULL DEFAULT 0,
  translated_block_count INT NOT NULL DEFAULT 0,
  estimated_input_tokens INT NOT NULL DEFAULT 0,
  estimated_output_tokens INT NOT NULL DEFAULT 0,
  estimated_prompt_overhead_tokens INT NOT NULL DEFAULT 0,
  estimated_total_tokens INT NOT NULL DEFAULT 0,
  estimated_cost_usd DECIMAL(12, 6) NOT NULL DEFAULT 0,
  estimated_cost_per_word_usd DECIMAL(12, 8) NOT NULL DEFAULT 0,
  token_price_per_1m_usd DECIMAL(12, 6) NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_usage_job_id (job_id),
  INDEX idx_usage_owner_created (owner_user_id, created_at),
  INDEX idx_usage_document_created (document_id, created_at),
  CONSTRAINT fk_usage_job_id
    FOREIGN KEY (job_id)
    REFERENCES jobs(job_id)
    ON DELETE CASCADE,
  CONSTRAINT fk_usage_document_id
    FOREIGN KEY (document_id)
    REFERENCES documents(document_id)
    ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS payment_orders (
  order_id VARCHAR(32) PRIMARY KEY,
  owner_user_id VARCHAR(255) NOT NULL,
  owner_email VARCHAR(512) NOT NULL,
  owner_auth_provider VARCHAR(64) NOT NULL,
  package_id VARCHAR(64) NOT NULL,
  credits INT NOT NULL,
  amount_cents INT NOT NULL,
  currency VARCHAR(8) NOT NULL DEFAULT 'USD',
  provider VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  checkout_url VARCHAR(1024) NULL,
  external_payment_id VARCHAR(255) NULL,
  metadata_json JSON NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_payment_orders_owner_created (owner_user_id, created_at),
  INDEX idx_payment_orders_status (status)
);

CREATE TABLE IF NOT EXISTS credit_ledger (
  entry_id VARCHAR(32) PRIMARY KEY,
  owner_user_id VARCHAR(255) NOT NULL,
  owner_email VARCHAR(512) NOT NULL,
  owner_auth_provider VARCHAR(64) NOT NULL,
  entry_type VARCHAR(64) NOT NULL,
  credit_delta INT NOT NULL,
  credits INT NOT NULL,
  status VARCHAR(32) NOT NULL,
  job_id VARCHAR(32) NULL,
  document_id VARCHAR(32) NULL,
  order_id VARCHAR(32) NULL,
  model_tier VARCHAR(64) NULL,
  metadata_json JSON NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_credit_ledger_owner_created (owner_user_id, created_at),
  INDEX idx_credit_ledger_job (job_id),
  INDEX idx_credit_ledger_order (order_id),
  INDEX idx_credit_ledger_owner_type (owner_user_id, entry_type),
  UNIQUE KEY uq_credit_ledger_job_type (job_id, entry_type),
  UNIQUE KEY uq_credit_ledger_order_type (order_id, entry_type),
  CONSTRAINT fk_credit_ledger_job_id
    FOREIGN KEY (job_id)
    REFERENCES jobs(job_id)
    ON DELETE SET NULL,
  CONSTRAINT fk_credit_ledger_document_id
    FOREIGN KEY (document_id)
    REFERENCES documents(document_id)
    ON DELETE SET NULL,
  CONSTRAINT fk_credit_ledger_order_id
    FOREIGN KEY (order_id)
    REFERENCES payment_orders(order_id)
    ON DELETE SET NULL
);
