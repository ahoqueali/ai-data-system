# Understanding the Three Modeling Layers: Ontology, Semantic Model, and Data Model

When building a data platform on BigQuery (or any warehouse), it helps to separate *what things
mean* from *how they're queried* from *how they're stored*. These are three distinct layers, and
conflating them is one of the most common sources of confusion in data modeling work. This
document explains each layer, why it exists, and how they relate to one another.

---

## Overview

| Layer | Question it answers | Changes how often | Owned by |
|---|---|---|---|
| **Ontology** | What *is* a Customer, an Order, a Region — and how do they relate? | Rarely | Business/domain experts, data architects |
| **Semantic model** | What metrics and dimensions can I query, and what do they mean? | Occasionally | Analytics engineers |
| **Data model** | What tables, columns, and types physically exist in BigQuery? | Frequently | Data engineers |

The dependency flows in one direction:

```
Ontology  --maps to-->  Semantic Model  --built on-->  Data Model  --lives in-->  BigQuery
```

The ontology is the most stable and abstract layer. The data model is the most concrete and most
likely to change (new columns, renamed tables, schema migrations). The semantic model is the
buffer between them — it's what shields your business logic from physical schema churn.

---

## 1. The Ontology Layer

**What it is:** A conceptual description of the entities in your business domain, their
properties, and the relationships between them — independent of any database, tool, or query
language.

**What it answers:** "What do we mean when we say 'Customer'? What is a Customer allowed to have,
and what can it relate to?"

**Why it exists:** Systems change. Databases get migrated, BI tools get replaced, teams rewrite
pipelines — but the underlying business concepts (a Customer places Orders; an Order has a status)
stay true regardless of implementation. The ontology captures that stable, tool-agnostic truth.

**What it contains:**
- **Entities** — the "nouns" of the business (Customer, Order, Region, Product)
- **Properties** — attributes an entity has (a Customer has a Region, an Order has an Amount)
- **Relationships** — how entities connect, including cardinality (one Customer places many
  Orders; one Order is placed by exactly one Customer)
- **Mappings** — pointers down to the semantic model, so the abstract concept can actually be
  queried

**Example structure:**

```yaml
entity: Customer
description: "A party who has signed up and may place one or more Orders"
properties:
  - name: hasRegion
    range: Region
  - name: hasLifetimeValue
    range: Money
relationships:
  - name: places
    target: Order
    cardinality: one-to-many
maps_to:
  semantic_model: customer
  dimensions: [region]
  measures: [lifetime_value]
```

**Tooling note:** Lightweight YAML like the above is often enough for analytics teams. If you need
formal reasoning, inference, or interoperability with external systems (e.g. sharing a shared
vocabulary across companies), a full ontology language like OWL or RDF (Turtle/JSON-LD), managed
in a tool like Protégé, is more appropriate — treat the YAML as a simplified bridge on top of that
canonical source.

---

## 2. The Semantic Model Layer

**What it is:** The business-facing abstraction over your physical tables — the metrics,
dimensions, and relationships that analysts and BI tools actually query. This is where a column
like `lifetime_value` becomes a governed, reusable metric like `total_ltv`.

**What it answers:** "What can I measure, slice, and filter by — and what's the single agreed-upon
definition, so two dashboards never disagree on how 'revenue' is calculated?"

**Why it exists:** Without this layer, every analyst and every BI tool reinvents its own SQL for
"total revenue" or "active customers" — and they subtly diverge. The semantic model centralizes
that logic once, so it's defined a single time and consumed everywhere.

**What it contains:**
- **Entities** — join keys, usually tied to a primary key in a table
- **Dimensions** — attributes you group or filter by (region, signup date, status)
- **Measures** — raw aggregations over a column (sum of lifetime_value, count of orders)
- **Metrics** — named, reusable business calculations built from measures (`total_ltv`,
  `avg_order_value`), including derived ones like ratios

