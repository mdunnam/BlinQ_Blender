# BlinQ Blender — Product Plan

## Product Overview

**BlinQ Blender** is a Blender add-on and the Blender-facing component of the **XMD ToolBox** ecosystem.

It solves a real gap: artists working across ZBrush and Blender currently have no coherent pipeline for transferring assets, syncing library metadata, tracking workflow state, or running review sessions between both apps. BlinQ closes that gap from the Blender side.

### The Ecosystem

```
┌─────────────────────────────────────────────────────────┐
│                    Artist's Desktop                      │
│                                                          │
│  ┌──────────────┐      ┌──────────────────────────────┐  │
│  │   ZBrush /   │◄────►│     BlinQ Blender Add-on     │  │
│  │ XMD Desktop  │      │  (UI, operators, assets)     │  │
│  └──────────────┘      └──────────┬───────────────────┘  │
│                                   │                      │
│                        ┌──────────▼───────────────────┐  │
│                        │    Optional Sidecar Process   │  │
│                        │  (IPC, AI, image ops, sync)  │  │
│                        └──────────┬───────────────────┘  │
└───────────────────────────────────┼─────────────────────┘
                                    │
                    ┌───────────────▼───────────────┐
                    │           XMD Cloud            │
                    │  License · Seats · Sync · API  │
                    └───────────────────────────────┘
```

| Component | Role |
|---|---|
| **BlinQ Blender add-on** | UI, operators, asset registration, metadata, previews, context actions — everything inside Blender |
| **Sidecar process** (optional) | Heavy compute: IPC transport, AI inference, large image processing, render-job orchestration |
| **XMD Desktop / ZBrush** | The other end of the bridge; mesh, texture, and camera data originates or lands here |
| **XMD Cloud** | License key validation, seat management, feature flags, subscription management. BlinQ caches a signed token locally for offline grace |

---

## Feature Areas

### 1. Licensing and Activation

The add-on is gated by XMD Cloud. On first launch (and periodically), it validates against XMD Cloud and caches a signed token with a TTL. If the service is unreachable and the cache is still valid, the add-on runs normally. If the token has expired and the service is unreachable, paid features degrade gracefully with a clear UI warning.

**Features:**
- License key entry in add-on preferences
- Startup activation check against XMD Cloud
- Signed token cache with configurable TTL
- Offline grace period with visual status indicator
- Seat limit enforcement and display
- Feature flag decoding from token (controls which tiers are enabled)
- Clear activation/error UI — no silent failures

---

### 2. Bridge (ZBrush ↔ Blender)

The core value proposition. Artists send meshes and textures between ZBrush/XMD Desktop and Blender without leaving either app.

**Features:**
- Bridge heartbeat — persistent status indicator showing whether XMD Desktop is connected
- **Send Mesh to Blender** — ZBrush/XMD → Blender mesh import (OBJ initially; USD/Alembic later)
- **Send Back to ZBrush** — selected Blender mesh → ZBrush/XMD export
- **Send Texture/Material** — texture maps and material data in both directions
- Polypaint / color-attribute roundtrip — ZBrush polypaint ↔ Blender vertex color/color attributes
- Face Set / PolyGroup translation — ZBrush polygroups ↔ Blender Face Sets
- Normal / displacement / mask roundtrip — named texture-set adapters and consistent naming conventions
- Multi-SubTool batch send
- USD / Alembic bridge tier (post-MVP, for complex pipeline needs)
- Persistent UUID tagging — every asset gets a durable XMD UUID so roundtrips can replace/merge correctly
- Deterministic naming rules — prevent identity loss on repeated send/return cycles

---

### 3. Asset Library and Browser Integration

BlinQ brings XMD-managed assets into Blender's native Asset Browser. Artists browse, tag, and manage their XMD library without leaving Blender.

**Asset types supported:**

| Type | Blender carrier | Notes |
|---|---|---|
| Brushes | Brush Asset Shelf / Asset Browser | Sculpt + paint brushes; IMM/VDM handled as spawnable objects |
| Alphas | Image datablocks + brush textures | XMD metadata layered on top |
| Textures | Image datablocks | Full sync in both directions |
| Materials | Material datablocks | Markable as assets; auto-preview |
| Lights / HDRIs | Light datablocks, world HDRIs | Light-rig and turntable presets |
| Render Presets | Panel/output presets | Cycles and Eevee settings |
| Array Mesh | Collections + geometry-node setups | Via collection assets |
| Fibers | Hair curves / geometry-node presets | Functional parity, not one-to-one clone |

