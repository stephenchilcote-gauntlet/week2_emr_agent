# OpenEMR Patient Summary Page — DOM Structure Analysis

## 1. Page Loading Architecture

### Entry Point: `demographics.php`
The patient summary page is `interface/patient_file/summary/demographics.php`. It is **not a single server-rendered page** — it uses a **hybrid approach**:

- **Server-rendered (PHP)**: The PAMI cards (Problems, Allergies, Medications, Immunizations) at the top are rendered inline via Twig templates during initial page load.
- **AJAX-loaded fragments**: Many sections in the left and right columns are loaded asynchronously after DOM ready via `placeHtml()`, a custom async fetch wrapper.

### AJAX-Loaded Sections (via `placeHtml()`)
```javascript
// Line 610-636 of demographics.php
placeHtml("stats.php", "stats_div", true)          // Additional issue types, immunizations (right col)
placeHtml("pnotes_fragment.php", "pnotes_ps_expand")
placeHtml("disc_fragment.php", "disclosures_ps_expand")
placeHtml("labdata_fragment.php", "labdata_ps_expand")
placeHtml("track_anything_fragment.php", "track_anything_ps_expand")
placeHtml("vitals_fragment.php", "vitals_ps_expand")
placeHtml("clinical_reminders_fragment.php", "clinical_reminders_ps_expand")
placeHtml("patient_reminders_fragment.php", "patient_reminders_ps_expand")
```

The `placeHtml()` function POSTs a CSRF token to the fragment URL, receives HTML, and sets `innerHTML` on the target `<div>`. **The target div is the collapsible body div inside each card** (the `id` matches the card id).

### `stats.php` — The Secondary Issue Types
`stats.php` is loaded into `#stats_div` (line 2022: `<div id="stats_div"></div>` in the right column). It renders additional issue type cards (surgery, dental, etc.) plus immunizations, using the same Twig templates as the main cards.

---

## 2. Card DOM Structure (card_base.html.twig)

Every expandable card section uses this hierarchy:

```html
<section class="card {card_bg_color} {card_text_color}">
  <div class="card-body p-1">
    <h6 class="card-title mb-0 d-flex p-1 justify-content-between">
      <!-- Collapse toggle link -->
      <a class="text-left font-weight-bolder" href="#"
         data-toggle="collapse"
         data-target="#{id}"
         aria-expanded="true"
         aria-controls="{id}">
        {title}
        <i class="ml-1 fa fa-fw fa-expand|fa-compress" data-target="#{id}"></i>
      </a>
      <!-- Action buttons (Add/Edit) -->
      <span>
        <a class="{btnClass}" href="..." onclick="...">
          <i class="fa fa-plus|fa-pencil-alt">&nbsp;</i>
        </a>
      </span>
    </h6>
    <!-- Collapsible body -->
    <div id="{id}" class="card-text collapse show|collapse">
      <div class="clearfix pt-2">
        <!-- Card-specific content rendered here -->
      </div>
    </div>
  </div>
</section>
```

### Collapse Mechanism
- **Bootstrap 4.6.2** native collapse via `data-toggle="collapse"` and `data-target="#{id}"`
- The collapsible body has class `collapse` (hidden) or `collapse show` (visible)
- The icon toggles between `fa-expand` (collapsed) and `fa-compress` (expanded)
- User preference is persisted via `updateUserVisibilitySetting()` which POSTs to `library/ajax/user_settings.php`

### Card Container Variants
Some cards pass `card_container_class_list` to override the default `['card']` class on `<section>`:
- PAMI cards at top: `['flex-fill', 'mx-1', 'card']` (flexbox layout)
- Other cards: default `['card']`

---

## 3. PAMI Card IDs

| Card | ID | Template |
|------|----|----------|
| Medical Problems | `medical_problem_ps_expand` | `patient/card/medical_problems.html.twig` |
| Allergies | `allergy_ps_expand` | `patient/card/allergies.html.twig` |
| Medications | `medication_ps_expand` | `patient/card/medication.html.twig` |
| Vitals | `vitals_ps_expand` | `patient/card/loader.html.twig` (AJAX) |
| Immunizations | `immunizations_ps_expand` | `patient/card/immunizations.html.twig` |
| Prescriptions | `prescriptions_ps_expand` | `patient/card/rx.html.twig` |
| Messages/Notes | `pnotes_ps_expand` | `patient/card/loader.html.twig` (AJAX) |
| Labs | `labdata_ps_expand` | `patient/card/loader.html.twig` (AJAX) |
| Clinical Reminders | `clinical_reminders_ps_expand` | `patient/card/loader.html.twig` (AJAX) |

---

## 4. List Item DOM Structure (`.list-group-item` rows)

### Medical Problems (`medical_problems.html.twig`)
```html
<div class="list-group list-group-flush pami-list">
  <!-- When items exist: -->
  <div class="list-group-item py-1 px-1" data-uuid="{uuid}">
    {title}
  </div>
  <!-- When no items and listTouched: -->
  <div class="list-group-item p-0 pl-1">None</div>
  <!-- When no items and not touched: -->
  <div class="list-group-item p-0 pl-1">Nothing Recorded</div>
</div>
```

