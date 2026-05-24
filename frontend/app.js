const state = {
  view: "documents",
  documents: [],
  selectedDocumentId: localStorage.getItem("lexiflow.selectedDocumentId") || "",
  selectedFile: null,
  activeJob: null,
  preview: {
    sectionId: "",
    offset: 0,
    limit: 40,
  },
  settings: {
    source_language: localStorage.getItem("lexiflow.sourceLanguage") || "English",
    target_language: localStorage.getItem("lexiflow.targetLanguage") || "German",
    document_type: localStorage.getItem("lexiflow.documentType") || "general",
    content_form: localStorage.getItem("lexiflow.contentForm") || "book",
    context_sections: 3,
    model_tier: localStorage.getItem("lexiflow.modelTier") || "balanced",
  },
};

const root = document.querySelector("#view-root");
const title = document.querySelector("#view-title");
const eyebrow = document.querySelector("#view-eyebrow");
const notice = document.querySelector("#notice");
const refreshButton = document.querySelector("#refresh-button");
const TOP_LANGUAGES = [
  "English",
  "Mandarin Chinese",
  "Hindi",
  "Spanish",
  "Arabic (Modern Standard)",
  "French",
  "Bengali",
  "Portuguese",
  "Indonesian",
  "Urdu",
  "Russian",
  "German",
  "Japanese",
  "Nigerian Pidgin",
  "Egyptian Arabic",
  "Marathi",
  "Vietnamese",
  "Telugu",
  "Swahili",
  "Hausa",
  "Turkish",
  "Western Punjabi",
  "Tagalog",
  "Tamil",
  "Yue Chinese (Cantonese)",
  "Wu Chinese (Shanghainese)",
  "Persian (Farsi)",
  "Korean",
  "Amharic",
  "Thai",
  "Javanese",
  "Italian",
  "Gujarati",
  "Kannada",
  "Levantine Arabic",
  "Sudanese Arabic",
  "Yoruba",
  "Bhojpuri",
  "Malayalam",
  "Polish",
  "Ukrainian",
  "Burmese",
  "Zulu",
  "Najdi Arabic",
  "Moroccan Arabic",
  "Cebuano",
  "Igbo",
  "Odia (Oriya)",
  "Nepali",
  "Xiang Chinese",
];
const SUPPORTED_FILE_EXTENSIONS = [".docx", ".epub", ".txt"];
const MAX_UPLOAD_BYTES = 15 * 1024 * 1024;
const FALLBACK_MODEL_TIERS = [
  {
    tier_id: "quick_draft",
    name: "Quick Draft",
    description: "Lowest credit use for fast first-pass translation.",
  },
  {
    tier_id: "balanced",
    name: "Balanced",
    description: "Recommended quality and credit balance for most documents.",
    recommended: true,
  },
  {
    tier_id: "precision",
    name: "Precision",
    description: "Highest quality pass for sensitive content.",
  },
];

document.addEventListener("DOMContentLoaded", init);

function init() {
  bindShell();
  navigate("documents");
}

function bindShell() {
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => navigate(button.dataset.view));
  });
  document.querySelectorAll("[data-action='new-upload']").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedFile = null;
      navigate("intake");
    });
  });
  refreshButton.addEventListener("click", () => reloadCurrentView());
}

async function navigate(view, options = {}) {
  state.view = view;
  setActiveNav(view);
  clearNotice();
  if (view === "documents") {
    await renderDashboard();
  } else if (view === "intake") {
    renderIntake();
  } else if (view === "workspace") {
    await renderWorkspace(options.documentId || state.selectedDocumentId);
  } else if (view === "usage") {
    await renderUsage();
  } else {
    renderSettings();
  }
}

function setActiveNav(view) {
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
}

async function reloadCurrentView() {
  await navigate(state.view);
}

async function apiFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const isFormData = options.body instanceof FormData;
  if (!isFormData && options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers,
    });
  } catch {
    throw new Error("Could not reach the translation server. Check that the backend is running, then retry.");
  }
  if (!response.ok) {
    let detail = "";
    const bodyText = await response.text();
    try {
      const payload = bodyText ? JSON.parse(bodyText) : null;
      detail = formatApiError(payload?.detail || payload, response.status);
    } catch {
      detail = formatApiError(bodyText, response.status);
    }
    throw new Error(detail);
  }
  const contentType = response.headers.get("Content-Type") || "";
  if (contentType.includes("application/json")) {
    return response.json();
  }
  return response.text();
}

function formatApiError(detail, status = 0) {
  if (typeof detail === "string") {
    return detail.trim() || fallbackErrorMessage(status);
  }
  if (Array.isArray(detail)) {
    return fallbackErrorMessage(status);
  }
  if (detail && typeof detail === "object") {
    const parts = [];
    const message = String(detail.message || "").trim() || fallbackErrorMessage(status);
    parts.push(message);
    const quotaMessage = formatQuotaError(detail.quota);
    if (quotaMessage) {
      parts.push(quotaMessage);
    }
    const fieldsMessage = formatValidationFields(detail.fields);
    if (fieldsMessage) {
      parts.push(fieldsMessage);
    }
    if (detail.action) {
      parts.push(String(detail.action));
    }
    return uniqueParts(parts).join(" ");
  }
  return fallbackErrorMessage(status);
}

function fallbackErrorMessage(status) {
  const messages = new Map([
    [400, "The request could not be processed. Please check the details and try again."],
    [401, "Please sign in with Google before continuing."],
    [402, "You do not have enough free words remaining for this translation."],
    [403, "You do not have access to this action."],
    [404, "We could not find that item. Refresh and try again."],
    [409, "This action cannot be completed right now. Refresh and try again."],
    [413, "This file is too large."],
    [422, "Some request fields are invalid. Please check them and try again."],
    [429, "You have reached a usage limit. Please try again later."],
    [500, "Something went wrong on our side. Please try again."],
  ]);
  return messages.get(status) || "Request failed. Please try again.";
}

function formatQuotaError(quota) {
  if (!quota || typeof quota !== "object") {
    return "";
  }
  if (quota.type === "daily_uploads") {
    return `Used ${number(quota.used)} of ${number(quota.limit)} uploads today.`;
  }
  if (quota.type === "active_translation_jobs") {
    return `You have ${number(quota.used)} of ${number(quota.limit)} translation jobs running.`;
  }
  if (quota.type === "lifetime_free_translation_words") {
    const remaining = number(quota.remaining_words || 0);
    const requested = number(quota.requested_words || 0);
    return `Remaining free words: ${remaining}. Requested: ${requested}.`;
  }
  return "";
}

