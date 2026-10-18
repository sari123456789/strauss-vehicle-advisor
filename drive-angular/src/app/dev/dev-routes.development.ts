import {Routes} from '@angular/router';

/** Development-only routes (never part of a production build). */
export const devRoutes: Routes = [
  {path: 'dev/ui-states', loadComponent: () => import('./ui-states.page').then(m => m.UiStatesPage)},
];
