const SESSION_KEY = "openemr_agent_session_id"
const DEFAULT_USER = "demo-user"
const MAX_CHARS = 8000
const WARN_CHARS = 7500
const RESOURCE_DISPLAY_NAMES = {
  Condition: "Medical Problems",
  AllergyIntolerance: "Allergies",
  MedicationRequest: "Medications",
  Encounter: "Encounters",
  Observation: "Vitals/Observations",
  Vital: "Vitals",
  SoapNote: "SOAP Note",
  Procedure: "Procedures",
  DiagnosticReport: "Reports",
}

const TOOL_DISPLAY_NAMES = {
  fhir_read: "Read patient record",
  openemr_api: "Query clinical data",
  get_page_context: "Read current page",
  submit_manifest: "Submit changes",
  open_patient_chart: "Open patient chart",
}

function toolDisplayName(name) {
  return TOOL_DISPLAY_NAMES[name] || name
}

const ACTION_LABELS = {
  create: "Add",
  update: "Update",
  delete: "Remove",
}

const VERIFICATION_LABELS = {
  grounding: { name: "Source", passedMsg: "Source verified in patient record" },
  confidence: { name: "Clarity", passedMsg: "Recommendation is clearly stated" },
  conflict: { name: "Conflicts", passedMsg: "No conflicts with current record" },
  constraint_icd10: { name: "Diagnosis Code", passedMsg: null },
  constraint_cpt: { name: "Procedure Code", passedMsg: null },
  constraint_document_sections: { name: "Document", passedMsg: null },
  medication_high_risk: { name: "Safety", passedMsg: null },
  medication_required_fields: { name: "Required Info", passedMsg: null },
  medication_duplicate: { name: "Duplicates", passedMsg: null },
  medication_allergy: { name: "Allergy Check", passedMsg: null },
}

const VALUE_FIELD_LABELS = {
  ref: null,
  status: "Status",
  title: "Name",
  code: "Code",
  code_text: "Description",
  display: "Description",
  drug: "Medication",
  dose: "Dose",
  route: "Route",
  freq: "Frequency",
  begdate: "Start date",
  enddate: "End date",
  severity: "Severity",
  reaction: "Reaction",
  onset: "Onset",
  type: "Type",
  outcome: "Outcome",
  occurrence: "Occurrence",
  note: "Notes",
  document: "Note text",
  text: "Note text",
  verification: "Verification",
  clinical_status: "Clinical status",
  category: "Category",
  subjective: "Subjective",
  objective: "Objective",
  assessment: "Assessment",
  plan: "Plan",
  reason: "Reason",
  facility: "Facility",
  date: "Date",
  onset_date: "Onset Date",
  sensitivity: null,
  pc_catid: null,
  bps: "Systolic BP",
  bpd: "Diastolic BP",
  weight: "Weight",
  height: "Height",
  temperature: "Temperature",
  temp_method: null,
  pulse: "Pulse",
  respiration: "Respiration",
  oxygen_saturation: "O₂ Sat",
  waist_circ: "Waist",
  head_circ: "Head Circ",
}

function humanizeFieldValue(key, value) {
  if (value === null || value === undefined) return null
  if (typeof value === "object") return null
  const str = String(value)
  if (key === "status" || key === "verification" || key === "clinical_status") {
    return str.charAt(0).toUpperCase() + str.slice(1).replace(/_/g, " ")
  }
  return str
}

function formatValueSummary(valueObj) {
  if (!valueObj || typeof valueObj !== "object") return []
  const lines = []
  for (const [key, value] of Object.entries(valueObj)) {
    const label = VALUE_FIELD_LABELS[key]
    if (label === null) continue
    if (label === undefined && typeof value === "object") continue
    const displayLabel = label || (key.charAt(0).toUpperCase() + key.slice(1).replace(/_/g, " "))
    const displayValue = humanizeFieldValue(key, value)
    if (displayValue) {
      lines.push({ label: displayLabel, value: displayValue })
    }
  }
  return lines
}

