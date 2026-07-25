# Roadmap — triage of the Improvement Specification

Status of every item in `Portage_Horse_Race_Application_Improvement_Specification.docx`
against the codebase. Legend: ✅ done · 🆕 added in v0.5.0 · 🔜 planned · ⏸ deferred (rationale).

## Shipped since this doc was written
- **v0.5.0:** FIN-02 (settlement gate), BKP-02 (backup health), REL-04 (update check), REL-01 (checksums), UX-04 (help page).
- **v0.6.0:** RPT-02 (settlement package ZIP), RPT-01 (payout signature), SEC-05 (audit filter/export), SET-02 (CSV import), SET-03 (readiness checklist), UX-07 (demo tournament), POS-05 (order references), POS-03 (repeat customer), FIN-04 (settlement lock + audited reopen), RPT-04 (contact tracking), FIN-05 (reconciliation now includes reversed/outstanding), TST-01 (property tests), TST-02 (golden lifecycle). Also fixed a latent auto-settle bug.
- **v0.7.0 (aesthetics & branding):** full design system in `static/css/app.css` (fairway-green brand, gradient navbar, soft-shadow cards, refined typography/forms/tables, tabular money, brand status pills); Bootstrap Icons bundled locally (offline) and applied consistently across every screen's nav, buttons and headers; refined app icon (supersampled disc with depth, multi-resolution `.ico`); brand mark + logo on the navbar and home hub. Advances **SET-06** (branding) and **UX-05** (accessibility: consistent iconography with text labels, focus outlines, status never by colour alone).
- **v0.8.0 (recommendations release):**
  - *Cashier (POS-01/08):* inline HTMX order card with search re-focus (no full reload), quick-tender buttons, fat-finger guard on 10+ entries, one-click undo of a whole order by reference, keyboard shortcuts.
  - *Cash reconciliation (FIN-05):* real drawer equation — opening float + cash in − cash paid out — replacing the counted-vs-gross variance that showed a phantom short; audited cash counts + over/short reason; unexplained variance blocks settlement; Club Handover Statement report.
  - *Setup reuse (SET-02/03/05):* clone a past event into a fresh draft; rename teams/players and move players (audited, guarded); CSV import auto-creates teams; name/date editable after sales; <3-player advisory.
  - *Customers/winners (POS-05, RPT-06, FIN-08):* printable checkout receipt + reference-code order lookup; winner notices and an outstanding-winners call sheet (print + CSV); per-winner waive/donate (WAIVED) to close out unreachable winners.
  - *Reliability & record (BKP-04/05/06, FIN-03):* native USB dialogs (copy-to / restore-from / save package); verified backups + retention; cadence auto-save; crash-recovery all-clear; settlement ZIP now self-proving (MANIFEST.sha256 + fingerprint + OPEN_ME.html).

Still open (highest value first): BKP-04 removable-drive export, SET-06 branding, POS-04 merge tool, RPT-06 winner notice, BKP-06 retention, BKP-05 pre-settlement integrity, RPT-05 en-CA formatting, SEC-04 retention/anonymize, REL-05 diagnostics, UX-05 accessibility pass. Deferred by design: DSP-\*, NET-\*, UX-06 themes, REL-06 code signing.

## Financial integrity & settlement
| ID | Status | Notes |
|----|--------|-------|
| FIN-01 pure cents engine | ✅ | `services/calculations.py`, integer cents/basis points, golden tests |
| FIN-02 pre-settlement gate | 🆕 | payouts blocked until wagering closed + 3 placements + split=100%; reasons shown |
| FIN-03 idempotent generation | ✅(partial) | duplicate generation blocked; regenerate clears. Settlement hash 🔜 |
| FIN-04 settlement lock | 🔜 | SETTLED state exists + voids blocked once payouts exist; strict read-only + audited reopen still to add |
| FIN-05 reconciliation totals | ✅ | reconciliation report; per-team + tournament. Add explicit voids/unpaid-liability lines 🔜 |
| FIN-06 rounding policy | ⏸ | deterministic default already enforced; making it configurable is low value |
| FIN-07 payout acknowledgement | ✅ | method/operator/time/note recorded; duplicate blocked |
| FIN-08 unclaimed *payout* handling | ✅(pool) 🔜(waive/transfer) | unclaimed *pool* disposition done; per-winner waive/transfer statuses 🔜 |

## Cashier
| ID | Status | Notes |
|----|--------|-------|
| POS-01 keyboard-first | 🔜 | search-driven already; explicit shortcuts to add |
| POS-02 atomic order cart | ✅ | cart + single atomic checkout |
| POS-03 rapid repeat | 🔜 | "same customer again" shortcut after checkout |
| POS-04 duplicate detection | ✅ / 🔜 merge | detect by name/phone/email done; admin **merge tool** deferred |
| POS-05 reference codes | 🔜 | human-readable per-order reference (e.g. A7F-0241) |
| POS-06 tender/change | ✅ | change shown at checkout |
| POS-07 configurable required fields | ⏸ | name-only default is fine for volunteers |
| POS-08 undo-last-sale | ✅ | void from recent activity / ledger |

