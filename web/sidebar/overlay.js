(function initOverlayEngine() {
  // Guard against double-loading (e.g. embed.js injected twice via ob_start + StyleFilterEvent)
  if (window.__overlayEngine) return

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
    Encounter: { tab: "enc", container: ".table.jumbotron", navigateUrl: "/interface/patient_file/history/encounters.php", supportsRowTarget: true },
    Observation: { tab: "enc", nestedFrame: "enc-forms", container: "#partable", supportsRowTarget: true },
    // NB: #vitals_ps_expand content is async-loaded via placeHtml("vitals_fragment.php").
    // If vitals overlays break on initial load, check timing against that async fetch.
    Vital: { tab: "pat", container: "#vitals_ps_expand", supportsRowTarget: true },
    SoapNote: { tab: "enc", nestedFrame: "enc-forms", container: "#partable", supportsRowTarget: true },
    Procedure: { tab: "enc", nestedFrame: "enc-forms", container: "#partable", supportsRowTarget: true },
    DiagnosticReport: { tab: "enc", container: null, rowSelector: null, supportsRowTarget: false },
  }

  var injectedElements = []
  var currentManifestItems = null

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

  function getFrameDocumentForMapping(mapping) {
    var frameDoc = getFrameDocument(mapping.tab)
    if (!frameDoc || !mapping.nestedFrame) return frameDoc
    try {
      var innerFrame = frameDoc.querySelector("iframe[name='" + mapping.nestedFrame + "']")
      if (!innerFrame) {
        // Named iframe not found — encounter_top.php renders the forms.php
        // Summary tab via TabsWrapper without a name attribute on the iframe.
        // Look for the iframe inside the active tab pane instead.
        innerFrame = frameDoc.querySelector(".tab-pane.active iframe")
          || frameDoc.querySelector(".tab-pane iframe")
      }
      if (innerFrame) {
        if (innerFrame.contentDocument) {
          return innerFrame.contentDocument
        }
        // Iframe exists but contentDocument not ready (still loading) — don't
        // fall back to parent doc since the target container won't be there
        return null
      }
    } catch (_e) {}
    // Nested iframe element doesn't exist at all (e.g. dojo harness or
    // simplified layouts without inner iframes) — fall back to parent frame
    return frameDoc
  }

  function ensureCardExpanded(containerEl) {
    if (!containerEl) return
    var card = containerEl.closest(".card")
    if (!card) return
    var collapse = card.querySelector(".collapse")
    if (collapse && !collapse.classList.contains("show")) {
      // Prefer Bootstrap's jQuery API when available (handles events + transitions)
      var frameWin = containerEl.ownerDocument && containerEl.ownerDocument.defaultView
      if (frameWin && frameWin.jQuery) {
        frameWin.jQuery(collapse).collapse("show")
      } else {
        collapse.classList.add("show")
        var toggle = card.querySelector("[data-toggle='collapse']")
        if (toggle) {
          toggle.setAttribute("aria-expanded", "true")
          toggle.classList.remove("collapsed")
        }
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

  function allItemsReviewed(items) {
    if (!items || !items.length) return false
    for (var i = 0; i < items.length; i++) {
      if (items[i].status !== "approved" && items[i].status !== "rejected") return false
    }
    return true
  }

  function createActionButtons(frameDoc, item, isFocused, allItems) {
    var container = frameDoc.createElement("div")
    container.className = "agent-overlay-actions"
    container.style.cssText =
      "display:flex;flex-direction:column;align-items:flex-end;" +
      "margin-left:auto;flex-shrink:0;padding:2px;width:fit-content;"

    var grid = frameDoc.createElement("div")
    grid.style.cssText =
      "display:grid;grid-template-columns:auto auto;gap:2px;"

    var btnStyle =
      "border:1px solid #d1d5db;border-radius:3px;cursor:pointer;" +
      "padding:1px 6px;background:#fff;color:#374151;" +
      "line-height:1.4;"

    var buttons = [
      { label: "\u2705", cls: "overlay-btn-accept", msg: { type: "overlay:accept", itemId: item.id }, style: btnStyle + "font-size:16px;" },
      { label: "\uD83D\uDEAB", cls: "overlay-btn-reject", msg: { type: "overlay:reject", itemId: item.id }, style: btnStyle + "font-size:16px;" },
    ]

    if (isFocused) {
      buttons.push(
        { label: "\u2039", cls: "overlay-btn-prev",   msg: { type: "overlay:navigate", delta: -1 }, style: btnStyle + "font-size:18px;font-weight:bold;" },
        { label: "\u203A", cls: "overlay-btn-next",   msg: { type: "overlay:navigate", delta: 1 }, style: btnStyle + "font-size:18px;font-weight:bold;" }
      )
    }

    for (var i = 0; i < buttons.length; i++) {
      var b = buttons[i]
      var btn = frameDoc.createElement("button")
      btn.className = b.cls
      btn.textContent = b.label
      btn.dataset.itemId = item.id
      btn.style.cssText = b.style
      ;(function (message) {
        btn.addEventListener("click", function (e) {
          e.stopPropagation()
          window.postMessage(message, "*")
        })
      })(b.msg)
      grid.appendChild(btn)
    }

    container.appendChild(grid)

    if (isFocused && allItems && allItemsReviewed(allItems)) {
      var hasApproved = false
      for (var j = 0; j < allItems.length; j++) {
        if (allItems[j].status === "approved") { hasApproved = true; break }
      }
      var execBtn = frameDoc.createElement("button")
      execBtn.className = "overlay-btn-execute"
      execBtn.textContent = hasApproved ? "Execute Changes" : "Discard All"
      execBtn.style.cssText =
        "border:none;border-radius:4px;cursor:pointer;" +
        "font-size:13px;font-weight:600;padding:4px 12px;margin-top:4px;" +
        "background:#0f766e;color:#fff;line-height:1.4;white-space:nowrap;"
      execBtn.addEventListener("click", function (e) {
        e.stopPropagation()
        window.postMessage({ type: "overlay:execute" }, "*")
      })
      container.appendChild(execBtn)
    }

    return container
  }

  function createBadge(doc, text, color) {
    var badge = doc.createElement("span")
    badge.className = "agent-overlay-badge"
    badge.textContent = text
    badge.style.cssText =
      "display:inline-block;font-size:10px;font-weight:600;padding:1px 6px;" +
      "border-radius:4px;margin-left:6px;vertical-align:middle;" +
      "background:" + color + ";color:#fff;"
    return badge
  }

  function createStatusBadge(doc, status) {
    if (status === "approved") return createBadge(doc, "\u2713 Approved", "#15803d")
    if (status === "rejected") return createBadge(doc, "\u2717 Rejected", "#b91c1c")
    return null
  }

  function createConfidenceBadge(doc, confidence, status) {
    if (status === "approved" || status === "rejected") return null
    if (!confidence || confidence === "high") return null
    if (confidence === "medium") return createBadge(doc, "\u26A0 Review suggested", "#d97706")
    if (confidence === "low") return createBadge(doc, "\u26A0 Needs review", "#b91c1c")
    return null
  }

  function applyFocusStyle(el) {
    el.style.boxShadow = "0 0 0 2px #0f766e, 0 0 8px rgba(15, 118, 110, 0.25)"
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

  function applyCreateOverlay(item, mapping, isFocused) {
    // SoapNote handles its own frameDoc lookup (may co-locate with encounter ghost)
    if (item.resource_type === "SoapNote") {
      return applyCreateOverlaySoap(item, mapping, isFocused)
    }
    if (item.resource_type === "Procedure") {
      return applyCreateOverlayProcedure(item, mapping, isFocused)
    }
    if (item.resource_type === "Observation") {
      return applyCreateOverlayObservation(item, mapping, isFocused)
    }

    var frameDoc = getFrameDocumentForMapping(mapping)
    if (!frameDoc) return { applied: false, reason: "Frame not available" }

    if (item.resource_type === "Encounter") {
      return applyCreateOverlayEncounter(frameDoc, item, isFocused)
    }
    if (item.resource_type === "Vital") {
      return applyCreateOverlayVital(frameDoc, item, isFocused)
    }

    var container = getContainerEl(frameDoc, mapping.container)
    if (!container) return { applied: false, reason: "Container not found" }

    var listGroup = container.querySelector(".list-group")
    if (!listGroup) {
      listGroup = container
    }

    // Build a ghost row that matches the real EMR row structure
    var ghost = frameDoc.createElement("div")
    ghost.className = "list-group-item p-1 agent-overlay-ghost"
    ghost.dataset.itemId = item.id
    if (item.status === "rejected") {
      ghost.style.cssText = "background:#f9fafb;border-left:3px solid #d1d5db;opacity:0.5;"
    } else if (item.status === "approved") {
      ghost.style.cssText = "background:#f0fdf4;border-left:3px solid #15803d;opacity:0.85;"
    } else {
      ghost.style.cssText = "background:#ECFDF5;border-left:3px solid #10b981;opacity:0.85;"
    }

    if (isFocused) applyFocusStyle(ghost)

    var summary = frameDoc.createElement("div")
    summary.className = "summary m-0 p-0 d-flex w-100 align-content-center"

    var fill = frameDoc.createElement("div")
    fill.className = "flex-fill pl-2"

    var titleEl = frameDoc.createElement("span")
    titleEl.className = "font-weight-bold"
    titleEl.textContent = buildDisplayTitle(item)
    fill.appendChild(titleEl)

    var statusBadge = createStatusBadge(frameDoc, item.status)
    if (statusBadge) {
      fill.appendChild(statusBadge)
    } else {
      var pendingSpan = frameDoc.createElement("span")
      pendingSpan.textContent = " (Pending)"
      pendingSpan.style.cssText = "font-style:italic;color:#6b7280;"
      fill.appendChild(pendingSpan)
    }

    var confBadge = createConfidenceBadge(frameDoc, item.confidence, item.status)
    if (confBadge) fill.appendChild(confBadge)

    summary.appendChild(fill)
    summary.appendChild(createActionButtons(frameDoc, item, isFocused, currentManifestItems))
    ghost.appendChild(summary)

    listGroup.insertBefore(ghost, listGroup.firstChild)
    injectedElements.push({ element: ghost, frameDoc: frameDoc })
    if (isFocused) scrollIntoView(ghost)

    return { applied: true }
  }

  function applyUpdateOverlay(item, mapping, isFocused) {
    var uuid = extractUuid(item.target_resource_id)
    var frameDoc = getFrameDocumentForMapping(mapping)
    if (!frameDoc) return { applied: false, reason: "Frame not available" }

    var row = findRowByUuid(frameDoc, mapping.container, uuid)
    if (!row) return { applied: false, reason: "Row not found for UUID " + uuid }

    row.dataset.originalBg = row.style.background || ""
    row.dataset.originalBorderLeft = row.style.borderLeft || ""
    row.dataset.originalOpacity = row.style.opacity || ""
    row.dataset.originalBoxShadow = row.style.boxShadow || ""
    if (item.status === "rejected") {
      row.style.background = "#f9fafb"
      row.style.borderLeft = "3px solid #d1d5db"
      row.style.opacity = "0.5"
    } else if (item.status === "approved") {
      row.style.background = "#f0fdf4"
      row.style.borderLeft = "3px solid #15803d"
    } else {
      row.style.background = "#FFFBEB"
      row.style.borderLeft = "3px solid #d97706"
    }

    if (isFocused) applyFocusStyle(row)

    // Read current text straight from the DOM row
    var currentText = row.textContent.trim()
    var proposedText = buildProposedRowText(currentText, item)

    // Wrap existing content + actions in a flex layout matching creation overlay
    var wrapper = frameDoc.createElement("div")
    wrapper.style.cssText = "display:flex;align-items:center;width:100%;"

    var contentDiv = frameDoc.createElement("div")
    contentDiv.style.cssText = "flex:1 1 auto;min-width:0;"
    while (row.firstChild) {
      contentDiv.appendChild(row.firstChild)
    }

    if (proposedText && proposedText !== currentText) {
      var diffEl = renderWordDiff(frameDoc, currentText, proposedText)
      contentDiv.appendChild(diffEl)
      injectedElements.push({ element: diffEl, frameDoc: frameDoc })
    }

    var statusBadge = createStatusBadge(frameDoc, item.status)
    if (statusBadge) {
      contentDiv.appendChild(statusBadge)
      injectedElements.push({ element: statusBadge, frameDoc: frameDoc })
    }

    var confBadge2 = createConfidenceBadge(frameDoc, item.confidence, item.status)
    if (confBadge2) {
      contentDiv.appendChild(confBadge2)
      injectedElements.push({ element: confBadge2, frameDoc: frameDoc })
    }

    wrapper.appendChild(contentDiv)
    wrapper.appendChild(createActionButtons(frameDoc, item, isFocused, currentManifestItems))
    row.appendChild(wrapper)
    injectedElements.push({ element: wrapper, frameDoc: frameDoc, unwrap: true })

    injectedElements.push({ element: row, frameDoc: frameDoc, restoreBg: true })
    if (isFocused) scrollIntoView(row)

    return { applied: true }
  }

  function applyDeleteOverlay(item, mapping, isFocused) {
    var uuid = extractUuid(item.target_resource_id)
    var frameDoc = getFrameDocumentForMapping(mapping)
    if (!frameDoc) return { applied: false, reason: "Frame not available" }

    var row = findRowByUuid(frameDoc, mapping.container, uuid)
    if (!row) return { applied: false, reason: "Row not found for UUID " + uuid }

    row.dataset.originalBg = row.style.background || ""
    row.dataset.originalBorderLeft = row.style.borderLeft || ""
    row.dataset.originalTextDecoration = row.style.textDecoration || ""
    row.dataset.originalOpacity = row.style.opacity || ""
    row.dataset.originalBoxShadow = row.style.boxShadow || ""

    if (item.status === "rejected") {
      row.style.background = "#f9fafb"
      row.style.borderLeft = "3px solid #d1d5db"
      row.style.opacity = "0.5"
    } else if (item.status === "approved") {
      row.style.background = "#f0fdf4"
      row.style.borderLeft = "3px solid #15803d"
      row.style.textDecoration = "line-through"
      row.style.opacity = "0.6"
    } else {
      row.style.background = "#FEE2E2"
      row.style.textDecoration = "line-through"
      row.style.opacity = "0.6"
    }

    if (isFocused) applyFocusStyle(row)

    // Wrap existing content + actions in a flex layout matching creation overlay
    var wrapper = frameDoc.createElement("div")
    wrapper.style.cssText = "display:flex;align-items:center;width:100%;"

    var contentDiv = frameDoc.createElement("div")
    contentDiv.style.cssText = "flex:1 1 auto;min-width:0;"
    while (row.firstChild) {
      contentDiv.appendChild(row.firstChild)
    }

    var statusBadge = createStatusBadge(frameDoc, item.status)
    if (statusBadge) {
      contentDiv.appendChild(statusBadge)
      injectedElements.push({ element: statusBadge, frameDoc: frameDoc })
    }

    var confBadge3 = createConfidenceBadge(frameDoc, item.confidence, item.status)
    if (confBadge3) {
      contentDiv.appendChild(confBadge3)
      injectedElements.push({ element: confBadge3, frameDoc: frameDoc })
    }

    wrapper.appendChild(contentDiv)
    wrapper.appendChild(createActionButtons(frameDoc, item, isFocused, currentManifestItems))
    row.appendChild(wrapper)
    injectedElements.push({ element: wrapper, frameDoc: frameDoc, unwrap: true })

    injectedElements.push({ element: row, frameDoc: frameDoc, restoreDelete: true })
    if (isFocused) scrollIntoView(row)

    return { applied: true }
  }

  function buildFormHolderGhost(frameDoc, item, isFocused, title, fields) {
    var ghost = frameDoc.createElement("div")
    ghost.className = "form-holder agent-overlay-ghost"
    ghost.dataset.itemId = item.id
    if (item.status === "rejected") {
      ghost.style.cssText = "background:#f9fafb;border-left:3px solid #d1d5db;opacity:0.5;"
    } else if (item.status === "approved") {
      ghost.style.cssText = "background:#f0fdf4;border-left:3px solid #15803d;opacity:0.85;"
    } else {
      ghost.style.cssText = "background:#ECFDF5;border-left:3px solid #10b981;opacity:0.85;"
    }
    if (isFocused) applyFocusStyle(ghost)

    var header = frameDoc.createElement("div")
    header.className = "form-header border-bottom border-dark w-100 d-flex align-items-center justify-content-between"

    var headerFill = frameDoc.createElement("div")
    headerFill.className = "form_header flex-fill pl-2"
    var h5 = frameDoc.createElement("h5")
    h5.className = "mb-0"
    h5.appendChild(frameDoc.createTextNode(title + " "))
    var small = frameDoc.createElement("small")
    small.className = "text-muted"
    small.textContent = "(Proposed)"
    h5.appendChild(small)

    var statusBadge = createStatusBadge(frameDoc, item.status)
    if (statusBadge) {
      h5.appendChild(statusBadge)
    } else {
      var pendingSpan = frameDoc.createElement("span")
      pendingSpan.textContent = " (Pending)"
      pendingSpan.style.cssText = "font-style:italic;color:#6b7280;"
      h5.appendChild(pendingSpan)
    }
    var confBadge = createConfidenceBadge(frameDoc, item.confidence, item.status)
    if (confBadge) h5.appendChild(confBadge)

    headerFill.appendChild(h5)
    header.appendChild(headerFill)
    header.appendChild(createActionButtons(frameDoc, item, isFocused, currentManifestItems))
    ghost.appendChild(header)

    var detail = frameDoc.createElement("div")
    detail.className = "form-detail formrow"
    var detailInner = frameDoc.createElement("div")
    detailInner.className = "mb-5"

    var table = frameDoc.createElement("table")
    for (var i = 0; i < fields.length; i++) {
      if (!fields[i].value) continue
      var tr = frameDoc.createElement("tr")
      var td = frameDoc.createElement("td")
      var labelSpan = frameDoc.createElement("span")
      labelSpan.className = "bold"
      labelSpan.textContent = fields[i].label + ": "
      td.appendChild(labelSpan)
      var valueSpan = frameDoc.createElement("span")
      valueSpan.className = "text"
      valueSpan.textContent = fields[i].value
      td.appendChild(valueSpan)
      tr.appendChild(td)
      table.appendChild(tr)
    }

    detailInner.appendChild(table)
    detail.appendChild(detailInner)
    ghost.appendChild(detail)
    return ghost
  }

  function findPendingEncounterDependency(item) {
    if (!item.depends_on || !item.depends_on.length || !currentManifestItems) return null
    for (var i = 0; i < item.depends_on.length; i++) {
      for (var j = 0; j < currentManifestItems.length; j++) {
        if (currentManifestItems[j].id === item.depends_on[i] &&
            currentManifestItems[j].resource_type === "Encounter" &&
            currentManifestItems[j].action === "create") {
          return currentManifestItems[j]
        }
      }
    }
    return null
  }

  function findGhostByItemId(itemId) {
    var frameNames = ["pat", "enc"]
    for (var f = 0; f < frameNames.length; f++) {
      var fd = getFrameDocument(frameNames[f])
      if (!fd) continue
      var ghost = fd.querySelector('.agent-overlay-ghost[data-item-id="' + itemId + '"]')
      if (ghost) return { element: ghost, frameDoc: fd }
    }
    return null
  }

  function buildCompactSoapGhost(doc, item, isFocused) {
    var pv = item.proposed_value || {}
    var wrapper = doc.createElement("div")
    wrapper.className = "agent-overlay-ghost agent-overlay-nested-soap"
    wrapper.dataset.itemId = item.id
    wrapper.style.cssText =
      "margin:8px 4px;padding:8px;background:#f0fdfa;border-left:2px solid #0d9488;" +
      "border-radius:4px;font-size:12px;"
    if (item.status === "rejected") {
      wrapper.style.background = "#f9fafb"
      wrapper.style.borderLeftColor = "#d1d5db"
      wrapper.style.opacity = "0.5"
    } else if (item.status === "approved") {
      wrapper.style.background = "#f0fdf4"
      wrapper.style.borderLeftColor = "#15803d"
    }
    if (isFocused) applyFocusStyle(wrapper)

    var headerRow = doc.createElement("div")
    headerRow.style.cssText = "display:flex;align-items:center;margin-bottom:4px;"

    var titleEl = doc.createElement("div")
    titleEl.style.cssText = "flex:1 1 auto;font-weight:600;color:#0f766e;"
    titleEl.textContent = "SOAP Note (Proposed)"
    var statusBadge = createStatusBadge(doc, item.status)
    if (statusBadge) titleEl.appendChild(statusBadge)
    var confBadge = createConfidenceBadge(doc, item.confidence, item.status)
    if (confBadge) titleEl.appendChild(confBadge)
    headerRow.appendChild(titleEl)
    headerRow.appendChild(createActionButtons(doc, item, isFocused, currentManifestItems))
    wrapper.appendChild(headerRow)

    var fields = [
      { label: "S", value: pv.subjective },
      { label: "O", value: pv.objective },
      { label: "A", value: pv.assessment },
      { label: "P", value: pv.plan },
    ]
    var table = doc.createElement("table")
    table.style.cssText = "width:100%;border-collapse:collapse;"
    for (var i = 0; i < fields.length; i++) {
      if (!fields[i].value) continue
      var tr = doc.createElement("tr")
      var tdLabel = doc.createElement("td")
      tdLabel.style.cssText =
        "font-weight:600;vertical-align:top;padding:1px 4px 1px 0;width:1%;white-space:nowrap;color:#374151;"
      tdLabel.textContent = fields[i].label + ":"
      var tdValue = doc.createElement("td")
      tdValue.style.cssText = "padding:1px 0;color:#4b5563;"
      var val = fields[i].value
      tdValue.textContent = val.length > 120 ? val.substring(0, 120) + "\u2026" : val
      tr.appendChild(tdLabel)
      tr.appendChild(tdValue)
      table.appendChild(tr)
    }
    wrapper.appendChild(table)
    return wrapper
  }

  function applyCreateOverlaySoap(item, mapping, isFocused) {
    // Part 2: Co-locate with pending Encounter ghost if depends_on references one
    var pendingEnc = findPendingEncounterDependency(item)
    if (pendingEnc) {
      var ghostMatch = findGhostByItemId(pendingEnc.id)
      if (ghostMatch) {
        var compact = buildCompactSoapGhost(ghostMatch.frameDoc, item, isFocused)
        ghostMatch.element.appendChild(compact)
        injectedElements.push({ element: compact, frameDoc: ghostMatch.frameDoc })
        if (isFocused) scrollIntoView(compact)
        return { applied: true }
      }
      return { applied: false, reason: "Will be created inside the new encounter" }
    }

    // Part 1: Normal path — encounter should already be loaded
    var frameDoc = getFrameDocumentForMapping(mapping)
    if (!frameDoc) return { applied: false, reason: "Frame not available" }

    var container = frameDoc.querySelector("#partable")
    if (!container) return { applied: false, reason: "Container #partable not found" }

    var pv = item.proposed_value || {}
    var ghost = buildFormHolderGhost(frameDoc, item, isFocused, "SOAP", [
      { label: "Subjective", value: pv.subjective },
      { label: "Objective", value: pv.objective },
      { label: "Assessment", value: pv.assessment },
      { label: "Plan", value: pv.plan },
    ])

    container.insertBefore(ghost, container.firstChild)
    injectedElements.push({ element: ghost, frameDoc: frameDoc })
    if (isFocused) scrollIntoView(ghost)
    return { applied: true }
  }

  function applyCreateOverlayProcedure(item, mapping, isFocused) {
    var frameDoc = getFrameDocumentForMapping(mapping)
    if (!frameDoc) return { applied: false, reason: "Frame not available" }

    var container = frameDoc.querySelector("#partable")
    if (!container) return { applied: false, reason: "Container #partable not found" }

    var pv = item.proposed_value || {}
    var fields = []
    if (pv.code_text || pv.display) fields.push({ label: "Procedure", value: pv.code_text || pv.display })
    if (pv.code) fields.push({ label: "Code", value: pv.code })
    if (pv.date) fields.push({ label: "Date", value: pv.date })
    if (pv.note) fields.push({ label: "Notes", value: pv.note })

    var ghost = buildFormHolderGhost(frameDoc, item, isFocused, "Procedure", fields)

    container.insertBefore(ghost, container.firstChild)
    injectedElements.push({ element: ghost, frameDoc: frameDoc })
    if (isFocused) scrollIntoView(ghost)
    return { applied: true }
  }

  function applyCreateOverlayObservation(item, mapping, isFocused) {
    var frameDoc = getFrameDocumentForMapping(mapping)
    if (!frameDoc) return { applied: false, reason: "Frame not available" }

    var container = frameDoc.querySelector("#partable")
    if (!container) return { applied: false, reason: "Container #partable not found" }

    var pv = item.proposed_value || {}
    var fields = []
    if (pv.code_text) {
      fields.push({ label: "Test", value: pv.code_text })
      if (pv.display) fields.push({ label: "Result", value: pv.display })
    } else if (pv.display) {
      fields.push({ label: "Test", value: pv.display })
    }
    if (pv.code) fields.push({ label: "Code", value: pv.code })
    if (pv.value) fields.push({ label: "Value", value: pv.value })
    if (pv.unit) fields.push({ label: "Unit", value: pv.unit })
    if (pv.date) fields.push({ label: "Date", value: pv.date })
    if (pv.interpretation) fields.push({ label: "Interpretation", value: pv.interpretation })

    var ghost = buildFormHolderGhost(frameDoc, item, isFocused, "Observation", fields)

    container.insertBefore(ghost, container.firstChild)
    injectedElements.push({ element: ghost, frameDoc: frameDoc })
    if (isFocused) scrollIntoView(ghost)
    return { applied: true }
  }

  function applyCreateOverlayEncounter(frameDoc, item, isFocused) {
    var table = frameDoc.querySelector(".table.jumbotron")
    if (!table) return { applied: false, reason: "Encounters table not found" }

    var tbody = table.querySelector("tbody") || table
    var pv = item.proposed_value || {}

    var ghost = frameDoc.createElement("tr")
    ghost.className = "encrow text agent-overlay-ghost"
    if (item.status === "rejected") {
      ghost.style.cssText = "background:#f9fafb;border-left:3px solid #d1d5db;opacity:0.5;"
    } else if (item.status === "approved") {
      ghost.style.cssText = "background:#f0fdf4;border-left:3px solid #15803d;opacity:0.85;"
    } else {
      ghost.style.cssText = "background:#ECFDF5;border-left:3px solid #10b981;opacity:0.85;"
    }
    if (isFocused) applyFocusStyle(ghost)

    // Count columns from thead to match table width
    var colCount = 6
    var theadRow = table.querySelector("thead tr")
    if (theadRow) colCount = theadRow.children.length

    // Date column
    var dateTd = frameDoc.createElement("td")
    dateTd.className = "align-top"
    dateTd.textContent = pv.date || "Pending"
    ghost.appendChild(dateTd)

    // Reason column — spans remaining columns to hold action buttons
    var reasonTd = frameDoc.createElement("td")
    reasonTd.setAttribute("colspan", String(Math.max(1, colCount - 1)))

    var contentWrapper = frameDoc.createElement("div")
    contentWrapper.style.cssText = "display:flex;align-items:center;width:100%;"

    var fill = frameDoc.createElement("div")
    fill.style.cssText = "flex:1 1 auto;min-width:0;"

    var titleEl = frameDoc.createElement("span")
    titleEl.className = "font-weight-bold"
    titleEl.textContent = pv.reason || item.description || "New Encounter"
    fill.appendChild(titleEl)

    if (pv.facility) {
      var facilitySpan = frameDoc.createElement("span")
      facilitySpan.style.cssText = "margin-left:8px;color:#6b7280;font-size:12px;"
      facilitySpan.textContent = "@ " + pv.facility
      fill.appendChild(facilitySpan)
    }

    var statusBadge = createStatusBadge(frameDoc, item.status)
    if (statusBadge) {
      fill.appendChild(statusBadge)
    } else {
      var pendingSpan = frameDoc.createElement("span")
      pendingSpan.textContent = " (Proposed)"
      pendingSpan.style.cssText = "font-style:italic;color:#6b7280;margin-left:6px;"
      fill.appendChild(pendingSpan)
    }

    var confBadge = createConfidenceBadge(frameDoc, item.confidence, item.status)
    if (confBadge) fill.appendChild(confBadge)

    contentWrapper.appendChild(fill)
    contentWrapper.appendChild(createActionButtons(frameDoc, item, isFocused, currentManifestItems))
    reasonTd.appendChild(contentWrapper)
    ghost.appendChild(reasonTd)

    tbody.insertBefore(ghost, tbody.firstChild)
    injectedElements.push({ element: ghost, frameDoc: frameDoc })
    if (isFocused) scrollIntoView(ghost)
    return { applied: true }
  }

  function applyCreateOverlayVital(frameDoc, item, isFocused) {
    var container = getContainerEl(frameDoc, "#vitals_ps_expand")
    if (!container) return { applied: false, reason: "Container not found" }

    var pv = item.proposed_value || {}
    var ghost = frameDoc.createElement("div")
    ghost.className = "agent-overlay-ghost"
    if (item.status === "rejected") {
      ghost.style.cssText = "background:#f9fafb;border-left:3px solid #d1d5db;opacity:0.5;padding:8px;margin-bottom:8px;"
    } else if (item.status === "approved") {
      ghost.style.cssText = "background:#f0fdf4;border-left:3px solid #15803d;opacity:0.85;padding:8px;margin-bottom:8px;"
    } else {
      ghost.style.cssText = "background:#ECFDF5;border-left:3px solid #10b981;opacity:0.85;padding:8px;margin-bottom:8px;"
    }
    if (isFocused) applyFocusStyle(ghost)

    var summary = frameDoc.createElement("div")
    summary.style.cssText = "display:flex;align-items:center;width:100%;"

    var fill = frameDoc.createElement("div")
    fill.style.cssText = "flex:1 1 auto;min-width:0;"

    var titleEl = frameDoc.createElement("b")
    titleEl.textContent = "Proposed Vitals"
    fill.appendChild(titleEl)

    var statusBadge = createStatusBadge(frameDoc, item.status)
    if (statusBadge) {
      fill.appendChild(statusBadge)
    } else {
      var pendingSpan = frameDoc.createElement("span")
      pendingSpan.textContent = " (Pending)"
      pendingSpan.style.cssText = "font-style:italic;color:#6b7280;"
      fill.appendChild(pendingSpan)
    }
    var confBadge = createConfidenceBadge(frameDoc, item.confidence, item.status)
    if (confBadge) fill.appendChild(confBadge)

    var parts = []
    if (pv.bps || pv.bpd) parts.push("BP: " + (pv.bps || "?") + "/" + (pv.bpd || "?"))
    if (pv.temperature) parts.push("Temp: " + pv.temperature)
    if (pv.pulse) parts.push("Pulse: " + pv.pulse)
    if (pv.respiration) parts.push("Resp: " + pv.respiration)
    if (pv.oxygen_saturation) parts.push("O\u2082: " + pv.oxygen_saturation + "%")
    if (pv.weight) parts.push("Wt: " + pv.weight)
    if (pv.height) parts.push("Ht: " + pv.height)

    if (parts.length > 0) {
      var detailEl = frameDoc.createElement("div")
      detailEl.style.cssText = "margin-top:4px;font-size:13px;"
      detailEl.textContent = parts.join(" \u00b7 ")
      fill.appendChild(detailEl)
    }

    summary.appendChild(fill)
    summary.appendChild(createActionButtons(frameDoc, item, isFocused, currentManifestItems))
    ghost.appendChild(summary)

    container.insertBefore(ghost, container.firstChild)
    injectedElements.push({ element: ghost, frameDoc: frameDoc })
    if (isFocused) scrollIntoView(ghost)
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

  function clearAllOverlays() {
    for (var i = injectedElements.length - 1; i >= 0; i--) {
      var entry = injectedElements[i]
      if (entry.restoreBg) {
        entry.element.style.background = entry.element.dataset.originalBg || ""
        entry.element.style.borderLeft = entry.element.dataset.originalBorderLeft || ""
        entry.element.style.opacity = entry.element.dataset.originalOpacity || ""
        entry.element.style.boxShadow = entry.element.dataset.originalBoxShadow || ""
        delete entry.element.dataset.originalBg
        delete entry.element.dataset.originalBorderLeft
        delete entry.element.dataset.originalOpacity
        delete entry.element.dataset.originalBoxShadow
      } else if (entry.restoreDelete) {
        entry.element.style.background = entry.element.dataset.originalBg || ""
        entry.element.style.borderLeft = entry.element.dataset.originalBorderLeft || ""
        entry.element.style.textDecoration = entry.element.dataset.originalTextDecoration || ""
        entry.element.style.opacity = entry.element.dataset.originalOpacity || ""
        entry.element.style.boxShadow = entry.element.dataset.originalBoxShadow || ""
        delete entry.element.dataset.originalBg
        delete entry.element.dataset.originalBorderLeft
        delete entry.element.dataset.originalTextDecoration
        delete entry.element.dataset.originalOpacity
        delete entry.element.dataset.originalBoxShadow
      } else if (entry.unwrap) {
        var parent = entry.element.parentNode
        if (parent) {
          var contentDiv = entry.element.firstChild
          if (contentDiv) {
            while (contentDiv.firstChild) {
              parent.insertBefore(contentDiv.firstChild, entry.element)
            }
          }
          parent.removeChild(entry.element)
        }
      } else {
        if (entry.element.parentNode) {
          entry.element.parentNode.removeChild(entry.element)
        }
      }
    }
    injectedElements = []
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

    // Poll until the Knockout model reflects the new patient
    var attempts = 0
    var poll = setInterval(function () {
      attempts++
      if (getCurrentPatientPid() === target || attempts >= 30) {
        clearInterval(poll)
        // Let the enc tab and DOM settle after demographics.php runs left_nav.setPatient
        setTimeout(callback, 300)
      }
    }, 200)
  }

  function needsEncounterLoading(items, encounterID) {
    if (!encounterID) return false
    for (var i = 0; i < items.length; i++) {
      if (items[i].resource_type !== "SoapNote") continue
      if (items[i].action !== "create") continue
      // If it depends on a pending Encounter create, it won't use the enc tab
      var hasPendingDep = false
      if (items[i].depends_on && items[i].depends_on.length) {
        for (var d = 0; d < items[i].depends_on.length; d++) {
          for (var j = 0; j < items.length; j++) {
            if (items[j].id === items[i].depends_on[d] &&
                items[j].resource_type === "Encounter" &&
                items[j].action === "create") {
              hasPendingDep = true
              break
            }
          }
          if (hasPendingDep) break
        }
      }
      if (!hasPendingDep) return true
    }
    return false
  }

  function ensureEncounterLoaded(encounterID, callback) {
    if (!encounterID) { callback(); return }

    var topWin = window.top || window
    if (typeof topWin.navigateTab !== "function") { callback(); return }

    // Check if enc-forms already has #partable loaded
    var mapping = RESOURCE_PAGE_MAP.SoapNote
    var frameDoc = getFrameDocumentForMapping(mapping)
    if (frameDoc && frameDoc.querySelector("#partable")) {
      callback()
      return
    }

    var webroot = topWin.webroot_url || ""
    var url = webroot + "/interface/patient_file/encounter/encounter_top.php?set_encounter=" + encodeURIComponent(encounterID)

    topWin.navigateTab(url, "enc", function () {
      if (typeof topWin.activateTabByName === "function") {
        topWin.activateTabByName("enc", true)
      }
    })

    var attempts = 0
    var poll = setInterval(function () {
      attempts++
      var fd = getFrameDocumentForMapping(mapping)
      if ((fd && fd.querySelector("#partable")) || attempts >= 15) {
        clearInterval(poll)
        setTimeout(callback, 200)
      }
    }, 200)
  }

  function navigateToTab(tabName, patientID) {
    try {
      var topWin = window.top || window

      // If the tab iframe already exists, just activate it
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

      // Tab doesn't exist — load the patient's dashboard to create it
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

  function navigateTabToUrl(tabName, url) {
    try {
      var topWin = window.top || window
      if (typeof topWin.navigateTab !== "function") return
      var webroot = topWin.webroot_url || ""
      topWin.navigateTab(webroot + url, tabName, function () {
        if (typeof topWin.activateTabByName === "function") {
          topWin.activateTabByName(tabName, true)
        }
      })
    } catch (_e) {
      // cross-origin or function not available
    }
  }

  function applySingleOverlay(item, isFocused) {
    var mapping = RESOURCE_PAGE_MAP[item.resource_type]
    if (!mapping || !mapping.supportsRowTarget) {
      return { applied: false, reason: "sidebar-only" }
    }

    if (item.action === "create") {
      return applyCreateOverlay(item, mapping, isFocused)
    }
    if (item.action === "update") {
      return applyUpdateOverlay(item, mapping, isFocused)
    }
    if (item.action === "delete") {
      return applyDeleteOverlay(item, mapping, isFocused)
    }

    return { applied: false, reason: "Unknown action: " + item.action }
  }

  function applyAllOverlays(items, focusIndex, patientID, callback) {
    clearAllOverlays()
    currentManifestItems = items

    // Determine focused item by ID so reordering doesn't break focus tracking
    var focusedItemId = items[focusIndex] ? items[focusIndex].id : null

    // Reorder: items without depends_on first so ghosts exist before dependents look for them
    var ordered = items.slice().sort(function (a, b) {
      var aDeps = (a.depends_on && a.depends_on.length) || 0
      var bDeps = (b.depends_on && b.depends_on.length) || 0
      return aDeps - bDeps
    })

    // Only activate the tab for the focused item — activateTabByName hides
    // other tabs, so navigating to every unique tab causes the last one to win,
    // not the focused one.  Overlay DOM writes work on iframe documents that
    // exist regardless of which tab is visible.
    var focusedItem = items[focusIndex]
    var focusedMapping = focusedItem ? RESOURCE_PAGE_MAP[focusedItem.resource_type] : null

    function doApply() {
      var results = []
      for (var i = 0; i < ordered.length; i++) {
        var isFocused = ordered[i].id === focusedItemId
        var result = applySingleOverlay(ordered[i], isFocused)
        results.push({
          itemId: ordered[i].id,
          applied: result.applied,
          reason: result.reason || null,
        })
      }
      currentManifestItems = null
      if (callback) callback(results)
      return results
    }

    if (focusedMapping && focusedMapping.navigateUrl) {
      navigateTabToUrl(focusedMapping.tab, focusedMapping.navigateUrl)
      // Poll until the target container appears in the loaded page
      var attempts = 0
      var poll = setInterval(function () {
        attempts++
        var frameDoc = getFrameDocument(focusedMapping.tab)
        var found = frameDoc && frameDoc.querySelector(focusedMapping.container)
        if (found || attempts >= 15) {
          clearInterval(poll)
          doApply()
        }
      }, 200)
    } else {
      if (focusedMapping && focusedMapping.tab) {
        navigateToTab(focusedMapping.tab, patientID)
      }
      return doApply()
    }
  }

  function applyOverlay(item, callback) {
    var mapping = RESOURCE_PAGE_MAP[item.resource_type]
    if (!mapping || !mapping.supportsRowTarget) {
      var r = { applied: false, reason: "sidebar-only" }
      if (callback) callback(r)
      return r
    }

    clearAllOverlays()

    function doApply() {
      var result
      if (item.action === "create") result = applyCreateOverlay(item, mapping, true)
      else if (item.action === "update") result = applyUpdateOverlay(item, mapping, true)
      else if (item.action === "delete") result = applyDeleteOverlay(item, mapping, true)
      else result = { applied: false, reason: "Unknown action: " + item.action }
      if (callback) callback(result)
      return result
    }

    if (mapping.navigateUrl) {
      navigateTabToUrl(mapping.tab, mapping.navigateUrl)
      var attempts = 0
      var poll = setInterval(function () {
        attempts++
        var frameDoc = getFrameDocument(mapping.tab)
        var found = frameDoc && frameDoc.querySelector(mapping.container)
        if (found || attempts >= 15) {
          clearInterval(poll)
          doApply()
        }
      }, 200)
    } else {
      navigateToTab(mapping.tab)
      return doApply()
    }
  }

  function handleRefresh(items) {
    var topWin = window.top || window
    var tabsToRefresh = {}
    items.forEach(function (item) {
      var mapping = RESOURCE_PAGE_MAP[item.resource_type]
      if (mapping && mapping.tab) {
        tabsToRefresh[mapping.tab] = true
      }
    })
    Object.keys(tabsToRefresh).forEach(function (tabName) {
      var frameEl = topWin.document.querySelector("iframe[name='" + tabName + "']")
      if (frameEl && frameEl.contentWindow) {
        try {
          frameEl.contentWindow.location.reload()
        } catch (_e) {
          // cross-origin guard: silently skip
        }
      }
    })
  }

  window.addEventListener("message", function (event) {
    if (!event.data || typeof event.data.type !== "string") return
    if (!event.data.type.startsWith("overlay:")) return

    if (event.data.type === "overlay:apply") {
      applyOverlay(event.data.item, function (result) {
        if (event.source) {
          event.source.postMessage({
            type: "overlay:result",
            itemId: event.data.item.id,
            applied: result.applied,
            reason: result.reason || null,
          }, "*")
        }
      })
    }

    if (event.data.type === "overlay:applyAll") {
      var items = event.data.items || []
      var focusIndex = event.data.focusIndex || 0
      var patientID = event.data.patientID || null
      var encounterID = event.data.encounterID || null
      var source = event.source

      ensurePatientLoaded(patientID, function () {
        var loadEnc = needsEncounterLoading(items, encounterID)
        var afterEnc = function () {
          applyAllOverlays(items, focusIndex, patientID, function (results) {
            if (source) {
              source.postMessage({
                type: "overlay:allResults",
                results: results,
              }, "*")
            }
          })
        }
        if (loadEnc) {
          ensureEncounterLoaded(encounterID, afterEnc)
        } else {
          afterEnc()
        }
      })
    }

    if (event.data.type === "overlay:clear") {
      clearAllOverlays()
    }

    if (event.data.type === "overlay:refresh") {
      handleRefresh(event.data.items || [])
    }

    if (event.data.type === "overlay:execute" ||
        event.data.type === "overlay:accept" ||
        event.data.type === "overlay:reject" ||
        event.data.type === "overlay:navigate") {
      var iframes = document.querySelectorAll("iframe")
      for (var i = 0; i < iframes.length; i++) {
        try {
          iframes[i].contentWindow.postMessage(event.data, "*")
        } catch (_e) {}
      }
    }
  })

  window.__overlayEngine = {
    applyOverlay: applyOverlay,
    applyAllOverlays: applyAllOverlays,
    clearAllOverlays: clearAllOverlays,
    navigateToTab: navigateToTab,
    navigateTabToUrl: navigateTabToUrl,
    ensurePatientLoaded: ensurePatientLoaded,
    ensureEncounterLoaded: ensureEncounterLoaded,
    getCurrentPatientPid: getCurrentPatientPid,
    RESOURCE_PAGE_MAP: RESOURCE_PAGE_MAP,
  }
})()
