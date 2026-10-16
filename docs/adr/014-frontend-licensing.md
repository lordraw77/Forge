# ADR-014 – Angular 21 + PrimeNG 21 (MIT) instead of PrimeNG 22

- Status: Accepted provisionally, **pending PO confirmation**
- Date: 2026-10-16
- Amends: ADR-012

## Context

The spec fixes Angular + PrimeNG and states "everything is open source and self-hostable". While scaffolding (E0.4) we found:

| Package | Last MIT version | From version | New licence |
| --- | --- | --- | --- |
| `primeng` | 21.1.10 (Angular 21) | 22.0.0 | PrimeUI commercial licence: free "Community" tier only for organisations under 1 M USD revenue, < 5 developers, < 10 employees; otherwise paid per developer. Requires a licence key (`@primeui/license-manager`); without it a licence banner is shown. |
| `@primeuix/themes` | 2.0.3 | 3.0.0-rc.1 | same |
| `primeicons` | 7.0.0 | 8.0.0 | same |

PrimeNG 22 only supports Angular 22, so staying on MIT PrimeNG means staying on Angular 21.

## Decision

- Pin **Angular 21.2.x**, **PrimeNG 21.1.10**, `@primeuix/themes` 2.0.3, `primeicons` 7.0.0 (exact versions for the PrimeUI packages).
- Keep usage of PrimeNG behind our own wrapper components in `shared/ui` where practical, so a later switch (licensed PrimeNG 22+, or another MIT library such as Angular Material/CDK) touches fewer files.
- CI fails if a dependency with a non-OSI licence enters the production tree (licence check to be added with E0.4 follow-up).

## Consequences

- No licence cost or key management; fully open-source frontend.
- Angular 21 support window: Angular majors get ~18 months of support (6 active + 12 LTS), so Angular 21 is supported until about mid-2027. Before that the PO must choose: buy PrimeUI licences and move to Angular/PrimeNG 22+, or migrate component library.
- PrimeNG 21 receives no new features from PrimeTek; security fixes depend on their community policy.

## Options for the PO

1. **Keep MIT stack (this ADR)** – zero cost, a planned migration decision before mid-2027.
2. **Buy PrimeUI commercial licences** – latest Angular/PrimeNG, cost per developer, licence key in the build.
3. **Switch to Angular Material + CDK** (MIT) – deviates from the spec's fixed choice; the screens table in the spec maps to PrimeNG components.
