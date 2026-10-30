---
version: alpha
name: Cineplex Foyer
description: A cinema reservation workspace with a quiet lilac foyer, a plum auditorium and a yellow selected-seat ticket.
colors:
  background: "#F3F2F6"
  paper: "#FFFFFF"
  ink: "#30283D"
  muted: "#71697D"
  line: "#DAD6E0"
  primary: "#68445E"
  primary-hover: "#513349"
  auditorium: "#302A3D"
  auditorium-raised: "#3D354B"
  auditorium-text: "#F6F2FA"
  auditorium-muted: "#C7BED3"
  seat-border: "#ADA0BB"
  ticket: "#F3CE78"
  success: "#28714F"
  success-surface: "#EDF5EF"
  danger: "#A03941"
  danger-surface: "#FCEFF0"
  warning: "#7A570F"
  warning-surface: "#FFF5DB"
typography:
  display:
    fontFamily: "Manrope, Segoe UI, sans-serif"
    fontSize: 40px
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: -1px
  heading:
    fontFamily: "Manrope, Segoe UI, sans-serif"
    fontSize: 24px
    fontWeight: 700
    lineHeight: 1.3
  body:
    fontFamily: "Manrope, Segoe UI, sans-serif"
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.6
  small:
    fontFamily: "Manrope, Segoe UI, sans-serif"
    fontSize: 12px
    fontWeight: 500
    lineHeight: 1.5
rounded:
  control: 8px
  auditorium: 16px
spacing:
  xs: 4px
  sm: 8px
  md: 12px
  lg: 16px
  xl: 24px
  xxl: 32px
  section: 48px
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.paper}"
    rounded: "{rounded.control}"
  seat-selected:
    backgroundColor: "{colors.ticket}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
  input:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
---

# Cineplex Foyer

## Product and purpose

Cineplex is a working cinema reservation app and a university gRPC demo. The
primary journey is selecting a schedule, choosing one available seat, entering
a name, and receiving a booking result. Streaming, retries and the diagnostic
tools are real features. There is no payment or authentication flow.

The source of truth for titles, dates, studios, availability and booking results
is the existing API. Existing images are illustrative artwork for seeded demo
films, never evidence of a real cinema programme. Do not invent duration, age
rating, reviews or availability. The existing Rp50.000 price is a simulation.

## Direction and rationale

The light foyer surrounds one dark auditorium. That contrast makes the seat
decision the focal point; the interface opens with the workflow, not a hero.
The warm yellow selection resembles a ticket and is confined to selected seats
and the seat number on the reservation slip. Mulberry is the primary action.
The second identity device is a dashed ticket separation on the summary, where
the selection becomes a reservation. No ornament elsewhere needs perforations.

Design read: reservation workspace for cinema visitors and practical-course
demonstrators, contemporary cinema foyer, ENERGY 2 / RHYTHM 2 / MOTION 1.
The three panels are different on purpose: programme list, auditorium and form.
No equally weighted feature-card grid, decorative numbers or oversized banner.

## Typography

Use Manrope for its open counters, friendly geometric forms and clear seat
numbers. Segoe UI is the offline Windows fallback; no font dependency is needed
to use the application. Load weights 400/500/600/700/800 only. Use 40px for the
page title (30px mobile), 24px for the movie title, 18px section headings, 14px
body/controls, 12px secondary labels. Ordinary labels use sentence case. Brand
spelling CINEPLEX is the existing proper name. IDs/logs may use Consolas 12px.
Text lines should stay under 75 characters. Body line-height is 1.6.

## Layout and responsive behaviour

The page is one route, `/`, with real anchors `#reservation` and `#systemTools`.
The app header has a wordmark, those two destinations and a real stream status.
There is no navigation for absent catalogue, account or payment pages.