## Setup & reusability
| SET-01 templates | ⏸→🔜 | reuse via clone; template store is a v1.2 item |
| SET-02 CSV player import | 🔜 | high value for 80-player teams (paste already works) |
| SET-03 setup checklist | 🔜 | dashboard warnings partial; dedicated checklist to add |
| SET-04 arbitrary teams/tiers | ✅ teams / ⏸ tiers | any number of teams; 1/2/3 tiers fixed by design |
| SET-05 clone/archive | ✅ archive / 🔜 clone | |
| SET-06 branding/logo | 🔜 | logo on reports (v1.2) |

## Reports & outputs
| RPT-01 print payout sheets | ✅(register) 🔜(per-place + signature) | |
| RPT-02 settlement package ZIP | 🔜 | one-click ZIP: reports + CSVs + audit + backup — high value |
| RPT-03 owner consolidation | ✅ | payouts consolidate per customer |
| RPT-04 contact-action tracking | 🔜 | called/emailed/no-response flags |
| RPT-05 filters + en-CA export | ✅ filters / 🔜 en-CA | |
| RPT-06 winner notice | 🔜 | |

## Backup, restore & portability
| BKP-01 tested restore | ✅ | validated restore, pre-restore safety backup, tested |
| BKP-02 backup health | 🆕 | last-backup time on Dashboard + Admin; stale warning |
| BKP-03 portable package | 🔜 | overlaps RPT-02 |
| BKP-04 removable-drive export | 🔜 | needs native file-dialog (pywebview supports it) |
| BKP-05 integrity check | ✅ startup / 🔜 pre-settlement | `PRAGMA quick_check` at startup |
| BKP-06 retention policy | 🔜 | backups currently keep-all |

## UX & accessibility
| UX-01 event-day mode | ✅(partial) | nav grouped; event tabs disabled without a tournament |
| UX-02 state banner | ✅ | status badge on every screen |
| UX-03 consequence-based confirm | ✅ | destructive actions confirm + PIN |
| UX-04 embedded help/quick-start | 🆕 | printable `/help` page |
| UX-05 accessibility pass | 🔜 | labels/focus/contrast largely present; formal WCAG pass pending |
| UX-06 themes | ⏸ | low value for a single-purpose tool |
| UX-07 demo tournament | 🔜 | |

## Live display
| DSP-01/02 read-only display, projector | ⏸ | v1.1+; only if used |
| DSP-03/04 popularity, QR | ⏸ | P3 |

## Security, privacy, audit
| SEC-01 protect destructive actions | ✅ | admin PIN gates + audit |
| SEC-02 loopback only | ✅ | binds 127.0.0.1; no LAN by default |
| SEC-03 protect exports / PII warning | ✅(warning) 🔜(password) | reports flagged sensitive |
| SEC-04 retention/anonymize | 🔜 | |
| SEC-05 audit usability | ✅(view) 🔜(filter/export) | structured before/after JSON stored |
| SEC-06 named operators | ✅(basic) | operator/station recorded |

## Installer, releases, updates
| REL-01 versioned + checksum | 🆕 | SHA-256 published with each installer |
| REL-02 upgrade/rollback test | ✅(manual) | upgrades verified; same AppId preserves data |
| REL-03 GitHub releases | ✅ | stable `/releases/latest` |
| REL-04 update check | 🆕 | manual "Check for updates" on Admin (offline-safe) |
| REL-05 diagnostic bundle | 🔜 | |
| REL-06 code signing | ⏸ | needs a paid cert; only if distributed widely |

## Testing & quality
| TST-01 property tests | 🔜 | |
| TST-02 golden end-to-end | ✅(partial) | settlement flow covered; single full-lifecycle test 🔜 |
| TST-03 crash/restart | ⏸ | transactions/idempotency in place; hard to automate |
| TST-04 clean-VM installer | ⏸ | manual |
| TST-05 UI regression shots | ✅(assets) | Playwright used for the walkthrough |
| TST-06 release checklist | 🔜 | |
| TST-07 volume test | 🔜 | |

## Network edition (NET-\*)
⏸ Explicitly deferred per the spec — single-machine design stays unless a real multi-cashier need appears.

---

### Next planned increment (v1.1 candidates, highest value first)
1. RPT-02 settlement package ZIP (permanent event record)
2. SET-02 CSV player import + SET-03 setup checklist
3. POS-05 reference codes + POS-03 repeat-customer
4. FIN-04 strict settlement lock + audited reopen
5. TST-01/02 property + golden lifecycle tests
