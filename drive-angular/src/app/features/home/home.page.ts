import {ChangeDetectionStrategy, Component} from '@angular/core';
import {RouterLink} from '@angular/router';
import {OptionCardComponent} from './option-card.component';
import {SlideIndicatorComponent} from './slide-indicator.component';

@Component({
  imports: [RouterLink, OptionCardComponent, SlideIndicatorComponent],
  templateUrl: './home.page.html',
  styleUrl: './home.page.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class HomePage {}
