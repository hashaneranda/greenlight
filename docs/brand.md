# Greenlight brand

Greenlight should feel like an app people want to open, not like the accounting system. Every screen answers one question: does this spend have a green light yet?

## The idea

A request's approval route is a row of signal lights. Green is approved, amber is waiting, hollow is next in line, red is sent back. The light that is waiting for you breathes. The same lights appear in lists, on the request page, in the app icon and in the review cards, so the meaning is learned once.

## Type

| Role | Font | Used for |
| --- | --- | --- |
| Primary, the brand voice | Bricolage Grotesque, self-hosted, variable weight and width | Page titles, section titles, money, totals, the wordmark |
| Secondary, for reading | The platform's own font: SF Pro on Apple devices, Segoe UI on Windows, Roboto on Android | Body text, labels, form fields, buttons, hints |

The brand font carries character where the eye lands first: the title and the amount. Everything people read for longer is set in the font their phone already uses, which is the most legible choice on that screen and costs nothing to load.

Bricolage is set slightly condensed (`--brand-width: 88%`) for titles and figures, so large amounts fit on a phone.

### Scale

One scale, defined as tokens in `app.css`. Nothing else is used.

| Token | Size | Use |
| --- | --- | --- |
| `--step--2` | 12 px | Currency codes, captions |
| `--step--1` | 14 px | Secondary lines, hints, footnotes |
| `--step-0` | 16 px | Body |
| `--step-1` | 18 px | Section titles, amounts in lists |
| `--step-2` | 22 px | Card titles, totals |
| `--step-3` | 28 px | Large titles |
| `--step-4` | 32 to 40 px | Page titles |
| `--step-5` | 38 to 52 px | The amount on a request |

Weights: regular 400 for reading, medium 600 for labels and emphasis, bold 700 for titles and money.

## Colour

| Token | Value | Meaning |
| --- | --- | --- |
| `--ink` | #132820 | Text, dark surfaces |
| `--paper` | #f2f4f1 | Page background |
| `--malachite` | #0d6a4e | Brand, primary actions, links |
| `--go` | #23a46b | Approved |
| `--wait` | #e49a0e | Waiting for someone |
| `--stop` | #c4402c | Sent back, over the approval |

Status colours are only used for status. Each one has a pale wash for backgrounds and a dark ink for text on that wash, both of which pass WCAG AA.

## Voice

Short, plain sentences in the second person: "Nadia, 1 needs your OK", "Send it back to Grace", "All clear". Buttons say what they do. AI is named where it acts ("Read by AI from invoice.pdf") and always leaves the decision to a person.

## Motion

One moment per screen at most: the waiting light breathes, a toast's light turns green on approval, a review card slides away when decided. Everything respects reduced motion.