function formatValidationFields(fields) {
  if (!Array.isArray(fields) || !fields.length) {
    return "";
  }
  const labels = fields
    .slice(0, 3)
    .map((field) => String(field.field || "field").replaceAll("_", " "))
    .filter(Boolean);
  return labels.length ? `Check: ${labels.join(", ")}.` : "";
}

function uniqueParts(parts) {
  const seen = new Set();
  return parts.filter((part) => {
    const normalized = String(part || "").trim();
    if (!normalized || seen.has(normalized)) {
      return false;
    }
    seen.add(normalized);
    return true;
  });
}

async function renderDashboard() {
  setHeader("Workspace", "Overview");
  root.innerHTML = loadingMarkup();
  try {
    const [documentsResponse, usage] = await Promise.all([
      apiFetch("/documents"),
      apiFetch("/usage/me").catch(() => null),
    ]);
    state.documents = documentsResponse.documents || [];
    root.innerHTML = dashboardMarkup(state.documents, usage);
    bindDashboard();
  } catch (error) {
    showError(error.message);
    root.innerHTML = emptyStateMarkup(
      "Unable to load dashboard",
      "Check that the backend is running and authenticated."
    );
  }
}

function dashboardMarkup(documents, usage) {
  const usageQuota = usage?.quota || {};
  const uploadQuota = usage?.daily_upload_quota || {};
  const activeJobs = usage?.active_translation_jobs || {};
  return `
    <div class="grid-2">
      <label class="upload-zone" id="drop-zone">
        <input id="file-input" type="file" accept=".docx,.epub,.txt" />
        <div>
          <div class="upload-icon">
            <span class="material-symbols-outlined">cloud_upload</span>
          </div>
          <h2>Drag and drop files to initiate</h2>
          <p class="muted">Upload DOCX, EPUB, or TXT files up to ${formatBytes(MAX_UPLOAD_BYTES)}.</p>
          <div style="display:flex; gap:8px; justify-content:center; margin-top:16px;">
            <span class="chip">DOCX</span>
            <span class="chip">EPUB</span>
            <span class="chip">TXT</span>
          </div>
        </div>
      </label>
      <div class="grid-3" style="grid-template-columns:1fr; gap:14px;">
        ${metricMarkup("Free words", `${number(usageQuota.used_words || 0)} / ${number(usageQuota.limit_words || 2000)}`, `${number(usageQuota.remaining_words || 0)} remaining`)}
        ${metricMarkup("Uploads today", `${uploadQuota.used || 0} / ${uploadQuota.limit || 5}`, `${uploadQuota.remaining ?? "-"} remaining`)}
        ${metricMarkup("Active jobs", `${activeJobs.used || 0} / ${activeJobs.limit || 2}`, "Running or queued")}
      </div>
    </div>

    <div class="section-title" style="margin-top:28px;">
      <div>
        <div class="eyebrow">Documents</div>
        <h2>Recent translations</h2>
      </div>
      <button class="secondary-button" id="new-upload-secondary">
        <span class="material-symbols-outlined">add</span>
        New Translation
      </button>
    </div>
    ${
      documents.length
        ? `<div class="document-grid">${documents.map(documentCardMarkup).join("")}</div>`
        : emptyStateMarkup("No documents yet", "Upload a document to start translating section by section.")
    }
  `;
}

function metricMarkup(label, value, subtext) {
  return `
    <div class="metric-card">
      <div class="metric-label">${escapeHtml(label)}</div>
      <div class="metric-value">${escapeHtml(value)}</div>
      <p class="muted">${escapeHtml(subtext)}</p>
    </div>
  `;
}

function documentCardMarkup(document) {
  const translated = Number(document.translation_cursor || 0);
  const total = Number(document.section_count || 0);
  const percent = total ? Math.round((translated / total) * 100) : 0;
  const filename = document.original_filename || document.document_id;
  return `
    <button class="document-card" data-document-id="${escapeAttribute(document.document_id)}">
      <div>
        <div style="display:flex; justify-content:space-between; gap:12px;">
          <h3>${escapeHtml(filename)}</h3>
          <span class="chip">${escapeHtml((document.source_format || "").toUpperCase())}</span>
        </div>
        <p class="muted">${translated} of ${total} sections translated</p>
      </div>
      <div>
        <div class="progress-track">
          <div class="progress-fill" style="width:${percent}%"></div>
        </div>
        <div style="display:flex; justify-content:space-between; margin-top:10px;">
          <span class="job-meta">${percent}% complete</span>
          <span class="job-meta">Open for credit quote</span>
        </div>
      </div>
    </button>
  `;
}

function bindDashboard() {
  const fileInput = document.querySelector("#file-input");
  const dropZone = document.querySelector("#drop-zone");
  fileInput?.addEventListener("change", (event) => {
    const file = event.target.files?.[0];
    if (file && selectUploadFile(file)) {
      navigate("intake");
    }
  });
  dropZone?.addEventListener("dragover", (event) => {
    event.preventDefault();
  });
  dropZone?.addEventListener("drop", (event) => {
    event.preventDefault();
    const file = event.dataTransfer.files?.[0];
    if (file && selectUploadFile(file)) {
      navigate("intake");
    }
  });
  document.querySelector("#new-upload-secondary")?.addEventListener("click", () => {
    state.selectedFile = null;
    navigate("intake");
  });
  document.querySelectorAll("[data-document-id]").forEach((card) => {
    card.addEventListener("click", () => {
      const documentId = card.dataset.documentId;
      state.selectedDocumentId = documentId;
      localStorage.setItem("lexiflow.selectedDocumentId", documentId);
      navigate("workspace", { documentId });
    });
  });
}

