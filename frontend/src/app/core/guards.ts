import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

import { Session } from './session';

/** Signed in and no pending account step; otherwise redirect to login or the step. */
export const authGuard: CanActivateFn = async (_route, state) => {
  const session = inject(Session);
  const router = inject(Router);
  const me = session.me() ?? (await session.load());
  if (!me) return router.createUrlTree(['/login'], { queryParams: { returnUrl: state.url } });
  const step = session.pendingStep(me.flags);
  return step ? router.parseUrl(step) : true;
};

/** Signed in (account steps themselves). */
export const signedInGuard: CanActivateFn = async () => {
  const session = inject(Session);
  const me = session.me() ?? (await session.load());
  return me ? true : inject(Router).parseUrl('/login');
};

export const superAdminGuard: CanActivateFn = () => {
  const session = inject(Session);
  return session.user()?.is_super_admin ? true : inject(Router).parseUrl('/');
};

/** The login page redirects away when a session already exists. */
export const guestGuard: CanActivateFn = async () => {
  const session = inject(Session);
  const me = session.me() ?? (await session.load());
  return me ? inject(Router).parseUrl(session.pendingStep(me.flags) ?? '/') : true;
};
