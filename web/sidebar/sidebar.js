const SESSION_KEY = "openemr_agent_session_id"
const DEFAULT_USER = "demo-user"
const MAX_CHARS = 8000
const WARN_CHARS = 7500

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
    await this.loadSessionList()

    if (this.state.sessionID) {
      const loaded = await this.loadConversation(this.state.sessionID)
      if (!loaded) {
        this.state.sessionID = null
        sessionStorage.removeItem(SESSION_KEY)
        this.renderSystemNotice("Your previous session expired. Starting a new conversation.")
      }
    }

    if (!this.state.sessionID) {
      await this.createSession()
    }

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
        this.state.patientID = event.data.pid || null
        this.state.encounterID = event.data.encounter_id || null
        this.state.patientName = event.data.pname || null
        this.updateContextDisplay()
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
    const headers = {
      "Content-Type": "application/json",
      ...(proxyBase ? {} : { "openemr_user_id": DEFAULT_USER }),
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
      const sessions = await this.api("/api/sessions")
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
      meta.textContent = patient
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
      this.state.tourIndex = 0
      this.renderReviewPanel()
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
      // Always coerce to string — the Knockout view model returns numeric pids
      // but the agent API schema requires patient_id to be a string.
      patient_id: this.state.patientID != null ? String(this.state.patientID) : null,
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
    this.renderMessage("user", message)
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
      this.state.tourIndex = 0
      this.renderReviewPanel()
      this.setStatus(this.phaseToStatus(data.phase))
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
    const block = document.createElement("article")
    block.className = `message role-${role}`

    const label = document.createElement("div")
    label.className = "message-label"
    label.textContent = role === "user" ? "You" : "Assistant"
    block.appendChild(label)

    const markdown = document.createElement("div")
    markdown.className = "markdown"
    markdown.innerHTML = this.renderMarkdown(content)
    block.appendChild(markdown)

    if (metadata) {
      const meta = document.createElement("div")
      meta.className = "meta"
      const toolText = (metadata.tools || [])
        .map((tool) => `${tool.name} × ${tool.count}`)
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
          li.textContent = `✓ ${tool.name} called ${tool.count} time(s)`
          list.appendChild(li)
        }
        details.appendChild(list)
        block.appendChild(details)
      }
    }

    this.el.chatArea.appendChild(block)
    this.scrollToBottom()
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

    this.renderTourCard(manifest.items[idx])
    this.requestAllOverlays(manifest.items)

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
        ok.textContent = `✓ All checks passed · Apply: ${approved} | Rejected: ${rejected} | Pending: ${pending}`
        this.el.reviewSummary.appendChild(ok)
      } else {
        this.el.reviewSummary.textContent = `Apply: ${approved} | Rejected: ${rejected} | Pending: ${pending}`
      }
      this.el.executeButton.disabled = false
      this.el.executeButton.textContent = approved > 0 ? "Execute Changes" : "Discard All"
    }
  }

  renderTourCard(item) {
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

    const title = document.createElement("strong")
    title.textContent = item.resource_type
    header.appendChild(title)

    const statusBadge = document.createElement("span")
    statusBadge.className = `review-card-status badge-${item.status}`
    statusBadge.textContent = item.status
    header.appendChild(statusBadge)

    if (item.confidence) {
      const conf = document.createElement("span")
      conf.className = `confidence-badge confidence-${item.confidence}`
      conf.textContent = item.confidence
      header.appendChild(conf)
    }

    card.appendChild(header)

    const desc = document.createElement("div")
    desc.className = "review-card-description"
    desc.textContent = item.description || "No description"
    card.appendChild(desc)

    if (item.current_value && (item.action === "update" || item.action === "delete")) {
      const section = document.createElement("div")
      section.className = "review-card-section"
      const label = document.createElement("div")
      label.className = "review-card-section-label"
      label.textContent = "Current Value"
      section.appendChild(label)
      const current = document.createElement("div")
      current.className = "review-card-current"
      current.textContent = JSON.stringify(item.current_value, null, 2)
      section.appendChild(current)
      card.appendChild(section)
    }

    const propSection = document.createElement("div")
    propSection.className = "review-card-section"
    const propLabel = document.createElement("div")
    propLabel.className = "review-card-section-label"
    propLabel.textContent = "Proposed Value"
    propSection.appendChild(propLabel)
    const edit = document.createElement("textarea")
    edit.value = JSON.stringify(item.proposed_value || {}, null, 2)
    propSection.appendChild(edit)
    card.appendChild(propSection)

    if (item.source_reference) {
      const srcDiv = document.createElement("div")
      srcDiv.className = "review-card-source"
      const srcLink = document.createElement("a")
      srcLink.href = "#"
      srcLink.textContent = `Source: ${item.source_reference}`
      srcLink.addEventListener("click", (e) => e.preventDefault())
      srcDiv.appendChild(srcLink)
      card.appendChild(srcDiv)
    }

    const actions = document.createElement("div")
    actions.className = "review-card-actions"
    const applyBtn = this.makeReviewButton("Apply", "btn-sm btn-accent", () => this.updateReviewItem(item.id, "approved", edit.value))
    const rejectBtn = this.makeReviewButton("Reject", "btn-sm btn-muted", () => this.updateReviewItem(item.id, "rejected", edit.value))
    const undoBtn = this.makeReviewButton("Undo", "btn-sm btn-muted", () => this.updateReviewItem(item.id, "pending", edit.value))
    if (item.status === "approved") applyBtn.classList.add("active-status")
    if (item.status === "rejected") rejectBtn.classList.add("active-status")
    actions.appendChild(applyBtn)
    actions.appendChild(rejectBtn)
    actions.appendChild(undoBtn)
    card.appendChild(actions)

    if (this.state.verificationResults && this.state.verificationResults.length > 0) {
      const itemResults = this.state.verificationResults.filter((r) => r.item_id === item.id)
      if (itemResults.length > 0) {
        const checksDiv = document.createElement("div")
        checksDiv.className = "verification-checks"
        for (const check of itemResults) {
          const row = document.createElement("div")
          row.className = "verification-check"

          const iconSpan = document.createElement("span")
          const passed = check.passed !== false
          iconSpan.className = `verification-check-icon ${passed ? "passed" : "failed-" + (check.severity || "error")}`
          iconSpan.textContent = passed ? "✓" : "✗"
          row.appendChild(iconSpan)

          const nameSpan = document.createElement("span")
          nameSpan.className = "verification-check-name"
          nameSpan.textContent = (check.check_name || "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
          row.appendChild(nameSpan)

          const msgSpan = document.createElement("span")
          msgSpan.className = "verification-check-message"
          msgSpan.textContent = check.message || ""
          row.appendChild(msgSpan)

          checksDiv.appendChild(row)
        }
        card.appendChild(checksDiv)
      }
    }

    this.el.reviewCards.appendChild(card)
  }

  tourNavigate(delta) {
    const manifest = this.state.pendingManifest
    if (!manifest || !manifest.items.length) return
    const newIndex = this.state.tourIndex + delta
    if (newIndex < 0 || newIndex >= manifest.items.length) return
    this.state.tourIndex = newIndex
    this.renderReviewPanel()
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

  requestAllOverlays(items) {
    this.postOverlayMessage({ type: "overlay:applyAll", items })
  }

  handleOverlayResult(data) {
    // overlay engine reports result; no special handling needed
  }

  handleInlineAction(itemId, status) {
    if (!this.state.pendingManifest) return
    const item = this.state.pendingManifest.items.find((i) => i.id === itemId)
    if (!item) return
    this.updateReviewItem(itemId, status, JSON.stringify(item.proposed_value || {}))
  }

  makeReviewButton(label, className, onClick) {
    const button = document.createElement("button")
    button.className = className
    button.textContent = label
    button.addEventListener("click", onClick)
    return button
  }

  async updateReviewItem(itemID, status, proposedValueText) {
    if (!this.state.pendingManifest) {
      return
    }
    const approvedItems = []
    const rejectedItems = []
    const modifiedItems = []

    for (const item of this.state.pendingManifest.items) {
      if (item.id === itemID) {
        item.status = status
        try {
          const parsed = JSON.parse(proposedValueText)
          item.proposed_value = parsed
          modifiedItems.push({ id: item.id, proposed_value: parsed })
        } catch (_error) {
          this.renderErrorBlock("Invalid JSON in modified value.")
          return
        }
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
    await this.updateReviewItem(this.state.pendingManifest.items[0].id, status, JSON.stringify(this.state.pendingManifest.items[0].proposed_value || {}))
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
      this.renderSystemNotice(`Execution finished: ${data.manifest_status || "completed"}.`)
      this.state.pendingManifest = null
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
    let rendered = this.escapeHtml(text || "")
    rendered = rendered.replace(/`([^`]+)`/g, "<code>$1</code>")
    rendered = rendered.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    rendered = rendered.replace(/\*([^*]+)\*/g, "<em>$1</em>")
    rendered = rendered.replace(/\n/g, "<br>")
    rendered = rendered.replace(
      /\b([A-Z][A-Za-z]+\/[A-Za-z0-9\-\.]+)\b/g,
      "<code>$1</code>"
    )
    return rendered
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
