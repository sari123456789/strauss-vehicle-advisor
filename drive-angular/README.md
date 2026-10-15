# DRIVE — Angular client
Hebrew RTL employee car advisor. Standalone Angular components, lazy routes, Signals feature state and a repository boundary for backend integration.

## Run
`npm ci` then `npm start`. Build: `npm run build`.

## Architecture
`core/models`: typed contracts. `core/data`: demo repository and API base token. `core/state`: thin UI facade; domain stores are in each `features/*/state` directory. `core/storage`: demo persistence. `core/data/http-drive.client.ts`: proposed typed HTTP adapter, inactive until server integration. `shared/ui`: presentational components. `features`: lazy pages by business domain. Global design tokens and responsive layouts in `styles.scss`.

## Demo limitations
No real authentication, tax calculations, server uploads or AI integration. Salary outputs are explicitly fixed illustrative examples. Browser storage is demo-only; do not enter real salary documents. Server must enforce authentication and authorization. Replace demo repository/state persistence with C# APIs, and conversation responses with Python agent integration. Use server-authenticated events or polling for representative answers across users. Production salary and comparison data must be versioned and authoritative in the server.

Automotive asset generated from the approved art direction; reference mockup is retained separately. History stores the chosen vehicle identifier, not an immutable backend calculation snapshot.

## Exact desktop homepage
The desktop homepage intentionally uses the approved mockup as a single visual layer with accessible Angular RouterLink hotspots, preserving exact palette and aspect ratio without distortion. It is a reference-image implementation, not fully selectable HTML text. Small-screen layout remains semantic responsive HTML. Replace with pixel-verified native components when a full production design-system implementation is required.
