# Changelog

Every release of Dynamic Cost, newest first. Each version's section is
published as the release notes on GitHub, which HACS shows when it offers
the update.

## [0.2.1] - Unreleased

### Added
- **An icon.** Dynamic Cost now has its own icon: a dynamic price line that
  zigzags like a lightning bolt, ending in the current price. Home Assistant
  2026.3 and later show it on the integration's pages, with a lighter version
  for dark themes. HACS doesn't show it yet; its successor, Marketplace in
  Home Assistant 2026.11, will.

## [0.2.0] - 2026-10-17

### Added
- **Stale-window warning.** In dynamic mode, a window only closes when the
  import price changes. If one stays open longer than a time you choose
  (default 120 minutes), a warning is logged that names the likely cause: an
  import price that hasn't changed, an export price or curtailment state
  still missing, or no import price at all yet. Usually it means a price
  sensor is wrong or stuck. Set the time under the hub's Reconfigure → dynamic
  tariffs.
- **Diagnostic sensors for dynamic hubs.** The window's status (open, held
  open, overdue, or no window yet), its import and export price, its
  curtailment state (with a curtailment source), when it opened and when the
  last window closed.

### Changed
- **Splits only appear when they can mean something.** Splits that need solar
  panels or a battery are created once you add one, and removed again when
  you remove the last one. Their totals are kept, so they carry on if the
  device comes back.

### Upgrading
- A hub without solar or a battery loses the splits that need them; they
  only ever showed 0. Home Assistant may then list leftover statistics for
  them under Developer Tools → Statistics, which you can delete.
- Existing dynamic hubs use the default warning time of 120 minutes until
  you change it.

## [0.1.0] - 2026-10-16

First release.

- Energy and financial splits for grid, solar, battery and house, valued at
  fixed tariffs or a dynamic contract's price sensors.
- Solar panels, batteries and general (sub-metered) devices, each added and
  configured from the hub's page.
- Optional curtailment source, so solar stored while curtailed counts as free.
- Totals and the open window survive restarts; unavailable sensors are waited
  for rather than guessed.