function renderIntake() {
  setHeader("Document Intake", "Analyze Document");
  const file = state.selectedFile;
  root.innerHTML = `
    <div class="intake-shell">
      <div class="panel intake-visual">
        <div>
          <div class="scan-document">
            <span class="material-symbols-outlined">${file ? "description" : "upload_file"}</span>
          </div>
          <div style="text-align:center; margin-top:18px;">
            <h2>${escapeHtml(file?.name || "Choose a document")}</h2>
            <p class="muted">Supported formats: DOCX, EPUB, TXT. Max upload size: ${formatBytes(MAX_UPLOAD_BYTES)}.</p>
            <label class="secondary-button" style="margin-top:16px;">
              <input id="intake-file-input" type="file" accept=".docx,.epub,.txt" hidden />
              <span class="material-symbols-outlined">folder_open</span>
              Select file
            </label>
          </div>
        </div>
      </div>

      <form class="panel" id="intake-form">
        <div class="section-title">
          <div>
            <div class="eyebrow">Configuration</div>
            <h2>Translation setup</h2>
          </div>
        </div>
        <div class="field">
          <label for="source-language">Source language</label>
          ${languageSelectMarkup("source-language", "source_language", state.settings.source_language)}
        </div>
        <div class="field" style="margin-top:16px;">
          <label for="target-language">Target language</label>
          ${languageSelectMarkup("target-language", "target_language", state.settings.target_language)}
        </div>
        <div class="field" style="margin-top:16px;">
          <label for="document-type">Document type</label>
          <select id="document-type" name="document_type">
            ${optionMarkup("general", "General")}
            ${optionMarkup("financial_report", "Financial Report")}
            ${optionMarkup("legal_contract", "Legal Contract")}
            ${optionMarkup("technical_manual", "Technical Manual")}
            ${optionMarkup("academic", "Academic")}
            ${optionMarkup("literary", "Literary")}
          </select>
        </div>
        <div class="field" style="margin-top:16px;">
          <label for="content-form">Content form</label>
          <select id="content-form" name="content_form">
            ${optionMarkup("book", "Book")}
            ${optionMarkup("article", "Article")}
            ${optionMarkup("report", "Report")}
            ${optionMarkup("manual_or_documentation", "Manual/Documentation")}
            ${optionMarkup("academic_paper", "Academic Paper")}
            ${optionMarkup("legal_or_policy", "Legal/Policy")}
          </select>
        </div>
        <div class="field" style="margin-top:16px;">
          <label for="model-tier">Model tier</label>
          ${modelTierSelectMarkup("model-tier", "model_tier", state.settings.model_tier)}
        </div>
        <button class="primary-button" type="submit" style="width:100%; margin-top:24px;" ${file ? "" : "disabled"}>
          <span class="material-symbols-outlined">document_scanner</span>
          Analyze Document
        </button>
        <p class="muted" style="text-align:center; margin-top:12px;">Parsing may take up to 30 seconds for large files.</p>
      </form>
    </div>
  `;
  document.querySelector("#document-type").value = state.settings.document_type;
  document.querySelector("#content-form").value = state.settings.content_form;
  bindIntake();
}

function optionMarkup(value, label) {
  return `<option value="${escapeAttribute(value)}">${escapeHtml(label)}</option>`;
}

function languageSelectMarkup(id, name, selectedLanguage) {
  const normalizedSelected = normalizeLanguageChoice(selectedLanguage);
  return `
    <select id="${escapeAttribute(id)}" name="${escapeAttribute(name)}">
      ${TOP_LANGUAGES.map((language) => {
        const selected = language === normalizedSelected ? " selected" : "";
        return `<option value="${escapeAttribute(language)}"${selected}>${escapeHtml(language)}</option>`;
      }).join("")}
    </select>
  `;
}

function modelTierSelectMarkup(id, name, selectedTier) {
  const normalized = normalizeModelTierId(selectedTier);
  return `
    <select id="${escapeAttribute(id)}" name="${escapeAttribute(name)}">
      ${FALLBACK_MODEL_TIERS.map((tier) => {
        const selected = tier.tier_id === normalized ? " selected" : "";
        const label = `${tier.name}${tier.recommended ? " (Recommended)" : ""}`;
        return `<option value="${escapeAttribute(tier.tier_id)}"${selected}>${escapeHtml(label)}</option>`;
      }).join("")}
    </select>
  `;
}

function modelTierOptionsMarkup(modelTiers, selectedTier) {
  const tiers = Array.isArray(modelTiers) && modelTiers.length ? modelTiers : FALLBACK_MODEL_TIERS;
  const normalized = normalizeModelTierId(selectedTier);
  return `
    <select id="workspace-model-tier" name="model_tier">
      ${tiers.map((tier) => {
        const tierId = String(tier.tier_id || "balanced");
        const selected = tierId === normalized ? " selected" : "";
        const label = `${tier.name || tierId}${tier.recommended ? " (Recommended)" : ""}`;
        return `<option value="${escapeAttribute(tierId)}"${selected}>${escapeHtml(label)}</option>`;
      }).join("")}
    </select>
  `;
}

function normalizeModelTierId(value) {
  const normalized = String(value || "balanced").trim().toLocaleLowerCase().replaceAll("-", "_");
  const aliases = new Map([
    ["cheap", "quick_draft"],
    ["draft", "quick_draft"],
    ["fast", "quick_draft"],
    ["moderate", "balanced"],
    ["standard", "balanced"],
    ["best", "precision"],
    ["pro", "precision"],
  ]);
  return aliases.get(normalized) || normalized || "balanced";
}

function normalizeLanguageChoice(language) {
  const normalized = String(language || "").trim().toLocaleLowerCase();
  const aliases = new Map([
    ["arabic", "Arabic (Modern Standard)"],
    ["modern standard arabic", "Arabic (Modern Standard)"],
    ["chinese", "Mandarin Chinese"],
    ["standard chinese", "Mandarin Chinese"],
    ["farsi", "Persian (Farsi)"],
    ["persian", "Persian (Farsi)"],
    ["iranian persian", "Persian (Farsi)"],
    ["cantonese", "Yue Chinese (Cantonese)"],
    ["tagalog/filipino", "Tagalog"],
    ["filipino", "Tagalog"],
    ["punjabi", "Western Punjabi"],
    ["oriya", "Odia (Oriya)"],
  ]);
  if (aliases.has(normalized)) {
    return aliases.get(normalized);
  }
  return TOP_LANGUAGES.find((item) => item.toLocaleLowerCase() === normalized) || "English";
}

function bindIntake() {
  document.querySelector("#intake-file-input")?.addEventListener("change", (event) => {
    const file = event.target.files?.[0];
    if (file && selectUploadFile(file)) {
      renderIntake();
    }
  });
  document.querySelector("#intake-form")?.addEventListener("submit", uploadDocument);
}

