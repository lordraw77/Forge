# Forge user guide · Guida utente di Forge

A step-by-step, illustrated guide to every screen of Forge, in two languages.
Una guida illustrata, passo per passo, di ogni schermata di Forge, in due lingue.

| English | Italiano |
| --- | --- |
| [Start here → English guide](en/README.md) | [Inizia da qui → Guida in italiano](it/README.md) |

Every chapter exists in both languages with the same structure and the same numbered figures. The figures are real screenshots of the application running in that language: English figures in `images/en/`, Italian figures in `images/it/`.

Ogni capitolo esiste nelle due lingue con la stessa struttura e le stesse figure numerate. Le figure sono schermate reali dell'applicazione nella rispettiva lingua: figure inglesi in `images/en/`, italiane in `images/it/`.

## How the screenshots are made · Come si fanno le schermate

The pictures are **generated, not drawn by hand**, so they can be regenerated after any change of the interface, in both languages, with one command:

```bash
make docs/screenshots          # seeds the demo project, then takes every picture in en and it
make docs/screenshots SHOTS='^03-'   # only the pictures whose name starts with 03-
```

How it works:

- `scripts/docs-seed.py` creates the demo project **Forge Demo** (users, models, aliases, prompts, …) through the public API. It is idempotent: running it again changes nothing.
- `frontend/docs-shots/shots.ts` lists every picture: the page to open, the elements to mark with numbers, the steps to take. A step never depends on visible text, so the same list produces the English and the Italian picture.
- `frontend/docs-shots/shots.spec.ts` runs the list with Playwright in a container, once per language, and writes `images/<language>/<name>.png`.
- Numbered orange markers are drawn on the page just before the picture is taken; the chapter text refers to them ("marker 3").
- The development stack contains leftovers of the automated tests. The runner hides them from the pictures by filtering the lists returned by the API (`frontend/docs-shots/session.ts`); nothing is deleted.

Le immagini sono **generate, non disegnate a mano**: si rigenerano dopo ogni modifica dell'interfaccia, in entrambe le lingue, con un solo comando (vedi sopra). Lo script `scripts/docs-seed.py` crea il progetto dimostrativo **Forge Demo**; `frontend/docs-shots/shots.ts` elenca ogni immagine; i passaggi non dipendono mai dal testo visibile, quindi la stessa lista produce l'immagine italiana e quella inglese. I dati dimostrativi (nomi e descrizioni di prompt, agenti, ecc.) sono in inglese in entrambe le lingue perché sono contenuto, non interfaccia.

## Conventions · Convenzioni

- **Bold** is the text of a button, tab or field exactly as it appears on screen in that language. · Il **grassetto** è il testo di un pulsante, scheda o campo così com'è sullo schermo in quella lingua.
- `Code` is something you type or a technical name. · Il `codice` è ciò che si digita o un nome tecnico.
- "Marker 3" is the orange circle numbered 3 in the figure above the text. · «Indicatore 3» è il cerchio arancione numerato 3 nella figura sopra il testo.
