/**
 * Renders a drink as a glass of proportional coloured layers.
 *
 * The starter drew a flat ring of segments. This draws the drink itself: each
 * ingredient becomes a band whose height is its share of the total parts, so
 * "one part foam to three parts milk" is legible at a glance rather than
 * something you work out from a legend.
 *
 * Accessibility notes, since a purely colour-coded graphic is useless to a
 * good number of people:
 *
 * * the SVG carries a text description listing every layer and its share;
 * * each band is also labelled with its percentage when there is room;
 * * the layer list below the glass repeats the same information as text.
 *
 * Colours come from the API, which constrains them to hex, rgb() or a CSS
 * colour name, so interpolating one into a style binding cannot smuggle in
 * arbitrary CSS.
 */

import { CommonModule } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { Drink, Ingredient } from '../core/models';

interface Band {
  ingredient: Ingredient;
  /** Share of the total, 0..1. */
  share: number;
  /** Y offset within the liquid area, in SVG units. */
  y: number;
  height: number;
  percent: number;
  label: string;
}

/** Liquid area of the glass, in SVG user units. */
const LIQUID_TOP = 26;
const LIQUID_HEIGHT = 112;

@Component({
  selector: 'cs-drink-graphic',
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <figure class="glass" [class.glass--compact]="compact()">
      <svg
        viewBox="0 0 120 176"
        role="img"
        [attr.aria-label]="description()"
        class="glass__svg"
      >
        <defs>
          <clipPath [attr.id]="clipId()">
            <!-- A tapered tumbler: the liquid is clipped to this shape. -->
            <path d="M20 22 H100 L90 150 Q88 158 80 158 H40 Q32 158 30 150 Z" />
          </clipPath>
          <linearGradient [attr.id]="shineId()" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stop-color="#ffffff" stop-opacity="0.34" />
            <stop offset="22%" stop-color="#ffffff" stop-opacity="0.05" />
            <stop offset="76%" stop-color="#ffffff" stop-opacity="0" />
            <stop offset="94%" stop-color="#ffffff" stop-opacity="0.16" />
          </linearGradient>
        </defs>

        <!-- Liquid layers, stacked from the top down. -->
        <g [attr.clip-path]="'url(#' + clipId() + ')'">
          <rect x="18" y="20" width="84" height="140" class="glass__empty" />
          @for (band of bands(); track band.label) {
            <rect
              x="18"
              width="84"
              [attr.y]="band.y"
              [attr.height]="band.height"
              [style.fill]="band.ingredient.color"
            />
          }
          <rect x="18" y="20" width="84" height="140" [attr.fill]="'url(#' + shineId() + ')'" />
        </g>

        <!-- Per-band percentage, only where the band is tall enough to hold it. -->
        @for (band of bands(); track band.label) {
          @if (band.height >= 16 && !compact()) {
            <text
              x="60"
              [attr.y]="band.y + band.height / 2 + 4"
              class="glass__band-label"
              [style.fill]="contrastFor(band.ingredient.color)"
            >{{ band.percent }}%</text>
          }
        }

        <!-- Glass outline, drawn last so it sits above the liquid. -->
        <path
          d="M20 22 H100 L90 150 Q88 158 80 158 H40 Q32 158 30 150 Z"
          class="glass__outline"
        />
        <ellipse cx="60" cy="22" rx="40" ry="6" class="glass__rim" />
        <path d="M34 166 H86" class="glass__saucer" />
      </svg>

      @if (!compact()) {
        <figcaption class="layers">
          @for (band of bands(); track band.label) {
            <span class="layers__item">
              <span
                class="layers__swatch"
                [style.background]="band.ingredient.color"
                aria-hidden="true"
              ></span>
              <span class="layers__text">
                {{ band.label }}
                <span class="layers__share">{{ band.percent }}%</span>
              </span>
            </span>
          }
        </figcaption>
      }
    </figure>
  `,
  styles: [
    `
      :host {
        display: block;
      }

      .glass {
        margin: 0;
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 10px;
      }

      .glass__svg {
        width: 100%;
        max-width: 132px;
        height: auto;
        display: block;
      }

      .glass--compact .glass__svg {
        max-width: 74px;
      }

      .glass__empty {
        fill: color-mix(in srgb, var(--cs-border) 45%, transparent);
      }

      .glass__outline {
        fill: none;
        stroke: var(--cs-border-strong);
        stroke-width: 2.4;
        stroke-linejoin: round;
      }

      .glass__rim {
        fill: none;
        stroke: var(--cs-border-strong);
        stroke-width: 2.4;
      }

      .glass__saucer {
        stroke: var(--cs-border-strong);
        stroke-width: 3.4;
        stroke-linecap: round;
        opacity: 0.75;
      }

      .glass__band-label {
        font-size: 10px;
        font-weight: 700;
        text-anchor: middle;
        letter-spacing: 0.02em;
        paint-order: stroke;
      }

      .layers {
        display: flex;
        flex-wrap: wrap;
        justify-content: center;
        gap: 6px 12px;
        font-size: 0.78rem;
      }

      .layers__item {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        color: var(--cs-text-muted);
      }

      .layers__swatch {
        width: 11px;
        height: 11px;
        border-radius: 3px;
        border: 1px solid var(--cs-border-strong);
        flex: none;
      }

      .layers__share {
        color: var(--cs-text-faint);
        font-variant-numeric: tabular-nums;
      }
    `,
  ],
})
export class DrinkGraphicComponent {
  /** The drink to draw. */
  readonly drink = input.required<Drink>();

  /** Smaller, label-free rendering for dense lists. */
  readonly compact = input(false);

  /**
   * Unique ids for the SVG `clipPath` and gradient.
   *
   * SVG ids are global to the document, so two graphics on one page would
   * otherwise share -- and fight over -- the same clip path.
   */
  private readonly uid = Math.random().toString(36).slice(2, 9);
  readonly clipId = computed(() => `glass-clip-${this.uid}`);
  readonly shineId = computed(() => `glass-shine-${this.uid}`);

  readonly bands = computed<Band[]>(() => {
    const recipe = this.drink().recipe ?? [];
    const total = recipe.reduce((sum, item) => sum + (Number(item.parts) || 0), 0);
    if (total <= 0) {
      return [];
    }

    let offset = LIQUID_TOP;
    return recipe.map((ingredient, index) => {
      const share = (Number(ingredient.parts) || 0) / total;
      const height = share * LIQUID_HEIGHT;
      const band: Band = {
        ingredient,
        share,
        y: offset,
        height,
        percent: Math.round(share * 100),
        // The public short form has no `name`; fall back to a position label
        // so the legend still distinguishes the layers.
        label: ingredient.name?.trim() || `Layer ${index + 1}`,
      };
      offset += height;
      return band;
    });
  });

  /** A text description of the whole drink, for screen readers. */
  readonly description = computed(() => {
    const drink = this.drink();
    const bands = this.bands();
    if (!bands.length) {
      return `${drink.title}: no recipe recorded.`;
    }
    const parts = bands
      .map((band) => `${band.label} ${band.percent} percent`)
      .join(', ');
    return `${drink.title}, ${bands.length} layers from top to bottom: ${parts}.`;
  });

  /**
   * Pick black or white for a label drawn on top of `color`.
   *
   * Uses the WCAG relative-luminance formula rather than a naive average, so
   * a saturated green and a saturated blue of the same RGB sum are treated
   * differently -- which is exactly how the eye treats them.
   */
  contrastFor(color: string): string {
    const rgb = parseColor(color);
    if (!rgb) {
      return '#1b1b1b';
    }
    const channel = (value: number): number => {
      const scaled = value / 255;
      return scaled <= 0.03928
        ? scaled / 12.92
        : Math.pow((scaled + 0.055) / 1.055, 2.4);
    };
    const luminance =
      0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
    return luminance > 0.45 ? '#1b1b1b' : '#ffffff';
  }
}

/** Resolved colours, keyed by the string that produced them. */
const colorCache = new Map<string, [number, number, number] | null>();

/**
 * Parse any accepted colour into RGB channels.
 *
 * Hex and `rgb()` are read directly. A CSS colour *name* is resolved by
 * asking the browser: there are 148 of them and hard-coding the table would
 * be both long and a second thing to keep correct. Results are cached,
 * because this is called while the preview re-renders on every keystroke.
 */
function parseColor(color: string): [number, number, number] | null {
  const value = color.trim();
  if (!value) {
    return null;
  }

  const cached = colorCache.get(value);
  if (cached !== undefined) {
    return cached;
  }

  const resolved = resolveColor(value);
  colorCache.set(value, resolved);
  return resolved;
}

function resolveColor(value: string): [number, number, number] | null {
  const hex = value.match(/^#([0-9a-f]{3,8})$/i);
  if (hex) {
    let digits = hex[1];
    if (digits.length === 3 || digits.length === 4) {
      digits = digits
        .slice(0, 3)
        .split('')
        .map((d) => d + d)
        .join('');
    }
    if (digits.length >= 6) {
      return [
        parseInt(digits.slice(0, 2), 16),
        parseInt(digits.slice(2, 4), 16),
        parseInt(digits.slice(4, 6), 16),
      ];
    }
    return null;
  }

  const rgb = value.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
  if (rgb) {
    return [Number(rgb[1]), Number(rgb[2]), Number(rgb[3])];
  }

  // A named colour. Let the browser do the lookup.
  if (typeof document === 'undefined') {
    return null;
  }
  const probe = document.createElement('span');
  probe.style.display = 'none';
  // An invalid value leaves the property empty, which is how an unknown
  // name is told apart from a real one that happens to be black.
  probe.style.color = value;
  if (!probe.style.color) {
    return null;
  }
  document.body.appendChild(probe);
  const computed = getComputedStyle(probe).color;
  probe.remove();

  const parsed = computed.match(/(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
  return parsed
    ? [Number(parsed[1]), Number(parsed[2]), Number(parsed[3])]
    : null;
}
