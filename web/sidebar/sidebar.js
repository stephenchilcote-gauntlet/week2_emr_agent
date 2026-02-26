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
    }

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
    }

    this.lastUserMessage = ""
  }

  async start() {
    this.bindEvents()
    this.refreshContext()
    this.showLoadingSkeleton()

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

    this.hideLoadingSkeleton()
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
    const globals = window.top || window
    const openemrGlobals = globals.openemrAgentContext || {}
    const patientID = openemrGlobals.pid || null
    const encounterID = openemrGlobals.encounter || null
    const patientName = openemrGlobals.patient_name || null
    const activeTab = openemrGlobals.active_tab_title || openemrGlobals.active_tab || null

    this.state.patientID = patientID
    this.state.encounterID = encounterID
    this.state.patientName = patientName
    this.state.activeTab = activeTab

    if (patientID) {
      const encounterText = encounterID ? ` · Enc: ${encounterID}` : ""
      const tabText = activeTab ? ` · ${activeTab}` : ""
      const nameText = patientName || patientID
      this.el.contextLine.textContent = `${nameText}${encounterText}${tabText}`
    } else {
      const tabText = activeTab ? `Tab: ${activeTab}` : "No patient selected"
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
    const headers = {
      "Content-Type": "application/json",
      "openemr_user_id": DEFAULT_USER,
      ...(options.headers || {}),
    }
    const response = await fetch(path, { ...options, headers })
    if (!response.ok) {
      const text = await response.text()
      throw new Error(text || `HTTP ${response.status}`)
    }
    return response.json()
  }

  async createSession(clearChat = false) {
    try {
      const data = await this.api("/api/sessions", { method: "POST" })
      this.state.sessionID = data.session_id
      sessionStorage.setItem(SESSION_KEY, data.session_id)
      this.setStatus("ready")
      if (clearChat) {
        this.el.chatArea.innerHTML = ""
      }
      this.state.pendingManifest = null
      this.renderReviewPanel()
      this.updateSessionDisplay()
      await this.loadSessionList()
    } catch (_error) {
      this.renderErrorBlock("Couldn't start a new conversation.", { onRetry: () => this.createSession(clearChat) })
      this.setStatus("error")
    }
  }

  async loadSessionList() {
    try {
      const sessions = await this.api("/api/sessions")
      this.state.sessions = sessions
      this.renderHistoryList()
    } catch (_error) {
      this.renderErrorBlock("Couldn't load conversation history.")
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

    try {
      const data = await this.api("/api/chat", {
        method: "POST",
        body: JSON.stringify({
          session_id: this.state.sessionID,
          message,
          page_context: this.buildPageContext(),
        }),
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
      this.renderReviewPanel()
      this.setStatus(this.phaseToStatus(data.phase))
      await this.loadSessionList()
    } catch (_error) {
      this.hideTypingIndicator()
      this.renderErrorBlock("Something went wrong sending your message.", {
        onRetry: () => {
          if (this.lastUserMessage) {
            this.sendMessage(this.lastUserMessage)
          }
        },
      })
      this.setStatus("error")
    } finally {
      this.toggleSend(true)
      if (this.state.phase !== "reviewing") {
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

  renderErrorBlock(text, { onRetry = null } = {}) {
    const block = document.createElement("div")
    block.className = "error-block"

    const dismiss = document.createElement("button")
    dismiss.className = "error-dismiss"
    dismiss.textContent = "×"
    dismiss.title = "Dismiss"
    dismiss.addEventListener("click", () => block.remove())
    block.appendChild(dismiss)

    const msg = document.createElement("span")
    msg.textContent = text
    block.appendChild(msg)

    if (onRetry) {
      const actions = document.createElement("div")
      actions.className = "error-actions"
      const retry = document.createElement("button")
      retry.textContent = "Try again"
      retry.addEventListener("click", () => {
        block.remove()
        onRetry()
      })
      actions.appendChild(retry)
      block.appendChild(actions)
    }

    this.el.chatArea.appendChild(block)
    this.scrollToBottom()
  }

  renderSystemNotice(text) {
    this.renderMessage("assistant", text)
  }

  renderReviewPanel() {
    const manifest = this.state.pendingManifest
    if (!manifest || !Array.isArray(manifest.items) || manifest.items.length === 0) {
      this.el.reviewPanel.classList.add("hidden")
      return
    }

    this.el.reviewPanel.classList.remove("hidden")
    this.el.reviewCards.innerHTML = ""

    let approved = 0
    let rejected = 0
    let pending = 0

    for (const item of manifest.items) {
      if (item.status === "approved") {
        approved += 1
      } else if (item.status === "rejected") {
        rejected += 1
      } else {
        pending += 1
      }

      const card = document.createElement("article")
      card.className = "review-card"
      if (item.status === "approved" || item.status === "rejected") {
        card.classList.add(`status-${item.status}`)
      }

      const statusBadge = `<span class="review-card-status badge-${item.status}">${item.status}</span>`
      card.innerHTML = `
        <div><strong>${this.escapeHtml(item.resource_type)}</strong> · ${this.escapeHtml(item.action)}${statusBadge}</div>
        <div>${this.escapeHtml(item.description || "No description")}</div>
      `

      const edit = document.createElement("textarea")
      edit.value = JSON.stringify(item.proposed_value || {}, null, 2)
      card.appendChild(edit)

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
      this.el.reviewCards.appendChild(card)
    }

    this.el.reviewSummary.textContent = `Apply: ${approved} | Rejected: ${rejected} | Pending: ${pending}`
    this.el.executeButton.textContent = approved > 0 ? "Execute Changes" : "Discard All"
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
          this.renderErrorBlock("The modified value isn't valid. Please check and try again.")
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
      await this.api(`/api/manifest/${this.state.sessionID}/approve`, {
        method: "POST",
        body: JSON.stringify({
          approved_items: approvedItems,
          rejected_items: rejectedItems,
          modified_items: modifiedItems,
        }),
      })
      this.renderReviewPanel()
    } catch (_error) {
      this.renderErrorBlock("Couldn't save your review.", { onRetry: () => this.updateReviewItem(itemID, status, proposedValueText) })
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
    } catch (_error) {
      this.renderErrorBlock("Couldn't apply the changes.", { onRetry: () => this.executeManifest() })
      this.setStatus("error")
    } finally {
      this.toggleSend(true)
    }
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

  showLoadingSkeleton() {
    const skeleton = document.createElement("div")
    skeleton.className = "loading-skeleton"
    skeleton.id = "loading-skeleton"
    for (let i = 0; i < 3; i++) {
      const line = document.createElement("div")
      line.className = "skeleton-line"
      skeleton.appendChild(line)
    }
    this.el.chatArea.appendChild(skeleton)
  }

  hideLoadingSkeleton() {
    const el = document.getElementById("loading-skeleton")
    if (el) el.remove()
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