**Features:**
- Register any Blender asset into the XMD Library ("Add to XMD Library" context action)
- Pull XMD-managed assets into Blender Asset Browser with tags, author, description, and previews
- Metadata/tag/catalog sync — XMD tags map to Blender catalogs and tags
- Preview generation and sync — schedules preview renders; updates Asset Browser thumbnails
- Favorites and history — user-local state keyed by XMD UUID, outside the Blender asset system
- Batch QC tools — tag fixes, preview refresh, catalog moves, origin checks across a whole library

---

### 4. Metadata and Search

**Features:**
- Full metadata editing: author, tags, description, category, source
- XMD namespace custom properties on every registered asset — stores UUID, source ID, sync hash, workflow state
- Catalog mapper — syncs XMD category tree to Blender catalog structure
- Smart filtering — filter by type, tag, author, workflow state, date
- Batch metadata push — update tags/authors across multiple selected assets at once
- Schema versioning — XMD metadata schema is versioned so add-on updates don't break existing libraries

---

### 5. Workflow Tracking

BlinQ tracks where an asset is in the artist's process. No more naming meshes `_final_v2_REAL_FINAL`.

**Features:**
- **Retopo state tracker** — records high-res source, retopo mesh, and shrinkwrap/bake state per asset
- **Workflow stacks** — named sequences of steps (e.g., Block → Detail → Retopo → Bake → Texture → Review)
- Active workflow displayed in N-panel with current step highlighted
- Step transitions trigger optional bridge actions (e.g., auto-export on moving to "Bake" step)
- Workflow stack import/export — share stacks between team members
- Usage logging — records which assets were used in which sessions (feeds studio analytics)

---

### 6. Reference Board and Review

A docked Blender panel for managing visual reference and review snapshots. Starts inside Blender; detached always-on-top window is a second step via the sidecar.

**Features:**
- Import images as reference (drag-and-drop, file picker, or from Asset Browser)
- Annotation tools — draw, text, arrow overlays on reference images
- Arrange/align tools — grid, stack, compare layout
- Review snapshots — render or viewport capture with one click; auto-named and dated
- Before/after compare mode — two snapshots side by side in the panel
- Contact-sheet export — composite of selected snapshots for sharing
- `.xmdref` file format — portable reference board that can be opened in XMD Desktop

---

### 7. Quick Actions (Pie Menu + Context Menus)

Fast access for sculpt sessions — actions should never require opening panels or menus.

**Features:**
- **XMD Pie Menu** (configurable hotkey): Send Mesh, Send Back, Import Texture, Open Reference Board, Random Kit, New Challenge, Compare Snapshot
- Asset Browser context menu actions: "Register in XMD," "Push Metadata," "Sync Preview," "Send to ZBrush," "Add to Workflow Stack"
- N-panel tab "XMD" in 3D View: bridge status + quick send/return + active workflow + retopo state + review tools

---

### 8. Alpha Lab *(post-MVP)*

Non-destructive alpha editing, preview, and export inside Blender.

**Features:**
- Non-destructive image operation stack (blur, invert, levels, blend, tiling)
- Procedural alpha generators — cracks, pores, weave, scales
- Real-time preview in a docked panel
- Export stack: PNG, TIFF, EXR; PSD strategy to be validated separately
- Batch export to XMD Library with auto-tagging

---

### 9. Brush Forge *(post-MVP)*

Guided brush creation and preset management inside Blender, built on native brush assets.

**Features:**
- Step-by-step brush builder (stroke, alpha, texture, falloff)
- Procedural alpha integration from Alpha Lab
- Brush preset export to XMD Library
- Stamp and layer presets as named brush variants

---

### 10. Render Integration *(post-MVP)*

Adapter to external render queues. Does not build a render manager from scratch.

**Features:**
- Render preset library — save, load, and sync Cycles/Eevee output configurations
- Flamenco adapter — submit jobs, track status, auto-return results
- Light-rig / HDRI browser — browse and apply light setups from XMD Library
- Turntable scene templates — one-click turntable setup for asset review renders

---

### 11. AI Features *(post-MVP, sidecar only)*

AI runs entirely out-of-process. The add-on is a consumer of AI results, not an AI runtime.

**Features:**
- Auto-tagging — semantic tags suggested when registering an asset
- Generated descriptions — AI-written asset descriptions as a starting point
- Natural language search — "find rough rock brushes" queries the XMD Library
- Workflow coach — suggests next steps based on asset state and past usage
- Kit builder — AI-assembled starter kits from the XMD Library seeded by a goal or mood

---

### 12. Studio and Team Features *(post-MVP)*

Managed by XMD Cloud; add-on exposes login, sync state, and review UI only.