### Allergies (`allergies.html.twig`)
```html
<div class="list-group list-group-flush pami-list">
  <div class="list-group-item p-1" data-uuid="{uuid}">
    <div class="d-flex w-100 justify-content-between">
      <div class="flex-fill" title="{title} Reaction: {reaction} - {severity}">
        {title} (<span class="bg-warning font-weight-bold px-1|">{severity}</span>)
      </div>
    </div>
  </div>
</div>
```
- Severe/life-threatening/fatal allergies get `bg-warning font-weight-bold px-1` classes
- Severity values checked: `severe`, `life_threatening_severity`, `fatal`

### Medications (`medication.html.twig`)
```html
<div class="list-group list-group-flush pami-list">
  <div class="list-group-item p-0 pl-1" data-uuid="{uuid}">
    <span class="font-weight-normal">{title}</span>
    <span>{drug_dosage_instructions}</span>
  </div>
</div>
```

### Immunizations (`immunizations.html.twig`)
```html
<div class="list-group list-group-flush imz">
  <div class="list-group-item d-flex w-100 p-1">
    <a href="#" class="link" onclick="javascript:load_location({url})">{cvx_text}</a>
  </div>
</div>
```
- Note: Immunizations do NOT have `data-uuid` attributes.
- Class is `imz` not `pami-list`.

### Vitals (AJAX via `vitals_fragment.php`)
Vitals are loaded via AJAX into `#vitals_ps_expand`. The content is NOT a list-group — it's free-form HTML:
```html
<div id='vitals'>
  <span class='text'><b>Most recent vitals from: {date}</b></span>
  <br/><br/>
  <!-- vitals report table from forms/vitals/report.php -->
  <span class='text'>
    <a href='../encounter/trend_form.php?formname=vitals'>Click here to view and graph all vitals.</a>
  </span>
</div>
```

---

## 5. `data-uuid` Attribute Details

### Source
- The `uuid` column in the `lists` database table
- Stored as **16-byte binary** in the DB
- Generated by `UuidRegistry` using ramsey/uuid Timestamp-first COMB Codec
- Converted to standard UUID string format (e.g., `550e8400-e29b-41d4-a716-446655440000`) by `UuidRegistry::uuidToString()` before passing to templates
- Services (`PatientIssuesService`, `AllergyIntoleranceService`, `ConditionService`) auto-populate missing UUIDs in their constructors

### Template Usage
```twig
data-uuid="{{ l.uuid|default('')|attr }}"     {# medical_problems.html.twig #}
data-uuid="{{ l.uuid|default('')|attr }}"     {# allergies.html.twig #}
data-uuid="{{ m.uuid|default('')|attr }}"     {# medication.html.twig #}
```
- Uses Twig `|default('')` filter so it won't error if uuid is null
- Uses `|attr` filter for HTML attribute escaping

### Which Cards Have `data-uuid`
- ✅ Medical Problems
- ✅ Allergies
- ✅ Medications
- ❌ Immunizations (no uuid in template)
- ❌ Vitals (not a list, AJAX loaded)
- ❌ Prescriptions (different data source)

---

## 6. Page Layout Hierarchy

```
body.mt-1.patient-demographic.bg-light
└── div#container_div.container-xl.mb-2
    ├── [dashboard_header.php — patient banner]
    ├── [PatientMenuRole nav bar]
    └── div.main.mb-1
        ├── div.row                              ← PAMI row (top)
        │   ├── div.p-1.col-md-{N}              ← Allergies card
        │   │   └── section.flex-fill.mx-1.card
        │   ├── div.p-1.col-md-{N}              ← Medical Problems card
        │   │   └── section.flex-fill.mx-1.card
        │   ├── div.p-1.col-md-{N}              ← Medications card
        │   │   └── section.flex-fill.mx-1.card
        │   └── div.col.m-0.p-0.mx-1           ← Prescriptions card (if enabled)
        │       └── section.card
        ├── div.row                              ← Care Team, Preferences row
        │   ├── div.col-12                       ← Care Team
        │   ├── div.col-12                       ← Treatment Preferences
        │   ├── div.col-12                       ← Care Experience Preferences
        │   ├── div.col-md-8.px-2               ← LEFT COLUMN
        │   │   ├── section.card                 ← Demographics
        │   │   ├── section.card                 ← Billing
        │   │   ├── section.card                 ← Insurance
        │   │   ├── section.card                 ← Messages (loader → AJAX)
        │   │   ├── section.card                 ← Patient Reminders (loader → AJAX)
        │   │   ├── section.card                 ← Disclosures (loader → AJAX)
        │   │   ├── section.card                 ← Amendments
        │   │   ├── section.card                 ← Labs (loader → AJAX)
        │   │   ├── section.card                 ← Vitals (loader → AJAX)
        │   │   └── section.card                 ← LBF forms (loader → AJAX)
        │   └── div.col-md-4.px-2               ← RIGHT COLUMN
        │       ├── section.card                 ← Portal/API card
        │       ├── section.card                 ← Photos
        │       ├── section.card                 ← Advance Directives
        │       ├── section.card                 ← Clinical Reminders (loader → AJAX)
        │       ├── section.card                 ← Appointments
        │       ├── div#stats_div               ← AJAX: stats.php (extra issue types, immunizations)
        │       └── section.card                 ← Track Anything (loader → AJAX)
```

