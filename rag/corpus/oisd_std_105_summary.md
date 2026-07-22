# OISD-STD-105 — Work Permit System (Summary)

**Publisher:** Oil Industry Safety Directorate (OISD), Ministry of Petroleum &
Natural Gas, Government of India.
**Current edition:** August 2023 (an earlier Revision I, September 2004, also
circulates).

> This is a compiled summary written for this project's RAG demo corpus — it is
> **not** a verbatim reproduction of the copyrighted standard, which OISD sells
> rather than distributes as a free PDF. For authoritative clause-level text,
> refer to the official OISD-STD-105 (Aug 2023 edition) via oisd.gov.in. Nothing
> below should be cited as an exact quotation of the standard.

## Purpose and scope

OISD-STD-105 defines the **permit-to-work (PTW) system** used across Indian
hydrocarbon processing and handling installations (refineries, gas plants, and by
extension, adopted widely across Indian heavy industry including steel plants) to
ensure that non-routine work in hazardous areas is planned, authorized, and executed
safely — preventing injury, property loss, and fire/explosion incidents.

## Permit types

The standard defines distinct permit formats, most commonly:

- **Cold work permit** — for work with no ignition source risk (e.g. inspection,
  non-hot mechanical work).
- **Hot work permit** — for any work generating a source of ignition (welding,
  cutting, grinding, use of open flame or spark-producing tools) in or near a
  hazardous area.
- **Confined space entry permit** — for entry into vessels, ducts, tanks, or other
  enclosed spaces where atmospheric hazards (toxic/flammable gas, oxygen
  deficiency) or restricted egress are a concern.
- **Electrical isolation / energization permit** — for work requiring electrical
  equipment to be de-energized and locked out, and for its safe re-energization.

**Critically, when a job requires more than one of these conditions at once — for
example, hot work to be carried out *inside* a confined space — the standard
requires a combined permit covering both hazard types, not two independently
authorized permits treated in isolation.** This is the exact failure mode a
compound-risk detector needs to watch for: a hot-work permit and a confined-space
permit both active in the same zone at the same time is not "two normal permits,"
it is a single elevated-risk condition.

## Core permit-to-work principles (well-established industry practice this standard
codifies)

- **Gas testing before and during work.** Before a hot-work or confined-space
  permit is issued, and periodically while the work is ongoing, the atmosphere must
  be tested for flammable gas (as %LEL), toxic gas, and oxygen content. A permit
  should not remain valid if conditions drift out of safe range during the job.
- **Isolation of energy sources.** Before confined-space entry or hot work,
  relevant process lines, electrical circuits, and rotating equipment in the area
  must be isolated/locked out, not merely shut down.
- **Time-bound validity.** Permits are valid for a defined shift/period and must be
  renewed (with re-inspection) rather than left open-ended.
- **Simultaneous-operations (SIMOPS) cross-check.** Before issuing a new permit, the
  issuer must check for other active permits or ongoing operations (e.g.
  maintenance) in the same or adjacent area that could interact dangerously — this
  is the standard's explicit mechanism for catching compound risk before work
  starts, not just monitoring for it afterward.
- **Issuer/receiver responsibility split.** The permit issuer (area
  owner/authorized person) is responsible for verifying the area is safe to start
  work; the permit receiver (the work crew/contractor) is responsible for
  maintaining safe conditions and stopping work if conditions change; both sign off.
- **Shift handover continuity.** An active permit spanning a shift changeover
  requires an explicit handover briefing between outgoing and incoming
  supervisors/permit issuers — a verbal-only or skipped handover during an open
  hot-work/confined-space permit is itself a recognized risk factor, not a neutral
  routine event.

## Why this matters for compound-risk detection

None of the individual elements above — a hot-work permit, a gas reading below the
high alarm, a routine shift handover — is itself the hazard. The standard's own
SIMOPS cross-check requirement exists because the *combination*, in the same area,
at the same time, is what the permit system is actually designed to prevent.
