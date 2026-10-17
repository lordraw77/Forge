import { definePreset, palette } from '@primeuix/themes';
import Aura from '@primeuix/themes/aura';

/** Brand colours (docs/brand). The mark keeps the pure orange; UI controls use AA-safe shades. */
export const BRAND = { orange: '#F25C05', amber: '#FFB224', navy: '#0B1220' } as const;

export const ForgePreset = definePreset(Aura, {
  semantic: {
    primary: palette(BRAND.orange),
    colorScheme: {
      // White text on primary.700 is 6.1:1; primary.500 itself is only 3.3:1 (WCAG 1.4.3).
      light: {
        primary: {
          color: '{primary.700}',
          contrastColor: '#ffffff',
          hoverColor: '{primary.800}',
          activeColor: '{primary.900}',
        },
        highlight: {
          background: '{primary.50}',
          focusBackground: '{primary.100}',
          color: '{primary.800}',
          focusColor: '{primary.900}',
        },
      },
      // Navy text on the brand orange is 5.6:1.
      dark: {
        primary: {
          color: '{primary.500}',
          contrastColor: BRAND.navy,
          hoverColor: '{primary.400}',
          activeColor: '{primary.300}',
        },
      },
    },
  },
});
