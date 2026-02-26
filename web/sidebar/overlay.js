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
    Encounter: { tab: "enc", container: null, rowSelector: null, supportsRowTarget: false },
    Observation: { tab: "enc", container: null, rowSelector: null, supportsRowTarget: false },
    Procedure: { tab: "enc", container: null, rowSelector: null, supportsRowTarget: false },
    DiagnosticReport: { tab: "enc", container: null, rowSelector: null, supportsRowTarget: false },
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

  function applyCreateOverlay(item, mapping) {
    var frameDoc = getFrameDocument(mapping.tab)
    if (!frameDoc) return { applied: false, reason: "Frame not available" }

    var container = getContainerEl(frameDoc, mapping.container)
    if (!container) return { applied: false, reason: "Container not found" }

    var listGroup = container.querySelector(".list-group")
    if (!listGroup) {
      listGroup = container
    }

    var ghost = frameDoc.createElement("div")
    ghost.className = "list-group-item agent-overlay-ghost"
    ghost.style.cssText =
      "background:#FEF3C7;border-left:3px solid #d97706;padding:6px 8px;" +
      "display:flex;align-items:center;gap:6px;"

    var badge = createBadge("Suggested", "#2563eb")
    ghost.appendChild(badge)

    var text = frameDoc.createElement("span")
    var displayText = item.description || ""
    if (item.proposed_value) {
      var pv = item.proposed_value
      displayText = pv.code_text || pv.title || pv.display || item.description || ""
      if (pv.code) {
        displayText += " (" + pv.code + ")"
      }
    }
    text.textContent = displayText
    ghost.appendChild(text)

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
    row.style.background = "#FEF3C7"

    var badge = createBadge("Suggested", "#2563eb")
    row.appendChild(badge)
    injectedElements.push({ element: badge, frameDoc: frameDoc })

    if (item.current_value && item.proposed_value) {
      var diffSpan = frameDoc.createElement("span")
      diffSpan.className = "agent-overlay-diff"
      diffSpan.style.cssText = "margin-left:6px;font-size:12px;"

      var oldText = item.current_value.title || item.current_value.display || ""
      var newText = item.proposed_value.title || item.proposed_value.display || ""
      if (oldText && newText && oldText !== newText) {
        var del = frameDoc.createElement("span")
        del.style.cssText = "text-decoration:line-through;opacity:0.6;"
        del.textContent = oldText
        diffSpan.appendChild(del)

        var arrow = frameDoc.createTextNode(" → ")
        diffSpan.appendChild(arrow)

        var ins = frameDoc.createElement("span")
        ins.style.fontWeight = "600"
        ins.textContent = newText
        diffSpan.appendChild(ins)

        row.appendChild(diffSpan)
        injectedElements.push({ element: diffSpan, frameDoc: frameDoc })
      }
    }

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
    injectedElements.push({ element: row, frameDoc: frameDoc, restoreDelete: true })
    scrollIntoView(row)

    return { applied: true }
  }

  function scrollIntoView(el) {
    try {
      el.scrollIntoView({ behavior: "smooth", block: "center" })
    } catch (_e) {
      // ignore
    }
  }

  function clearAllOverlays() {
    for (var i = injectedElements.length - 1; i >= 0; i--) {
      var entry = injectedElements[i]
      if (entry.restoreBg) {
        entry.element.style.background = entry.element.dataset.originalBg || ""
        delete entry.element.dataset.originalBg
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

  function applyOverlay(item) {
    var mapping = RESOURCE_PAGE_MAP[item.resource_type]
    if (!mapping || !mapping.supportsRowTarget) {
      return { applied: false, reason: "sidebar-only" }
    }

    clearAllOverlays()

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

    if (event.data.type === "overlay:clear") {
      clearAllOverlays()
    }
  })

  window.__overlayEngine = {
    applyOverlay: applyOverlay,
    clearAllOverlays: clearAllOverlays,
    RESOURCE_PAGE_MAP: RESOURCE_PAGE_MAP,
  }
})()
