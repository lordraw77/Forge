export * from './admin.service';
import { AdminService } from './admin.service';
export * from './auth.service';
import { AuthService } from './auth.service';
export * from './health.service';
import { HealthService } from './health.service';
export * from './me.service';
import { MeService } from './me.service';
export * from './projects.service';
import { ProjectsService } from './projects.service';
export * from './system.service';
import { SystemService } from './system.service';
export const APIS = [
  AdminService,
  AuthService,
  HealthService,
  MeService,
  ProjectsService,
  SystemService,
];
