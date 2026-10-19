import { inject, Injectable } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { ProjectModelsService } from '../../api-client';

export interface ModelOption {
  value: string;
  label: string;
  kind: 'alias' | 'model';
}

export type ModelKind = 'chat' | 'embedding' | 'reranker';

/** Models and aliases of one type the project can use, for the model dropdowns. An alias
 * belongs to the type of the first model in its chain. */
@Injectable({ providedIn: 'root' })
export class ModelOptions {
  private readonly api = inject(ProjectModelsService);

  async load(projectId: string, kind: ModelKind = 'chat'): Promise<ModelOption[]> {
    const [aliases, models] = await Promise.all([
      firstValueFrom(this.api.projectListAliases({ projectId })),
      firstValueFrom(this.api.projectListModels({ projectId })),
    ]);
    const typeOf = new Map(models.map((m) => [m.name, m.model_type]));
    const options: ModelOption[] = aliases
      .filter((a) => a.model_names.length > 0 && typeOf.get(a.model_names[0]) === kind)
      .map((a) => ({
        value: a.name,
        label: `${a.name} → ${a.model_names.join(', ')}`,
        kind: 'alias',
      }));
    for (const m of models) {
      if (m.model_type === kind && m.status === 'active') {
        const extra = kind === 'embedding' && m.embedding_dims ? `, ${m.embedding_dims} dims` : '';
        options.push({
          value: m.name,
          label: `${m.display_name} (${m.name}${extra})`,
          kind: 'model',
        });
      }
    }
    return options;
  }
}
