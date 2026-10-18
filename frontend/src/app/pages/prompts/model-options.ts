import { inject, Injectable } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { ProjectModelsService } from '../../api-client';

export interface ModelOption {
  value: string;
  label: string;
  kind: 'alias' | 'model';
}

/** Chat models and aliases the project can use, for the model dropdowns. */
@Injectable({ providedIn: 'root' })
export class ModelOptions {
  private readonly api = inject(ProjectModelsService);

  async load(projectId: string): Promise<ModelOption[]> {
    const [aliases, models] = await Promise.all([
      firstValueFrom(this.api.projectListAliases({ projectId })),
      firstValueFrom(this.api.projectListModels({ projectId })),
    ]);
    const options: ModelOption[] = aliases
      .filter((a) => a.model_names.length > 0)
      .map((a) => ({
        value: a.name,
        label: `${a.name} → ${a.model_names.join(', ')}`,
        kind: 'alias',
      }));
    for (const m of models) {
      if (m.model_type === 'chat' && m.status === 'active') {
        options.push({ value: m.name, label: `${m.display_name} (${m.name})`, kind: 'model' });
      }
    }
    return options;
  }
}
