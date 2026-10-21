import {TestBed} from '@angular/core/testing';
import type {OptionItem} from '../../core/api/types';
import {VehicleCardComponent} from './vehicle-card.component';
import {vehicleImageUrl} from './vehicle-images';

const ATTO: OptionItem = {
  optionKey: 'vehicle:CAR-10',
  category: 'regular',
  catalogueId: 'CAR-10',
  group: 'ג',
  manufacturerName: 'בי ווי די',
  commercialName: 'ATTO 3',
  propulsion: 'electric',
  valueOfUse: '3130',
  defaultParticipation: '300',
  eligibility: 'eligible',
  reasons: ['group_own'],
} as OptionItem;

function render(option: OptionItem, compact = false) {
  const fixture = TestBed.createComponent(VehicleCardComponent);
  fixture.componentRef.setInput('option', option);
  fixture.componentRef.setInput('compact', compact);
  fixture.detectChanges();
  return fixture;
}

describe('VehicleCardComponent', () => {
  it('shows the catalogue photo and the Business data of the option', () => {
    const el: HTMLElement = render(ATTO).nativeElement;
    expect(el.querySelector('img')?.getAttribute('src')).toBe('/assets/vehicles/CAR-10_BYD-Atto-3_2025.png');
    expect(el.querySelector('h2')?.textContent).toContain('ATTO 3');
    expect(el.textContent).toContain('שווי שימוש חודשי');
    expect(el.textContent).toContain('3,130');
  });

  it('falls back to the generic car art when the photo cannot load', () => {
    const fixture = render(ATTO);
    fixture.nativeElement.querySelector('img').dispatchEvent(new Event('error'));
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('drive-car')).not.toBeNull();
  });

  it('uses the generic car art for an option without a known photo', () => {
    const el: HTMLElement = render({...ATTO, optionKey: 'waiver', category: 'waiver', catalogueId: null}).nativeElement;
    expect(el.querySelector('drive-car')).not.toBeNull();
    expect(vehicleImageUrl('CAR-99')).toBeNull();
  });

  it('emits the option key from the comparison button and hides reasons when compact', () => {
    const fixture = render(ATTO, true);
    const emitted: string[] = [];
    fixture.componentInstance.toggle.subscribe(key => emitted.push(key));
    fixture.nativeElement.querySelector('button').click();
    expect(emitted).toEqual(['vehicle:CAR-10']);
    expect(fixture.nativeElement.querySelector('p.tiny')).toBeNull();
  });
});