**Example structure (dbt/MetricFlow style):**

```yaml
semantic_models:
  - name: customer
    model: ref('customer')
    entities:
      - name: customer
        type: primary
        expr: customer_id
    dimensions:
      - name: region
        type: categorical
    measures:
      - name: lifetime_value
        agg: sum

metrics:
  - name: total_ltv
    type: simple
    type_params:
      measure: lifetime_value
```

**Tooling note:** This example uses dbt's MetricFlow syntax, but the same concepts map directly
onto Cube's schema format or Google's Malloy language, which compiles straight to BigQuery SQL
without needing dbt at all.

---

## 3. The Data Model Layer

**What it is:** The physical, literal structure of your data as it exists in BigQuery — table
names, column names, types, partitioning, and clustering.

**What it answers:** "What table do I actually query, what's the column called, what type is it,
and how is it physically organized for performance and cost?"

**Why it exists:** This is the ground truth — the layer that actually stores bytes. Every other
layer ultimately resolves down to this one. Physical modeling decisions here (partitioning,
clustering, types) directly affect query cost and performance in BigQuery, which is why it's kept
separate from the more abstract layers above — you want the freedom to optimize physical storage
without breaking every dashboard built on top of it.

**What it contains:**
- **Tables/columns** — names, types (STRING, NUMERIC, DATE, etc.), and required/nullable mode
- **Partitioning** — usually by a date column, to prune scanned data and control cost
- **Clustering** — to speed up filtering/joining on frequently used columns
- **Descriptions** — column-level documentation, ideally carried into BigQuery's own metadata

**Example structure:**

```yaml
table: customer
dataset: core
partition_by:
  field: signup_date
  type: DATE
cluster_by: [region, customer_id]
columns:
  - name: customer_id
    type: STRING
    mode: REQUIRED
  - name: lifetime_value
    type: NUMERIC
```

This YAML is typically compiled into a `CREATE TABLE` DDL statement and deployed to BigQuery
directly, keeping schema changes version-controlled and reviewable like any other code change.

---

## How the Layers Relate

A single business concept flows through all three layers like this:

1. **Ontology** says: *"A Customer has a lifetime value."* (a stable business fact)
2. **Semantic model** says: *"`lifetime_value` is a `sum` measure on the `customer` entity, and
   `total_ltv` is the metric analysts query."* (a governed, queryable definition)
3. **Data model** says: *"`lifetime_value` is a `NUMERIC` column in `core.customer`, clustered by
   `region`."* (the physical reality in BigQuery)

If the data engineering team renames a column or migrates a table, only the data model and its
direct semantic mapping need to change — the ontology, and anything built on top of the semantic
layer's metric names, stays untouched. This is the core value of keeping the layers separate:
**change isolation**. A physical schema migration shouldn't force a rewrite of every dashboard, and
a shift in business terminology shouldn't require a database migration.

---

## Quick Reference: Who Touches What

- **Data engineers** own the **data model** — they decide partitioning, clustering, types, and
  physical table structure for cost and performance.
- **Analytics engineers** own the **semantic model** — they decide what metrics exist, how they're
  calculated, and what dimensions are exposed to BI tools.
- **Domain experts / architects** own the **ontology** — they decide what the business concepts
  actually are and how they relate, independent of any particular tool or database.

Keeping these three responsibilities and artifacts separate — even when a small team wears all
three hats — makes the system easier to reason about, audit, and evolve over time.

# BigQuery Ontology / Semantic / Data Model Stack

A minimal, working example of three linked modeling layers, all defined in YAML:

```
ontology/           <- conceptual layer: entities, properties, relationships
semantic_models/     <- business layer: dimensions, measures, metrics (dbt/MetricFlow style)
data_model/           <- physical layer: BigQuery table schemas
scripts/
  generate_ddl.py     <- data_model/*.yaml -> generated_ddl/*.sql (BigQuery DDL)
  validate_mapping.py <- checks ontology -> semantic -> data_model references are consistent
generated_ddl/        <- output of generate_ddl.py (gitignore-able, regenerated on demand)
```

