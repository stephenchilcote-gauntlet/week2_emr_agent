/**
 * OpenEMR DOM Capture — Bookmarklet / Console Snippet
 *
 * Run this in the browser console on a live OpenEMR instance to
 * download HTML snapshots of the "pat" and "enc" iframes for use
 * with the Overlay Dojo.
 *
 * Usage:
 *   1. Open OpenEMR with a patient loaded
 *   2. Open browser console (F12)
 *   3. Paste this entire script and press Enter
 *   4. Two files will download: pat-snapshot.html, enc-snapshot.html
 */
;(function captureSnapshots() {
  function downloadFile(filename, content) {
    var blob = new Blob([content], { type: "text/html;charset=utf-8" })
    var url = URL.createObjectURL(blob)
    var a = document.createElement("a")
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  function captureFrame(name) {
    var frame = document.querySelector("iframe[name='" + name + "']")
    if (!frame) {
      console.warn("[Capture] iframe[name=" + name + "] not found")
      return null
    }
    try {
      var doc = frame.contentDocument
      if (!doc) {
        console.warn("[Capture] iframe[name=" + name + "] contentDocument is null (cross-origin?)")
        return null
      }

      // Clone to avoid mutating the live DOM
      var clone = doc.documentElement.cloneNode(true)

      // Remove scripts to keep snapshots static
      var scripts = clone.querySelectorAll("script")
      for (var i = 0; i < scripts.length; i++) {
        scripts[i].parentNode.removeChild(scripts[i])
      }

      // Remove overlay engine artifacts if present
      var overlayEls = clone.querySelectorAll(
        ".agent-overlay-ghost, .agent-overlay-actions, .agent-overlay-badge, .agent-overlay-diff"
      )
      for (var j = 0; j < overlayEls.length; j++) {
        overlayEls[j].parentNode.removeChild(overlayEls[j])
      }

      // Inject CDN links for Bootstrap + FontAwesome so snapshots render standalone
      var head = clone.querySelector("head")
      if (head) {
        var bootstrapCSS = doc.createElement("link")
        bootstrapCSS.rel = "stylesheet"
        bootstrapCSS.href = "https://cdn.jsdelivr.net/npm/bootstrap@4.6.2/dist/css/bootstrap.min.css"
        head.insertBefore(bootstrapCSS, head.firstChild)

        var faCSS = doc.createElement("link")
        faCSS.rel = "stylesheet"
        faCSS.href = "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/css/all.min.css"
        head.appendChild(faCSS)
      }

      // Add Bootstrap JS at end of body for collapse functionality
      var body = clone.querySelector("body")
      if (body) {
        var jqScript = doc.createElement("script")
        jqScript.src = "https://cdn.jsdelivr.net/npm/jquery@3.7.1/dist/jquery.min.js"
        body.appendChild(jqScript)

        var bsScript = doc.createElement("script")
        bsScript.src = "https://cdn.jsdelivr.net/npm/bootstrap@4.6.2/dist/js/bootstrap.bundle.min.js"
        body.appendChild(bsScript)
      }

      return "<!doctype html>\n" + clone.outerHTML
    } catch (e) {
      console.error("[Capture] Error capturing iframe[name=" + name + "]:", e)
      return null
    }
  }

  var frames = ["pat", "enc"]
  var captured = 0

  for (var k = 0; k < frames.length; k++) {
    var html = captureFrame(frames[k])
    if (html) {
      downloadFile(frames[k] + "-snapshot.html", html)
      captured++
      console.log("[Capture] Downloaded " + frames[k] + "-snapshot.html (" + html.length + " bytes)")
    }
  }

  if (captured === 0) {
    console.warn("[Capture] No frames captured. Make sure a patient is loaded and both pat/enc tabs exist.")
  } else {
    console.log("[Capture] Done! " + captured + " snapshot(s) saved. Copy to web/dojo/snapshots/")
  }
})()