function selectUploadFile(file) {
  const error = validateUploadFile(file);
  if (error) {
    state.selectedFile = null;
    showError(error);
    return false;
  }
  state.selectedFile = file;
  clearNotice();
  return true;
}

function validateUploadFile(file) {
  if (!file) {
    return "Choose a DOCX, EPUB, or TXT file first.";
  }
  const filename = String(file.name || "").toLocaleLowerCase();
  const supported = SUPPORTED_FILE_EXTENSIONS.some((extension) => filename.endsWith(extension));
  if (!supported) {
    return "Unsupported file type. Upload a DOCX, EPUB, or TXT file.";
  }
  if (file.size === 0) {
    return "This file is empty. Upload a document that contains selectable text.";
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return `This file is too large. Upload a file smaller than ${formatBytes(MAX_UPLOAD_BYTES)}.`;
  }
  return "";
}

async function uploadDocument(event) {
  event.preventDefault();
  const fileError = validateUploadFile(state.selectedFile);
  if (fileError) {
    showError(fileError);
    return;
  }
  const form = event.currentTarget;
  const formData = new FormData(form);
  state.settings = {
    source_language: String(formData.get("source_language") || "English").trim() || "English",
    target_language: String(formData.get("target_language") || "German").trim() || "German",
    document_type: String(formData.get("document_type") || "general"),
    content_form: String(formData.get("content_form") || "book"),
    context_sections: 3,
    model_tier: normalizeModelTierId(String(formData.get("model_tier") || "balanced")),
  };
  persistSettings();

  const payload = new FormData();
  payload.append("file", state.selectedFile);
  payload.append("target_words_per_section", "600");
  payload.append("source_language", state.settings.source_language);
  root.innerHTML = loadingMarkup("Analyzing document structure...");
  try {
    const documentSummary = await apiFetch("/documents/upload", {
      method: "POST",
      body: payload,
    });
    state.selectedDocumentId = documentSummary.document_id;
    localStorage.setItem("lexiflow.selectedDocumentId", state.selectedDocumentId);
    state.preview.offset = 0;
    showNotice("Document is ready for preview.");
    await navigate("workspace", { documentId: state.selectedDocumentId });
  } catch (error) {
    showError(error.message);
    renderIntake();
  }
}

async function renderWorkspace(documentId) {
  if (!documentId) {
    setHeader("Workspace", "No document selected");
    root.innerHTML = emptyStateMarkup("Pick a document", "Upload or open a recent document to continue.");
    return;
  }
  if (state.selectedDocumentId !== documentId) {
    state.preview.offset = 0;
    state.preview.sectionId = "";
  }
  state.selectedDocumentId = documentId;
  localStorage.setItem("lexiflow.selectedDocumentId", documentId);
  setHeader("Translation Workspace", "Document Preview");
  root.innerHTML = loadingMarkup();
  try {
    const [documentSummary, sectionsResponse, wallet, modelTiersResponse] = await Promise.all([
      apiFetch(`/documents/${encodeURIComponent(documentId)}`),
      apiFetch(`/documents/${encodeURIComponent(documentId)}/sections`),
      apiFetch("/billing/wallet").catch(() => null),
      apiFetch("/billing/model-tiers").catch(() => null),
    ]);
    state.preview.sectionId = currentPreviewSectionId(documentSummary, sectionsResponse);
    const previewOffset = Math.max(0, Number(state.preview.offset || 0));
    const previewLimit = Math.max(1, Number(state.preview.limit || 40));
    const previewPath = previewUrl(documentId, state.preview.sectionId, previewOffset, previewLimit);
    const quotePath = `/documents/${encodeURIComponent(documentId)}/quote?model_tier=${encodeURIComponent(state.settings.model_tier)}`;
    const [previewResponse, quoteResponse] = await Promise.all([
      apiFetch(previewPath),
      apiFetch(quotePath).catch(() => null),
    ]);
    if (documentSummary.source_language) {
      state.settings.source_language = String(documentSummary.source_language);
      persistSettings();
    }
    root.innerHTML = workspaceMarkup(
      documentSummary,
      sectionsResponse,
      previewResponse,
      quoteResponse,
      wallet,
      modelTiersResponse?.model_tiers || FALLBACK_MODEL_TIERS,
    );
    bindWorkspace(documentSummary, previewResponse);
  } catch (error) {
    showError(error.message);
    root.innerHTML = emptyStateMarkup("Workspace unavailable", "The document could not be loaded.");
  }
}

function previewUrl(documentId, sectionId, offset, limit) {
  const params = new URLSearchParams({
    offset: String(offset),
    limit: String(limit),
  });
  if (sectionId) {
    params.set("section_id", sectionId);
  }
  return `/documents/${encodeURIComponent(documentId)}/preview?${params.toString()}`;
}

function currentPreviewSectionId(documentSummary, sectionsResponse) {
  const sections = sectionsResponse.sections || [];
  const selected = String(state.preview.sectionId || "");
  if (selected && sections.some((section) => section.section_id === selected)) {
    return selected;
  }
  return String(
    documentSummary.next_section_id
      || documentSummary.last_translated_section_id
      || sections[0]?.section_id
      || "",
  );
}