---

## 7. JS/CSS Dependencies

### Loaded by `Header::setupHeader(['common', 'utility'])`

| Library | Version | Notes |
|---------|---------|-------|
| **jQuery** | 3.7.1 | Always loaded first |
| **jQuery UI** | 1.12.1 | |
| **Bootstrap** | 4.6.2 | Bootstrap Bundle (includes Popper.js) |
| **FontAwesome** | 6.7.2 | Free version, all styles |
| **Select2** | 4.0.13 | |
| **Moment.js** | 2.30.1 | |

### Key JS APIs Used
- **Bootstrap Collapse**: `data-toggle="collapse"` / `data-target="#{id}"` — standard Bootstrap 4 collapse
- **Custom `placeHtml()`**: Async fetch to load fragment HTML into target div
- **Custom `updateUserVisibilitySetting()`**: POSTs to `library/ajax/user_settings.php` to save collapse state
- **Custom `toggleIndicator()`**: Legacy toggle function for some widgets
- **`dlgopen()`**: OpenEMR's custom modal/dialog opener (wraps Bootstrap modals + iframes)
- **`top.restoreSession()`**: Session keep-alive mechanism (crucial — called before most navigation/AJAX)

### CSS Architecture
- Bootstrap 4.6.2 classes throughout
- Custom CSS in `<style>` block within demographics.php:
  - `.card { box-shadow: 1px 1px 1px hsl(0 0% 0% / .2); border-radius: 0; }`
  - `section { background: var(--white); margin-top: .25em; padding: .25em; }`
  - `body { background: var(--bg) !important; }` where `--bg: hsl(0 0% 90%);`
- `.pami-list` class on list containers (has commented-out max-height/overflow-y styles)

---

## 8. Important Timing Considerations for Overlay Code

### Race Conditions
1. **PAMI cards (Problems, Allergies, Medications)**: Available immediately on DOM ready — they are server-rendered inline.
2. **Stats div**: Loaded via AJAX `placeHtml("stats.php", "stats_div")` — must wait for this promise to resolve before interacting with stats_div content (immunizations, extra issue types).
3. **Vitals**: Loaded via AJAX `placeHtml("vitals_fragment.php", "vitals_ps_expand")` — content appears asynchronously.
4. **Collapse event handlers**: The collapse click handler for stats_div content is attached in `.then()` after placeHtml resolves (line 611-613).

### Selectors for Overlay Targeting

```javascript
// Find all PAMI cards (server-rendered, available at DOMContentLoaded):
document.querySelectorAll('#allergy_ps_expand .list-group-item[data-uuid]')
document.querySelectorAll('#medical_problem_ps_expand .list-group-item[data-uuid]')
document.querySelectorAll('#medication_ps_expand .list-group-item[data-uuid]')

// Find vitals card (AJAX-loaded, need MutationObserver or polling):
document.querySelector('#vitals_ps_expand #vitals')

// Find a specific item by UUID:
document.querySelector('.list-group-item[data-uuid="550e8400-e29b-41d4-a716-446655440000"]')

// Find the collapse body of any card:
document.querySelector('#medical_problem_ps_expand')  // This IS the collapse div

// Check if card is expanded:
document.querySelector('#medical_problem_ps_expand').classList.contains('show')

// Toggle card open:
$('#medical_problem_ps_expand').collapse('show')  // Bootstrap 4 jQuery API
```

### Collapse Events (Bootstrap 4)
```javascript
$('#medical_problem_ps_expand').on('show.bs.collapse', function() { /* opening */ })
$('#medical_problem_ps_expand').on('shown.bs.collapse', function() { /* opened */ })
$('#medical_problem_ps_expand').on('hide.bs.collapse', function() { /* closing */ })
$('#medical_problem_ps_expand').on('hidden.bs.collapse', function() { /* closed */ })
```

---

## 9. `stats.php` Secondary Rendering

`stats.php` (loaded into `#stats_div`) processes `$ISSUE_TYPES` **excluding** `allergy`, `medication`, and `medical_problem` (those are already rendered inline). It renders remaining types (surgery, dental, etc.) using the same `medical_problems.html.twig` or `medication.html.twig` templates with `{type}_ps_expand` IDs.

It also renders:
- Immunizations card (`immunizations_ps_expand`) using `immunizations.html.twig`
- Current medications eRx card (if eRx enabled)
- Treatment protocols / Injury log cards

---

## 10. The Loader Pattern

Cards that load content via AJAX use `loader.html.twig`:
```html
<!-- Initially shows spinner -->
<div class="text ml-2">
  <div class="spinner-border spinner-border-sm" role="status">
    <span class="sr-only">Loading...</span>
  </div>
</div>
```

The spinner is replaced when `placeHtml()` sets `innerHTML` on the card's collapse div. The card shell (section > card-body > h6 + collapse div) is rendered server-side; only the inner content is AJAX-loaded.