**Features:**
- Login to XMD Cloud from add-on preferences
- Shared library sync — team-wide XMD Library changes appear in Blender Asset Browser
- Role-based access — shared libraries respect read/write roles from XMD Cloud
- Floating seat management — seat check-in/out shown in add-on status
- Review threads — comment on assets from within Blender; threads stored in XMD Cloud
- Studio dependency audit — usage log shows which assets are used in which `.blend` files

---

## Phased Roadmap

### Phase 1 — Foundation

> **Goal:** Add-on installs cleanly, bridge is live, activation is working.

- [x] Add-on scaffold: register/unregister, preferences, version detection
- [x] XMD Cloud activation: key entry, startup check, token cache, offline grace, status UI
- [x] Bridge transport: file-based IPC, heartbeat monitor, connection status indicator
- [x] Logging and diagnostics panel
- [ ] Cross-version compatibility: 4.5 LTS, 5.0, 5.1 *(verified on 5.1; 4.5/5.0 not yet smoke-tested)*

**Exit criteria:** Add-on installs on all three Blender versions, shows bridge status, correctly enforces/bypasses activation state.

---

### Phase 2 — MVP

> **Goal:** Artists can roundtrip assets between ZBrush and Blender, and see XMD assets in the Asset Browser.

- [x] Send Mesh to Blender (OBJ)
- [x] Send Mesh back to ZBrush
- [x] Send Texture / Material
- [ ] Polypaint → Blender vertex color roundtrip *(blocked on XMD Desktop schema)*
- [ ] Face Set / PolyGroup roundtrip *(blocked on XMD Desktop schema)*
- [x] Asset Browser registration — mark assets, write metadata, generate previews
- [x] "Add to XMD Library" context action
- [x] Metadata sync: tags, author, catalog, description
- [x] Preview sync: schedule and update Asset Browser thumbnails
- [x] UUID tagging and deterministic naming
- [x] XMD N-panel (bridge status, send/return, quick library actions)
- [x] XMD Pie Menu (core actions)
- [x] Retopo state tracker

**Exit criteria:** End-to-end roundtrip works reliably; XMD assets visible and searchable in Asset Browser.

---

### Phase 3 — Workflow

> **Goal:** Artists can manage a full asset lifecycle from import through review without leaving the product.

- [x] Workflow stacks — define, activate, step through *(import/export deferred)*
- [x] Random Kit generator
- [x] Challenge generator
- [x] Reference board — import, list, open *(annotate, arrange deferred to Phase 5)*
- [x] Review snapshots — capture viewport *(compare and contact-sheet deferred)*
- [x] Light-rig / HDRI browser *(HDRI loader done; light-rig collection presets deferred)*
- [x] Render preset library
- [x] Batch metadata QC tools

**Exit criteria:** Artists can run a complete sculpt-to-review session using only BlinQ surfaces.

---

### Phase 4 — Team Tools

> **Goal:** Small teams can share libraries and submit tracked render jobs.

- [ ] XMD Cloud login and shared library sync
- [ ] Role-based library access
- [ ] Floating seat display
- [ ] Workflow stack sharing
- [ ] Usage logging
- [ ] Flamenco render adapter
- [ ] Studio dependency audit

**Exit criteria:** Two-person team can share an XMD library, see each other's changes in Blender, and submit renders.

---

### Phase 5 — Advanced Creation

> **Goal:** Asset creation inside the Blender ecosystem is credible and repeatable.

- [ ] Alpha Lab v1 — image op stack, procedural generators, preview, export
- [ ] Brush Forge v1 — guided builder, preset export
- [ ] USD / Alembic bridge tier
- [ ] Stronger preview modes and compare view
- [ ] Normal / displacement / mask roundtrip
- [ ] Array Mesh via collection assets

**Exit criteria:** Artists can create, edit, and export original alphas and brushes without leaving Blender.

---

### Phase 6 — AI Tier

> **Goal:** Optional AI features work cross-platform without destabilizing Blender.

- [ ] Sidecar AI service protocol defined and stable
- [ ] Auto-tagging on asset registration
- [ ] Generated descriptions
- [ ] Natural language library search
- [ ] Workflow coach suggestions
- [ ] AI kit builder

**Exit criteria:** AI features are opt-in, run in sidecar, degrade gracefully offline, and do not affect core add-on stability.

---

### Phase 7 — Commerce and Studio Platform

> **Goal:** Product behaves like a platform, not just an add-on.

- [ ] Marketplace browser in Blender (buy/download asset packs)
- [ ] Creator upload flow
- [ ] Review threads per asset
- [ ] Analytics dashboard (personal + studio)
- [ ] Storefront and ratings
- [ ] Advanced studio seat and role management

