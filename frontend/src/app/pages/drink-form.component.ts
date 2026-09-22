/**
 * Create/edit dialog for a drink.
 *
 * The form previews the drink as it is edited, because the thing being
 * configured is a picture and a list of numbers is a poor proxy for it.
 *
 * Validation here mirrors the server's, which is the authority: colours are
 * restricted to hex, rgb() or a CSS colour name, parts must be positive, and
 * a recipe needs at least one ingredient. Validating in the browser makes the
 * form pleasant; validating in the model makes it correct.
 */

import { CommonModule } from '@angular/common';
import {
  Component,
  ElementRef,
  OnInit,
  computed,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';

import { DrinkGraphicComponent } from '../shared/drink-graphic.component';
import { Drink } from '../core/models';

/** An ingredient row while it is being edited, where fields may be blank. */
export interface DraftIngredient {
  name: string;
  color: string;
  parts: number;
}

/** A drink being edited. `id` is null for a new one. */
export interface DrinkDraft {
  id: number | null;
  title: string;
  recipe: DraftIngredient[];
}

const HEX = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/;
const RGB = /^rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(?:,\s*(?:0|1|0?\.\d+)\s*)?\)$/;
const NAMED = /^[a-zA-Z]{3,20}$/;

/** A palette of ready-made layer colours, so nobody has to type hex. */
const SWATCHES = [
  '#2ec4f1',
  '#4b2e1e',
  '#f4e6cd',
  '#c8a27a',
  '#6f9b4a',
  '#b3322a',
  '#d98c2b',
  '#8e6bb5',
  '#2e8b57',
  '#efe2c8',
];

@Component({
  selector: 'cs-drink-form',
  standalone: true,
  imports: [CommonModule, FormsModule, DrinkGraphicComponent],
  templateUrl: './drink-form.component.html',
  styleUrl: './drink-form.component.scss',
})
export class DrinkFormComponent implements OnInit {
  readonly draft = input.required<DrinkDraft>();

  readonly save = output<DrinkDraft>();
  readonly cancel = output<void>();

  private readonly titleInput =
    viewChild<ElementRef<HTMLInputElement>>('titleInput');

  readonly title = signal('');
  readonly recipe = signal<DraftIngredient[]>([]);
  readonly swatches = SWATCHES;

  /** True once the user has tried to submit, so errors are not premature. */
  readonly submitted = signal(false);

  readonly isNew = computed(() => this.draft().id === null);

  readonly titleError = computed(() => {
    const value = this.title().trim();
    if (!value) {
      return 'A title is required.';
    }
    if (value.length > 80) {
      return 'Titles are limited to 80 characters.';
    }
    return null;
  });

  readonly recipeErrors = computed(() =>
    this.recipe().map((row) => {
      if (!row.name.trim()) {
        return 'Name this ingredient.';
      }
      if (row.name.trim().length > 60) {
        return 'Ingredient names are limited to 60 characters.';
      }
      const color = row.color.trim();
      if (!color) {
        return 'Pick a colour.';
      }
      if (!HEX.test(color) && !RGB.test(color) && !NAMED.test(color)) {
        return 'Use a hex value (#2ec4f1), rgb(), or a CSS colour name.';
      }
      const parts = Number(row.parts);
      if (!Number.isFinite(parts) || parts <= 0) {
        return 'Parts must be greater than zero.';
      }
      if (parts > 1000) {
        return 'Parts must be 1000 or less.';
      }
      return null;
    }),
  );

  readonly isValid = computed(
    () =>
      this.titleError() === null &&
      this.recipe().length > 0 &&
      this.recipeErrors().every((error) => error === null),
  );

  /** What the drink will look like once saved. */
  readonly preview = computed<Drink>(() => ({
    id: this.draft().id ?? 0,
    title: this.title().trim() || 'Untitled drink',
    recipe: this.recipe()
      .filter((row) => Number(row.parts) > 0)
      .map((row) => ({
        name: row.name.trim() || 'Unnamed',
        // Fall back to a neutral colour so an in-progress value does not
        // make the preview vanish mid-keystroke.
        color: this.isDrawableColor(row.color) ? row.color.trim() : '#cccccc',
        parts: Number(row.parts),
      })),
  }));

  readonly totalParts = computed(() =>
    this.recipe().reduce((sum, row) => sum + (Number(row.parts) || 0), 0),
  );

  ngOnInit(): void {
    const source = this.draft();
    this.title.set(source.title);
    this.recipe.set(source.recipe.map((row) => ({ ...row })));

    // Focus the title so the dialog is usable from the keyboard immediately.
    setTimeout(() => this.titleInput()?.nativeElement.focus(), 40);
  }

  addIngredient(): void {
    if (this.recipe().length >= 20) {
      return;
    }
    const nextColor = SWATCHES[this.recipe().length % SWATCHES.length];
    this.recipe.update((rows) => [
      ...rows,
      { name: '', color: nextColor, parts: 1 },
    ]);
  }

  removeIngredient(index: number): void {
    this.recipe.update((rows) => rows.filter((_, position) => position !== index));
  }

  moveIngredient(index: number, direction: -1 | 1): void {
    const target = index + direction;
    this.recipe.update((rows) => {
      if (target < 0 || target >= rows.length) {
        return rows;
      }
      const next = [...rows];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }

  updateField(index: number, field: keyof DraftIngredient, value: string): void {
    this.recipe.update((rows) =>
      rows.map((row, position) =>
        position === index
          ? { ...row, [field]: field === 'parts' ? Number(value) : value }
          : row,
      ),
    );
  }

  submit(): void {
    this.submitted.set(true);
    if (!this.isValid()) {
      return;
    }
    this.save.emit({
      id: this.draft().id,
      title: this.title(),
      recipe: this.recipe(),
    });
  }

  close(): void {
    this.cancel.emit();
  }

  /** Close on Escape, the behaviour a dialog is expected to have. */
  onKeydown(event: KeyboardEvent): void {
    if (event.key === 'Escape') {
      event.stopPropagation();
      this.close();
    }
  }

  /** Percentage this row contributes, for the inline hint. */
  shareOf(index: number): number {
    const total = this.totalParts();
    if (total <= 0) {
      return 0;
    }
    return Math.round(((Number(this.recipe()[index]?.parts) || 0) / total) * 100);
  }

  private isDrawableColor(color: string): boolean {
    const value = color.trim();
    return HEX.test(value) || RGB.test(value) || NAMED.test(value);
  }
}
