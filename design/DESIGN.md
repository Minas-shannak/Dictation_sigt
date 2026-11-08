# Dictation app: "Booth 07" design spec

A calm, retro language-lab look: warm paper-cream background, an olive speaker-grille panel around the video, chunky "tape deck" keys, and one orange "record light" accent. No gradients, no neon, no emoji.

The three reference mockups in this folder (`home.dc.html`, `practice.dc.html`, `video-complete.dc.html`) show the exact markup and inline styles. They are design-tool files, not runnable pages:
- `{{panel}}` = `#4F5B45`
- `{{rec}}` = `#D9622B`
- `{{done}}` = `#2F6B5E`
- `{{current}}` = `#B5521F`
- `{{hint}}` = `#6A4FA0`
- Ignore `<x-dc>`, `<helmet>`, `support.js` and the `<script type="text/x-dc">` block.

---

## 1. Design tokens (put these in `:root`)

```css
:root {
  /* surfaces */
  --bg:            #EDE6D6;  /* page background, paper cream */
  --bg-band:       #E3DAC6;  /* alternate section band (Home "Pick up a tape") */
  --card:          #F5F0E4;  /* cards: stats, caption timing */
  --card-inset:    #FBF8F1;  /* tiles inside cards, typing "answer sheet", tables */
  --input-bg:      #F7F2E7;
  --console-frame: #CDBE9F;  /* tan frame around the player */
  --panel:         #4F5B45;  /* olive speaker grille */
  --screen:        #1A1C17;  /* video area, cassette window */
  --cassette:      #2B2E27;  /* cassette body */
  --key:           #F3ECDD;  /* tape-deck key face */
  --key-edge:      #A8987A;  /* tape-deck key bottom edge */

  /* lines */
  --line:          #D3C8B1;
  --line-soft:     #E3D9C4;
  --line-strong:   #B9AA8A;  /* input borders */
  --ruled:         #EFE6D3;  /* ruled lines on the answer sheet */

  /* text */
  --ink:           #23261F;
  --ink-2:         #4A473E;
  --ink-muted:     #5E5A4E;
  --on-dark:       #F3ECDD;
  --on-dark-muted: #B8B19F;

  /* accents */
  --rec:           #D9622B;  /* record light, play button */
  --rec-edge:      #8E3C14;
  --link:          #A3461A;
  --error:         #B8423A;
  --error-text:    #A3332C;
  --missed-bg:     #F6DFCF;
  --missed-text:   #8E3C14;

  /* dictation word colors */
  --word-done:     #2F6B5E;  /* finished words */
  --word-current:  #B5521F;  /* current word + underline */
  --word-hint:     #6A4FA0;  /* hinted letter */
  --word-hint-bg:  #ECE4F6;
  --word-hidden:   #8C8578;  /* the dots */
}
```

## 2. Typography

Google Fonts:
```html
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Familjen+Grotesk:wght@400..700&family=Atkinson+Hyperlegible:wght@400;700&family=JetBrains+Mono:wght@400;600&display=swap">
```

| Role | Font | Notes |
|---|---|---|
| Headings, logo, big stat numbers | Familjen Grotesk 700 | tight letter-spacing (-0.02em to -0.035em) |
| Body, buttons, labels | Atkinson Hyperlegible 400/700 | |
| Anything typed, the dictation line, counters, small caps labels | JetBrains Mono 400/600 | small labels: 12–13px, uppercase, letter-spacing 0.12em |

The dictation line is JetBrains Mono, 30px, line-height 1.5, 18px gap between words.

## 3. Components

**Logo:** the "Sound to text" mark: three sound bars become two dots, then an orange cursor. Files are in `design/logo/`:
- `logo-mark.svg` (dark ink, for light backgrounds) and `logo-mark-light.svg` (cream, for dark backgrounds).
- In the header, inline the mark at 30×30px, then "Dictation" in Familjen Grotesk 700 22px with a 10px gap. The cursor bar uses `--rec`.
- Favicon: copy `favicon.svg`, `favicon-32.png` and `apple-touch-icon.png` into `static/` and add to `<head>`:
  ```html
  <link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
  <link rel="icon" href="/static/favicon-32.png" sizes="32x32" type="image/png">
  <link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
  ```

**Pill input + button:** inputs are pill-shaped (`border-radius: 999px`), 1.5px `--line-strong` border, `--input-bg` fill, JetBrains Mono text. The primary button is an olive (`--panel`) pill with cream text, sometimes with a small orange dot before the label.

**Tape-deck keys (player controls):** 52×46px, radius 7px, `--key` face, no border except `border-bottom: 4px solid var(--key-edge)`. Play/pause is the same shape in `--rec` with a `--rec-edge` bottom edge and white icon. The Hint key uses `--word-hint` for its text and icon. On press, translate down 2px and shrink the bottom edge to 2px.

**Speaker grille:** an olive panel with a dot pattern:
```css
background-color: var(--panel);
background-image: radial-gradient(circle, rgba(0,0,0,.28) 1.6px, transparent 1.8px);
background-size: 13px 13px;
```

**Player console:** an outer `--console-frame` box (radius 22px, padding 14px, soft shadow `0 24px 48px -30px rgba(35,38,31,.45)`). Inside is the grille panel (radius 14px, padding 18px). Inside that is the video (`--screen`, radius 8px, 16:9) with a small mono label at top-left, e.g. "SIDE A · LINE 014 · PAUSED".

