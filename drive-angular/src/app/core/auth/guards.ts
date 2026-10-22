import {inject} from '@angular/core';
import {CanActivateFn, Router} from '@angular/router';
import {SessionService} from './session.service';

/** Employee screens: a signed-in employee (after the startup session restore). */
export const employeeGuard: CanActivateFn = async (_route, state) => {
  const session = inject(SessionService);
  const router = inject(Router);
  await session.restore();
  if (session.isEmployee()) return true;
  if (session.isRepresentative()) return router.parseUrl('/representative');
  return router.createUrlTree(['/sign-in'], {queryParams: {returnUrl: state.url}});
};

/** The representative inbox: a signed-in representative only. */
export const representativeGuard: CanActivateFn = async (_route, state) => {
  const session = inject(SessionService);
  const router = inject(Router);
  await session.restore();
  if (session.isRepresentative()) return true;
  if (session.isEmployee()) return router.parseUrl('/advisor');
  return router.createUrlTree(['/sign-in'], {queryParams: {returnUrl: state.url}});
};

/** The home page is the employee's entry point; a representative goes to the request centre. */
export const homeGuard: CanActivateFn = async () => {
  const session = inject(SessionService);
  const router = inject(Router);
  await session.restore();
  return session.isRepresentative() ? router.parseUrl('/representative') : true;
};
