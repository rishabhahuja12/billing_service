# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Backend and infrastructure engineers / SaaS founders who need to verify that metering, quota enforcement, and invoice generation are working correctly. Used in a development or staging context, sitting at a laptop with multiple browser tabs open — not on a phone, not in a dark room. The user's job is to confirm that events are being recorded accurately, pricing math is correct, and invoices reflect real usage.

## Product Purpose

A Usage Metering & Billing Engine dashboard that lets operators inject synthetic usage events, observe real-time cost accumulation, verify idempotency and quota enforcement, and generate invoices — all without touching the API directly. The backend is a FastAPI service with SQLite, integer micro-cent pricing, and Stripe test-mode webhooks.

## Positioning

Production-grade integer-safe billing math (micro-cents, not floats) with strict idempotency keys and pre-action quota gates — the kind of financial correctness infrastructure that most toy billing engines skip.

## Operating Context

- Developer runs `uvicorn app.main:app --port 8000` locally
- Dashboard served at `http://localhost:8000/` as a single static HTML file
- API endpoints: `GET /api/v1/customers/{tenant_id}/usage`, `POST /api/v1/events`, `POST /api/v1/invoices`, `GET /health`
- Multiple tenants selectable (tenant_001, tenant_002, etc.)
- Used alongside Swagger docs at `/docs`

## Capabilities and Constraints

- Single HTML file — no build step, no npm, no framework
- Must keep all existing JS API calls intact
- Sections required: health status, 4 metric cards, event simulator form, activity console log, invoice table
- 10 pytest tests must continue to pass (no backend changes)
- Tenant switcher must update all data views

## Brand Commitments

None — no existing brand, no logo, no color constraints. The old UI (dark glassmorphism + purple) is the anti-reference.

## Evidence on Hand

- DESIGN.md: full system architecture and pricing spec
- Working API server at localhost:8000
- 10/10 pytest tests passing

## Product Principles

1. Precision over decoration — billing math is serious; the UI should feel like it respects that
2. Data legibility first — numbers must be instantly scannable; layout serves the data, not the other way
3. Operator-grade, not consumer-grade — this is a tool, not a product page; density and efficiency beat friendliness
