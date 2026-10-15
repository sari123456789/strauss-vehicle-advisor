import {Injectable,InjectionToken} from '@angular/core';import {of,delay,Observable} from 'rxjs';import {Vehicle,Employee} from '../models';
export const API_BASE_URL=new InjectionToken<string>('API_BASE_URL',{providedIn:'root',factory:()=>'/api'});
export abstract class DriveRepository {abstract vehicles():Observable<Vehicle[]>;}
@Injectable({providedIn:'root'}) export class DemoDriveRepository extends DriveRepository {
vehicles(){return of<Vehicle[]>([
{id:'ev',name:'Electric GT',type:'electric',group:'ג',participation:350,benefit:3200,description:'נסיעה שקטה. בחירה חכמה.'},
{id:'hybrid',name:'Hybrid Executive',type:'hybrid',group:'ב',participation:250,benefit:3510,description:'איזון בין נוחות לביצועים.'},
{id:'family',name:'Family Touring',type:'family',group:'ד',participation:500,benefit:4200,description:'יותר מקום לכל מה שחשוב.'}]).pipe(delay(200));}}
