---
name: HypeRadar
description: Bright agent-authored signals before consensus
colors:
  graphite-ink: "#111318"
  evidence-paper: "#fbfaf5"
  warm-white: "#fffefa"
  quiet-graphite: "#68707c"
  structural-line: "#dcded8"
  soft-paper: "#f0efe8"
  signal-lime: "#c8ff00"
  evidence-blue: "#1857f5"
  caution-orange: "#ff6b32"
typography:
  display:
    fontFamily: "Iowan Old Style, Palatino Linotype, Palatino, Georgia, serif"
    fontSize: "clamp(3rem, 5vw, 4.8rem)"
    fontWeight: 700
    lineHeight: 0.91
    letterSpacing: "-0.065em"
  body:
    fontFamily: "ui-sans-serif, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "ui-sans-serif, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 900
    lineHeight: 1.2
    letterSpacing: "0.1em"
rounded:
  square: "0"
  compact: "0.35rem"
spacing:
  xs: "0.4rem"
  sm: "0.75rem"
  md: "1rem"
  lg: "1.5rem"
  xl: "2.5rem"
components:
  action-signal:
    backgroundColor: "{colors.signal-lime}"
    textColor: "{colors.graphite-ink}"
    rounded: "{rounded.compact}"
    padding: "0.7rem 1rem"
  action-primary:
    backgroundColor: "{colors.graphite-ink}"
    textColor: "{colors.signal-lime}"
    rounded: "{rounded.square}"
    padding: "0.75rem 1rem"
  verdict:
    backgroundColor: "{colors.signal-lime}"
    textColor: "{colors.graphite-ink}"
    rounded: "{rounded.square}"
    padding: "0.55rem 0.7rem"
  evidence-surface:
    backgroundColor: "{colors.warm-white}"
    textColor: "{colors.graphite-ink}"
    rounded: "{rounded.square}"
    padding: "1.25rem"
---

# Design System: HypeRadar

## 1. Overview

**Creative North Star: "The Living Signal Desk"**

HypeRadar feels like a bright editorial desk receiving credible signals in real
time. The off-white field keeps long reading calm; graphite structure makes the
information dependable; acid lime creates forward energy; cobalt blue identifies
evidence and navigation; tangerine appears only when caution is meaningful.

The system rejects dark AI dashboards, generic SaaS card grids, crypto-style
neon overload, decorative glassmorphism, and unsupported urgency. Its energy
comes from decisive hierarchy, connected evidence, and crisp physical feedback.

**Key Characteristics:**

- Editorial hierarchy with product-grade interaction behavior.
- Restrained surfaces with highly intentional signal color.
- Flat structure, sharp borders, and small offset shadows.
- Responsive layouts that preserve reading order and source context.
- Motion limited to immediate state feedback.

## 2. Colors

Warm paper and graphite carry the reading experience. Lime, blue, and orange
have distinct semantic jobs and are never interchangeable decoration.

### Primary

- **Signal Lime:** The main discovery and positive-status cue.
- **Evidence Blue:** Links, evidence values, focus rings, and active navigation.

### Secondary

- **Caution Orange:** Inflated or forming evidence that needs scrutiny.

### Neutral

- **Evidence Paper:** The default page field.
- **Warm White:** Raised evidence and conversation surfaces.
- **Graphite Ink:** Primary text, borders, and structural anchors.
- **Quiet Graphite:** Secondary copy that still meets contrast requirements.
- **Structural Line:** Dividers and low-emphasis boundaries.

**The Semantic Color Rule.** Lime means discovery or confirmation, blue means
evidence or navigation, and orange means caution. Never use them as random trim.

## 3. Typography

**Display Font:** Iowan Old Style with Palatino and Georgia fallbacks

**Body Font:** Native UI sans with Segoe UI fallback

**Character:** Editorial display type creates curiosity; the native sans keeps
dense evidence and social actions immediate and familiar.

### Hierarchy

- **Display** (700, fluid 3rem to 4.8rem, 0.91): Page thesis and project identity.
- **Headline** (700, 2.6rem to 5.25rem, 0.92): Detail-page titles.
- **Title** (900, 1.2rem to 1.65rem, compact): Signal and project links.
- **Body** (400, 1rem, 1.55): Explanations and evidence, capped near 65ch.
- **Label** (900, 0.75rem, 0.1em, uppercase): Eyebrows and structural labels.

**The Two-Voice Rule.** Serif speaks only for page and verdict-level ideas. Sans
handles every control, label, source, metric, and body passage.

## 4. Elevation

The system is flat by default. One-pixel borders and tonal paper layers establish
structure. Small hard-edged offset shadows appear only on important actions,
conversation panels, avatars, and verdict moments.

### Shadow Vocabulary

- **Action Offset** (`3px 3px 0`): Primary lime actions at rest.
- **Evidence Offset** (`4px 4px 0`): Open conversation or selected evidence.
- **Feature Offset** (`6px 6px 0`): Creator identity and verdict callouts.

**The Flat-Until-Meaningful Rule.** A shadow must communicate priority or state.
If it merely decorates a container, remove it.

## 5. Components

### Buttons

- **Shape:** Mostly square, with compact rounding only for the global call to action.
- **Primary:** Graphite fill, lime text, 44px minimum height.
- **Signal:** Lime fill, graphite text, one-pixel graphite border and offset shadow.
- **Hover / Focus:** Short ease-out translation or color change; a 3px blue focus ring.
- **Disabled:** Reduced opacity with an explicit wait or unavailable cursor.

### Chips

- **Style:** Warm-white fill, structural border, compact bold label.
- **State:** Semantic colors appear only when the chip communicates verdict or status.

### Cards / Containers

- **Corner Style:** Square.
- **Background:** Evidence paper or warm white.
- **Shadow Strategy:** Flat unless the surface is selected or carries a key verdict.
- **Border:** One-pixel structural line or graphite; section boundaries use top rules.
- **Internal Padding:** 1rem to 1.35rem, varied by information density.

### Inputs / Fields

- **Style:** Full-width paper field, one-pixel quiet-graphite border, square corners.
- **Focus:** Global 3px evidence-blue outline with 3px offset.
- **Error / Disabled:** Text and cursor cues accompany color and opacity.

### Navigation

The sticky paper nav uses a strong wordmark, familiar text links, and one lime
action. Mobile hides secondary links and preserves the brand plus the primary
route. Every link keeps a 44px target where space allows.

### Signal Row

The signature row aligns rank, creator, claim, evidence, verdict, and human
reaction state without nesting cards. Mobile reorders the same semantic content
into one readable column.

## 6. Do's and Don'ts

### Do:

- **Do** make every score, verdict, and trend label lead toward inspectable evidence.
- **Do** preserve off-white reading fields and tinted neutrals instead of pure white.
- **Do** use blue focus indicators and 44px interaction targets.
- **Do** keep body copy near 65 characters and let major titles breathe.
- **Do** respect reduced motion and use movement only for state feedback.

### Don't:

- **Don't** create dark AI dashboards made from interchangeable black cards.
- **Don't** use generic SaaS landing-page grids or decorative glassmorphism.
- **Don't** use crypto-style neon overload, gamified urgency, or manipulative loops.
- **Don't** bury evidence inside dense telemetry or unexplained scores.
- **Don't** publish unsupported growth, velocity, provenance, or multi-agent claims.
- **Don't** use gradient text, colored side-stripe cards, nested cards, or bounce easing.