**Line counter:** split-flap style. Each digit is a cream tile (20×28px, radius 3px, mono 600 16px) on a dark `--ink` strip. Next to it: "/ 86", a cream progress bar on `rgba(0,0,0,.35)`, and the time.

**Cards:** `--card` background, 1px `--line` border, radius 18px. Inner stat tiles use `--card-inset`, a 1px `--line-soft` border and radius 10px. Big numbers are Familjen Grotesk 700 in `--ink`.

**Missed-word chips:** pill-shaped, `--missed-bg` background, `--missed-text` color, mono 13px.

**Answer sheet (typing area):** `--card-inset` background, radius 18px, with ruled lines:
```css
background-image: repeating-linear-gradient(to bottom, transparent 0, transparent 47px, var(--ruled) 47px, var(--ruled) 48px);
```
- Finished words: `--word-done`.
- Current word: typed letters in `--word-current` with a 3px `--word-current` underline.
- Hinted letter: `--word-hint` on a `--word-hint-bg` rounded highlight.
- A blinking cursor (`▍`) follows the current word.
- Hidden letters are `·` dots in `--word-hidden`.
- The typing input is a 58px-tall white pill. On a wrong letter, its border turns `--error` and a status line appears in `--error-text`: a red dot, then "Wrong letter · 2 of 3 tries before a hint".

**Keycaps (shortcut legend):** `<kbd>` elements with `--input-bg` fill, 1px `#C9BB9E` border, 3–4px bottom border, JetBrains Mono.

**Cassette (recent videos and results):**
- Body: a `--cassette` card, radius 16px.
- Label: a cream strip with a 7px colored top border holding the video title.
- Window: a dark pill with two reels made of brown `#6B4F3A` circles, each with a cream hub.
- Progress: reel sizes show it. The left reel is big at the start, and the right reel grows as lines are finished.

## 4. Screens / app states

The app is one page, so these are **states**, not separate pages.

### A. Home (no video loaded) — reference `home.dc.html`
- **Header:** logo, links (Your tapes · How it works · Keys), and a round headphones mute button.
- **Hero**, two columns that stack on mobile:
  - Left side:
    - A small mono label: "BOOTH 07 · LISTENING PRACTICE".
    - The H1 "Train your ear with any YouTube video.", clamp(42px, 5.2vw, 70px).
    - The subtitle.
    - A URL pill input and a "Start listening" olive pill button with an orange dot.
    - A hint line.
  - Right side: a decorative console illustration. It shows the grille, the video screen with faint waveform bars, a subtitle bar with a half-typed line, two spinning reels, three transport keys and a "014" counter.
- **"Pick up a tape"** on a `--bg-band` strip: recent videos as cassettes, each with "Line X / Y" and "Resume →", or "Done · WPM · %" and "Results →".
- **"One line at a time, at your pace":** three cards with grille headers numbered 01, 02 and 03.
- **"Hands stay on the keys":** a grid of the shortcut keycaps.
- **Footer:** dark `--ink` bar reading "Runs on your computer" and "python app.py → 127.0.0.1:8000".

### B. Practice (video loaded) — reference `practice.dc.html`
- **Header:** logo, a URL pill with an olive Load button, the mute button and the volume slider (`accent-color: var(--panel)`).
- **Row:** the player console on the left (flex 999 1 560px) and the sidebar on the right (flex 1 1 300px).
  - Console, top to bottom: the video, the counter row, then the keys row (prev, replay, play, next, 0.75×, spacer, Hint, fullscreen).
  - Sidebar card "THIS TAPE": WPM and accuracy tiles, plus missed-word chips.
  - Sidebar card "CAPTION TIMING": round − and + keys around the offset value, e.g. "−0.20 s".
- **Answer sheet:**
  - Header: "Type what you heard" and "This line · 38 WPM · 96%".
  - The dictation line.
  - The input with its error status.
  - A dashed divider, then a legend (Finished, Current word, Hinted letter) and shortcut keycaps.

### C. Video complete — reference `video-complete.dc.html`
- **Header:** mono label "SIDE A · 86 / 86 LINES", H1 "End of tape.", and the video title and length. A fully wound cassette sits beside it.
- **Stats:** a `--card` panel holding four inset tiles: WPM, Accuracy, Time practicing, and Hints used (that number in `--word-hint`).
- **"Words to listen to again":** a table showing each word (mono, `--error-text`), its misses as small red dots, the line number (zero-padded), and a "▶ Replay line" key.
- **Actions:** "Practice missed lines" (olive pill), "Load another tape" (outline pill) and "Rewind and start over" (text link).

## 5. Motion
- **Cursor blink:** `steps(1)`, 1.1s.
- **Reels:** rotate at 7s per turn, linear. They spin only while the video is playing.
- **Key press:** 2px push down.
- **Reduced motion:** wrap all of these in `@media (prefers-reduced-motion: reduce) { animation: none }`.

## 6. Responsive and accessibility
- Max content width is 1240px with a 16px side gutter. Rows use flex-wrap so the sidebar drops under the player on narrow screens, and wide tables scroll inside their box.
- Touch targets are at least 44px. Icon-only buttons need an `aria-label` that includes the shortcut, e.g. "Replay line (Tab)".
- Body text is at least 13px, and every color pair above passes 4.5:1 contrast.
