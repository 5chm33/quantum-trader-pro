# Data Contract Flow Visual Review

The first rendering was logically correct but too wide and sparse for normal Markdown display. The source was simplified into a compact top-down graph and rendered again.

The final rendering clearly shows the provider payload and immutable raw-byte hash, the separate normalization and availability gate, quarantine for future or unavailable records, the options instrument/quote/underlying/rate/dividend dependency graph, independent valuation and surface diagnostics, and the final immutable snapshot manifest. Code, lockfile, schema hashes, experiment ledger, and holdout seal enter the snapshot independently. The public evidence output is visually separated from private licensed OPRA or vendor rows.

No arrow implies that licensed rows are public, that provider Greeks are authoritative, or that a record may bypass the `available_at` decision cutoff. Text and labels remain legible at full resolution, and the color treatment consistently distinguishes safe admitted state, causal gate, and quarantined/private state.
