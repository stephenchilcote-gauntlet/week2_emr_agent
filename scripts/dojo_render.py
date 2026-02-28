#!/usr/bin/env python3
"""
Overlay Dojo CLI renderer.

Serves the dojo on localhost, opens it in headless Chromium via Playwright,
fires a manifest, and screenshots the result.

Usage:
    uv run scripts/dojo_render.py 1              # manifest #1, screenshot pat tab
    uv run scripts/dojo_render.py 5 --tab enc    # manifest #5, screenshot enc tab
    uv run scripts/dojo_render.py 1 5 9 11       # multiple manifests, one screenshot each
    uv run scripts/dojo_render.py --all           # all 15 manifests
    uv run scripts/dojo_render.py 1 --headed      # visible browser for debugging
"""
from __future__ import annotations

import argparse
import http.server
import json
import sys
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "web" / "dojo" / "screenshots"
PORT = 18923  # arbitrary high port unlikely to collide


def serve_web_dir() -> http.server.HTTPServer:
    """Start a simple HTTP server for the web/ directory in a background thread."""
    web_dir = str(WEB_DIR)

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=web_dir, **kwargs)

        def log_message(self, format, *args):
            pass  # suppress request logging

    server = http.server.HTTPServer(("127.0.0.1", PORT), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def render_manifest(
    page,
    manifest_num: int,
    tab: str,
    output_dir: Path,
) -> Path:
    """Fire a manifest and screenshot the result. Returns the output path."""
    # Clear any previous overlays
    page.evaluate("Dojo.clearOverlays()")
    time.sleep(0.1)

    # Fire the manifest
    page.evaluate(f"Dojo.fireManifest({manifest_num - 1})")

    # Wait for overlay rendering
    time.sleep(0.5)

    # Switch to requested tab
    page.evaluate(f"Dojo.switchTab('{tab}')")
    time.sleep(0.2)

    # Screenshot the iframe content
    iframe_el = page.query_selector(f"iframe[name='{tab}']")
    frame = iframe_el.content_frame()

    out_path = output_dir / f"manifest-{manifest_num:02d}-{tab}.png"
    # Screenshot the iframe element (shows it as rendered in the parent page)
    iframe_el.screenshot(path=str(out_path))

    # Also grab the log area text for diagnostics
    log_text = page.evaluate(
        "document.getElementById('log-area').innerText"
    )

    return out_path, log_text


def main():
    parser = argparse.ArgumentParser(description="Overlay Dojo CLI renderer")
    parser.add_argument(
        "manifests",
        nargs="*",
        type=int,
        help="Manifest numbers to render (1-15)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Render all 15 manifests",
    )
    parser.add_argument(
        "--tab",
        default="pat",
        choices=["pat", "enc"],
        help="Which tab to screenshot (default: pat)",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run browser in headed mode for debugging",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=OUTPUT_DIR,
        help=f"Output directory (default: {OUTPUT_DIR})",
    )
    args = parser.parse_args()

    if args.all:
        manifest_nums = list(range(1, 16))
    elif args.manifests:
        manifest_nums = args.manifests
    else:
        parser.print_help()
        sys.exit(1)

    for n in manifest_nums:
        if n < 1 or n > 15:
            print(f"Error: manifest number {n} out of range (1-15)", file=sys.stderr)
            sys.exit(1)

    args.out.mkdir(parents=True, exist_ok=True)

    # Start HTTP server
    server = serve_web_dir()
    print(f"Serving {WEB_DIR} on http://127.0.0.1:{PORT}")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not args.headed)
            context = browser.new_context(viewport={"width": 1400, "height": 900})
            page = context.new_page()

            # Collect console messages for debugging
            console_msgs = []
            page.on("console", lambda msg: console_msgs.append(
                f"[{msg.type}] {msg.text}"
            ))

            url = f"http://127.0.0.1:{PORT}/dojo/index.html"
            print(f"Loading {url}")
            page.goto(url, wait_until="networkidle")

            # Wait for iframes to be ready
            page.wait_for_selector("iframe[name='pat']", state="attached")
            page.wait_for_selector("iframe[name='enc']", state="attached")

            # Wait for iframe content to load
            pat_frame = page.query_selector("iframe[name='pat']").content_frame()
            enc_frame = page.query_selector("iframe[name='enc']").content_frame()

            if pat_frame:
                pat_frame.wait_for_load_state("domcontentloaded")
            if enc_frame:
                enc_frame.wait_for_load_state("domcontentloaded")

            # Extra settle time for Bootstrap CSS from CDN
            time.sleep(1)

            # Verify iframe accessibility
            can_access = page.evaluate("""() => {
                try {
                    var f = document.querySelector("iframe[name='pat']");
                    var doc = f.contentDocument;
                    var items = doc.querySelectorAll('.list-group-item');
                    return {ok: true, items: items.length, title: doc.title};
                } catch(e) {
                    return {ok: false, error: e.message};
                }
            }""")
            print(f"iframe access check: {json.dumps(can_access)}")

            if not can_access.get("ok"):
                print("ERROR: Cannot access iframe contentDocument!", file=sys.stderr)
                print("Console messages:", file=sys.stderr)
                for m in console_msgs:
                    print(f"  {m}", file=sys.stderr)
                sys.exit(1)

            # Determine tab per manifest (batches/enc manifests need enc tab)
            # Manifests 14 (Encounter create) targets enc tab
            enc_manifests = {14}

            for num in manifest_nums:
                tab = args.tab
                # Auto-detect tab for known enc-only manifests
                if num in enc_manifests and args.tab == "pat":
                    tab = "pat"  # still screenshot pat, it's sidebar-only

                out_path, log_text = render_manifest(page, num, tab, args.out)
                print(f"  #{num:2d} → {out_path.name}")
                # Print last few log lines
                for line in log_text.strip().split("\n")[-3:]:
                    print(f"       {line.strip()}")

            browser.close()

    finally:
        server.shutdown()

    print(f"\nDone. Screenshots in {args.out}/")


if __name__ == "__main__":
    main()
