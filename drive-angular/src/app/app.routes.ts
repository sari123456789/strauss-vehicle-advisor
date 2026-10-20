import {Routes} from '@angular/router';
import {employeeGuard, representativeGuard} from './core/auth/guards';
import {devRoutes} from './dev/dev-routes';

export const routes: Routes = [
  ...devRoutes,
  {path: '', loadComponent: () => import('./features/home/home.page').then(m => m.HomePage)},
  {path: 'how-it-works', loadComponent: () => import('./features/how-it-works/how.page').then(m => m.HowPage)},
  {path: 'sign-in', loadComponent: () => import('./features/auth/sign-in.page').then(m => m.SignInPage)},
  {path: 'advisor', canActivate: [employeeGuard], loadComponent: () => import('./features/advisor/advisor.page').then(m => m.AdvisorPage)},
  {
    path: 'advisor/:conversationId',
    canActivate: [employeeGuard],
    loadComponent: () => import('./features/advisor/advisor.page').then(m => m.AdvisorPage),
  },
  {path: 'profile', canActivate: [employeeGuard], loadComponent: () => import('./features/profile/profile.page').then(m => m.ProfilePage)},
  {path: 'catalog', canActivate: [employeeGuard], loadComponent: () => import('./features/catalog/catalog.page').then(m => m.CatalogPage)},
  {
    path: 'comparison',
    canActivate: [employeeGuard],
    loadComponent: () => import('./features/comparisons/comparison.page').then(m => m.ComparisonPage),
  },
  {
    path: 'calculation',
    canActivate: [employeeGuard],
    loadComponent: () => import('./features/comparisons/calculation.page').then(m => m.CalculationPage),
  },
  {path: 'history', canActivate: [employeeGuard], loadComponent: () => import('./features/comparisons/history.page').then(m => m.HistoryPage)},
  {
    path: 'representative',
    canActivate: [representativeGuard],
    loadComponent: () => import('./features/representative/representative.page').then(m => m.RepresentativePage),
  },
  {path: '**', redirectTo: ''},
];
