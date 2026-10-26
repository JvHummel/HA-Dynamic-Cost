# Changelog

Every release of Dynamic Cost, newest first. Each version's section is
published as the release notes on GitHub, which HACS shows when it offers
the update.

## [0.3.0] - 2026-10-26

### Added
- **Summaries.** Per source, what moved and what it cost or earned in all,
  without adding up splits in templates: *Grid import*, *Grid export* and
  *Grid net cost*; with solar, *Solar production* and *Solar value*; with a
  battery, *Battery charged*, *Battery discharged* and *Battery net savings*
  (what the battery earned, minus what charging it cost). On a hub that was
  already counting, they start from its split totals, so they match the
  splits from the beginning.
- **Lock sensors for dynamic hubs.** *Export price lock* shows whether the
  window's export price is still waiting for a reading, tentative (read, but
  it may still change during the first minute of the window) or locked. With
  a curtailment source, *Curtailment lock* does the same for the curtailment
  state.
- **An icon.** Dynamic Cost now has its own icon: a dynamic price line that
  zigzags like a lightning bolt, ending in the current price. Home Assistant
  2026.3 and later show it on the integration's pages, with a lighter version
  for dark themes. HACS doesn't show it yet; its successor, Marketplace in
  Home Assistant 2026.11, will.

### Changed
- **Sensors in any energy unit.** Energy sensors are converted to kWh from
  whatever unit they report in: Wh, kWh, MWh and so on. A solar inverter that
  reports its production in Wh used to be counted as if it were kWh, a
  thousand times too much. Price sensors in a currency per MWh are converted
  to per kWh too. Setup now refuses an energy sensor whose unit isn't energy,
  such as a power sensor in W.
- **The window's export price and curtailment state show straight away.**
  They used to read *unknown* until they locked; now they show the tentative
  value meanwhile, and the lock sensors say which it is. What a window is
  settled at doesn't change: always the locked value.
- **Grid export cost is now Grid export profit.** Exporting usually earns
  money, so it now counts up as a profit, like the other profit splits; only
  exporting at a negative price makes it go down.

### Upgrading
- *Grid export profit* replaces *Grid export cost* as a new entity, and the
  old one is removed. Your total so far carries over, with its sign turned
  around. Point any dashboard card that showed the old entity at the new
  one; Home Assistant may list leftover statistics for the old one under
  Developer Tools → Statistics, which you can delete.
- Hubs carry on counting from where they were. Totals already counted from
  a sensor in Wh or MWh are off by a factor 1000 and stay that way: they have
  flowed into the splits, so the update can't repair them. To start those
  totals over, remove the hub and add it again.

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
