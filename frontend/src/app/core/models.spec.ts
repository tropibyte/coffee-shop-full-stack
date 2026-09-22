/**
 * Tests for the role ladder.
 *
 * `rankOf` is the frontend half of the privilege hierarchy: it decides which
 * controls a page offers. The server decides whether they work, but a UI that
 * ranks people wrongly either hides work someone can do or invites them to
 * attempt something that will be refused.
 */

import { describe, expect, it } from 'vitest';

import { ROLE_RANKS, rankLabel, rankOf } from './models';

describe('rankOf', () => {
  it('ranks each known role', () => {
    expect(rankOf(['Barista'])).toBe(0);
    expect(rankOf(['Manager'])).toBe(1);
    expect(rankOf(['Administrator'])).toBe(2);
  });

  it('ignores case and surrounding space, as the server does', () => {
    expect(rankOf(['  MANAGER  '])).toBe(1);
    expect(rankOf(['administrator'])).toBe(2);
  });

  it('takes the highest rank when several roles are held', () => {
    expect(rankOf(['Barista', 'Administrator', 'Manager'])).toBe(2);
  });

  it('returns -1 for no roles, so an unranked account outranks nobody', () => {
    expect(rankOf([])).toBe(-1);
    expect(rankOf(undefined)).toBe(-1);
  });

  it('ignores roles it does not recognise', () => {
    expect(rankOf(['Owner', 'Superuser'])).toBe(-1);
    // An unknown role alongside a known one must not raise the rank.
    expect(rankOf(['Owner', 'Barista'])).toBe(0);
  });

  it('matches the ladder the backend defines', () => {
    // ROLE_RANKS mirrors src/management/users_api.py. If one moves and the
    // other does not, the UI and the API disagree about who outranks whom.
    expect(ROLE_RANKS).toEqual({ barista: 0, manager: 1, administrator: 2 });
  });
});

describe('rankLabel', () => {
  it.each([
    [2, 'Administrator'],
    [1, 'Manager'],
    [0, 'Barista'],
    [-1, 'Guest'],
  ])('labels rank %i as %s', (rank, label) => {
    expect(rankLabel(rank)).toBe(label);
  });
});
