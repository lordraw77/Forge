import { AssistIn } from '../../api-client';

export interface FrameworkOption {
  value: AssistIn['framework'];
  label: string;
  hint: string;
}

/**
 * Help Me Write frameworks (US-1.13.1). Free Form is the default and imposes nothing: the model
 * then picks the framework that fits the description best and the result names it.
 */
export const FRAMEWORKS: readonly FrameworkOption[] = [
  {
    value: null,
    label: $localize`Free Form`,
    hint: $localize`No fixed structure. Forge recommends the best-fitting framework and shows which one it used.`,
  },
  {
    value: 'co_star',
    label: 'CO-STAR',
    hint: $localize`Context, Objective, Style, Tone, Audience, Response format. Good for content and communication.`,
  },
  {
    value: 'risen',
    label: 'RISEN',
    hint: $localize`Role, Instructions, Steps, End goal, Narrowing. Good for multi-step tasks with constraints.`,
  },
  {
    value: 'rtf',
    label: 'RTF',
    hint: $localize`Role, Task, Format. Short and direct, for simple tasks.`,
  },
  {
    value: 'crispe',
    label: 'CRISPE',
    hint: $localize`Capacity and role, Insight, Statement, Personality, Experiment. Good for exploring variants.`,
  },
];

export const FREE_FORM_KEY = 'free_form';
