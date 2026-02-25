# OpenEMR Sidebar Integration

This repo now ships a production-ready sidebar bundle served by the agent API.

## Endpoints

- `GET /ui` → standalone sidebar shell
- `GET /ui/assets/sidebar.css` / `sidebar.js` / `embed.js` → assets

When deployed behind OpenEMR with path prefix `/agent-api`, these become:

- `/agent-api/ui`
- `/agent-api/ui/assets/embed.js`

## OpenEMR Injection (PHP Module)

Add this script tag on every OpenEMR page layout (top frame):

```php
<script src="/agent-api/ui/assets/embed.js"></script>
```

`embed.js` will:

1. Read OpenEMR globals (`pid`, `encounter`) into `window.openemrAgentContext`
2. Inject a fixed right-column sidebar host
3. Load the sidebar iframe from `/agent-api/ui`
4. Keep main content visible by applying `margin-right: 380px` on desktop widths

## Trusted User Header

The sidebar JS expects OpenEMR to inject `openemr_user_id` through your PHP proxy layer for all `/agent-api/api/*` requests. The backend enforces this header.
