import { detectVariables } from './variables';

describe('detectVariables', () => {
  it('finds shorthand and Jinja variables in order, once', () => {
    expect(detectVariables('Hi {name}, {{ topic|upper }} {name}', '{{ extra }}')).toEqual([
      'name',
      'topic',
      'extra',
    ]);
  });

  it('ignores JSON, loop and set variables, and globals', () => {
    const text =
      '{"a": 1} {% for item in items %}{{ item }}{{ loop.index }}{% endfor %}' +
      '{% set n = 2 %}{{ n }} {{ range(3) }}';
    expect(detectVariables(text)).toEqual(['items']);
  });
});
