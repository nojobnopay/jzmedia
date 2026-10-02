---
name: jzmedia-ui
description: Maintain jzmedia's Vue interface design system and review or implement its page, control, dialog, feedback, and responsive UI changes. Use for jzmedia UI work; backend-only tasks do not need this workflow.
---

# jzmedia UI

Read the repository's `AGENTS.md` and [界面规范与验收](../../../docs/developer/design-system.md) before making interface changes. Use the user's requested scope and existing product conventions to choose the affected pages.

## Work from the existing system

- Keep the dark media presentation, artwork, and clear primary actions unless the user requests a different visual direction. Management tools can be denser than browsing pages.
- Use `frontend/src/styles/tokens.css` for colors and recurring sizes; `base.css` for shared native controls. Keep page layout in the corresponding styles file. Remove replaced scoped declarations instead of adding stronger ancestor overrides.
- Reuse `JzButton`, `JzField`, `JzDialog`, `AppIcon`, `EmptyState`, and `BrowseToolbar` when their contracts fit. Consult their source before adding props. Keep `PlayerIcon` and the player's specialized geometry/lifecycle.
- Primary button styling must work after Teleport to body. Dialog title, focus containment/return, Escape, busy protection, and overlay layer are part of the component contract. Do not mount two focus traps for the same dialog.
- Keep loading, empty, no-results, and error distinct. Errors offer a retry or relevant next action. An unresolved request must not show a completed empty state.
- Label configuration scope (whole service, selected video library, current browser) where it can change the user's decision. Match actual navigation names in guidance.

## Preserve media workflows

Keep existing library scope, route query/back restoration, async cancellation/generations, playback progress isolation, file-change guards, and explicit preview/confirmation for physical operations. Visual cleanup does not authorize scanning or organizing real media. For screenshots use mock API fixtures or the documented temporary demo instance.

## Review the result

- For a substantial layout change, inspect representative current screenshots and record the intended palette, type hierarchy, and layout before implementation. Compare the same fixture and viewport after editing.
- Preview shared states with `node scripts/smoke_design_system.mjs --demo`; run without `--demo` for the browser checks, or with `--capture` for screenshots under `output/playwright/`.
- Exercise affected real pages with `scripts/smoke_settings_ui.mjs` or `scripts/smoke_ai_ui.mjs`; these use isolated fixtures. Cover desktop and phone, long content, busy/error states, keyboard focus, and closing overlays. Keep playback preview lifecycle checks when touching a containing dialog.
- Run frontend test/lint/build and affected browser checks. If interface or navigation changes affect a tutorial, follow the documented asset capture and manifest workflow.
- Report actual verification and remaining limitations. Store maintenance findings in `docs/private/`, not the public help site or search index.