## Why three layers

- **data_model/**: what actually exists in BigQuery (tables, columns, types, partitioning).
- **semantic_models/**: what analysts/BI tools query (metrics like `total_ltv`, dimensions like `region`),
  decoupled from physical column names so the physical model can evolve independently.
- **ontology/**: what the business actually means by "Customer", "SavingAccount", etc. — entities
  and relationships that are true regardless of which database or semantic tool you use. This is the
  layer that stays stable even if you migrate off BigQuery or dbt entirely.

## Files by layer

| Entity | Ontology | Semantic Model | Data Model | DDL |
|---|---|---|---|---|
| Customer | `ontology/customer.yaml` | `semantic_models/customer.yml` | `data_model/customer.yaml` | `generated_ddl/customer.sql` |
| SavingAccount | `ontology/saving_account.yaml` | `semantic_models/saving_account.yml` | `data_model/saving_account.yaml` | `generated_ddl/saving_account.sql` |
| CurrentAccount | `ontology/current_account.yaml` | `semantic_models/current_account.yml` | `data_model/current_account.yaml` | `generated_ddl/current_account.sql` |
| MortgageAccount | `ontology/mortgage_account.yaml` | `semantic_models/mortgage_account.yml` | `data_model/mortgage_account.yaml` | `generated_ddl/mortgage_account.sql` |
| Transaction | `ontology/transaction.yaml` | `semantic_models/transaction.yml` | `data_model/transaction.yaml` | `generated_ddl/transaction.sql` |

Dependency direction: **ontology → semantic_models → data_model**. Each ontology entity's
`maps_to` block points at a semantic model; each semantic model's `model: ref(...)` points at a
data_model table.

## Entities

| Entity | Table | Description |
|---|---|---|
| Customer | `customer` | A party who has signed up and may hold Accounts and Mortgages |
| SavingAccount | `saving_account` | A deposit account that earns interest |
| CurrentAccount | `current_account` | A transactional account for everyday spending |
| MortgageAccount | `mortgage_account` | A secured loan used to finance a real estate purchase |
| Transaction | `transaction` | A financial event that debits or credits an Account balance |

## Usage

```bash
pip install pyyaml --break-system-packages

# 1. Generate BigQuery DDL from the data model YAML
python scripts/generate_ddl.py
cat generated_ddl/customer.sql

# 2. (optional) Actually deploy to BigQuery — requires `bq` CLI configured
python scripts/generate_ddl.py --apply

# 3. Validate that ontology / semantic / data model layers are consistent
python scripts/validate_mapping.py
```

Run `validate_mapping.py` in CI on every PR that touches `data_model/`, `semantic_models/`, or
`ontology/` — it catches broken references (e.g. an ontology mapping to a measure that was
renamed or removed) before they hit production.

## Extending this

- **More tables**: add a YAML file to `data_model/`, a matching block to `semantic_models/`, and
  an entity to `ontology/` with a `maps_to` pointing at it.
- **Real ontology rigor**: if you need formal inference/reasoning or interoperability with other
  systems, treat `ontology/*.yaml` as a lightweight bridge and maintain the canonical ontology in
  OWL/RDF (e.g. via Protégé), regenerating the YAML bridge from it.
- **Semantic layer engine**: this example mirrors dbt's MetricFlow YAML syntax. It also translates
  directly to Cube's schema format or to Malloy source/view definitions if you'd rather compile
  straight to BigQuery SQL without dbt.
- **Drift detection against live BigQuery**: extend `validate_mapping.py` to also query
  `INFORMATION_SCHEMA.COLUMNS` and diff against `data_model/*.yaml`, so you catch cases where
  someone manually altered a table outside of this pipeline.