function formatSourceReference(ref) {
  if (!ref) return null
  const match = ref.match(/^(\w+)\//)
  if (!match) return ref
  const type = match[1]
  const typeLabels = {
    Encounter: "Based on current encounter",
    Patient: "Based on patient record",
    Condition: "Based on existing condition",
    MedicationRequest: "Based on existing medication",
    AllergyIntolerance: "Based on existing allergy record",
    Observation: "Based on recorded observation",
    Procedure: "Based on procedure record",
    DiagnosticReport: "Based on diagnostic report",
  }
  return typeLabels[type] || `Based on ${RESOURCE_DISPLAY_NAMES[type] || type} record`
}

function formatVerificationCheck(check) {
  const info = VERIFICATION_LABELS[check.check_name] || {}
  const name = info.name || check.check_name.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase())
  const passed = check.passed !== false
  let message = check.message || ""
  if (passed && info.passedMsg) {
    message = info.passedMsg
  } else if (!passed) {
    message = message
      .replace(/\b\w+\/[0-9a-f]{8}-[0-9a-f-]+/gi, s => {
        const m = s.match(/^(\w+)\//)
        return m ? (RESOURCE_DISPLAY_NAMES[m[1]] || m[1]).toLowerCase() + " record" : s
      })
  }
  return { name, message, passed }
}

function formatExecutionContent(text) {
  const match = text.match(/^Execution complete\.\s*(\d+)\s*succeeded,\s*(\d+)\s*failed,\s*(\d+)\s*skipped\.?$/i)
  if (!match) return null
  const succeeded = parseInt(match[1], 10)
  const failed = parseInt(match[2], 10)
  const total = succeeded + failed
  if (total === 0) return "No changes were applied."
  if (failed === 0 && succeeded === 1) return "✓ Change applied successfully."
  if (failed === 0) return `✓ All ${succeeded} changes applied successfully.`
  if (succeeded === 0 && failed === 1) return "The change could not be applied. Please review and try again."
  if (succeeded === 0) return `${failed} changes could not be applied. Please review and try again.`
  return `${succeeded} change${succeeded > 1 ? "s" : ""} applied. ${failed} could not be completed — please review.`
}

class SidebarApp {
  constructor() {
    this.state = {
      sessionID: sessionStorage.getItem(SESSION_KEY),
      phase: "ready",
      pendingManifest: null,
      patientID: null,
      encounterID: null,
      patientName: null,
      pendingMessages: false,
      showHistory: false,
      sessions: [],
      tourIndex: 0,
      verificationResults: null,
      verificationPassed: null,
      manifestOpenemrPid: null,
    }

    this.abortController = null

    this.el = {
      statusPill: document.getElementById("status-pill"),
      statusText: document.getElementById("status-text"),
      contextLine: document.getElementById("context-line"),
      historyToggle: document.getElementById("history-toggle"),
      historyPanel: document.getElementById("history-panel"),
      historyList: document.getElementById("history-list"),
      chatShell: document.getElementById("chat-shell"),
      newConversation: document.getElementById("new-conversation"),
      chatArea: document.getElementById("chat-area"),
      chatInput: document.getElementById("chat-input"),
      sendButton: document.getElementById("send-button"),
      charCounter: document.getElementById("char-counter"),
      sessionIdRow: document.getElementById("session-id-row"),
      newMessagesPill: document.getElementById("new-messages-pill"),
      reviewPanel: document.getElementById("review-panel"),
      reviewCards: document.getElementById("review-cards"),
      reviewSummary: document.getElementById("review-summary"),
      applyAll: document.getElementById("apply-all"),
      rejectAll: document.getElementById("reject-all"),
      executeButton: document.getElementById("execute-button"),
      tourPrev: document.getElementById("tour-prev"),
      tourNext: document.getElementById("tour-next"),
      tourProgress: document.getElementById("tour-progress"),
      auditToggle: document.getElementById("audit-toggle"),
      auditPanel: document.getElementById("audit-panel"),
      auditList: document.getElementById("audit-list"),
    }

    this.lastUserMessage = ""
  }

  async start() {
    this.bindEvents()
    this.refreshContext()
    sessionStorage.removeItem(SESSION_KEY)
    this.state.sessionID = null
    await this.createSession()
    this.toggleSend(true)
  }

  bindEvents() {
    this.el.newConversation.addEventListener("click", () => {
      this.setHistoryVisible(false)
      this.createSession(true)
    })

    this.el.historyToggle.addEventListener("click", () => {
      this.setHistoryVisible(!this.state.showHistory)
    })

    this.el.sendButton.addEventListener("click", () => this.sendMessage())

    this.el.chatInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault()
        this.sendMessage()
      }
    })

    this.el.chatInput.addEventListener("input", () => {
      this.resizeInput()
      this.updateCharacterCounter()
      this.updateSendButtonVisibility()
    })

    this.el.newMessagesPill.addEventListener("click", () => {
      this.scrollToBottom(true)
      this.el.newMessagesPill.classList.add("hidden")
    })

    if (this.el.sessionIdRow) {
      this.el.sessionIdRow.addEventListener("click", () => {
        if (this.state.sessionID) {
          navigator.clipboard.writeText(this.state.sessionID).then(() => {
            this.el.sessionIdRow.textContent = "Copied!"
            setTimeout(() => this.updateSessionDisplay(), 1500)
          })
        }
      })
    }

    this.el.applyAll.addEventListener("click", () => this.bulkReview("approved"))
    this.el.rejectAll.addEventListener("click", () => this.bulkReview("rejected"))
    this.el.executeButton.addEventListener("click", () => this.executeManifest())
    if (this.el.auditToggle) {
      this.el.auditToggle.addEventListener("click", () => this.toggleAuditPanel())
    }
    this.el.tourPrev.addEventListener("click", () => this.tourNavigate(-1))
    this.el.tourNext.addEventListener("click", () => this.tourNavigate(1))

    window.addEventListener("message", (event) => {
      if (event.data && event.data.type === "clinical-assistant-context") {
        const newPid = event.data.pid || null
        const oldPid = this.state.patientID
        this.state.patientID = newPid
        this.state.encounterID = event.data.encounter_id || null
        this.state.patientName = event.data.pname || null
        // Keep OPENEMR_SESSION_CONTEXT in sync so refreshContext() doesn't
        // overwrite the live state with the stale page-load snapshot.
        if (window.OPENEMR_SESSION_CONTEXT) {
          window.OPENEMR_SESSION_CONTEXT.pid = newPid
          window.OPENEMR_SESSION_CONTEXT.encounter = event.data.encounter_id || null
          window.OPENEMR_SESSION_CONTEXT.patient_name = event.data.pname || null
        }
        this.updateContextDisplay()
        if (newPid !== oldPid) {
          this.state.pendingManifest = null
          this.state.verificationResults = null
          this.state.verificationPassed = null
          this.state.manifestOpenemrPid = null
          this.renderReviewPanel()
          this.createSession(true)
        }
      }
      if (event.data && event.data.type === "overlay:result") {
        this.handleOverlayResult(event.data)
      }
      if (event.data && event.data.type === "overlay:accept") {
        this.handleInlineAction(event.data.itemId, "approved")
      }
      if (event.data && event.data.type === "overlay:reject") {
        this.handleInlineAction(event.data.itemId, "rejected")
      }
      if (event.data && event.data.type === "overlay:navigate") {
        this.tourNavigate(event.data.delta)
      }
      if (event.data && event.data.type === "overlay:execute") {
        this.executeManifest()
      }
    })

    this.el.chatArea.addEventListener("scroll", () => {
      if (this.isNearBottom()) {
        this.el.newMessagesPill.classList.add("hidden")
        this.state.pendingMessages = false
      }
    })
  }

  setHistoryVisible(visible) {
    this.state.showHistory = visible
    this.el.historyPanel.classList.toggle("hidden", !visible)
    this.el.chatShell.classList.toggle("hidden", visible)
    this.el.historyToggle.classList.toggle("active", visible)
  }

  refreshContext() {
    // Source 1: OPENEMR_SESSION_CONTEXT (set by sidebar_frame.php)
    const ctx = window.OPENEMR_SESSION_CONTEXT
    if (ctx && ctx.pid) {
      this.state.patientID = ctx.pid
      this.state.encounterID = ctx.encounter || null
      this.state.patientName = ctx.patient_name || null
      this.state.activeTab = null
      this.updateContextDisplay()
      return
    }

    // Source 2: openemrAgentContext (standalone dev mode)
    const globals = window.top || window
    const openemrGlobals = globals.openemrAgentContext || {}
    this.state.patientID = openemrGlobals.pid || null
    this.state.encounterID = openemrGlobals.encounter || null
    this.state.patientName = openemrGlobals.patient_name || null
    this.state.activeTab = openemrGlobals.active_tab_title || openemrGlobals.active_tab || null
    this.updateContextDisplay()
  }

  updateContextDisplay() {
    if (this.state.patientID) {
      const encounterText = this.state.encounterID ? ` · Enc: ${this.state.encounterID}` : ""
      const tabText = this.state.activeTab ? ` · ${this.state.activeTab}` : ""
      const nameText = this.state.patientName || this.state.patientID
      this.el.contextLine.textContent = `${nameText}${encounterText}${tabText}`
    } else {
      const tabText = this.state.activeTab ? `Tab: ${this.state.activeTab}` : "No patient selected"
      this.el.contextLine.textContent = tabText
    }
  }

  updateSessionDisplay() {
    if (!this.el.sessionIdRow) return
    if (this.state.sessionID) {
      this.el.sessionIdRow.textContent = `Session: ${this.state.sessionID.slice(0, 8)}…`
      this.el.sessionIdRow.title = `Click to copy full ID: ${this.state.sessionID}`
      this.el.sessionIdRow.classList.remove("hidden")
    } else {
      this.el.sessionIdRow.classList.add("hidden")
    }
  }

  updateSendButtonVisibility() {
    const hasText = this.el.chatInput.value.trim().length > 0
    this.el.sendButton.classList.toggle("visible", hasText)
  }

  async api(path, options = {}) {
    const proxyBase = window.OPENEMR_AGENT_PROXY
    let url
    if (proxyBase) {
      const separator = proxyBase.includes("?") ? "&" : "?"
      url = `${proxyBase}${separator}path=${encodeURIComponent(path)}`
    } else {
      url = path
    }
    const authToken = window.OPENEMR_AUTH_TOKEN
    const headers = {
      "Content-Type": "application/json",
      ...(proxyBase ? {} : { "openemr_user_id": DEFAULT_USER }),
      ...(authToken ? { "X-Sidebar-Token": authToken } : {}),
      ...(options.headers || {}),
    }
    const fetchOptions = { ...options, headers }
    if (options.signal) {
      fetchOptions.signal = options.signal
    }
    const response = await fetch(url, fetchOptions)
    if (!response.ok) {
      const text = await response.text()
      throw new Error(text || `HTTP ${response.status}`)
    }
    return response.json()
  }

  async createSession(clearChat = false) {
    if (this.abortController) {
      this.abortController.abort()
      this.abortController = null
    }
    try {
      const data = await this.api("/api/sessions", { method: "POST" })
      this.state.sessionID = data.session_id
      sessionStorage.setItem(SESSION_KEY, data.session_id)
      this.setStatus("ready")
      if (clearChat) {
        this.el.chatArea.innerHTML = ""
      }
      this.state.pendingManifest = null
      this.state.tourIndex = 0
      this.renderReviewPanel()
      this.updateSessionDisplay()
      await this.loadSessionList()
    } catch (error) {
      this.renderErrorBlock(`Failed to create a session: ${error.message}`)
      this.setStatus("error")
    }
  }

  async loadSessionList() {
    try {
      const params = new URLSearchParams()
      if (this.state.patientID) {
        params.set("patient_id", this.state.patientID)
      }
      const qs = params.toString()
      const sessions = await this.api(`/api/sessions${qs ? "?" + qs : ""}`)
      this.state.sessions = sessions
      this.renderHistoryList()
    } catch (error) {
      this.renderErrorBlock(`Unable to load conversation history: ${error.message}`)
    }
  }

  renderHistoryList() {
    this.el.historyList.innerHTML = ""
    const sessions = this.state.sessions

    if (sessions.length === 0) {
      const empty = document.createElement("div")
      empty.className = "history-empty"
      empty.textContent = "No previous conversations"
      this.el.historyList.appendChild(empty)
      return
    }

    for (const session of sessions) {
      const btn = document.createElement("button")
      btn.className = "history-item"
      if (session.session_id === this.state.sessionID) {
        btn.classList.add("active")
      }

      const preview = document.createElement("div")
      preview.className = "history-item-preview"
      preview.textContent = session.first_message_preview || "(empty)"
      btn.appendChild(preview)

      const meta = document.createElement("div")
      meta.className = "history-item-meta"
      const patient = session.patient_name || session.patient_id || "No patient"
      const cost = session.total_cost_usd > 0
        ? ` · $${session.total_cost_usd < 0.01 ? session.total_cost_usd.toFixed(4) : session.total_cost_usd.toFixed(2)}`
        : ""
      meta.textContent = `${patient}${cost}`
      btn.appendChild(meta)

      btn.addEventListener("click", () => {
        this.loadConversation(session.session_id)
        this.setHistoryVisible(false)
      })

      this.el.historyList.appendChild(btn)
    }
  }

  async loadConversation(sessionID) {
    try {
      const data = await this.api(`/api/sessions/${sessionID}/messages`)
      this.state.sessionID = sessionID
      sessionStorage.setItem(SESSION_KEY, sessionID)
      this.el.chatArea.innerHTML = ""

      for (const message of data.messages || []) {
        this.renderMessage(message.role, message.content || "", null)
      }

      this.state.pendingManifest = data.manifest || null
      this.state.manifestOpenemrPid = data.openemr_pid || null
      this.state.tourIndex = 0
      this.state.tourCardHeight = 0
      this.el.reviewCards.style.minHeight = ""
      this.renderReviewPanel()
      this.setStatus(this.phaseToStatus(data.phase || "planning"))
      this.updateSessionDisplay()
      this.renderHistoryList()
      this.scrollToBottom(true)
      return true
    } catch (_error) {
      return false
    }
  }

  buildPageContext() {
    this.refreshContext()
    const globals = (window.top || window).openemrAgentContext || {}
    return {
      patient_id: this.state.patientID,
      encounter_id: this.state.encounterID,
      page_type: globals.active_tab || globals.active_tab_title || null,
      visible_data: {
        patient_name: this.state.patientName,
        active_tab: globals.active_tab || null,
        active_tab_title: globals.active_tab_title || null,
        active_tab_url: globals.active_tab_url || null,
      },
    }
  }

  async sendMessage(messageOverride = null) {
    if (this.state.phase === "thinking" || this.state.phase === "executing") {
      return
    }

    const raw = messageOverride !== null ? messageOverride : this.el.chatInput.value
    const message = raw.trim()
    if (!message) {
      return
    }

    if (message.length > MAX_CHARS) {
      this.el.chatInput.title = "Message too long — shorten to under 8,000 characters."
      return
    }

    this.lastUserMessage = message
    if (messageOverride === null) {
      this.renderMessage("user", message)
    }
    this.el.chatInput.value = ""
    this.resizeInput()
    this.updateCharacterCounter()
    this.updateSendButtonVisibility()

    this.setStatus("thinking")
    this.toggleSend(false)
    this.showTypingIndicator()
    const started = performance.now()

    this.abortController = new AbortController()
    const signal = this.abortController.signal
    let hadError = false

    try {
      const data = await this.api("/api/chat", {
        method: "POST",
        body: JSON.stringify({
          session_id: this.state.sessionID,
          message,
          page_context: this.buildPageContext(),
        }),
        signal,
      })

      this.state.sessionID = data.session_id
      sessionStorage.setItem(SESSION_KEY, data.session_id)

      this.hideTypingIndicator()
      const latencyMs = performance.now() - started
      this.renderMessage("assistant", data.response || "", {
        latencyMs,
        tools: data.tool_calls_summary || [],
      })

      this.state.pendingManifest = data.manifest || null
      this.state.manifestOpenemrPid = data.openemr_pid || null
      this.state.tourIndex = 0
      this.state.tourCardHeight = 0
      this.el.reviewCards.style.minHeight = ""
      this.renderReviewPanel()
      this.setStatus(this.phaseToStatus(data.phase))
      if (data.navigate_to_patient) {
        this.navigateToPatient(data.navigate_to_patient)
      }
      await this.loadSessionList()
    } catch (error) {
      this.hideTypingIndicator()
      if (error.name === "AbortError") {
        return
      }
      hadError = true
      this.renderRetryableError(error.message)
      this.setStatus("error")
    } finally {
      this.abortController = null
      this.toggleSend(true)
      if (!hadError && this.state.phase !== "reviewing") {
        this.setStatus("ready")
      }
    }
  }

  phaseToStatus(phase) {
    if (phase === "reviewing") {
      return "reviewing"
    }
    if (phase === "executing") {
      return "executing"
    }
    if (phase === "complete") {
      return "ready"
    }
    return "ready"
  }

  showTypingIndicator() {
    this.hideTypingIndicator()
    const indicator = document.createElement("div")
    indicator.className = "typing-indicator"
    indicator.id = "typing-indicator"
    const spinner = document.createElement("span")
    spinner.className = "typing-spinner"
    indicator.appendChild(spinner)
    const label = document.createElement("span")
    label.className = "typing-label"
    label.textContent = "Thinking…"
    indicator.appendChild(label)
    this.el.chatArea.appendChild(indicator)
    this.scrollToBottom()
  }

  hideTypingIndicator() {
    const existing = document.getElementById("typing-indicator")
    if (existing) {
      existing.remove()
    }
  }

  renderMessage(role, content, metadata = null) {
    const messageIndex = this.el.chatArea.querySelectorAll(".message").length
    const block = document.createElement("article")
    block.className = `message role-${role}`
    block.dataset.messageIndex = messageIndex

    const label = document.createElement("div")
    label.className = "message-label"
    label.textContent = role === "user" ? "You" : "Assistant"
    block.appendChild(label)

    let displayContent = content
    if (role === "assistant") {
      const friendly = formatExecutionContent(content)
      if (friendly) displayContent = friendly
    }

    const markdown = document.createElement("div")
    markdown.className = "markdown"
    markdown.innerHTML = this.renderMarkdown(displayContent)
    block.appendChild(markdown)

    if (metadata) {
      const meta = document.createElement("div")
      meta.className = "meta"
      const toolText = (metadata.tools || [])
        .map((tool) => `${toolDisplayName(tool.name)} × ${tool.count}`)
        .join(", ")
      const latencyText = `${(metadata.latencyMs / 1000).toFixed(1)}s`
      meta.textContent = toolText ? `${latencyText} · ${toolText}` : latencyText
      block.appendChild(meta)

      if ((metadata.tools || []).length > 0) {
        const details = document.createElement("details")
        details.className = "activity"
        const summary = document.createElement("summary")
        summary.textContent = "Activity"
        details.appendChild(summary)
        const list = document.createElement("ul")
        for (const tool of metadata.tools) {
          const li = document.createElement("li")
          li.textContent = `✓ ${toolDisplayName(tool.name)} called ${tool.count} time(s)`
          list.appendChild(li)
        }
        details.appendChild(list)
        block.appendChild(details)
      }
    }

    if (role === "assistant" && content) {
      block.appendChild(this.renderFeedbackButtons(messageIndex))
    }

    this.el.chatArea.appendChild(block)
    this.scrollToBottom()
  }

  renderFeedbackButtons(messageIndex) {
    const container = document.createElement("div")
    container.className = "feedback-buttons"

    const upBtn = document.createElement("button")
    upBtn.className = "feedback-btn"
    upBtn.dataset.rating = "up"
    upBtn.title = "Helpful"
    upBtn.textContent = "👍"

    const downBtn = document.createElement("button")
    downBtn.className = "feedback-btn"
    downBtn.dataset.rating = "down"
    downBtn.title = "Not helpful"
    downBtn.textContent = "👎"

    const handler = async (rating, btn) => {
      container.querySelectorAll(".feedback-btn").forEach(b => b.classList.remove("active"))
      btn.classList.add("active")
      try {
        await this.api(`/api/sessions/${this.state.sessionID}/feedback`, {
          method: "POST",
          body: JSON.stringify({ message_index: messageIndex, rating }),
        })
      } catch (_e) {
        btn.classList.remove("active")
      }
    }

    upBtn.addEventListener("click", () => handler("up", upBtn))
    downBtn.addEventListener("click", () => handler("down", downBtn))

    container.appendChild(upBtn)
    container.appendChild(downBtn)
    return container
  }

  renderRetryableError(text) {
    const block = document.createElement("div")
    block.className = "error-block"
    block.innerHTML = `<strong>Assistant error:</strong> ${this.escapeHtml(text)}`
    const retry = document.createElement("button")
    retry.textContent = "Retry"
    retry.addEventListener("click", () => {
      block.remove()
      if (this.lastUserMessage) {
        this.sendMessage(this.lastUserMessage)
      }
    })
    block.appendChild(document.createElement("br"))
    block.appendChild(retry)
    this.el.chatArea.appendChild(block)
    this.scrollToBottom()
  }

  renderErrorBlock(text) {
    const block = document.createElement("div")
    block.className = "error-block"
    block.textContent = text
    this.el.chatArea.appendChild(block)
  }

  renderSystemNotice(text) {
    this.renderMessage("assistant", text)
  }

  renderReviewPanel() {
    const manifest = this.state.pendingManifest
    if (!manifest || !Array.isArray(manifest.items) || manifest.items.length === 0) {
      this.el.reviewPanel.classList.add("hidden")
      this.postOverlayMessage({ type: "overlay:clear" })
      return
    }

    this.el.reviewPanel.classList.remove("hidden")
    const total = manifest.items.length
    const idx = Math.max(0, Math.min(this.state.tourIndex, total - 1))
    this.state.tourIndex = idx

    this.el.tourProgress.textContent = `${idx + 1} of ${total}`
    this.el.tourPrev.disabled = idx === 0
    this.el.tourNext.disabled = idx === total - 1

    if (!this.state.tourCardHeight) {
      this.measureMaxCardHeight(manifest.items)
    }

    const terminal = ["completed", "failed", "rejected", "executing"]
    const isTerminal = terminal.includes(manifest.status)

    this.renderTourCard(manifest.items[idx], isTerminal)
    this.requestAllOverlays(manifest.items, idx)

    this.el.applyAll.disabled = isTerminal
    this.el.rejectAll.disabled = isTerminal

    if (isTerminal) {
      const labels = { completed: "✓ Applied", failed: "✗ Failed", rejected: "✗ Rejected", executing: "⏳ Executing" }
      this.el.reviewSummary.innerHTML = ""
      const badge = document.createElement("div")
      badge.className = `verification-summary manifest-${manifest.status}`
      badge.textContent = labels[manifest.status] || manifest.status
      this.el.reviewSummary.appendChild(badge)
      this.el.executeButton.disabled = true
      this.el.executeButton.textContent = labels[manifest.status] || manifest.status
      return
    }

    let approved = 0
    let rejected = 0
    let pending = 0
    for (const item of manifest.items) {
      if (item.status === "approved") approved += 1
      else if (item.status === "rejected") rejected += 1
      else pending += 1
    }

    if (this.state.verificationPassed === false) {
      this.el.reviewSummary.innerHTML = ""
      const warn = document.createElement("div")
      warn.className = "verification-summary"
      warn.textContent = "⚠ Verification failed — resolve errors before executing"
      this.el.reviewSummary.appendChild(warn)
      this.el.executeButton.disabled = true
    } else {
      if (this.state.verificationPassed === true) {
        this.el.reviewSummary.innerHTML = ""
        const ok = document.createElement("div")
        ok.className = "verification-summary all-passed"
        ok.textContent = `✓ All checks passed · ${approved} to apply, ${rejected} rejected, ${pending} pending`
        this.el.reviewSummary.appendChild(ok)
      } else {
        this.el.reviewSummary.textContent = `${approved} to apply, ${rejected} rejected, ${pending} pending`
      }
      this.el.executeButton.disabled = false
      this.el.executeButton.textContent = approved > 0 ? "Execute Changes" : "Discard All"
    }
  }

  renderTourCard(item, isTerminal = false) {
    this.el.reviewCards.innerHTML = ""
    const card = document.createElement("article")
    card.className = "review-card"
    if (item.status === "approved" || item.status === "rejected") {
      card.classList.add(`status-${item.status}`)
    }

    const actionIcons = { create: "+", update: "✎", delete: "✕" }
    const header = document.createElement("div")
    header.className = "review-card-header"

    const icon = document.createElement("span")
    icon.className = `review-card-action-icon action-${item.action}`
    icon.textContent = actionIcons[item.action] || "?"
    header.appendChild(icon)

    const actionLabel = ACTION_LABELS[item.action] || item.action
    const resourceLabel = RESOURCE_DISPLAY_NAMES[item.resource_type] || item.resource_type
    const title = document.createElement("strong")
    title.textContent = `${actionLabel} ${resourceLabel}`
    header.appendChild(title)

    const statusBadge = document.createElement("span")
    statusBadge.className = `review-card-status badge-${item.status}`
    statusBadge.textContent = item.status
    header.appendChild(statusBadge)

    if (item.confidence && item.confidence !== "high") {
      const conf = document.createElement("span")
      conf.className = `confidence-badge confidence-${item.confidence}`
      conf.textContent = item.confidence === "low" ? "⚠ Needs review" : "Review suggested"
      header.appendChild(conf)
    }

    card.appendChild(header)

    const desc = document.createElement("div")
    desc.className = "review-card-description"
    desc.textContent = item.description || "No description"
    card.appendChild(desc)

    if (!this.isInPageResource(item.resource_type)) {
      const note = document.createElement("div")
      note.className = "review-card-sidebar-note"
      note.textContent = "Cannot preview in-page — review details here."
      card.appendChild(note)
    }

    if (item.current_value && (item.action === "update" || item.action === "delete")) {
      const currentLines = formatValueSummary(item.current_value)
      if (currentLines.length > 0) {
        const section = document.createElement("div")
        section.className = "review-card-section"
        const label = document.createElement("div")
        label.className = "review-card-section-label"
        label.textContent = "Current"
        section.appendChild(label)
        const current = document.createElement("div")
        current.className = "review-card-current"
        current.textContent = currentLines.map(l => `${l.label}: ${l.value}`).join("\n")
        section.appendChild(current)
        card.appendChild(section)
      }
    }

    const proposedLines = formatValueSummary(item.proposed_value)
    const propSection = document.createElement("div")
    propSection.className = "review-card-section"
    const propLabel = document.createElement("div")
    propLabel.className = "review-card-section-label"
    propLabel.textContent = "Proposed Changes"
    propSection.appendChild(propLabel)

    if (proposedLines.length > 0) {
      const summaryDiv = document.createElement("div")
      summaryDiv.className = "review-card-value-summary"
      for (const line of proposedLines) {
        const row = document.createElement("div")
        row.className = "review-card-value-row"
        const k = document.createElement("span")
        k.className = "review-card-value-label"
        k.textContent = line.label
        const v = document.createElement("span")
        v.className = "review-card-value-content"
        v.textContent = line.value
        row.appendChild(k)
        row.appendChild(v)
        summaryDiv.appendChild(row)
      }
      propSection.appendChild(summaryDiv)
    }

    card.appendChild(propSection)

    if (item.source_reference) {
      const srcDiv = document.createElement("div")
      srcDiv.className = "review-card-source"
      srcDiv.textContent = formatSourceReference(item.source_reference)
      card.appendChild(srcDiv)
    }

    if (!isTerminal) {
      const actions = document.createElement("div")
      actions.className = "review-card-actions"
      const applyBtn = this.makeReviewButton("Apply", "btn-sm btn-accent", () => this.updateReviewItem(item.id, "approved", item.proposed_value))
      const rejectBtn = this.makeReviewButton("Reject", "btn-sm btn-muted", () => this.updateReviewItem(item.id, "rejected", item.proposed_value))
      const undoBtn = this.makeReviewButton("Undo", "btn-sm btn-muted", () => this.updateReviewItem(item.id, "pending", item.proposed_value))
      if (item.status === "approved") applyBtn.classList.add("active-status")
      if (item.status === "rejected") rejectBtn.classList.add("active-status")
      actions.appendChild(applyBtn)
      actions.appendChild(rejectBtn)
      actions.appendChild(undoBtn)
      card.appendChild(actions)
    }

    if (this.state.verificationResults && this.state.verificationResults.length > 0) {
      const itemResults = this.state.verificationResults.filter((r) => r.item_id === item.id)
      if (itemResults.length > 0) {
        const checksDiv = document.createElement("div")
        checksDiv.className = "verification-checks"
        for (const check of itemResults) {
          const formatted = formatVerificationCheck(check)
          const row = document.createElement("div")
          row.className = "verification-check"

          const iconSpan = document.createElement("span")
          iconSpan.className = `verification-check-icon ${formatted.passed ? "passed" : "failed-" + (check.severity || "error")}`
          iconSpan.textContent = formatted.passed ? "✓" : "✗"
          row.appendChild(iconSpan)

          const nameSpan = document.createElement("span")
          nameSpan.className = "verification-check-name"
          nameSpan.textContent = formatted.name
          row.appendChild(nameSpan)

          const msgSpan = document.createElement("span")
          msgSpan.className = "verification-check-message"
          msgSpan.textContent = formatted.message
          row.appendChild(msgSpan)

          checksDiv.appendChild(row)
        }
        card.appendChild(checksDiv)
      }
    }

    this.el.reviewCards.appendChild(card)
  }

  measureMaxCardHeight(items) {
    this.el.reviewCards.style.visibility = "hidden"
    let maxH = 0
    for (const item of items) {
      this.renderTourCard(item)
      const card = this.el.reviewCards.firstElementChild
      if (card) {
        maxH = Math.max(maxH, this.el.reviewCards.scrollHeight)
      }
    }
    this.el.reviewCards.style.visibility = ""
    this.state.tourCardHeight = maxH
    if (maxH > 0) {
      this.el.reviewCards.style.height = `${maxH}px`
    }
  }

  isInPageResource(resourceType) {
    const supported = ["Condition", "AllergyIntolerance", "MedicationRequest", "Encounter", "SoapNote", "Procedure", "Observation"]
    return supported.includes(resourceType)
  }

  tourNavigate(delta) {
    const manifest = this.state.pendingManifest
    if (!manifest || !manifest.items.length) return
    const newIndex = this.state.tourIndex + delta
    if (newIndex < 0 || newIndex >= manifest.items.length) return
    this.state.tourIndex = newIndex
    this.renderReviewPanel()
  }

  navigateToPatient(nav) {
    const { pid, pname, dob } = nav
    try {
      const p = window.parent || window
      const url =
        (p.webroot_url || "") +
        "/interface/patient_file/summary/demographics.php?set_pid=" +
        encodeURIComponent(pid)

      // Setting RTop.location calls navigateTab("pat", activateTabByName).
      // In navigateTab's "existing tab" branch, a one('load', activate) callback is
      // registered — it fires after demographics.php loads, re-showing "pat" after
      // left_nav.setPatient() hides it.
      //
      // In navigateTab's "new tab" branch (first open, no "pat" iframe yet), activateTabByName
      // fires immediately (synchronous) but NO load callback is registered. demographics.php
      // then loads, window.onload calls left_nav.setPatient() → navigateTab("enc") →
      // openExistingTab hides "pat" — and nothing re-shows it.
      //
      // Fix: after RTop.location = url (synchronous, iframe now exists in DOM for both cases),
      // register our own one('load') on "pat" to re-activate it after setPatient() runs.
      // The parent's iframe.load fires AFTER demographics.php's window.load, so "pat" ends
      // up as the final visible tab.
      p.RTop.location = url
      p.$("iframe[name='pat']").one("load", function () {
        p.activateTabByName("pat", true)
      })
    } catch (_e) {
      // parent not accessible (cross-origin)
    }
  }

  postOverlayMessage(msg) {
    try {
      if (window.parent && window.parent !== window) {
        window.parent.postMessage(msg, "*")
      }
    } catch (_e) {
      // parent not accessible
    }
  }

  requestOverlay(item) {
    this.postOverlayMessage({ type: "overlay:apply", item })
  }

  requestAllOverlays(items, focusIndex) {
    const patientID = this.state.manifestOpenemrPid || this.state.patientID
    const manifest = this.state.pendingManifest
    const encounterID = (manifest && manifest.encounter_id) || this.state.encounterID || null
    this.postOverlayMessage({ type: "overlay:applyAll", items, focusIndex: focusIndex || 0, patientID, encounterID })
  }

  handleOverlayResult(data) {
    if (!data.applied && data.reason === "sidebar-only") {
      // expected for non-dashboard resources, no action needed
    }
  }

  handleInlineAction(itemId, status) {
    if (!this.state.pendingManifest) return
    const item = this.state.pendingManifest.items.find((i) => i.id === itemId)
    if (!item) return
    this.updateReviewItem(itemId, status, item.proposed_value || {})
  }

  makeReviewButton(label, className, onClick) {
    const button = document.createElement("button")
    button.className = className
    button.textContent = label
    button.addEventListener("click", onClick)
    return button
  }

  async updateReviewItem(itemID, status, proposedValue) {
    if (!this.state.pendingManifest) {
      return
    }
    const approvedItems = []
    const rejectedItems = []
    const modifiedItems = []

    for (const item of this.state.pendingManifest.items) {
      if (item.id === itemID) {
        item.status = status
        item.proposed_value = proposedValue
        modifiedItems.push({ id: item.id, proposed_value: proposedValue })
      }
      if (item.status === "approved") {
        approvedItems.push(item.id)
      }
      if (item.status === "rejected") {
        rejectedItems.push(item.id)
      }
    }

    try {
      const result = await this.api(`/api/manifest/${this.state.sessionID}/approve`, {
        method: "POST",
        body: JSON.stringify({
          approved_items: approvedItems,
          rejected_items: rejectedItems,
          modified_items: modifiedItems,
        }),
      })
      this.state.verificationResults = result.results || []
      this.state.verificationPassed = result.passed
      this.renderReviewPanel()
    } catch (error) {
      this.renderErrorBlock(`Failed to update manifest review: ${error.message}`)
    }
  }

  async bulkReview(status) {
    if (!this.state.pendingManifest) {
      return
    }
    for (const item of this.state.pendingManifest.items) {
      item.status = status
    }
    this.renderReviewPanel()
    await this.updateReviewItem(this.state.pendingManifest.items[0].id, status, this.state.pendingManifest.items[0].proposed_value || {})
  }

  async executeManifest() {
    if (!this.state.pendingManifest) {
      return
    }
    const approved = this.state.pendingManifest.items.filter((item) => item.status === "approved")
    if (approved.length === 0) {
      this.state.pendingManifest = null
      this.renderReviewPanel()
      this.setStatus("ready")
      return
    }

    this.setStatus("executing")
    this.toggleSend(false)
    try {
      const data = await this.api(`/api/manifest/${this.state.sessionID}/execute`, { method: "POST" })
      const items = data.items || []
      const completed = items.filter(i => i.status === "completed").length
      const failed = items.filter(i => i.status === "failed").length
      const statusMsg = formatExecutionContent(`Execution complete. ${completed} succeeded, ${failed} failed, 0 skipped.`) || "Changes processed."
      this.renderSystemNotice(statusMsg)
      this.state.pendingManifest = null
      // Reload affected OpenEMR tab(s) so clinician sees updated data
      if (completed > 0) {
        this.postOverlayMessage({
          type: "overlay:refresh",
          items: items.filter((i) => i.status === "completed"),
        })
      }
      this.renderReviewPanel()
      this.setStatus("ready")
    } catch (error) {
      this.renderErrorBlock(`Execution failed: ${error.message}`)
      this.setStatus("error")
    } finally {
      this.toggleSend(true)
    }
  }

  async toggleAuditPanel() {
    if (!this.el.auditPanel) return
    const visible = !this.el.auditPanel.classList.contains("hidden")
    if (visible) {
      this.el.auditPanel.classList.add("hidden")
      this.el.auditToggle.classList.remove("active")
      return
    }
    this.el.auditPanel.classList.remove("hidden")
    this.el.auditToggle.classList.add("active")
    if (this.state.sessionID) {
      await this.loadAuditTrail()
    }
  }

  async loadAuditTrail() {
    if (!this.el.auditList) return
    try {
      const data = await this.api(`/api/sessions/${this.state.sessionID}/audit`)
      const events = data.events || data || []
      this.el.auditList.innerHTML = ""
      if (events.length === 0) {
        this.el.auditList.textContent = "No audit events yet."
        return
      }
      for (const event of events) {
        const row = document.createElement("div")
        row.className = "audit-event"

        const time = document.createElement("span")
        time.className = "audit-event-time"
        time.textContent = this.formatAuditTime(event.timestamp)
        row.appendChild(time)

        const type = document.createElement("span")
        type.className = "audit-event-type"
        type.textContent = this.formatEventType(event.event_type)
        row.appendChild(type)

        const summary = document.createElement("span")
        summary.className = "audit-event-summary"
        summary.textContent = event.summary || ""
        row.appendChild(summary)

        this.el.auditList.appendChild(row)
      }
    } catch (error) {
      this.el.auditList.textContent = `Failed to load audit trail: ${error.message}`
    }
  }

  formatAuditTime(timestamp) {
    if (!timestamp) return ""
    try {
      const d = new Date(timestamp)
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    } catch (_e) {
      return timestamp
    }
  }

  formatEventType(eventType) {
    if (!eventType) return ""
    const icons = {
      chat_received: "💬",
      assistant_responded: "🤖",
      manifest_reviewed: "📋",
      manifest_executed: "⚡",
      verification_ran: "🔍",
    }
    const icon = icons[eventType] || "•"
    const label = eventType.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
    return `${icon} ${label}`
  }

  resizeInput() {
    const el = this.el.chatInput
    el.style.height = "auto"
    const maxHeight = 100
    el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`
    el.style.overflowY = el.scrollHeight > maxHeight ? "auto" : "hidden"
  }

  updateCharacterCounter() {
    const count = this.el.chatInput.value.length
    this.el.charCounter.textContent = `${count} / ${MAX_CHARS}`
    const show = count >= WARN_CHARS
    this.el.charCounter.classList.toggle("hidden", !show)
    const overLimit = count > MAX_CHARS
    this.el.chatInput.classList.toggle("over-limit", overLimit)
    this.el.sendButton.disabled = overLimit || this.el.sendButton.disabled
    this.el.sendButton.title = overLimit
      ? "Message too long — shorten to under 8,000 characters."
      : ""
  }

  setStatus(state) {
    this.state.phase = state
    const textMap = {
      ready: "Ready",
      thinking: "Thinking…",
      reviewing: "Review Changes",
      executing: "Applying…",
      error: "Error",
    }
    this.el.statusPill.dataset.state = state
    this.el.statusText.textContent = textMap[state] || textMap.ready
  }

  toggleSend(enabled) {
    const overLimit = this.el.chatInput.value.length > MAX_CHARS
    this.el.sendButton.disabled = !enabled || overLimit
    if (!enabled) {
      this.el.sendButton.title = "Waiting for the assistant to finish."
    } else if (!overLimit) {
      this.el.sendButton.title = ""
    }
  }

  isNearBottom() {
    const target = this.el.chatArea
    return target.scrollHeight - target.scrollTop - target.clientHeight <= 50
  }

  scrollToBottom(force = false) {
    const shouldScroll = force || this.isNearBottom()
    if (shouldScroll) {
      this.el.chatArea.scrollTop = this.el.chatArea.scrollHeight
      this.el.newMessagesPill.classList.add("hidden")
      this.state.pendingMessages = false
      return
    }
    this.state.pendingMessages = true
    this.el.newMessagesPill.classList.remove("hidden")
  }

  renderMarkdown(text) {
    const html = marked.parse(text || "", { breaks: true })
    return html.replace(/<table[\s\S]*?<\/table>/g, (m) => `<div class="table-wrap">${m}</div>`)
  }

  escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;")
  }
}

window.addEventListener("DOMContentLoaded", () => {
  const app = new SidebarApp()
  app.start()
})