function workspaceMarkup(
  documentSummary,
  sectionsResponse,
  previewResponse,
  quoteResponse,
  wallet,
  modelTiers,
) {
  const blocks = previewResponse.blocks || [];
  const sourceBlocks = previewBlocksMarkup(blocks, "source");
  const targetBlocks = state.activeJob
    ? skeletonMarkup()
    : previewBlocksMarkup(blocks, "target");
  const translated = Number(documentSummary.translation_cursor || 0);
  const total = Number(documentSummary.section_count || 0);
  const remainingEstimate = sectionsResponse.remaining_estimate;
  const nextQuote = quoteResponse?.next_section;
  const remainingQuote = quoteResponse?.remaining_document;
  const allTranslated = !documentSummary.next_section_id;
  const canRetranslate = Boolean(documentSummary.last_translated_section_id);
  const canTranslateRest = Number(remainingEstimate?.remaining_block_count || 0) > 0;
  const sourceLanguage = documentSummary.source_language || state.settings.source_language || "English";
  const balanceCredits = wallet?.balance_credits ?? quoteResponse?.wallet?.balance_credits;
  return `
    <div class="workspace">
      ${state.activeJob ? jobBannerMarkup(state.activeJob) : ""}
      <div class="workspace-header">
        <div class="workspace-title">
          <span class="material-symbols-outlined">description</span>
          <h2>${escapeHtml(documentSummary.original_filename || documentSummary.document_id)}</h2>
          <span class="chip">${escapeHtml((documentSummary.source_format || "").toUpperCase())}</span>
          <span class="chip">${escapeHtml(sourceLanguage)} -> ${escapeHtml(state.settings.target_language)}</span>
          <span class="chip">${formatCredits(balanceCredits)} available</span>
        </div>
        <div>
          <div class="section-meta">Section ${translated} of ${total}</div>
          ${sectionStripMarkup(total, translated)}
        </div>
      </div>

      ${sectionPreviewNavMarkup(sectionsResponse, previewResponse)}
      ${previewPagerMarkup(previewResponse)}

      <div class="preview-grid">
        <section class="preview-pane">
          <div class="preview-header">Source Preview</div>
          <div class="preview-body">${sourceBlocks || emptyPaneMarkup("No source preview available.")}</div>
        </section>
        <section class="preview-pane">
          <div class="preview-header">Target Preview - Read Only</div>
          <div class="preview-body">${targetBlocks || emptyPaneMarkup("Translate the next section to create a preview.")}</div>
        </section>
      </div>

      ${previewPagerMarkup(previewResponse)}

      <div class="action-bar">
        <div class="action-group">
          <label class="tier-picker">
            <span>Model</span>
            ${modelTierOptionsMarkup(modelTiers, state.settings.model_tier)}
          </label>
          <button class="ghost-button" id="retranslate-button" ${canRetranslate && !state.activeJob ? "" : "disabled"}>
            <span class="material-symbols-outlined">refresh</span>
            Retranslate Last Section
          </button>
          <button class="secondary-button" id="export-button" ${translated > 0 && !state.activeJob ? "" : "disabled"}>
            <span class="material-symbols-outlined">download</span>
            Export
          </button>
        </div>
        <div class="action-group">
          <button class="secondary-button" id="translate-next-button" ${!allTranslated && !state.activeJob ? "" : "disabled"}>
            <span>Translate Next Section</span>
            <span class="button-price">est. ${formatCredits(nextQuote?.estimated_credits)}</span>
          </button>
          <button class="primary-button recommended-button" id="translate-rest-button" ${canTranslateRest && !state.activeJob ? "" : "disabled"}>
            <span>Translate Remaining Document</span>
            <span class="button-price">est. ${formatCredits(remainingQuote?.estimated_credits)}</span>
            <span class="material-symbols-outlined">bolt</span>
          </button>
        </div>
      </div>
    </div>
  `;
}

function previewBlocksMarkup(blocks, pane) {
  const parts = [];
  let lastSectionId = "";
  blocks.forEach((block) => {
    const sectionId = String(block.section_id || "");
    const translated = block.status === "translated";
    if (pane === "target" && !translated && sectionId && sectionId !== lastSectionId) {
      parts.push(sectionPlaceholderMarkup(sectionId));
    }
    parts.push(blockMarkup(block, pane));
    if (sectionId) {
      lastSectionId = sectionId;
    }
  });
  return parts.join("");
}

function sectionPlaceholderMarkup(sectionId) {
  const sectionNumber = Number(String(sectionId).replace(/\D+/g, ""));
  const label = Number.isFinite(sectionNumber) && sectionNumber > 0
    ? `Section ${sectionNumber}`
    : "Section";
  return `
    <div class="section-preview-marker">
      <span>${escapeHtml(label)}</span>
      <strong>Original until translated</strong>
    </div>
  `;
}

function sectionPreviewNavMarkup(sectionsResponse, previewResponse) {
  const sections = sectionsResponse.sections || [];
  if (!sections.length) {
    return "";
  }
  const sectionId = String(previewResponse.section_id || state.preview.sectionId || "");
  const index = sections.findIndex((section) => section.section_id === sectionId);
  const safeIndex = index >= 0 ? index : 0;
  const current = sections[safeIndex] || {};
  const sectionNumber = Number(current.index || safeIndex + 1);
  const sectionTotal = Number(sectionsResponse.section_count || sections.length || 0);
  const status = String(current.status || "source").replaceAll("_", " ");
  const words = Number(current.word_count || 0);
  return `
    <div class="section-preview-nav">
      <button class="secondary-button compact-button" data-preview-section="previous" ${safeIndex > 0 ? "" : "disabled"}>
        <span class="material-symbols-outlined">chevron_left</span>
        Previous Section
      </button>
      <div class="section-preview-current">
        <span>Previewing section ${sectionNumber} of ${sectionTotal}</span>
        <strong>${escapeHtml(status)}${words ? ` - ${words} words` : ""}</strong>
      </div>
      <button class="secondary-button compact-button" data-preview-section="next" ${safeIndex < sections.length - 1 ? "" : "disabled"}>
        Next Section
        <span class="material-symbols-outlined">chevron_right</span>
      </button>
    </div>
  `;
}

function blockMarkup(block, pane) {
  const translated = block.status === "translated";
  const text = pane === "source" ? block.source_text : block.display_text;
  const className = translated
    ? "translated"
    : pane === "source"
      ? "source"
      : "placeholder";
  return `
    <div class="block ${className}">
      <p>${escapeHtml(text || "")}</p>
    </div>
  `;
}

function previewPagerMarkup(previewResponse) {
  const totalBlocks = Number(previewResponse.total_blocks || 0);
  if (totalBlocks <= 0) {
    return "";
  }
  const offset = Number(previewResponse.offset || 0);
  const limit = Number(previewResponse.limit || state.preview.limit || 40);
  const start = Math.min(totalBlocks, offset + 1);
  const end = Math.min(totalBlocks, offset + limit);
  const page = Number(previewResponse.page || 0);
  const pageCount = Number(previewResponse.page_count || 0);
  if (pageCount <= 1) {
    return "";
  }
  return `
    <div class="preview-pager">
      <div class="preview-page-meta">
        Blocks ${start}-${end} of ${totalBlocks}
        ${pageCount > 1 ? `<span>Page ${page} of ${pageCount}</span>` : ""}
      </div>
      <div class="preview-page-actions">
        <button class="icon-button" data-preview-page="first" title="First preview page" ${previewResponse.has_previous ? "" : "disabled"}>
          <span class="material-symbols-outlined">first_page</span>
        </button>
        <button class="secondary-button compact-button" data-preview-page="previous" ${previewResponse.has_previous ? "" : "disabled"}>
          <span class="material-symbols-outlined">chevron_left</span>
          Previous Page
        </button>
        <button class="secondary-button compact-button" data-preview-page="next" ${previewResponse.has_next ? "" : "disabled"}>
          Next Page
          <span class="material-symbols-outlined">chevron_right</span>
        </button>
        <button class="icon-button" data-preview-page="last" title="Last preview page" ${previewResponse.has_next ? "" : "disabled"}>
          <span class="material-symbols-outlined">last_page</span>
        </button>
      </div>
    </div>
  `;
}

