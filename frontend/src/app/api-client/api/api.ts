export * from './admin.service';
import { AdminService } from './admin.service';
export * from './adminModels.service';
import { AdminModelsService } from './adminModels.service';
export * from './auth.service';
import { AuthService } from './auth.service';
export * from './bundles.service';
import { BundlesService } from './bundles.service';
export * from './data.service';
import { DataService } from './data.service';
export * from './health.service';
import { HealthService } from './health.service';
export * from './me.service';
import { MeService } from './me.service';
export * from './openai.service';
import { OpenaiService } from './openai.service';
export * from './projectModels.service';
import { ProjectModelsService } from './projectModels.service';
export * from './projects.service';
import { ProjectsService } from './projects.service';
export * from './prompts.service';
import { PromptsService } from './prompts.service';
export * from './rag.service';
import { RagService } from './rag.service';
export * from './system.service';
import { SystemService } from './system.service';
export const APIS = [
  AdminService,
  AdminModelsService,
  AuthService,
  BundlesService,
  DataService,
  HealthService,
  MeService,
  OpenaiService,
  ProjectModelsService,
  ProjectsService,
  PromptsService,
  RagService,
  SystemService,
];
