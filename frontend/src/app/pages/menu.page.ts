/**
 * The drink menu.
 *
 * One page, three progressively richer views, decided entirely by what the
 * caller's token permits:
 *
 * * **anyone** sees the drinks and their proportions;
 * * **`get:drinks-detail`** additionally sees ingredient names, fetched from
 *   `/drinks-detail` rather than `/drinks`;
 * * **`post:drinks` / `patch:drinks` / `delete:drinks`** get the editing
 *   controls.
 *
 * The permission checks here choose what to *render*. Every one of them is
 * made again by the API, so a visitor who hand-edits their token gets buttons
 * that return 403.
 */

import { CommonModule } from '@angular/common';
import { Component, OnInit, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';

import { ApiFailure, DrinksService } from '../core/api';
import { AuthService } from '../core/auth.service';
import { Drink, Ingredient } from '../core/models';
import { DrinkGraphicComponent } from '../shared/drink-graphic.component';
import { DrinkFormComponent, DrinkDraft } from './drink-form.component';

@Component({
  selector: 'cs-menu-page',
  standalone: true,
  imports: [CommonModule, FormsModule, DrinkGraphicComponent, DrinkFormComponent],
  templateUrl: './menu.page.html',
  styleUrl: './menu.page.scss',
})
export class MenuPage implements OnInit {
  private readonly drinksApi = inject(DrinksService);
  private readonly route = inject(ActivatedRoute);
  readonly auth = inject(AuthService);

  readonly drinks = signal<Drink[]>([]);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  readonly search = signal('');
  readonly toast = signal<string | null>(null);

  /** The drink being edited, or 'new', or null when the form is closed. */
  readonly editing = signal<Drink | 'new' | null>(null);

  /** Incremented to ask for a re-fetch without changing any other input. */
  private readonly reloadCounter = signal(0);

  /** A permission the router refused, passed through as a query parameter. */
  readonly deniedPermission = signal<string | null>(null);

  readonly canSeeRecipes = computed(() => this.auth.can('get:drinks-detail'));
  readonly canCreate = computed(() => this.auth.can('post:drinks'));
  readonly canEdit = computed(() => this.auth.can('patch:drinks'));
  readonly canDelete = computed(() => this.auth.can('delete:drinks'));
  readonly canManage = computed(() => this.canCreate() || this.canEdit() || this.canDelete());

  readonly visible = computed(() => {
    const term = this.search().trim().toLowerCase();
    if (!term) {
      return this.drinks();
    }
    return this.drinks().filter((drink) => {
      if (drink.title.toLowerCase().includes(term)) {
        return true;
      }
      return drink.recipe.some((part) =>
        (part.name ?? '').toLowerCase().includes(term),
      );
    });
  });

  constructor() {
    // Re-fetch whenever the caller's permissions change, or when something
    // asks for a reload. See fetchDrinks for why this is an effect.
    effect(() => {
      const detailed = this.canSeeRecipes();
      this.reloadCounter();
      this.fetchDrinks(detailed);
    });
  }

  readonly draft = computed<DrinkDraft | null>(() => {
    const target = this.editing();
    if (target === null) {
      return null;
    }
    if (target === 'new') {
      return {
        id: null,
        title: '',
        recipe: [{ name: '', color: '#2ec4f1', parts: 1 }],
      };
    }
    return {
      id: target.id,
      title: target.title,
      // A copy: editing the form must not mutate what the list is showing.
      recipe: target.recipe.map((part) => ({ ...part, name: part.name ?? '' })),
    };
  });

  ngOnInit(): void {
    this.deniedPermission.set(this.route.snapshot.queryParamMap.get('denied'));
  }

  /** Ask for a re-fetch. Bumped by the Refresh button and after every write. */
  load(): void {
    this.reloadCounter.update((count) => count + 1);
  }

  /**
   * Fetch the menu at the richest level the caller is currently allowed.
   *
   * Driven by an effect rather than by ngOnInit, because the caller's
   * permissions can change *after* this page has already rendered:
   *
   * * Coming back from Auth0, the router activates this route before the PKCE
   *   code exchange has finished. A fetch at that moment sees no token and
   *   returns the public short form -- no ingredient names -- and without this
   *   effect the page would go on showing public data under a staff-level
   *   caption once the token landed a moment later.
   * * Signing out has to drop the ingredient names immediately, rather than
   *   leaving recipes on screen until the next reload.
   *
   * `canSeeRecipes()` is read here explicitly, not left to be picked up
   * transitively inside the request, so the dependency cannot be refactored
   * away by accident.
   */
  private fetchDrinks(detailed: boolean): void {
    this.loading.set(true);
    this.error.set(null);

    const request = detailed
      ? this.drinksApi.listDetailed()
      : this.drinksApi.listPublic();

    request.subscribe({
      next: (drinks) => {
        this.drinks.set(drinks);
        this.loading.set(false);
      },
      error: (failure: ApiFailure) => {
        this.error.set(failure.message);
        this.loading.set(false);
      },
    });
  }

  startCreate(): void {
    this.editing.set('new');
  }

  startEdit(drink: Drink): void {
    this.editing.set(drink);
  }

  cancelEdit(): void {
    this.editing.set(null);
  }

  /** Create or update, depending on whether the draft carries an id. */
  save(draft: DrinkDraft): void {
    const recipe: Ingredient[] = draft.recipe.map((part) => ({
      name: part.name.trim(),
      color: part.color.trim(),
      parts: Number(part.parts),
    }));

    const done = (saved: Drink, verb: string) => {
      this.editing.set(null);
      this.showToast(`${saved.title} ${verb}.`);
      this.load();
    };

    const fail = (failure: ApiFailure) => {
      this.error.set(failure.message);
      this.editing.set(null);
    };

    if (draft.id === null) {
      this.drinksApi.create(draft.title.trim(), recipe).subscribe({
        next: (saved) => done(saved, 'added to the menu'),
        error: fail,
      });
    } else {
      this.drinksApi
        .update(draft.id, { title: draft.title.trim(), recipe })
        .subscribe({ next: (saved) => done(saved, 'updated'), error: fail });
    }
  }

  remove(drink: Drink): void {
    const confirmed = window.confirm(
      `Remove "${drink.title}" from the menu?\n\n` +
        'This cannot be undone, though the deletion is recorded in the audit ' +
        'trail along with the full recipe.',
    );
    if (!confirmed) {
      return;
    }

    this.drinksApi.remove(drink.id).subscribe({
      next: () => {
        this.showToast(`${drink.title} removed.`);
        this.load();
      },
      error: (failure: ApiFailure) => this.error.set(failure.message),
    });
  }

  dismissError(): void {
    this.error.set(null);
  }

  dismissDenied(): void {
    this.deniedPermission.set(null);
  }

  private showToast(message: string): void {
    this.toast.set(message);
    setTimeout(() => this.toast.set(null), 3200);
  }
}
