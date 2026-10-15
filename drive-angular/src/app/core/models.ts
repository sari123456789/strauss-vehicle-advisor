export interface Vehicle {id:string; name:string; type:'electric'|'hybrid'|'family'; group:string; participation:number; benefit:number; description:string;}
export interface Employee {salary:number; additional:number; group:string; credits:number; pension:number; fund:number;}
export interface Message {role:'advisor'|'user';text:string;}
export interface Inquiry {id:string; employee:string; question:string; status:'open'|'answered'; answer?:string;}
export interface Comparison {id:string;date:string;vehicleId:string;status:'complete'|'pending';}
