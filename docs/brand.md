# Greenlight brand: Bold Signal

Greenlight should feel like an app people want to open, not like the accounting system. Every screen leads with one big answer, a number, a count or a decision, and everything else is quieter. Colour means status, never decoration.

## The idea

A request's approval route is a row of signal lights. Green is approved, amber is waiting, hollow is next in line, red is sent back. The light that is waiting for you breathes. The same lights appear in lists, on the request page, in the review cards and in the app icon, so the meaning is learned once.

One vivid signal green is the only accent. It is always a fill with ink on it: the primary button, the "+", the current tab, the unread count, the approved-but-unpaid part of a spend bar. It never carries text on white, because it is too light to read there.

## Type

| Role | Font | Used for |
| --- | --- | --- |
| Display | Archivo, self-hosted variable font, set heavy (800 to 900) and condensed (`--condensed: 75%`) | Page titles, section titles, counts and money |
| Reading | The platform's own font: SF Pro on Apple devices, Segoe UI on Windows, Roboto on Android | Body text, labels, form fields, buttons, hints |

Page titles are uppercase and tight ("NADIA, 3 NEED YOUR OK"). On a request, in a list or on a review card the amount is the one giant, so the request's own title stays in normal case below it. Money always uses tabular figures, and an amount in a local currency always shows its US dollar value too.

| Token | Size | Use |
| --- | --- | --- |
| `--step--2` | 12 px | Currency codes, captions, counts |
| `--step--1` | 14 px | Secondary lines, hints |
| `--step-0` | 16 px | Body |
| `--step-1` | 18 px | Lead paragraphs |
| `--step-2` | 22 px | Section titles, amounts in lists |
| `--step-3` | 28 to 36 px | Totals, card titles |
| `--step-4` | 40 to 60 px | Page titles |
| `--step-5` | 48 to 80 px | The amount on a request |

## Colour

| Token | Value | Meaning |
| --- | --- | --- |
| `--canvas` | #ffffff | Page |
| `--sunken` | #f4f6f3 | Cards you act on, fields, quiet buttons |
| `--line` | #e3e8e3 | Hairlines between rows |
| `--ink` | #0b1a12 | Text, dark cards, section rules |
| `--muted` | #5e6b64 | Secondary text |
| `--signal` | #3ddc84 | The one accent, as a fill |
| `--signal-ink` | #0b6b47 | Links and green text on white |
| `--go` | #12804f | Approved |
| `--wait` / `--wait-mark` | #ffb224 / #b56a00 | Waiting: the fill, and the light's ring |
| `--stop` | #c8372d | Sent back, over the approval |
| `--focus` | ink, or white on ink surfaces | The 3px keyboard focus ring |

Each status also has a wash for backgrounds and an ink for text on that wash.

Measured contrast (WCAG 2.2; text needs 4.5:1, lights and other state marks 3:1):

| Pair | Ratio | Allowed for |
| --- | --- | --- |
| ink on canvas | 17.9 | Text |
| ink on signal | 10.1 | Text: buttons, counts |
| muted on canvas / on sunken | 5.6 / 5.1 | Text |
| signal-ink on canvas | 6.6 | Links |
| go on canvas | 5.0 | Text and lights |
| stop on canvas | 5.2 | Text and lights |
| wait-mark on canvas | 4.2 | Lights only, never text |
| go-ink on go-wash, wait-ink on wait-wash, stop-ink on stop-wash | 7.1, 7.3, 7.1 | Text on status washes |
| signal on canvas | 1.8 | Never on its own: signal only appears as a fill with ink on it |

## Spend bars

Money in Payments and Insights is drawn as one bar per row: paid in ink, approved and still to pay in signal, waiting for approval in wait, with a 2px canvas gap between parts. The numbers are always written beside the bar in the same order, so the bar is never the only way to read them. A project's bar is a bullet bar: the track is its budget and an ink tick marks the budget line.

## Shape and layout

- Buttons are pills (`--r-pill`). The primary button is signal with ink text; the others are sunken with ink text. A danger button is the stop wash, and a solid stop for the final "send back".
- Cards (`--r-card`, sunken, no border, no shadow) are only for things you act on: a request in review, a demo step, a suggestion to accept, a drop zone. Sections are open, under a title with a 2px ink rule.
- No gradients, glass or shadows. Hierarchy comes from size, weight and solid fills.
- Spacing comes from the `--s-*` scale, 4 px steps up to 64 px.
- Phone: a header with the bell, and a bottom tab bar with the signal "+". Desktop: a sidebar with one "New request" and the current page as an ink pill.

## Voice

Short, plain sentences in the second person: "Nadia, 3 need your OK", "Send it back to Grace", "All clear". Buttons say what they do. AI is named where it acts ("Read by AI from invoice.pdf") and always leaves the decision to a person.

## Motion

One moment per screen at most: the waiting light breathes, a toast's light turns signal green, a decided review card slides away. Responses use an ease-out under 300 ms. The breathing light is a slow ambient ease-in-out on purpose, since it is a state rather than a response. Everything respects reduced motion.