At >=1180px, use a maximum 1320px canvas with 32px side gutters and three columns:
244px programme / flexible auditorium (minimum 360px) / 300px reservation.
Columns align at the top, never stretch to the tallest unrelated content.
At 800-1179px, programme spans the page as compact schedule rows; auditorium
and summary use two columns. Below 800px, schedule selection becomes a vertical
compact list and the auditorium precedes the full-width reservation form. Keep
film title and time visible; never make selection depend on horizontal swiping.
At <=480px, use 16px gutters, 44px seats and a 12px centre aisle. At 320px, the
five-seat row plus row label must fit without clipping or horizontal overflow.
Do not mask layout failures with body overflow-x:hidden.

Use spacing tokens 4/8/12/16/24/32/48px. The programme and ticket are unframed
on the page; the auditorium alone is a large coloured surface. Diagnostics sit
below the reservation workspace in a native disclosure. No hero or fixed
floating purchase bar. A receipt follows the booking result, not a new route.

## Surfaces and components

Page background uses background; inputs and ticket use paper. Use line for quiet
separators; muted for secondary text. Auditorium uses auditorium and raised for
seats. Its text is auditorium-text, its secondary labels auditorium-muted.
Main action uses primary with white text; hover uses primary-hover. Use borders
before shadows. Only notifications may have a small 0 8px 24px ink/10% shadow.
Radius: 8px controls, 16px auditorium; no pill-shaped containers.

- Programme: button rows with fixed 52x72px illustrative posters. Selection has
  a subtle primary tint, border and explicit selected label. Titles wrap.
- Seats: native 48x46px buttons (44px mobile) with chair-shaped top corners and
  a quiet base. Available has an outline, selected has ticket fill and a check,
  booked has a cross plus disabled semantics. Never imply unknown seats exist.
  Preserve focus on rerender when its seat still exists. Row letters and aisle
  communicate actual layout, not decoration. The screen is a simple curved line.
- Ticket: paper surface with a dashed divider between film/time and seat/name.
  Selected number is prominent. Price is labelled simulation. Name input has a
  visible label. One prominent Book action; disabled with a reason before selection.
- Request details: native details for the readonly UUID and generate action.
  Technical descriptions stay secondary to the reservation journey.
- Feedback: inline persistent result with title, message and booking ID, plus a
  short toast in a polite live region. Errors say what to do next. Unknown
  outcome must keep the original UUID/payload and offer retry, never a new order.
- Diagnostics: real buttons for deduplication, concurrency and benchmark.
  Busy/disabled states prevent overlapping runs. Present measured benchmark as
  a labelled table with units, not fabricated statistics. Log entries wrap long
  IDs and are bounded. Clear log is a button. Diagnostics must not disappear.
- No new dialog, table library, icon library, routing or authentication system.

## Interaction and motion

Use 140ms background/border/opacity transitions only as feedback to user action.
No looping status dots, scroll reveals, animated gradients or moving seats.
Respect prefers-reduced-motion. Loading occupies the same stable auditorium
area and includes text. Connection errors offer recovery and disable stale seat
choices until a fresh snapshot. Stream reconnection must not reset pending orders.
Every visible control has an implementation and loading, error or empty feedback.

## Accessibility

Normal text must meet 4.5:1; large text and control outlines 3:1. Do not lower
opacity on secondary text to create untested colours. Primary focus is a 3px
mulberry ring on light; ticket yellow ring on auditorium. All targets >=44px.
Use native buttons, labels, details/summary, a skip link, header/nav/main/footer,
one h1 and ordered headings. Tab/Enter/Space must work without a mouse. Selected
and booked state cannot rely on colour alone. Use aria-pressed and disabled on
seats; role=status on connection and booking results. Log is not an aria-live
firehose. Toast live region is polite. Inputs keep readable focus styles.

## Consistency and maintenance

`web/static/css/app.css` derives its variables from these tokens. All frontend
behaviour lives in `web/static/js/app.js`. Keep existing API paths and business
rules. Do not introduce another CSS system or dependency. Existing routes and
Python/gRPC/database code do not change for a visual redesign. Use the existing
brand as text; do not create a new logo or invent customer/financial claims.
Validate this file with `npx @google/design.md lint DESIGN.md`, test RPC/state,
and inspect screenshots at desktop, tablet, 375px and 320px before handoff.
