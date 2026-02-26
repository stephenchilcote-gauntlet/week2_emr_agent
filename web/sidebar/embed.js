(function injectClinicalAssistantSidebar() {
  const SIDEBAR_ID = "openemr-clinical-assistant-sidebar"
  const MIN_WIDTH = 1024
  const CONTEXT_POLL_MS = 2000

  if (document.getElementById(SIDEBAR_ID)) {
    return
  }

  function getActiveTabInfo() {
    try {
      const topWin = window.top || window
      const tabs = topWin.app_view_model &&
        topWin.app_view_model.application_data &&
        topWin.app_view_model.application_data.tabs &&
        topWin.app_view_model.application_data.tabs.tabsList &&
        topWin.app_view_model.application_data.tabs.tabsList()
      if (!tabs || !Array.isArray(tabs)) {
        return { name: null, title: null, url: null }
      }
      for (let i = 0; i < tabs.length; i++) {
        const tab = tabs[i]
        if (tab.visible && tab.visible() && !(tab.locked && tab.locked())) {
          return {
            name: tab.name ? tab.name() : null,
            title: tab.title ? tab.title() : null,
            url: tab.url ? tab.url() : null,
          }
        }
      }
    } catch (_e) {
      // cross-origin or missing view model
    }
    return { name: null, title: null, url: null }
  }

  function collectContext() {
    const topWin = window.top || window

    let pid = null
    let encounter = null
    let patientName = null

    // Read from OpenEMR's Knockout view model (the real source of truth)
    try {
      const appData = topWin.app_view_model &&
        topWin.app_view_model.application_data
      if (appData) {
        const patient = appData.patient && appData.patient()
        if (patient) {
          pid = patient.pid ? patient.pid() : null
          patientName = patient.pname ? patient.pname() : null
          encounter = patient.selectedEncounterID
            ? patient.selectedEncounterID()
            : null
        }
      }
    } catch (_e) {
      // view model not available yet
    }

    const activeTab = getActiveTabInfo()

    topWin.openemrAgentContext = {
      pid: pid,
      encounter: encounter,
      patient_name: patientName,
      active_tab: activeTab.name,
      active_tab_title: activeTab.title,
      active_tab_url: activeTab.url,
    }
  }

  function mount() {
    if (!document.body) {
      return
    }

    collectContext()

    const wrapper = document.createElement("div")
    wrapper.id = SIDEBAR_ID
    wrapper.style.position = "fixed"
    wrapper.style.top = "0"
    wrapper.style.right = "0"
    wrapper.style.width = "380px"
    wrapper.style.height = "100vh"
    wrapper.style.zIndex = "2147480000"
    wrapper.style.borderLeft = "1px solid #d1d5db"
    wrapper.style.background = "#fff"
    wrapper.style.boxShadow = "0 14px 34px rgba(15, 23, 42, 0.12)"

    const frame = document.createElement("iframe")
    frame.src = "/agent-api/ui"
    frame.title = "Clinical Assistant"
    frame.style.width = "100%"
    frame.style.height = "100%"
    frame.style.border = "0"

    wrapper.appendChild(frame)
    document.body.appendChild(wrapper)

    var overlayScript = document.createElement("script")
    overlayScript.src = "/agent-api/ui/assets/overlay.js"
    document.head.appendChild(overlayScript)

    if (window.innerWidth >= MIN_WIDTH) {
      document.body.style.marginRight = "380px"
    }

    // Poll for context changes (tab switches, patient selections)
    setInterval(collectContext, CONTEXT_POLL_MS)
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount, { once: true })
  } else {
    mount()
  }
})()
