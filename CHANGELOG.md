# Changelog

## [Unreleased]

## [0.18.0] - 2026-11-18

- A request the provider turned down (a wrong key, no balance, a refused prompt) no longer holds the bird back for six hours: nothing was bought, so Repaint and the next refresh work at once. A lost or 5xx answer still waits, since it may have been paid for.
- A submission kie.ai refuses hands its charge back, in the widget's day and in Tesserae's spending record. So does a fal.ai request the widget's own key is refused for.
- The new locking works on Windows (no `fcntl` at import).
- Repaint clears kie.ai tasks that have nothing to collect, on current Tesserae.
- A cut-out that failed is no longer kept as an empty result.

- Prevent duplicate painting and cutout requests after a restart; apply shared spending ceilings to every provider.

- Use the image service selected in Tesserae settings, including kie.ai, while keeping existing paintings and daily limits.
