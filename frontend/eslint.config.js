// @ts-check
const eslint = require('@eslint/js');
const { defineConfig } = require('eslint/config');
const tseslint = require('typescript-eslint');
const angular = require('angular-eslint');

module.exports = defineConfig([
  // Generated from the backend OpenAPI spec (make api/client): never edited by hand.
  { ignores: ['src/app/api-client/**'] },
  {
    files: ['**/*.ts'],
    extends: [
      eslint.configs.recommended,
      tseslint.configs.recommended,
      tseslint.configs.stylistic,
      angular.configs.tsRecommended,
    ],
    processor: angular.processInlineTemplates,
    rules: {
      '@angular-eslint/directive-selector': [
        'error',
        {
          type: 'attribute',
          prefix: 'forge',
          style: 'camelCase',
        },
      ],
      '@angular-eslint/component-selector': [
        'error',
        {
          type: 'element',
          prefix: 'forge',
          style: 'kebab-case',
        },
      ],
    },
  },
  {
    files: ['**/*.html'],
    extends: [angular.configs.templateRecommended, angular.configs.templateAccessibility],
    rules: {
      // `<label nz-checkbox nzId="…">` renders its own <input id="…"> inside the label; NG-ZORRO
      // form controls carry the id through `nzId`.
      '@angular-eslint/template/label-has-associated-control': [
        'error',
        {
          labelComponents: [{ selector: 'label', inputs: ['for', 'htmlFor', 'nzId'] }],
          controlComponents: ['nz-select', 'nz-input-number', 'forge-password'],
        },
      ],
    },
  },
]);
