(function injectClinicalAssistantSidebar() {
    if (window.top !== window.self) {
        return;
    }

    // Skip login/logout pages — no session exists, sidebar_frame would redirect top window
    if (/\/(login|login_screen)\.php/i.test(window.location.pathname)) {
        return;
    }

    var SIDEBAR_ID = 'openemr-clinical-assistant-sidebar';
    if (document.getElementById(SIDEBAR_ID)) {
        return;
    }

    var SIDEBAR_WIDTH = 380;
    var moduleRoot = '/interface/modules/custom_modules/oe-module-clinical-assistant/public';
    var site = new URLSearchParams(window.location.search).get('site') || 'default';
    var frameSrc = moduleRoot + '/sidebar_frame.php?site=' + encodeURIComponent(site);

    function mount() {
        if (!document.body) {
            return;
        }
        // Guard against double-mount when embed.js is injected twice
        // (ob_start + StyleFilterEvent can both pass the IIFE guard before mount runs)
        if (document.getElementById(SIDEBAR_ID)) {
            return;
        }

        // --- Restructure DOM: wrap existing body children + sidebar in a flex row ---
        var outerShell = document.createElement('div');
        outerShell.id = 'ca-shell';

        var contentPane = document.createElement('div');
        contentPane.id = 'ca-content';

        // Move every existing body child into the content pane
        while (document.body.firstChild) {
            contentPane.appendChild(document.body.firstChild);
        }

        var sidebar = document.createElement('div');
        sidebar.id = SIDEBAR_ID;

        var frame = document.createElement('iframe');
        frame.src = frameSrc;
        frame.title = 'Clinical Assistant';

        sidebar.appendChild(frame);
        outerShell.appendChild(contentPane);
        outerShell.appendChild(sidebar);
        document.body.appendChild(outerShell);

        // Inject overlay engine into the parent frame for in-page highlights
        var overlayScript = document.createElement('script');
        overlayScript.src = moduleRoot + '/assets/overlay.js';
        document.head.appendChild(overlayScript);

        setupContextBridge(frame);

        // --- Inject layout CSS ---
        var s = document.createElement('style');
        s.id = 'clinical-assistant-layout';
        s.textContent =
            // Reset body — let the shell fill the viewport
            'html, body { width: 100% !important; min-width: 0 !important; height: 100% !important; margin: 0 !important; padding: 0 !important; overflow: hidden !important; display: block !important; }' +
            // Flex row: content takes remaining space, sidebar is fixed-width
            '#ca-shell { display: flex; flex-direction: row; width: 100vw; height: 100vh; overflow: hidden; }' +
            '#ca-content { flex: 1 1 0%; min-width: 0; height: 100%; overflow: auto; display: flex; flex-direction: column; }' +
            // Preserve OpenEMR flex layout inside the content pane
            '#ca-content > #mainBox { flex: 1 1 auto; min-height: 0; width: 100% !important; }' +
            // Sidebar
            '#' + SIDEBAR_ID + ' { flex: 0 0 ' + SIDEBAR_WIDTH + 'px; width: ' + SIDEBAR_WIDTH + 'px; height: 100%; border-left: 1px solid #d1d5db; background: #fff; box-shadow: 0 14px 34px rgba(15,23,42,0.12); z-index: 2147480000; }' +
            '#' + SIDEBAR_ID + ' > iframe { width: 100%; height: 100%; border: 0; }';
        document.head.appendChild(s);
    }

    function setupContextBridge(frame) {
        var pollId = setInterval(function () {
            if (typeof app_view_model === 'undefined' || !app_view_model.application_data) {
                return;
            }
            clearInterval(pollId);
            initBridge();
        }, 250);

        function initBridge() {
            var encSub = null;

            // Cache patient identity from the patient subscriber so that
            // tab-visibility-triggered sendContext() calls never read a
            // transient null from the knockout observable during tab switches.
            var cachedPid = null;
            var cachedPname = null;

            var initialPatient = app_view_model.application_data.patient();
            cachedPid = initialPatient ? String(initialPatient.pid()) : null;
            cachedPname = initialPatient ? initialPatient.pname() : null;

            function getActivePageUrl() {
                try {
                    var tabs = app_view_model.application_data.tabs.tabsList();
                    for (var i = 0; i < tabs.length; i++) {
                        if (tabs[i].visible() && !tabs[i].locked()) {
                            return tabs[i].url() || '';
                        }
                    }
                } catch (e) { /* tabs not ready */ }
                return window.location.pathname;
            }

            function sendContext() {
                if (!frame.contentWindow) {
                    return;
                }
                var patient = app_view_model.application_data.patient();
                frame.contentWindow.postMessage({
                    type: 'clinical-assistant-context',
                    pid: cachedPid,
                    pname: cachedPname,
                    encounter_id: patient && patient.selectedEncounterID()
                        ? String(patient.selectedEncounterID()) : null,
                    page_url: getActivePageUrl()
                }, '*');
            }

            function watchPatient(patient) {
                if (encSub) { encSub.dispose(); encSub = null; }
                if (patient && patient.selectedEncounterID) {
                    encSub = patient.selectedEncounterID.subscribe(function () {
                        sendContext();
                    });
                }
            }

            function watchTabs() {
                var tabs = app_view_model.application_data.tabs.tabsList();
                for (var i = 0; i < tabs.length; i++) {
                    tabs[i].visible.subscribe(function () { sendContext(); });
                }
                app_view_model.application_data.tabs.tabsList.subscribe(function (changes) {
                    for (var ci = 0; ci < changes.length; ci++) {
                        if (changes[ci].status === 'added') {
                            changes[ci].value.visible.subscribe(function () {
                                sendContext();
                            });
                        }
                    }
                    sendContext();
                }, null, 'arrayChange');
            }

            app_view_model.application_data.patient.subscribe(function (newPatient) {
                cachedPid = newPatient ? String(newPatient.pid()) : null;
                cachedPname = newPatient ? newPatient.pname() : null;
                watchPatient(newPatient);
                sendContext();
            });

            watchPatient(app_view_model.application_data.patient());
            watchTabs();

            frame.addEventListener('load', function () { sendContext(); });
            if (frame.contentWindow) { sendContext(); }
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', mount, { once: true });
    } else {
        mount();
    }
})();
