import {optionLabel} from '../../core/api/labels';
import type {OptionItem} from '../../core/api/types';

/**
 * The chat message that asks the advisor to compare the selected options with today. Sent from
 * the vehicles panel and from the comparison page, so the advisor asks what it still needs (one
 * confirmation of saved details at most) and shows the difference in the chat. Model names stay
 * in the catalogue's spelling, which is what the advisor looks up.
 */
export function compareMessage(options: OptionItem[]): string | null {
  const names = options.filter(o => o.optionKey !== 'waiver').map(o => o.commercialName || optionLabel(o));
  if (options.some(o => o.optionKey === 'waiver')) names.push('ויתור על הרכב');
  if (!names.length) return null;
  return names.length === 1
    ? `אני רוצה לראות כמה ישתנה לי הנטו לעומת היום עם ${names[0]}`
    : `אני רוצה לראות כמה ישתנה לי הנטו לעומת היום בכל אחת מהאפשרויות: ${names.join(', ')}`;
}