function skeletonMarkup() {
  return `
    <div class="skeleton-lines" aria-label="Translation running">
      <div></div>
      <div></div>
      <div></div>
      <div></div>
      <div></div>
    </div>
  `;
}

function sectionStripMarkup(total, cursor) {
  if (!total) {
    return "";
  }
  const dots = [];
  const capped = Math.min(total, 28);
  for (let index = 1; index <= capped; index += 1) {
    dots.push(`<span class="section-dot ${index <= cursor ? "done" : index === cursor + 1 ? "current" : ""}"></span>`);
  }
  return `<div class="section-strip">${dots.join("")}</div>`;
}

function jobBannerMarkup(job) {
  const progress = jobDisplayProgress(job);
  const isRunning = ["queued", "running"].includes(String(job.status || ""));
  return `
    <div class="job-banner ${isRunning ? "running" : ""}" data-job-started-at="${escapeAttribute(job.created_at || "")}">
      <div>
        <strong>${escapeHtml(job.message || "Translation running")}</strong>
        <div class="job-meta">Job ${escapeHtml(job.job_id)} - ${escapeHtml(job.status || "queued")}</div>
      </div>
      <div class="job-progress-wrap">
        <div class="progress-track job-progress-track">
          <div class="progress-fill job-progress-fill" style="width:${progress}%"></div>
        </div>
        <div class="job-progress-label">${progress}%</div>
      </div>
    </div>
  `;
}

function jobDisplayProgress(job) {
  const rawProgress = Number(job.progress || 0);
  if (rawProgress >= 100 || job.status === "succeeded" || job.status === "failed") {
    return Math.max(0, Math.min(100, Math.round(rawProgress)));
  }
  const startedAt = new Date(job.created_at || Date.now()).getTime();
  const elapsedSeconds = Number.isFinite(startedAt)
    ? Math.max(0, (Date.now() - startedAt) / 1000)
    : 0;
  const animatedProgress = 12 + Math.min(78, Math.log2(elapsedSeconds + 1) * 14);
  return Math.max(rawProgress, Math.min(90, Math.round(animatedProgress)));
}

function bindWorkspace(documentSummary, previewResponse) {
  document.querySelector("#translate-next-button")?.addEventListener("click", () => {
    startTranslation(documentSummary.document_id, "translate-next");
  });
  document.querySelector("#translate-rest-button")?.addEventListener("click", () => {
    startTranslation(documentSummary.document_id, "translate-rest");
  });
  document.querySelector("#retranslate-button")?.addEventListener("click", () => {
    startTranslation(documentSummary.document_id, "retranslate-last");
  });
  document.querySelector("#export-button")?.addEventListener("click", () => {
    exportDocument(documentSummary.document_id);
  });
  document.querySelector("#workspace-model-tier")?.addEventListener("change", async (event) => {
    state.settings.model_tier = normalizeModelTierId(event.target.value);
    persistSettings();
    await renderWorkspace(documentSummary.document_id);
  });
  document.querySelectorAll("[data-preview-page]").forEach((button) => {
    button.addEventListener("click", () => {
      movePreviewPage(documentSummary.document_id, previewResponse, button.dataset.previewPage);
    });
  });
  document.querySelectorAll("[data-preview-section]").forEach((button) => {
    button.addEventListener("click", () => {
      movePreviewSection(documentSummary.document_id, button.dataset.previewSection);
    });
  });
}

async function movePreviewSection(documentId, direction) {
  const sectionsResponse = await apiFetch(`/documents/${encodeURIComponent(documentId)}/sections`);
  const sections = sectionsResponse.sections || [];
  if (!sections.length) {
    return;
  }
  const currentSectionId = state.preview.sectionId || sections[0].section_id;
  const currentIndex = Math.max(
    0,
    sections.findIndex((section) => section.section_id === currentSectionId),
  );
  const nextIndex = direction === "previous"
    ? Math.max(0, currentIndex - 1)
    : Math.min(sections.length - 1, currentIndex + 1);
  state.preview.sectionId = String(sections[nextIndex]?.section_id || "");
  state.preview.offset = 0;
  await renderWorkspace(documentId);
}

async function movePreviewPage(documentId, previewResponse, direction) {
  const offset = Number(previewResponse.offset || 0);
  const limit = Number(previewResponse.limit || state.preview.limit || 40);
  const totalBlocks = Number(previewResponse.total_blocks || 0);
  let nextOffset = offset;
  if (direction === "first") {
    nextOffset = 0;
  } else if (direction === "previous") {
    nextOffset = Math.max(0, offset - limit);
  } else if (direction === "next") {
    nextOffset = Math.min(Math.max(0, totalBlocks - 1), offset + limit);
  } else if (direction === "last") {
    nextOffset = Math.max(0, Math.floor((Math.max(1, totalBlocks) - 1) / limit) * limit);
  }
  state.preview.offset = nextOffset;
  await renderWorkspace(documentId);
}

