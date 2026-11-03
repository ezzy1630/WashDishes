// Send and league review keep every housemate in the same alphabetical position.
import { h } from './core.js';

export function nameChips(people, { selected = null, guess = null, choose, disabled = false }) {
  return [...people].sort((a, b) => a.name.localeCompare(b.name)).map(person => {
    const chosen = selected === person.id, guessed = selected == null && guess === person.id;
    return h('button', {
      type: 'button', class: `name-chip${chosen ? ' on' : ''}${guessed ? ' guess' : ''}`,
      'aria-pressed': String(chosen), disabled,
      onclick: e => { if (!chosen) choose(person, e.currentTarget); },
    }, person.name, guessed ? h('small', {}, 'guess') : null);
  });
}
