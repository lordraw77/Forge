# ADR-017 – NG-ZORRO on the latest Angular instead of PrimeNG

- Status: Accepted (PO, 2026-10-18)
- Date: 2026-10-18
- Supersedes: [ADR-014](014-frontend-licensing.md)
- Amends: [ADR-012](012-frontend-architecture.md), the component library

## Context

PrimeNG 22 and later are commercial. ADR-014 pinned Angular 21 and PrimeNG 21 (MIT), which meant a forced decision before Angular 21's support ends in mid-2027. The PO chose to switch the component library and move to the current Angular now, while the UI is still small (about ten screens).

## Decision

- **Angular 22.2** (latest stable on 2026-10-18), TypeScript 6.0, angular-eslint 22.
- **NG-ZORRO 22.1** (`ng-zorro-antd`, MIT, Ant Design for Angular) with `@ant-design/icons-angular` (MIT). It has a large component set close to PrimeNG's: table, select, modal, drawer, tree, transfer, timeline, upload, segmented and others.
- **Theme:** NG-ZORRO's CSS-variable theme (`ng-zorro-antd.variable.css`). The brand orange becomes `--ant-primary-*` with AA-safe shades (#C2410C under white text, 5.2:1). Forge tokens are `--forge-*` in `styles.scss`.
- **Icons** are registered statically (`provideNzIcons`): NG-ZORRO would otherwise fetch icons at runtime, which the CSP forbids.
- **Language:** NG-ZORRO's own texts follow the UI language (`en_US` or `it_IT`).
- **Change detection:** components use Angular 22's default `OnPush` on a zoneless application. Template rows iterate component signals, not `nz-table`'s internal data.
- **Lint:** `label-has-associated-control` knows NG-ZORRO controls (`nzId` on `<label nz-checkbox>`, `nz-select`, `nz-input-number`).
- **Specification:** the screen tables now name NG-ZORRO components (`nz-select`, `nz-tabs`, `nz-table`, `nz-drawer`, …).

## Consequences

- No commercial licence, and no pinning to an Angular major that is going out of support.
- **Migration cost.** Every screen was rewritten, while element ids, `data-testid` attributes and message texts were kept, so translations and E2E tests carried over. E2E helpers know that `nz-select` lists are virtual-scrolled.
- **Look.** It changes from Aura to Ant Design, adapted to the brand.
- **Licence gate.** The PrimeNG exception is removed from the policy.