**Exit criteria:** Artists can browse, purchase, and download assets from within Blender; studios can manage access from XMD Cloud.

---

## Technical Breakdown

### Architecture Layers

| Layer | Technology | Runs where |
|---|---|---|
| Blender add-on | Python, `bpy` | Inside Blender process |
| IPC transport | File-based or local socket | Add-on ↔ Sidecar |
| Sidecar process | Python or native binary | Separate OS process |
| XMD Cloud client | HTTPS REST | Sidecar or add-on (activation only) |
| License cache | Signed JSON token, local file | User data directory |

### Key Blender APIs Used

| API | Used for |
|---|---|
| `bpy.app.timers` | Heartbeat polling, IPC polling, queued preview work |
| `bpy.msgbus` | Reactive sync when assets/materials/IDs change |
| `bpy.app.handlers` | Startup sync, file-open indexing, autosave hooks |
| Asset metadata API | Author, tags, description, catalog, previews |
| Custom properties | XMD UUIDs, sync hashes, workflow state per datablock |
| Keymaps / pie menus | Pie menu and hotkey registration |
| GPU / offscreen | Preview compare mode, Alpha Lab previews |
| Background/CLI mode | CI, headless tests, batch rendering |

### Module Structure

```
blinq_blender/
├── __init__.py               # Registration, version checks
├── prefs.py                  # XMDPreferences, FeatureFlags
├── models.py                 # AssetRecord, WorkflowStack, BridgeJob, AnnotationRecord
├── bridge/
│   ├── ipc.py                # CommandEnvelope, HeartbeatMonitor, IPCTransport
│   └── io.py                 # MeshImporter, MeshExporter, TextureImporter, MaterialBuilder
├── assets/
│   ├── index.py              # AssetIndexer, CatalogMapper, MetadataMapper
│   └── previews.py           # PreviewManager, PreviewQueueItem
├── workflow/
│   └── service.py            # WorkflowService, RetopoTracker, RandomKitService, ChallengeService
├── review/
│   └── service.py            # ReferenceBoardService, AnnotationService, CompareService
├── ui/
│   ├── panels.py             # N-panel classes
│   └── menus.py              # XMDPieMenu, context menus
└── integrations/
    ├── cloud.py              # CloudLicenseClient, SeatValidator, ActivationState, LicenseCache
    ├── render.py             # RenderAdapter, FlamencoAdapter
    └── ai.py                 # AIProvider, EmbeddingClient, TagSuggestionClient
```

### Compatibility Matrix

| Blender version | Status | Python version | Notes |
|---|---|---|---|
| 5.1.x | Feature target | TBD | Latest stable at time of writing |
| 5.0.x | Supported | TBD | Second release line |
| 4.5 LTS | Compatibility floor | TBD | Long-term support; minimum supported version |
| < 4.5 | Not supported | — | API too divergent; not worth the maintenance cost |

### Design Rules

1. **No heavy threading inside Blender.** Use `bpy.app.timers` for polling; move computation to the sidecar.
2. **Add-on is a consumer, not a runtime.** AI, image processing, and render management all live outside the Blender process.
3. **UUID is the identity contract.** Every asset that crosses the bridge gets a durable XMD UUID written as a custom property. No UUID = no reliable roundtrip.
4. **Fail gracefully.** If XMD Cloud is unreachable, if the sidecar is not running, or if the bridge is disconnected — the add-on warns clearly and degrades, never hard-blocks.
5. **No Qt inside Blender.** PySide6/Qt is for the external companion only.
6. **Translate, don't clone.** ZBrush concepts (IMM brushes, PolyGroups, Spotlight) map to the nearest Blender equivalent, not a forced one-to-one copy.

---

## Open Questions

| Question | Owner | Needed by |
|---|---|---|
| XMD Cloud activation API contract — token format, TTL, seat fields, feature flag encoding | XMD Cloud team | Phase 1 (Foundation) |
| XMD Desktop IPC protocol — command envelope format, transport type (file vs. socket), versioning | XMD Desktop team | Phase 1 (Foundation) |
| Which XMD roadmap items are actually shipped vs. in-progress vs. planned? | XMD product team | Before Phase 2 scoping |
| PSD export stack for Alpha Lab — is a layered PSD required, or is PNG/TIFF/EXR sufficient for v1? | Art direction / product | Phase 5 (Advanced Creation) |
| Live ZBrush turntable camera sync — what capability does XMD Desktop expose on its side? | XMD Desktop team | Phase 3+ |
| Official Blender Extensions platform distribution — is the add-on code GPL or proprietary? This determines which distribution channel is available. | Legal / business | Before any public release |
