(function initOverlayEngine() {
  const RESOURCE_PAGE_MAP = {
    Condition: {
      tab: "pat",
      container: "#medical_problem_ps_expand",
      rowSelector: ".list-group-item",
      supportsRowTarget: true,
    },
    AllergyIntolerance: {
      tab: "pat",
      container: "#allergy_ps_expand",
      rowSelector: ".list-group-item",
      supportsRowTarget: true,
    },
    MedicationRequest: {
      tab: "pat",
      container: "#medication_ps_expand",
      rowSelector: ".list-group-item",
      supportsRowTarget: true,
    },
    Encounter: { tab: "enc", container: null, rowSelector: null, supportsRowTarget: true },
    Observation: { tab: "enc", container: null, rowSelector: null, supportsRowTarget: true },
    Procedure: { tab: "enc", container: null, rowSelector: null, supportsRowTarget: true },
    DiagnosticReport: { tab: "enc", container: null, rowSelector: null, supportsRowTarget: true },
    Vital: { tab: "pat", container: "#vitals_ps_expand", rowSelector: null, supportsRowTarget: true },
    SoapNote: { tab: "enc", container: null, rowSelector: null, supportsRowTarget: true },
  }

  var injectedElements = []

  function getFrameDocument(tabName) {
    try {
      var frame = document.querySelector("iframe[name='" + tabName + "']")
      if (frame && frame.contentDocument) {
        return frame.contentDocument
      }
    } catch (_e) {
      // cross-origin or not loaded
    }
    return null
  }

  function ensureCardExpanded(containerEl) {
    if (!containerEl) return
    var card = containerEl.closest(".card")
    if (!card) return
    var collapse = card.querySelector(".collapse")
    if (collapse && !collapse.classList.contains("show")) {
      collapse.classList.add("show")
      var toggle = card.querySelector("[data-toggle='collapse']")
      if (toggle) {
        toggle.setAttribute("aria-expanded", "true")
        toggle.classList.remove("collapsed")
      }
    }
  }

  function extractUuid(reference) {
    if (!reference) return null
    var parts = reference.split("/")
    return parts.length >= 2 ? parts[parts.length - 1] : reference
  }

  function findRowByUuid(frameDoc, containerSelector, uuid) {
    if (!frameDoc || !containerSelector || !uuid) return null
    var container = frameDoc.querySelector(containerSelector)
    if (!container) return null
    ensureCardExpanded(container)
    return container.querySelector('[data-uuid="' + uuid + '"]')
  }

  function getContainerEl(frameDoc, containerSelector) {
    if (!frameDoc || !containerSelector) return null
    var container = frameDoc.querySelector(containerSelector)
    if (container) {
      ensureCardExpanded(container)
    }
    return container
  }

  function createActionButtons(frameDoc, item) {
    var grid = frameDoc.createElement("div")
    grid.className = "agent-overlay-actions"
    grid.style.cssText =
      "display:grid;grid-template-columns:1fr 1fr;gap:2px;" +
      "margin-left:auto;flex-shrink:0;padding:2px;"

    var btnStyle =
      "border:1px solid #d1d5db;border-radius:3px;cursor:pointer;" +
      "font-size:11px;padding:1px 6px;background:#fff;color:#374151;" +
      "line-height:1.4;"

    var buttons = [
      { label: "\u2713", cls: "overlay-btn-accept", msg: { type: "overlay:accept", itemId: item.id } },
      { label: "\u2717", cls: "overlay-btn-reject", msg: { type: "overlay:reject", itemId: item.id } },
      { label: "\u2039", cls: "overlay-btn-prev",   msg: { type: "overlay:navigate", delta: -1 } },
      { label: "\u203A", cls: "overlay-btn-next",   msg: { type: "overlay:navigate", delta: 1 } },
    ]

    for (var i = 0; i < buttons.length; i++) {
      var b = buttons[i]
      var btn = frameDoc.createElement("button")
      btn.className = b.cls
      btn.textContent = b.label
      btn.dataset.itemId = item.id
      btn.style.cssText = btnStyle
      ;(function (message) {
        btn.addEventListener("click", function (e) {
          e.stopPropagation()
          window.postMessage(message, "*")
        })
      })(b.msg)
      grid.appendChild(btn)
    }

    return grid
  }

  function createBadge(text, color) {
    var badge = document.createElement("span")
    badge.className = "agent-overlay-badge"
    badge.textContent = text
    badge.style.cssText =
      "display:inline-block;font-size:10px;font-weight:600;padding:1px 6px;" +
      "border-radius:4px;margin-left:6px;vertical-align:middle;" +
      "background:" + color + ";color:#fff;"
    return badge
  }

  function buildDisplayTitle(item) {
    if (!item.proposed_value) return item.description || ""
    var pv = item.proposed_value
    var title = pv.code_text || pv.title || pv.display || ""
    if (pv.code && title) {
      title += " (" + pv.code + ")"
    }
    if (title) return title

    // Medication-specific: compose from drug + dose + route + freq
    if (pv.drug || pv.dose || pv.freq || pv.route) {
      var parts = []
      if (pv.drug) parts.push(pv.drug)
      if (pv.dose) parts.push(pv.dose)
      if (pv.route) parts.push(pv.route)
      if (pv.freq) parts.push(pv.freq)
      if (parts.length > 0) return parts.join(" ")
    }

    return item.description || ""
  }

  function buildProposedRowText(currentText, item) {
    var pv = item.proposed_value
    if (!pv) return null

    var fullTitle = pv.code_text || pv.title || pv.display
    if (fullTitle) {
      if (pv.code) fullTitle += " (" + pv.code + ")"
      return fullTitle
    }

    // Medication-specific: drug name from proposed_value or from existing row
    if (pv.dose || pv.freq || pv.route || pv.drug) {
      var drug = pv.drug || currentText.split(/\s+/)[0] || ""
      var parts = [drug]
      if (pv.dose) parts.push(pv.dose)
      if (pv.route) parts.push(pv.route)
      if (pv.freq) parts.push(pv.freq)
      return parts.join(" ")
    }

    return null
  }

  function wordDiff(oldText, newText) {
    var oldWords = oldText.split(/\s+/).filter(Boolean)
    var newWords = newText.split(/\s+/).filter(Boolean)

    // Common prefix
    var i = 0
    while (i < oldWords.length && i < newWords.length && oldWords[i] === newWords[i]) i++

    // Common suffix
    var j = 0
    while (
      j < oldWords.length - i &&
      j < newWords.length - i &&
      oldWords[oldWords.length - 1 - j] === newWords[newWords.length - 1 - j]
    ) j++

    return {
      same: oldWords.slice(0, i).join(" "),
      removed: oldWords.slice(i, oldWords.length - j).join(" "),
      added: newWords.slice(i, newWords.length - j).join(" "),
      suffix: j > 0 ? oldWords.slice(oldWords.length - j).join(" ") : "",
    }
  }

  function renderWordDiff(frameDoc, oldText, newText) {
    var diff = wordDiff(oldText, newText)
    var container = frameDoc.createElement("div")
    container.className = "agent-overlay-diff"
    container.style.cssText = "margin-top:2px;font-size:12px;padding-left:8px;"

    var arrow = frameDoc.createElement("span")
    arrow.textContent = "→ "
    arrow.style.color = "#6b7280"
    container.appendChild(arrow)

    if (diff.same) {
      var sameSpan = frameDoc.createElement("span")
      sameSpan.textContent = diff.same + " "
      container.appendChild(sameSpan)
    }

    if (diff.removed) {
      var delSpan = frameDoc.createElement("span")
      delSpan.style.cssText = "text-decoration:line-through;opacity:0.6;"
      delSpan.textContent = diff.removed
      container.appendChild(delSpan)
      container.appendChild(frameDoc.createTextNode(" "))
    }

    if (diff.added) {
      var addSpan = frameDoc.createElement("span")
      addSpan.style.cssText =
        "font-weight:600;background:#D1FAE5;padding:0 3px;border-radius:2px;"
      addSpan.textContent = diff.added
      container.appendChild(addSpan)
    }

    if (diff.suffix) {
      container.appendChild(frameDoc.createTextNode(" " + diff.suffix))
    }

    return container
  }

  function applyCreateOverlay(item, mapping) {
    var frameDoc = getFrameDocument(mapping.tab)
    if (!frameDoc) return { applied: false, reason: "Frame not available" }

    var container = getContainerEl(frameDoc, mapping.container)
    if (!container) return { applied: false, reason: "Container not found" }

    var listGroup = container.querySelector(".list-group")
    if (!listGroup) {
      listGroup = container
    }

    // Build a ghost row that matches the real EMR row structure
    var ghost = frameDoc.createElement("div")
    ghost.className = "list-group-item p-1 agent-overlay-ghost"
    ghost.style.cssText =
      "background:#ECFDF5;border-left:3px solid #10b981;opacity:0.85;"

    var summary = frameDoc.createElement("div")
    summary.className = "summary m-0 p-0 d-flex w-100 align-content-center"

    var fill = frameDoc.createElement("div")
    fill.className = "flex-fill pl-2"

    var titleEl = frameDoc.createElement("span")
    titleEl.className = "font-weight-bold"
    titleEl.textContent = buildDisplayTitle(item)
    fill.appendChild(titleEl)

    var statusSpan = frameDoc.createElement("span")
    statusSpan.textContent = " (Pending)"
    statusSpan.style.cssText = "font-style:italic;color:#6b7280;"
    fill.appendChild(statusSpan)

    summary.appendChild(fill)
    summary.appendChild(createActionButtons(frameDoc, item))
    ghost.appendChild(summary)

    listGroup.insertBefore(ghost, listGroup.firstChild)
    injectedElements.push({ element: ghost, frameDoc: frameDoc })
    scrollIntoView(ghost)

    return { applied: true }
  }

  function applyUpdateOverlay(item, mapping) {
    var uuid = extractUuid(item.target_resource_id)
    var frameDoc = getFrameDocument(mapping.tab)
    if (!frameDoc) return { applied: false, reason: "Frame not available" }

    var row = findRowByUuid(frameDoc, mapping.container, uuid)
    if (!row) return { applied: false, reason: "Row not found for UUID " + uuid }

    row.dataset.originalBg = row.style.background || ""
    row.dataset.originalBorderLeft = row.style.borderLeft || ""
    row.style.background = "#ECFDF5"
    row.style.borderLeft = "3px solid #10b981"

    // Read current text straight from the DOM row
    var currentText = row.textContent.trim()
    var proposedText = buildProposedRowText(currentText, item)

    if (proposedText && proposedText !== currentText) {
      var diffEl = renderWordDiff(frameDoc, currentText, proposedText)
      row.appendChild(diffEl)
      injectedElements.push({ element: diffEl, frameDoc: frameDoc })
    }

    var actionsEl = createActionButtons(frameDoc, item)
    row.appendChild(actionsEl)
    injectedElements.push({ element: actionsEl, frameDoc: frameDoc })

    injectedElements.push({ element: row, frameDoc: frameDoc, restoreBg: true })
    scrollIntoView(row)

    return { applied: true }
  }

  function applyDeleteOverlay(item, mapping) {
    var uuid = extractUuid(item.target_resource_id)
    var frameDoc = getFrameDocument(mapping.tab)
    if (!frameDoc) return { applied: false, reason: "Frame not available" }

    var row = findRowByUuid(frameDoc, mapping.container, uuid)
    if (!row) return { applied: false, reason: "Row not found for UUID " + uuid }

    row.dataset.originalBg = row.style.background || ""
    row.dataset.originalTextDecoration = row.style.textDecoration || ""
    row.dataset.originalOpacity = row.style.opacity || ""
    row.style.background = "#FEE2E2"
    row.style.textDecoration = "line-through"
    row.style.opacity = "0.6"

    var badge = createBadge("Remove", "#dc2626")
    row.appendChild(badge)
    injectedElements.push({ element: badge, frameDoc: frameDoc })

    var actionsEl = createActionButtons(frameDoc, item)
    row.appendChild(actionsEl)
    injectedElements.push({ element: actionsEl, frameDoc: frameDoc })

    injectedElements.push({ element: row, frameDoc: frameDoc, restoreDelete: true })
    scrollIntoView(row)

    return { applied: true }
  }

  function scrollIntoView(el) {
    try {
      var win = (el.ownerDocument && el.ownerDocument.defaultView) || window
      ;(win.requestAnimationFrame || setTimeout)(function () {
        try {
          el.scrollIntoView({ behavior: "smooth", block: "center" })
        } catch (_e) {}
      })
    } catch (_e) {
      // ignore
    }
  }

  function getCurrentPatientPid() {
    try {
      var topWin = window.top || window
      var appData = topWin.app_view_model && topWin.app_view_model.application_data
      if (!appData) return null
      var patient = typeof appData.patient === "function" ? appData.patient() : null
      if (!patient) return null
      var pid = typeof patient.pid === "function" ? patient.pid() : null
      if (pid == null || pid === "") return null
      return String(pid)
    } catch (_e) {
      return null
    }
  }

  function ensurePatientLoaded(patientID, callback) {
    if (!patientID) { callback(); return }

    var target = String(patientID)
    var currentPid = getCurrentPatientPid()
    if (currentPid === target) { callback(); return }

    var topWin = window.top || window
    if (typeof topWin.navigateTab !== "function") { callback(); return }

    var webroot = topWin.webroot_url || ""
    var url = webroot + "/interface/patient_file/summary/demographics.php?set_pid=" + encodeURIComponent(patientID)

    topWin.navigateTab(url, "pat", function () {
      if (typeof topWin.activateTabByName === "function") {
        topWin.activateTabByName("pat", true)
      }
    })

    var attempts = 0
    var poll = setInterval(function () {
      attempts++
      if (getCurrentPatientPid() === target || attempts >= 30) {
        clearInterval(poll)
        setTimeout(callback, 300)
      }
    }, 200)
  }

  function clearAllOverlays() {
    for (var i = injectedElements.length - 1; i >= 0; i--) {
      var entry = injectedElements[i]
      if (entry.restoreBg) {
        entry.element.style.background = entry.element.dataset.originalBg || ""
        entry.element.style.borderLeft = entry.element.dataset.originalBorderLeft || ""
        delete entry.element.dataset.originalBg
        delete entry.element.dataset.originalBorderLeft
      } else if (entry.restoreDelete) {
        entry.element.style.background = entry.element.dataset.originalBg || ""
        entry.element.style.textDecoration = entry.element.dataset.originalTextDecoration || ""
        entry.element.style.opacity = entry.element.dataset.originalOpacity || ""
        delete entry.element.dataset.originalBg
        delete entry.element.dataset.originalTextDecoration
        delete entry.element.dataset.originalOpacity
      } else {
        if (entry.element.parentNode) {
          entry.element.parentNode.removeChild(entry.element)
        }
      }
    }
    injectedElements = []
  }

  function navigateToTab(tabName, patientID) {
    try {
      var topWin = window.top || window

      var hasTab = false
      try {
        hasTab = !!topWin.document.querySelector("iframe[name='" + tabName + "']")
      } catch (_e) {}

      if (hasTab) {
        if (typeof topWin.activateTabByName === "function") {
          topWin.activateTabByName(tabName, true)
        }
        return
      }

      if (patientID && typeof topWin.navigateTab === "function") {
        var webroot = topWin.webroot_url || ""
        var url = webroot + "/interface/patient_file/summary/demographics.php?set_pid=" + encodeURIComponent(patientID)
        topWin.navigateTab(url, "pat", function () {
          if (typeof topWin.activateTabByName === "function") {
            topWin.activateTabByName("pat", true)
          }
        })
      }
    } catch (_e) {
      // cross-origin or function not available
    }
  }

  function applySingleOverlay(item) {
    var mapping = RESOURCE_PAGE_MAP[item.resource_type]
    if (!mapping) {
      return { applied: false, reason: "Unknown resource type: " + item.resource_type }
    }

    if (item.action === "create") {
      return applyCreateOverlay(item, mapping)
    }
    if (item.action === "update") {
      return applyUpdateOverlay(item, mapping)
    }
    if (item.action === "delete") {
      return applyDeleteOverlay(item, mapping)
    }

    return { applied: false, reason: "Unknown action: " + item.action }
  }

  function applyAllOverlays(items, focusIndex, patientID) {
    clearAllOverlays()

    var focusedItem = items[focusIndex]
    if (focusedItem) {
      var focusedMapping = RESOURCE_PAGE_MAP[focusedItem.resource_type]
      if (focusedMapping && focusedMapping.tab) {
        navigateToTab(focusedMapping.tab, patientID)
      }
    }

    var results = []
    for (var i = 0; i < items.length; i++) {
      var result = applySingleOverlay(items[i])
      results.push({
        itemId: items[i].id,
        applied: result.applied,
        reason: result.reason || null,
      })
    }
    return results
  }

  function applyOverlay(item) {
    var mapping = RESOURCE_PAGE_MAP[item.resource_type]
    if (!mapping) {
      return { applied: false, reason: "Unknown resource type: " + item.resource_type }
    }

    clearAllOverlays()
    navigateToTab(mapping.tab)

    if (item.action === "create") {
      return applyCreateOverlay(item, mapping)
    }
    if (item.action === "update") {
      return applyUpdateOverlay(item, mapping)
    }
    if (item.action === "delete") {
      return applyDeleteOverlay(item, mapping)
    }

    return { applied: false, reason: "Unknown action: " + item.action }
  }

  window.addEventListener("message", function (event) {
    if (!event.data || typeof event.data.type !== "string") return
    if (!event.data.type.startsWith("overlay:")) return

    if (event.data.type === "overlay:apply") {
      var result = applyOverlay(event.data.item)
      if (event.source) {
        event.source.postMessage({
          type: "overlay:result",
          itemId: event.data.item.id,
          applied: result.applied,
          reason: result.reason || null,
        }, "*")
      }
    }

    if (event.data.type === "overlay:applyAll") {
      var items = event.data.items || []
      var focusIndex = event.data.focusIndex || 0
      var patientID = event.data.patientID || null
      var source = event.source

      ensurePatientLoaded(patientID, function () {
        var results = applyAllOverlays(items, focusIndex, patientID)
        if (source) {
          source.postMessage({
            type: "overlay:allResults",
            results: results,
          }, "*")
        }
      })
    }

    if (event.data.type === "overlay:clear") {
      clearAllOverlays()
    }
  })

  window.__overlayEngine = {
    applyOverlay: applyOverlay,
    applyAllOverlays: applyAllOverlays,
    clearAllOverlays: clearAllOverlays,
    navigateToTab: navigateToTab,
    ensurePatientLoaded: ensurePatientLoaded,
    getCurrentPatientPid: getCurrentPatientPid,
    RESOURCE_PAGE_MAP: RESOURCE_PAGE_MAP,
  }
})()
