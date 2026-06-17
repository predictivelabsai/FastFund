# SFO Hub — User Guide

SFO Hub is an AI relationship-manager for JTC Group's Private Office. It helps you
understand a family office's needs and surface the right next services — in a
single conversational workspace.

## The workspace

The app is three panes (the same layout as TaxHub):

- **Left — Client book.** Every family office, with AUM, domicile and lifecycle
  stage (lead / onboarding / client). Click one to open it. Below are quick
  actions and links to the dashboard, service catalogue and help.
- **Centre — AI Assistant.** Chat with the advisor. It answers in a warm,
  advisory voice, grounded in the open family's profile, and shows which
  specialist agent it used (⚙ chips).
- **Right — Workspace.** The open family's profile (AUM, family, asset-mix bar,
  current services, pain points) and their live **recommendations**, each with a
  cross-sell / upsell tag, a fit %, an estimated annual value, and a rationale.

## Typical flows

**Review an existing client.** Open the family → ask *"Where are the gaps in
their current services?"* or *"What should we offer them next?"*. The advisor
runs the recommender and explains each suggestion. Use **↻ Regenerate** in the
right pane to re-run the engine after the conversation surfaces new context.

**New lead.** Open a lead-stage family → ask *"Tell me about this family's
governance setup"* to walk an intake conversation, then *"What should we offer
them next?"* to turn it into a costed roadmap.

**Sales / training.** Use the **Analytics dashboard** to see the service-interest
heatmap, the upsell funnel, and the simulated pipeline value — and to practise
conversations against the synthetic book.

## How recommendations are made

A transparent **rule catalogue** fires on the profile (current services, asset
mix, pain points, AUM, stage). The **cross-sell graph** expands the candidate set
with services that commonly bundle together. When an LLM is configured, an **AI
layer** re-ranks each candidate for *this* family and rewrites the rationale in a
relationship-manager voice. Every recommendation is traceable to the rule or
graph edge that produced it (shown as its `source`).

## Good things to ask

- "Give me a profile summary of this family."
- "Where are the gaps in their current services?"
- "What should we offer them next, and why?"
- "Explain JTC's luxury asset administration."
- "How do family offices typically allocate capital?"

## Notes

- **All data is synthetic.** No real client data is used; the book is generated
  by `data/synth.py`.
- Without an AI key the advisor still works — it falls back to the rule-based
  recommender, so you always get suggestions.
