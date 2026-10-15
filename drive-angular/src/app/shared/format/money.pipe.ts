import {Pipe, PipeTransform} from '@angular/core';
import {DecimalFormatOptions, formatDecimalString, MISSING_VALUE_DISPLAY} from './decimal-format';

/**
 * `{{ amount | money }}` → "11,693 ₪"; `{{ delta | money:'exceptZero' }}` → "+722 ₪".
 * Formatting only (see decimal-format.ts). Wrap the output in <bdi> inside RTL text.
 */
@Pipe({name: 'money'})
export class MoneyPipe implements PipeTransform {
  transform(value: string | null | undefined, signDisplay: DecimalFormatOptions['signDisplay'] = 'auto', currency = true): string {
    try {
      return formatDecimalString(value, {signDisplay, currency});
    } catch (error) {
      // A malformed amount is a contract violation; never show a guessed number.
      console.error(error);
      return MISSING_VALUE_DISPLAY;
    }
  }
}