async function startTranslation(documentId, endpoint) {
  clearNotice();
  if (sameLanguage(state.settings.source_language, state.settings.target_language)) {
    showError("Choose different source and target languages before starting translation.");
    return;
  }
  try {
    const job = await apiFetch(`/documents/${encodeURIComponent(documentId)}/${endpoint}`, {
      method: "POST",
      body: JSON.stringify({
        ...state.settings,
        idempotency_key: `${endpoint}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      }),
    });
    state.activeJob = job;
    await renderWorkspace(documentId);
    scheduleJobPoll(job.job_id, documentId);
  } catch (error) {
    showError(error.message);
  }
}

function sameLanguage(sourceLanguage, targetLanguage) {
  return normalizeComparable(sourceLanguage) === normalizeComparable(targetLanguage);
}

function normalizeComparable(value) {
  return String(value || "").trim().toLocaleLowerCase();
}

function scheduleJobPoll(jobId, documentId) {
  window.setTimeout(() => pollJob(jobId, documentId), 1800);
}

async function pollJob(jobId, documentId) {
  try {
    const job = await apiFetch(`/jobs/${encodeURIComponent(jobId)}`);
    state.activeJob = job;
    if (job.status === "succeeded") {
      state.activeJob = null;
      showNotice("Translation complete. Preview refreshed.");
      await renderWorkspace(documentId);
      return;
    }
    if (job.status === "failed") {
      state.activeJob = null;
      showError(job.error || "Translation failed.");
      await renderWorkspace(documentId);
      return;
    }
    updateActiveJobUi(job);
    scheduleJobPoll(jobId, documentId);
  } catch (error) {
    state.activeJob = null;
    showError(error.message);
  }
}

function updateActiveJobUi(job) {
  const banner = document.querySelector(".job-banner");
  if (!banner) {
    return;
  }
  const title = banner.querySelector("strong");
  if (title) {
    title.textContent = job.message || "Translation running";
  }
  const meta = banner.querySelector(".job-meta");
  if (meta) {
    meta.textContent = `Job ${job.job_id || ""} - ${job.status || "queued"}`;
  }
  const progress = banner.querySelector(".progress-fill");
  if (progress) {
    progress.style.width = `${jobDisplayProgress(job)}%`;
  }
  const progressLabel = banner.querySelector(".job-progress-label");
  if (progressLabel) {
    progressLabel.textContent = `${jobDisplayProgress(job)}%`;
  }
}

async function exportDocument(documentId) {
  try {
    const exportResult = await apiFetch(`/documents/${encodeURIComponent(documentId)}/export`, {
      method: "POST",
    });
    showNotice("Export created. Download starting.");
    window.location.href = exportResult.download_path;
  } catch (error) {
    showError(error.message);
  }
}

async function renderUsage() {
  setHeader("Usage", "Usage Command Center");
  root.innerHTML = loadingMarkup();
  try {
    const [usage, docs, wallet, packages] = await Promise.all([
      apiFetch("/usage/me"),
      apiFetch("/documents").catch(() => ({ documents: [] })),
      apiFetch("/billing/wallet").catch(() => null),
      apiFetch("/billing/credit-packages").catch(() => null),
    ]);
    const documentsById = new Map((docs.documents || []).map((doc) => [doc.document_id, doc]));
    root.innerHTML = usageMarkup(usage, documentsById, wallet, packages);
    bindUsage();
  } catch (error) {
    showError(error.message);
    root.innerHTML = emptyStateMarkup("Usage unavailable", "Usage data could not be loaded.");
  }
}

function usageMarkup(usage, documentsById, wallet, packagesResponse) {
  const quota = usage.quota || {};
  const uploadQuota = usage.daily_upload_quota || {};
  const activeJobs = usage.active_translation_jobs || {};
  const records = usage.records || [];
  return `
    <div class="grid-6">
      ${metricMarkup("Free words used", `${number(quota.used_words || 0)} / ${number(quota.limit_words || 2000)}`, "Lifetime allowance")}
      ${metricMarkup("Free words remaining", number(quota.remaining_words || 0), "Available lifetime words")}
      ${metricMarkup("Words translated", number(usage.total_word_count || 0), "Successful jobs")}
      ${metricMarkup("Uploads today", `${uploadQuota.used || 0} / ${uploadQuota.limit || 5}`, `${uploadQuota.remaining ?? "-"} remaining`)}
      ${metricMarkup("Active jobs", `${activeJobs.used || 0} / ${activeJobs.limit || 2}`, "Queued or running")}
      ${metricMarkup("Wallet credits", formatCredits(wallet?.balance_credits), "Available balance")}
    </div>
    ${creditPackagesMarkup(packagesResponse)}
    <div class="panel" style="margin-top:22px;">
      <div class="section-title">
        <div>
          <div class="eyebrow">Records</div>
          <h2>Recent translation jobs</h2>
        </div>
      </div>
      ${
        records.length
          ? `<table class="table">
              <thead>
                <tr>
                  <th>Job</th>
                  <th>Document</th>
                  <th>Words</th>
                  <th>Tokens</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                ${records.map((record) => usageRowMarkup(record, documentsById)).join("")}
              </tbody>
            </table>`
          : emptyStateMarkup("No usage records yet", "Translate a section to create the first usage record.")
      }
    </div>
  `;
}

function creditPackagesMarkup(packagesResponse) {
  const packages = packagesResponse?.packages || [];
  if (!packages.length) {
    return "";
  }
  const provider = String(packagesResponse.provider || "razorpay");
  const checkoutEnabled = Boolean(packagesResponse.checkout_enabled);
  const providerLabel = provider === "razorpay" ? "Razorpay Checkout" : "Local mock checkout";
  return `
    <div class="panel" style="margin-top:22px;">
      <div class="section-title">
        <div>
          <div class="eyebrow">Wallet</div>
          <h2>Add credits</h2>
        </div>
        <span class="chip">${escapeHtml(providerLabel)}</span>
      </div>
      <div class="credit-package-grid">
        ${packages.map((item) => creditPackageMarkup(item, provider, checkoutEnabled)).join("")}
      </div>
    </div>
  `;
}

function creditPackageMarkup(item, provider, checkoutEnabled) {
  return `
    <div class="credit-package-card">
      <div>
        <h3>${escapeHtml(item.name || item.package_id)}</h3>
        <p class="muted">${formatCredits(item.credits)} for ${formatMoney(item.amount_cents, item.currency)}</p>
      </div>
      <button class="primary-button" data-checkout-package="${escapeAttribute(item.package_id)}" data-checkout-provider="${escapeAttribute(provider)}" ${checkoutEnabled ? "" : "disabled"}>
        Add Credits
      </button>
    </div>
  `;
}

function bindUsage() {
  document.querySelectorAll("[data-checkout-package]").forEach((button) => {
    button.addEventListener("click", () => {
      startCheckout(button.dataset.checkoutPackage, button.dataset.checkoutProvider);
    });
  });
}

async function startCheckout(packageId, provider) {
  clearNotice();
  try {
    const checkout = await apiFetch("/billing/checkout-session", {
      method: "POST",
      body: JSON.stringify({
        package_id: packageId,
        provider,
      }),
    });
    if (checkout.provider === "razorpay") {
      await openRazorpayCheckout(checkout);
      return;
    }
    if (checkout.checkout_url && /^https?:\/\//i.test(checkout.checkout_url)) {
      window.location.href = checkout.checkout_url;
      return;
    }
    showNotice("Checkout created. Credits are added after verified payment completion.");
  } catch (error) {
    showError(error.message);
  }
}

async function openRazorpayCheckout(checkout) {
  if (!checkout.razorpay_key_id || !checkout.razorpay_order_id) {
    showError("Razorpay checkout is not ready. Please retry in a moment.");
    return;
  }
  await loadRazorpayScript();
  const options = {
    key: checkout.razorpay_key_id,
    amount: checkout.amount_cents,
    currency: checkout.currency,
    name: "LexiFlow AI",
    description: `${formatCredits(checkout.credits)} wallet top-up`,
    order_id: checkout.razorpay_order_id,
    prefill: {},
    notes: {
      order_id: checkout.order_id,
    },
    handler: () => {
      showNotice("Payment submitted. Credits will appear after Razorpay verifies the payment.");
      window.setTimeout(() => renderUsage(), 2500);
    },
    modal: {
      ondismiss: () => showNotice("Checkout closed. No credits were added."),
    },
  };
  const razorpayCheckout = new window.Razorpay(options);
  razorpayCheckout.open();
}

function loadRazorpayScript() {
  if (window.Razorpay) {
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    const existing = document.querySelector("script[data-razorpay-checkout]");
    if (existing) {
      existing.addEventListener("load", resolve, { once: true });
      existing.addEventListener("error", reject, { once: true });
      return;
    }
    const script = document.createElement("script");
    script.src = "https://checkout.razorpay.com/v1/checkout.js";
    script.async = true;
    script.dataset.razorpayCheckout = "true";
    script.onload = resolve;
    script.onerror = () => reject(new Error("Could not load Razorpay Checkout. Please retry."));
    document.head.appendChild(script);
  });
}

function usageRowMarkup(record, documentsById) {
  const document = documentsById.get(record.document_id);
  return `
    <tr>
      <td>${escapeHtml(record.job_id || "")}</td>
      <td>${escapeHtml(document?.original_filename || record.document_id || "")}</td>
      <td>${number(record.word_count || 0)}</td>
      <td>${number(record.estimated_total_tokens || 0)}</td>
      <td>${formatDate(record.created_at)}</td>
    </tr>
  `;
}

function renderSettings() {
  setHeader("Settings", "Preferences");
  root.innerHTML = `
    <div class="settings-card panel">
      <div class="section-title">
        <div>
          <div class="eyebrow">Local defaults</div>
          <h2>Translation preferences</h2>
        </div>
      </div>
      <form id="settings-form" class="form-grid">
        <div class="field">
          <label for="settings-source-language">Source language</label>
          ${languageSelectMarkup("settings-source-language", "source_language", state.settings.source_language)}
        </div>
        <div class="field">
          <label for="settings-target-language">Target language</label>
          ${languageSelectMarkup("settings-target-language", "target_language", state.settings.target_language)}
        </div>
        <div class="field">
          <label for="settings-document-type">Document type</label>
          <input id="settings-document-type" name="document_type" value="${escapeAttribute(state.settings.document_type)}" />
        </div>
        <div class="field">
          <label for="settings-content-form">Content form</label>
          <input id="settings-content-form" name="content_form" value="${escapeAttribute(state.settings.content_form)}" />
        </div>
        <div class="field">
          <label for="settings-model-tier">Default model tier</label>
          ${modelTierSelectMarkup("settings-model-tier", "model_tier", state.settings.model_tier)}
        </div>
        <div class="field" style="justify-content:end;">
          <button class="primary-button" type="submit">Save preferences</button>
        </div>
      </form>
    </div>
  `;
  document.querySelector("#settings-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    state.settings.source_language = String(formData.get("source_language") || "English").trim() || "English";
    state.settings.target_language = String(formData.get("target_language") || "German").trim() || "German";
    state.settings.document_type = String(formData.get("document_type") || "general");
    state.settings.content_form = String(formData.get("content_form") || "book");
    state.settings.model_tier = normalizeModelTierId(String(formData.get("model_tier") || "balanced"));
    persistSettings();
    showNotice("Preferences saved.");
  });
}

function persistSettings() {
  localStorage.setItem("lexiflow.sourceLanguage", state.settings.source_language);
  localStorage.setItem("lexiflow.targetLanguage", state.settings.target_language);
  localStorage.setItem("lexiflow.documentType", state.settings.document_type);
  localStorage.setItem("lexiflow.contentForm", state.settings.content_form);
  localStorage.setItem("lexiflow.modelTier", state.settings.model_tier);
}

function setHeader(nextEyebrow, nextTitle) {
  eyebrow.textContent = nextEyebrow;
  title.textContent = nextTitle;
}

function showNotice(message) {
  notice.textContent = message;
  notice.classList.remove("hidden", "error");
}

function showError(message) {
  notice.textContent = message || "Something went wrong. Please try again.";
  notice.classList.remove("hidden");
  notice.classList.add("error");
}

function clearNotice() {
  notice.textContent = "";
  notice.classList.add("hidden");
  notice.classList.remove("error");
}

function loadingMarkup(message = "Preparing workspace...") {
  return `
    <div class="loading-card">
      <div class="scan-document">
        <span class="material-symbols-outlined">description</span>
      </div>
      <p>${escapeHtml(message)}</p>
    </div>
  `;
}

function emptyStateMarkup(heading, body) {
  return `
    <div class="empty-pane">
      <div>
        <h2>${escapeHtml(heading)}</h2>
        <p class="muted" style="margin-top:8px;">${escapeHtml(body)}</p>
      </div>
    </div>
  `;
}

function formatCredits(value) {
  const credits = Number(value || 0);
  return `${number(credits)} credits`;
}

function formatMoney(amountCents, currency = "USD") {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: String(currency || "USD").toUpperCase(),
  }).format(Number(amountCents || 0) / 100);
}

function number(value) {
  return new Intl.NumberFormat("en-US").format(Number(value || 0));
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (bytes >= 1024 * 1024) {
    return `${Number((bytes / (1024 * 1024)).toFixed(1))} MB`;
  }
  if (bytes >= 1024) {
    return `${Number((bytes / 1024).toFixed(1))} KB`;
  }
  return `${bytes} bytes`;
}

function formatDate(value) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleString();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttribute(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}
