<?php

/**
 * Clinical Assistant sidebar frame host
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    OpenEMR Community
 * @copyright Copyright (c) 2026 OpenEMR
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

// Use ignoreAuth so globals.php does NOT redirect unauthenticated requests
// to login_screen.php (which uses top.location.href and would navigate the
// entire OpenEMR window away from the main UI, breaking the sidebar loop).
// We perform our own auth check below.
$ignoreAuth = true;
require_once __DIR__ . '/../../../../globals.php';

// Return a silent empty page if not authenticated — no redirect.
if (empty($_SESSION['authUserID']) && empty($_SESSION['authUser'])) {
    http_response_code(401);
    echo '<!doctype html><html><body></body></html>';
    exit;
}

require_once __DIR__ . '/../../../../globals.php';

$assetBase = $GLOBALS['web_root'] . '/interface/modules/custom_modules/oe-module-clinical-assistant/public/assets';
$site = $_GET['site'] ?? 'default';

// Generate an HMAC-signed auth token for the sidebar.
// OpenEMR's restoreSession() mechanism can overwrite the PHP session cookie
// when multiple browser tabs are open, causing proxy.php to lose the
// authenticated session.  This token lets proxy.php verify the user
// independently of the session cookie.
$sidebarAuthToken = '';
$authUserId = (string)($_SESSION['authUserID'] ?? $_SESSION['authUser'] ?? '');
if ($authUserId !== '') {
    $keyFile = $GLOBALS['OE_SITE_DIR'] . '/documents/certificates/oaprivate.key';
    if (file_exists($keyFile)) {
        $key = file_get_contents($keyFile);
        $expires = time() + 7200; // 2 hours (matches OpenEMR session timeout)
        $payload = $authUserId . ':' . $expires;
        $signature = hash_hmac('sha256', $payload, $key);
        $sidebarAuthToken = base64_encode($payload . ':' . $signature);
    }
}

// Resolve patient context from the PHP session for the sidebar header
$sessionPid = !empty($pid) ? (string)$pid : null;
$sessionEncounter = !empty($encounter) ? (string)$encounter : null;
$sessionPatientName = null;
if ($sessionPid) {
    require_once $GLOBALS['fileroot'] . '/library/patient.inc.php';
    $ptData = getPatientData($sessionPid, 'fname, lname');
    if ($ptData) {
        $sessionPatientName = trim(($ptData['fname'] ?? '') . ' ' . ($ptData['lname'] ?? ''));
    }
}
?>
<!doctype html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Clinical Assistant</title>
    <link rel="stylesheet" href="<?= attr($assetBase . '/sidebar.css') ?>">
</head>
<body>
<aside class="sidebar" id="sidebar-root">
    <header class="sidebar-header">
        <div class="title-row">
            <span class="header-title">Clinical Assistant</span>
            <div class="header-actions">
                <button id="history-toggle" class="header-btn" title="Conversation history">History</button>
                <button id="audit-toggle" class="header-btn" title="Audit trail">Audit</button>
                <button id="new-conversation" class="header-btn" title="New conversation">+ New</button>
            </div>
        </div>
        <div class="context-bar">
            <div class="context-row" id="context-line">No patient selected</div>
            <div class="status-pill" id="status-pill" data-state="ready" aria-live="polite">
                <span class="status-dot" id="status-dot"></span>
                <span id="status-text">Ready</span>
            </div>
        </div>
        <div class="session-id-row hidden" id="session-id-row"></div>
    </header>

    <div id="history-panel" class="history-panel hidden">
        <div id="history-list" class="history-list"></div>
    </div>

    <div id="audit-panel" class="audit-panel hidden">
        <div id="audit-list"></div>
    </div>

    <main class="chat-shell" id="chat-shell">
        <section id="chat-area" class="chat-area" aria-live="polite"></section>
        <button id="new-messages-pill" class="new-messages-pill hidden">↓ New messages</button>
    </main>

    <section id="review-panel" class="review-panel hidden">
        <div class="review-header">
            <div class="review-tour-nav">
                <button id="tour-prev" class="tour-arrow" title="Previous item" disabled>‹</button>
                <span id="tour-progress" class="tour-progress">1 of 1</span>
                <button id="tour-next" class="tour-arrow" title="Next item" disabled>›</button>
            </div>
            <div class="review-header-actions">
                <button id="apply-all" class="btn-sm btn-accent">Apply All</button>
                <button id="reject-all" class="btn-sm btn-muted">Reject All</button>
            </div>
        </div>
        <div id="review-cards" class="review-cards"></div>
        <div class="review-footer">
            <span id="review-summary">No pending changes.</span>
            <button id="execute-button" class="btn-sm btn-accent">Execute Changes</button>
        </div>
    </section>

    <footer class="input-bar">
        <div class="input-row">
            <label class="visually-hidden" for="chat-input">Message</label>
            <textarea id="chat-input" rows="1" maxlength="12000" placeholder="Ask about charts, orders, clinical notes…"></textarea>
            <button id="send-button" class="send-btn" title="Send message" disabled>
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M8 14V2M8 2L3 7M8 2L13 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </button>
        </div>
        <span id="char-counter" class="char-counter hidden">0 / 8000</span>
    </footer>
</aside>

<script>
    window.OPENEMR_AGENT_PROXY = "<?= attr($GLOBALS['web_root'] . '/interface/modules/custom_modules/oe-module-clinical-assistant/public/proxy.php?site=' . urlencode($site)) ?>";
    window.OPENEMR_AUTH_TOKEN = <?= json_encode($sidebarAuthToken) ?>;
    window.OPENEMR_SESSION_CONTEXT = {
        pid: <?= json_encode($sessionPid) ?>,
        encounter: <?= json_encode($sessionEncounter) ?>,
        patient_name: <?= json_encode($sessionPatientName) ?>
    };
</script>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script src="<?= attr($assetBase . '/sidebar.js') ?>"></script>
</body>
</html>
